"""Независимый пересчёт из data/*.parquet — эталон для смысловых тестов.

Намеренно НЕ импортирует модули пайплайна (features, roles, resilience, …): иначе код проверял бы сам
себя. Из пакета берутся только пороги (mycelium.config) — это входные параметры, а не логика.
Правила — по тексту CLAUDE.md §7–§12, написаны заново.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "out"


@lru_cache(maxsize=1)
def raw():
    E = pd.read_parquet(DATA / "edges.parquet")
    N = pd.read_parquet(DATA / "nodes.parquet")
    T = pd.read_parquet(DATA / "transactions.parquet")
    T["date"] = pd.to_datetime(T["date"])
    return E, N, T


@lru_cache(maxsize=1)
def graph() -> nx.DiGraph:
    E, N, _ = raw()
    G = nx.DiGraph()
    G.add_nodes_from(int(g) for g in N.gid)
    for s, d, k, n, dep in zip(E.src, E.dst, E.sum_kzt, E.n_tx, E.depth):
        G.add_edge(int(s), int(d), w=float(k), n=int(n), depth=int(dep))
    return G


@lru_cache(maxsize=1)
def metrics() -> pd.DataFrame:
    """Метрики узла заново: степени, суммы, пропуск, граница выборки, удержание."""
    E, N, T = raw()
    m = N.set_index("gid")[["depth", "is_seed"]].copy()
    m["in_deg"] = E.groupby("dst").size().reindex(m.index).fillna(0).astype(int)
    m["out_deg"] = E.groupby("src").size().reindex(m.index).fillna(0).astype(int)
    m["in_kzt"] = E.groupby("dst").sum_kzt.sum().reindex(m.index).fillna(0.0)
    m["out_kzt"] = E.groupby("src").sum_kzt.sum().reindex(m.index).fillna(0.0)
    m["pass_through"] = np.where(~m.is_seed & (m.in_kzt > 0), m.out_kzt / m.in_kzt.replace(0, np.nan), np.nan)
    m["truncated"] = (m.depth == 4) & (m.out_deg == 0)
    m["hold"] = pd.Series({g: hold_median(g) for g in m.index}, dtype=float)
    return m


def hold_median(g: int) -> float:
    """Для каждого входящего перевода — дней до ближайшего исходящего с датой ≥ даты входа; медиана."""
    _, _, T = raw()
    ins = T[T.dst == g].date.tolist()
    outs = sorted(T[T.src == g].date.tolist())
    gaps = []
    for d in ins:
        later = [o for o in outs if o >= d]
        if later:
            gaps.append((later[0] - d).days)
    return float(np.median(gaps)) if gaps else float("nan")


def role_by_rules(r: pd.Series, th: dict) -> str:
    """Правила CLAUDE.md §8.2, первое совпадение побеждает — своя реализация."""
    if r.in_deg == 0 and r.out_deg == 0:
        return "peripheral"
    if r.in_deg >= th["COORD_MIN_IN"] and r.out_deg >= th["COORD_MIN_OUT"]:
        return "coordinator"
    if r.out_deg >= th["DIST_MIN_OUT"]:
        return "distributor"
    if r.in_deg >= th["CONS_MIN_IN"]:
        return "consolidator"
    pt, hold = r.pass_through, r.hold
    if (not r.is_seed and not r.truncated and r.in_deg >= 1 and r.out_deg >= 1
            and pt == pt and pt >= th["TR_MIN_PASS"] and hold == hold and hold <= th["TR_MAX_HOLD"]):
        return "transit"
    if r.out_deg == 0 and not r.truncated and r.in_deg >= 1 and r.in_kzt >= th["TERM_MIN_KZT"]:
        return "terminal"
    return "peripheral"


def reach(removed: set = frozenset()) -> tuple[float, float]:
    """Поток денег курьеров: сумма рёбер, отправитель которых достижим от оставшихся курьеров (и всё/≥3 колено)."""
    G = graph()
    _, N, _ = raw()
    H = G.subgraph([v for v in G if v not in removed])
    live = set()
    for s in N.gid[N.is_seed]:
        s = int(s)
        if s in H:
            live |= {s} | nx.descendants(H, s)
    tot = deep = 0.0
    for u, v, d in H.edges(data=True):
        if u in live:
            tot += d["w"]
            deep += d["w"] if d["depth"] >= 3 else 0.0
    return tot, deep


def parse_kzt(text: str) -> list[tuple[float, float]]:
    """«1,8 млн ₸» → (1 800 000, допуск 50 000); «850 тыс. ₸» → (850 000, 500); «950 ₸» → (950, 0.5)."""
    out = []
    for m in re.finditer(r"(\d+(?:,\d+)?)\s*(млн|тыс\.)?\s*₸", text):
        num, unit = m.group(1), m.group(2)
        v = float(num.replace(",", "."))
        dec = len(num.split(",")[1]) if "," in num else 0
        mult = 1e6 if unit == "млн" else 1e3 if unit else 1.0
        out.append((v * mult, 0.5 * 10 ** (-dec) * mult))
    return out


def kzt_close(text_value: tuple[float, float], true_value: float) -> bool:
    v, tol = text_value
    return abs(v - true_value) <= tol + 1e-6
