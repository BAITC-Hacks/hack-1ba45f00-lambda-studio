"""Блокировка: зависимый поток, оценка набора, жадный план, сравнение стратегий (CLAUDE.md §11).

«Проверять» и «блокировать» — разные вопросы: топ проверки (priority.py) ищет центры сбора и распределения,
план блокировки — узлы, через которые идут самые большие потоки. Выдаём оба.

Метрики после удаления набора узлов:
  reach_kzt    — сумма рёбер, отправитель которых достижим от оставшихся курьеров;
  deep_kzt     — то же для рёбер с depth ≥ 3 (сколько доходит до верхних уровней);
  n_components — слабосвязные компоненты среди оставшихся узлов с рёбрами.
Эталон расчёта — explore.reach_kzt; здесь то же самое на заранее построенном индексе (~1 мс на оценку),
потому что evaluate() вызывает и POST /api/block.
"""
from __future__ import annotations

from collections import deque
from typing import Iterable

import networkx as nx
import numpy as np
import pandas as pd

from mycelium import config
from mycelium.evidence import fmt_kzt

DEEP_MIN_DEPTH = 3
STRATEGY_SIZES = (5, 10, 20)
RANDOM_SETS = 100
PLAN_CANDIDATES = 80


class _Index:
    """Граф в виде массивов: быстрый BFS от курьеров и суммы по рёбрам."""

    def __init__(self, G: nx.DiGraph, seeds: Iterable[int]):
        self.nodes = list(G.nodes)
        self.pos = {v: i for i, v in enumerate(self.nodes)}
        self.succ = [[self.pos[w] for w in G.successors(v)] for v in self.nodes]
        edges = list(G.edges(data=True))
        self.eu = np.array([self.pos[u] for u, _, _ in edges], dtype=int)
        self.ev = np.array([self.pos[v] for _, v, _ in edges], dtype=int)
        self.esum = np.array([d["sum_kzt"] for _, _, d in edges], dtype=float)
        self.edeep = np.array([d["depth"] >= DEEP_MIN_DEPTH for _, _, d in edges], dtype=bool)
        self.seeds = [self.pos[s] for s in seeds if s in self.pos]
        self.n_edges = len(edges)

    def evaluate(self, removed_idx: set) -> dict:
        n = len(self.nodes)
        dead = np.zeros(n, dtype=bool)
        dead[list(removed_idx)] = True
        live = np.zeros(n, dtype=bool)
        q = deque(s for s in self.seeds if not dead[s])
        for s in q:
            live[s] = True
        while q:
            u = q.popleft()
            for w in self.succ[u]:
                if not live[w] and not dead[w]:
                    live[w] = True
                    q.append(w)
        keep = ~dead[self.eu] & ~dead[self.ev]
        moving = keep & live[self.eu]
        return {
            "reach_kzt": float(self.esum[moving].sum()),
            "deep_kzt": float(self.esum[moving & self.edeep].sum()),
            "n_components": self._components(keep),
        }

    def _components(self, keep: np.ndarray) -> int:
        parent = {}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for u, v in zip(self.eu[keep].tolist(), self.ev[keep].tolist()):
            parent.setdefault(u, u)
            parent.setdefault(v, v)
            ru, rv = find(u), find(v)
            if ru != rv:
                parent[ru] = rv
        return len({find(x) for x in parent})


def _index(G: nx.DiGraph, df: pd.DataFrame) -> _Index:
    """Индекс строится один раз на граф и кэшируется в G.graph."""
    key = (G.number_of_nodes(), G.number_of_edges())
    cached = G.graph.get("_resilience_index")
    if cached is None or cached[0] != key:
        cached = (key, _Index(G, df.index[df.is_seed]))
        G.graph["_resilience_index"] = cached
    return cached[1]


def _drop_pct(base: float, after: float) -> float:
    return round(100.0 * (base - after) / base, 1) if base else 0.0


def evaluate(G: nx.DiGraph, df: pd.DataFrame, removed: list) -> dict:
    """Метрики до и после удаления узлов removed (gid int). < 100 мс — используется в POST /api/block."""
    ix = _index(G, df)
    base = ix.evaluate(set())
    after = ix.evaluate({ix.pos[int(g)] for g in removed if int(g) in ix.pos})
    return {
        "baseline": base, "after": after,
        "drop_reach_pct": _drop_pct(base["reach_kzt"], after["reach_kzt"]),
        "drop_deep_pct": _drop_pct(base["deep_kzt"], after["deep_kzt"]),
        "removed": [int(g) for g in removed],
    }


