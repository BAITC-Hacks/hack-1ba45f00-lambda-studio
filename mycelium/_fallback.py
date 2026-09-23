"""ВРЕМЕННЫЕ фолбэки для модулей участника B (temporal, clusters, resilience).

Нужны, только пока B не закоммитил свои файлы: pipeline.py импортирует модули B по сигнатурам
CLAUDE.md §6.8 и берёт функцию отсюда, лишь если модуля нет (с громким предупреждением).
Логика — как в эталоне explore.py и §7/§9. Когда модули B появятся, файл можно удалить.
"""
from __future__ import annotations

import math

import networkx as nx
import numpy as np
import pandas as pd

from mycelium import config
from mycelium import explore


# ── temporal.node_temporal ────────────────────────────────────────────────────

def node_temporal(T: pd.DataFrame) -> pd.DataFrame:
    """index=gid; hold_median_days, fast_pass_share, sync_max_payers."""
    outs = T.groupby("src")["date"].apply(lambda s: np.sort(s.values))
    hold, fast = {}, {}
    for g, grp in T.groupby("dst"):
        if g not in outs.index:
            continue
        od = outs[g]
        idx = np.searchsorted(od, grp.date.values)
        h, w = [], []
        for i, d, s in zip(idx, grp.date.values, grp.sum_kzt.values):
            if i < len(od):
                h.append((od[i] - d) / np.timedelta64(1, "D"))
                w.append(s)
        if h:
            hold[g] = float(np.median(h))
            fast[g] = float(sum(s for x, s in zip(h, w) if x <= config.FAST_PASS_DAYS) / grp.sum_kzt.sum())
    sync = T.groupby(["dst", "date"])["src"].nunique().groupby("dst").max()
    res = pd.DataFrame({"hold_median_days": pd.Series(hold, dtype=float),
                        "fast_pass_share": pd.Series(fast, dtype=float)})
    res = res.join(sync.rename("sync_max_payers"), how="outer")
    res["sync_max_payers"] = res.sync_max_payers.fillna(0).astype(int)
    res.index.name = "gid"
    return res


# ── clusters.assign_clusters / describe_clusters ──────────────────────────────

def assign_clusters(G: nx.DiGraph, df: pd.DataFrame) -> pd.Series:
    """Louvain на ненаправленной проекции, w = log1p(sum_kzt). 0 — узлы без рёбер; 1… по убыванию размера."""
    UG = nx.Graph()
    for u, v, d in G.edges(data=True):
        w = math.log1p(d["sum_kzt"])
        if UG.has_edge(u, v):          # встречные переводы складываем
            UG[u][v]["w"] += w
        else:
            UG.add_edge(u, v, w=w)
    comms = nx.community.louvain_communities(UG, weight="w", seed=config.SEED)
    comms = sorted(comms, key=lambda c: (-len(c), min(c)))
    cid = pd.Series(config.NO_EDGES_CLUSTER, index=df.index, dtype=int)
    for i, c in enumerate(comms, start=1):
        cid[list(c)] = i
    return cid


def describe_clusters(G: nx.DiGraph, df: pd.DataFrame) -> pd.DataFrame:
    """Схема clusters.csv. Вызывается после ролей и приоритета."""
    from mycelium.evidence import fmt_kzt

    cid = df.cluster_id
    internal: dict = {}
    inflow: dict = {}
    for u, v, d in G.edges(data=True):
        cu, cv = cid[u], cid[v]
        if cu == cv:
            internal[cu] = internal.get(cu, 0.0) + d["sum_kzt"]
        else:
            inflow.setdefault(cv, {}).setdefault(cu, 0.0)
            inflow[cv][cu] += d["sum_kzt"]
    max_core = df.core_size.max()
    rows = []
    for c, g in df.groupby("cluster_id", sort=True):
        g = g.assign(_gid=g.index).sort_values(["priority_score", "_gid"], ascending=[False, True])
        n_seed = int(g.is_seed.sum())
        s = internal.get(c, 0.0)
        by_role = lambda role: g[g.role == role]
        if c == config.NO_EDGES_CLUSTER:
            hyp = "Узлы без переводов ≥5 тыс. ₸ в выгрузке"
        elif len(by_role("coordinator")):
            k = len(by_role("coordinator"))
            hyp = (f"Признаки центра сети: координаторов {k}, курьеров {n_seed}, "
                   f"оборот {fmt_kzt(s)}; ключевой узел {by_role('coordinator').index[0]}")
        elif len(by_role("consolidator")) and n_seed >= 2:
            hyp = f"Признаки ячейки сбора: деньги курьеров ({n_seed}) сходятся к {by_role('consolidator').index[0]}"
        elif len(by_role("distributor")):
            d0 = by_role("distributor").iloc[0]
            hyp = f"Признаки веерной раздачи от {by_role('distributor').index[0]} на получателей: {int(d0.out_deg)}"
        elif n_seed == 0:
            src = sorted(inflow.get(c, {}).items(), key=lambda kv: -kv[1])[:3]
            hyp = ("Промежуточный слой без курьеров: принимает деньги из кластеров "
                   + (", ".join(str(k) for k, _ in src) if src else "—"))
        else:
            hyp = f"Малая группа вокруг курьера {g[g.is_seed].index[0]}: узлов {len(g)}, оборот {fmt_kzt(s)}"
        m = int(((g.core_size == max_core) & (g.core_size > 1)).sum())
        if m and c != config.NO_EDGES_CLUSTER:
            hyp += f"; в ядре-кольце узлов: {m}"
        rows.append({
            "cluster_id": int(c), "n_nodes": len(g), "n_seed": n_seed, "sum_kzt_internal": round(s, 2),
            "top_gids": "|".join(str(x) for x in g.index[:config.CLUSTER_TOP_GIDS]),
            "hypothesis": hyp,
        })
    return pd.DataFrame(rows)


# ── resilience.cut_kzt ────────────────────────────────────────────────────────

def cut_kzt(G: nx.DiGraph, df: pd.DataFrame) -> pd.Series:
    """Зависимый поток по узлу — эталон explore.cut_kzt."""
    return explore.cut_kzt(G, df).astype(float)
