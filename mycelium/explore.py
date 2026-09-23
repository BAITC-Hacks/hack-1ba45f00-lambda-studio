"""Калибровка порогов на данных кейса «Граф денег».

Запуск:  python -m mycelium.explore --data data
Самодостаточный: не импортирует остальной пакет, чтобы работать с первой минуты.
Печатает распределения метрик, счётчики ролей при текущих порогах,
устойчивость ролей и проверку стратегий блокировки. Ничего не записывает.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

SEED = 42
# Пороги, откалиброванные на данных (дублируются в config.py — источник правды там).
TH = {
    "COORD_MIN_IN": 8,        # координатор: собирает минимум от
    "COORD_MIN_OUT": 20,      # ...и раздаёт минимум на
    "DIST_MIN_OUT": 20,       # распределитель: получателей минимум
    "CONS_MIN_IN": 6,         # консолидатор: плательщиков минимум
    "TR_MIN_PASS": 0.8,       # транзит: отдал дальше минимум 80 % полученного
    "TR_MAX_HOLD": 3,         # транзит: медиана удержания не больше, дней
    "TERM_MIN_KZT": 200_000,  # конечный получатель: осело минимум, ₸
}
INT_KEYS = {"COORD_MIN_IN", "COORD_MIN_OUT", "DIST_MIN_OUT", "CONS_MIN_IN", "TR_MAX_HOLD"}


def load(data: Path):
    E = pd.read_parquet(data / "edges.parquet")
    N = pd.read_parquet(data / "nodes.parquet")
    T = pd.read_parquet(data / "transactions.parquet")
    T["date"] = pd.to_datetime(T["date"])
    G = nx.DiGraph()
    G.add_nodes_from(N.gid)
    for r in E.itertuples(index=False):
        G.add_edge(r.src, r.dst, sum_kzt=float(r.sum_kzt), n_tx=int(r.n_tx), depth=int(r.depth))
    return E, N, T, G


def features(N, T, G) -> pd.DataFrame:
    df = N.set_index("gid")[["depth", "is_seed"]].copy()
    df["in_deg"] = pd.Series(dict(G.in_degree()))
    df["out_deg"] = pd.Series(dict(G.out_degree()))
    df["in_kzt"] = pd.Series(dict(G.in_degree(weight="sum_kzt")))
    df["out_kzt"] = pd.Series(dict(G.out_degree(weight="sum_kzt")))
    df["flow_kzt"] = df[["in_kzt", "out_kzt"]].max(axis=1)
    ok = (df.in_kzt > 0) & ~df.is_seed           # у seed входящие занижены — не считаем
    df["pass_through"] = np.where(ok, df.out_kzt / df.in_kzt.where(df.in_kzt > 0), np.nan)
    df["truncated"] = (df.depth == 4) & (df.out_deg == 0)
    seeds = list(df.index[df.is_seed])
    reach = dict.fromkeys(G, 0)
    for s in seeds:
        for v in nx.descendants(G, s):
            reach[v] += 1
    df["seed_reach"] = pd.Series(reach)
    df["betweenness"] = pd.Series(nx.betweenness_centrality(G))
    scc = {v: len(c) for c in nx.strongly_connected_components(G) for v in c}
    df["core_size"] = pd.Series(scc)                  # >1 → узел в кольце (деньги возвращаются)
    # медиана удержания: для каждого входящего — дней до ближайшего исходящего не раньше даты входа
    outs = T.groupby("src")["date"].apply(lambda s: np.sort(s.values))
    hold = {}
    for g, grp in T.groupby("dst"):
        if g not in outs.index:
            continue
        od = outs[g]
        idx = np.searchsorted(od, grp.date.values)
        h = [(od[i] - d) / np.timedelta64(1, "D") for i, d in zip(idx, grp.date.values) if i < len(od)]
        if h:
            hold[g] = float(np.median(h))
    df["hold_median_days"] = pd.Series(hold)
    df["sync_max_payers"] = T.groupby(["dst", "date"])["src"].nunique().groupby("dst").max()
    df["sync_max_payers"] = df["sync_max_payers"].fillna(0).astype(int)
    return df


def reach_kzt(G, seeds, removed=()):
    H = G.copy()
    H.remove_nodes_from(removed)
    live = set()
    for s in seeds:
        if s in H:
            live |= nx.descendants(H, s) | {s}
    tot = deep = 0.0
    for u, _, d in H.edges(data=True):
        if u in live:
            tot += d["sum_kzt"]
            deep += d["sum_kzt"] if d["depth"] >= 3 else 0.0
    return tot, deep


def cut_kzt(G, df):
    """Зависимый поток: сколько ₸ перестаёт двигаться от курьеров, если убрать только этот узел."""
    seeds = list(df.index[df.is_seed])
    base, _ = reach_kzt(G, seeds)
    res = {}
    for v in df.index[(~df.is_seed) & (df.out_deg > 0)]:
        res[v] = base - reach_kzt(G, seeds, [v])[0]
    return pd.Series(res).reindex(df.index).fillna(0.0)


def assign(df, th=TH) -> pd.Series:
    r = pd.Series("peripheral", index=df.index)
    orphan = (df.in_deg == 0) & (df.out_deg == 0)
    rules = [  # применяются снизу вверх, поэтому верхнее правило побеждает
        ("coordinator", (df.in_deg >= th["COORD_MIN_IN"]) & (df.out_deg >= th["COORD_MIN_OUT"])),
        ("distributor", df.out_deg >= th["DIST_MIN_OUT"]),
        ("consolidator", df.in_deg >= th["CONS_MIN_IN"]),
        ("transit", ~df.is_seed & ~df.truncated & (df.in_deg >= 1) & (df.out_deg >= 1)
                    & (df.pass_through >= th["TR_MIN_PASS"]) & (df.hold_median_days <= th["TR_MAX_HOLD"])),
        ("terminal", (df.out_deg == 0) & ~df.truncated & (df.in_deg >= 1) & (df.in_kzt >= th["TERM_MIN_KZT"])),
    ]
    for name, mask in reversed(rules):
        r[mask & ~orphan] = name
    return r


def stability(df, n=50):
    base = assign(df)
    rng = np.random.default_rng(SEED)
    same = np.zeros(len(df))
    for _ in range(n):
        th = {k: (max(1, round(v * rng.uniform(0.8, 1.2))) if k in INT_KEYS else v * rng.uniform(0.8, 1.2))
              for k, v in TH.items()}
        same += assign(df, th).values == base.values
    return base, pd.Series(same / n, index=df.index)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    a = ap.parse_args()
    t0 = time.time()
    _, N, T, G = load(Path(a.data))
    df = features(N, T, G)
    pd.set_option("display.width", 200)

    print("\n=== РАСПРЕДЕЛЕНИЯ ===")
    print("плательщиков (in_deg) ≥ 5:", df.in_deg[df.in_deg >= 5].value_counts().sort_index().to_dict())
    print("получателей (out_deg) ≥ 20:", sorted(df.out_deg[df.out_deg >= 20].tolist()))
    nm = df[~df.is_seed & (df.in_deg > 0) & (df.out_deg > 0)]
    print(f"не-seed с входом и выходом: {len(nm)}; пропуск 0.8–1.2: {nm.pass_through.between(0.8, 1.2).sum()}; "
          f"пропуск > 1.2 (вход вне выборки): {(nm.pass_through > 1.2).sum()}")
    print("обрезанные 4-м коленом:", int(df.truncated.sum()), "| макс. плательщиков у них:", int(df[df.truncated].in_deg.max()))
    print("охват курьеров (seed_reach):", df.seed_reach.value_counts().sort_index().to_dict())
    cores = sorted({c for c in df.core_size if c > 1}, reverse=True)
    print("кольца (сильно связные группы > 1 узла), размеры:", cores[:10], "| узлов в кольцах:", int((df.core_size > 1).sum()))

    print("\n=== РОЛИ ПРИ ТЕКУЩИХ ПОРОГАХ ===")
    role, score = stability(df)
    df["role"], df["role_score"] = role, score
    print(pd.DataFrame({"узлов": role.value_counts(), "средняя устойчивость": score.groupby(role).mean().round(2)}))
    print("роли курьеров:", role[df.is_seed].value_counts().to_dict())

    print("\n=== ЗАВИСИМЫЙ ПОТОК (блокировка одного узла) ===")
    df["cut_kzt"] = cut_kzt(G, df)
    cols = ["role", "in_deg", "out_deg", "in_kzt", "out_kzt", "cut_kzt"]
    print(df.sort_values("cut_kzt", ascending=False)[cols].head(10).to_string())

    print(f"\nГотово за {time.time() - t0:.1f} с")


if __name__ == "__main__":
    main()
