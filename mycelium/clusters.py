"""Кластеры (CLAUDE.md §9): Louvain на ненаправленной проекции и шаблонные гипотезы.

Направление переводов при кластеризации теряется — это оговорено в README и ROLES.md.
Роли и приоритет при этом считаются по направленному графу.
"""
from __future__ import annotations

import math

import networkx as nx
import pandas as pd

from mycelium import config
from mycelium.evidence import fmt_kzt


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