def cut_kzt(G: nx.DiGraph, df: pd.DataFrame) -> pd.Series:
    """Зависимый поток: сколько ₸ перестаёт двигаться от курьеров, если убрать только этот узел.
    Считается для не-seed с исходящими; остальным 0."""
    ix = _index(G, df)
    base = ix.evaluate(set())["reach_kzt"]
    res = {}
    for v in df.index[(~df.is_seed) & (df.out_deg > 0)]:
        res[v] = base - ix.evaluate({ix.pos[v]})["reach_kzt"]
    return pd.Series(res, dtype=float).reindex(df.index).fillna(0.0)


def greedy_plan(G: nx.DiGraph, df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Жадный план: на каждом шаге из PLAN_CANDIDATES не-seed с наибольшим cut_kzt берём узел,
    удаление которого сильнее всего снижает reach_kzt с учётом уже удалённых. Схема blocking_plan.csv."""
    ix = _index(G, df)
    pool = df[~df.is_seed].assign(_gid=df.index[~df.is_seed]) \
        .sort_values(["cut_kzt", "_gid"], ascending=[False, True]).index[:PLAN_CANDIDATES].tolist()
    removed: list = []
    current = ix.evaluate(set())["reach_kzt"]
    rows = []
    for step in range(1, n + 1):
        best, best_reach = None, None
        taken = {ix.pos[g] for g in removed}
        for g in pool:
            if g in removed:
                continue
            r = ix.evaluate(taken | {ix.pos[g]})["reach_kzt"]
            if best_reach is None or r < best_reach - 1e-6:
                best, best_reach = g, r
        if best is None:
            break
        removed.append(best)
        marginal = current - best_reach
        current = best_reach
        rows.append({
            "step": step, "gid": int(best), "role": df.at[best, "role"],
            "cut_kzt_marginal": round(marginal, 2), "reach_kzt_after": round(best_reach, 2),
            "why": f"шаг {step}: блокировка отсекает ещё {fmt_kzt(marginal)} потока",
        })
    return pd.DataFrame(rows, columns=["step", "gid", "role", "cut_kzt_marginal", "reach_kzt_after", "why"])


def _strategy_entry(ix: _Index, base: dict, ids: list) -> dict:
    after = ix.evaluate({ix.pos[g] for g in ids})
    return {
        "removed": [str(g) for g in ids],
        "reach_kzt": round(after["reach_kzt"], 2), "deep_kzt": round(after["deep_kzt"], 2),
        "n_components": after["n_components"],
        "drop_reach_pct": _drop_pct(base["reach_kzt"], after["reach_kzt"]),
        "drop_deep_pct": _drop_pct(base["deep_kzt"], after["deep_kzt"]),
    }


def strategies(G: nx.DiGraph, df: pd.DataFrame, plan: pd.DataFrame) -> dict:
    """Форма resilience.json: baseline + стратегии priority / plan / turnover / random для N = 5/10/20."""
    ix = _index(G, df)
    base = ix.evaluate(set())
    cand = df[~df.is_seed].assign(_gid=df.index[~df.is_seed])
    by_priority = cand.sort_values(["priority_score", "_gid"], ascending=[False, True]).index.tolist()
    by_turnover = cand.sort_values(["flow_kzt", "_gid"], ascending=[False, True]).index.tolist()
    by_plan = plan.gid.astype("int64").tolist()
    pool = cand.index.to_numpy()
    rng = np.random.default_rng(config.SEED)

    out = {"baseline": {"reach_kzt": round(base["reach_kzt"], 2), "deep_kzt": round(base["deep_kzt"], 2),
                        "n_components": base["n_components"]},
           "strategies": {"priority": {}, "turnover": {}, "plan": {}, "random": {}}}
    for n in STRATEGY_SIZES:
        out["strategies"]["priority"][str(n)] = _strategy_entry(ix, base, by_priority[:n])
        out["strategies"]["turnover"][str(n)] = _strategy_entry(ix, base, by_turnover[:n])
        out["strategies"]["plan"][str(n)] = _strategy_entry(ix, base, by_plan[:n])
        runs = [ix.evaluate({ix.pos[int(g)] for g in rng.choice(pool, size=n, replace=False)})
                for _ in range(RANDOM_SETS)]
        mean = {k: float(np.mean([r[k] for r in runs])) for k in ("reach_kzt", "deep_kzt", "n_components")}
        out["strategies"]["random"][str(n)] = {
            "reach_kzt": round(mean["reach_kzt"], 2), "deep_kzt": round(mean["deep_kzt"], 2),
            "n_components": round(mean["n_components"], 1),
            "drop_reach_pct": _drop_pct(base["reach_kzt"], mean["reach_kzt"]),
            "drop_deep_pct": _drop_pct(base["deep_kzt"], mean["deep_kzt"]),
        }
    return out
