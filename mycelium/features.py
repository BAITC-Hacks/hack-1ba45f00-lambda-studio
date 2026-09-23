"""Метрики узла (CLAUDE.md §7). Временные метрики — в temporal.py (участник B).

Все метрики ролей направленные. Эталон расчётов — mycelium/explore.py.
"""
from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd

from mycelium import config


def node_features(nodes: pd.DataFrame, G: nx.DiGraph) -> pd.DataFrame:
    """Структурные метрики. index = gid (int)."""
    df = nodes.set_index("gid")[["depth", "is_seed"]].copy()
    df.index.name = "gid"

    def per_node(values: dict, dtype) -> pd.Series:
        return pd.Series(values).reindex(df.index).fillna(0).astype(dtype)

    df["in_deg"] = per_node(dict(G.in_degree()), int)
    df["out_deg"] = per_node(dict(G.out_degree()), int)
    df["in_kzt"] = per_node(dict(G.in_degree(weight="sum_kzt")), float)
    df["out_kzt"] = per_node(dict(G.out_degree(weight="sum_kzt")), float)
    df["in_tx"] = per_node(dict(G.in_degree(weight="n_tx")), int)
    df["out_tx"] = per_node(dict(G.out_degree(weight="n_tx")), int)
    df["flow_kzt"] = df[["in_kzt", "out_kzt"]].max(axis=1)
    df["has_edges"] = (df.in_deg + df.out_deg) > 0
    df["avg_in_tx_kzt"] = (df.in_kzt / df.in_tx.where(df.in_tx > 0)).astype(float)
    df["avg_out_tx_kzt"] = (df.out_kzt / df.out_tx.where(df.out_tx > 0)).astype(float)

    # Коэффициент пропуска. Для seed не считаем: граф собран от них, их входящие занижены.
    ok = (df.in_kzt > 0) & ~df.is_seed
    df["pass_through"] = (df.out_kzt / df.in_kzt.where(ok)).astype(float)

    # Граница выборки: на последнем колене исходящие не собирались → out_deg = 0 ничего не значит.
    # При depth < MAX_DEPTH узел раскрыт обходом, и out_deg = 0 — наблюдаемый факт «деньги остались».
    df["truncated"] = (df.depth == config.MAX_DEPTH) & (df.out_deg == 0)
    df["inflow_outside_sample"] = ~df.is_seed & (df.pass_through > config.INFLOW_OUTSIDE_PASS)

    seeds = [g for g in df.index[df.is_seed]]
    seed_set = set(seeds)
    reach = dict.fromkeys(df.index, 0)
    for s in seeds:
        for v in nx.descendants(G, s):
            reach[v] += 1
    df["seed_reach"] = pd.Series(reach).reindex(df.index).astype(int)
    df["n_seed_payers"] = pd.Series({v: sum(1 for u in G.predecessors(v) if u in seed_set)
                                     for v in df.index}).astype(int)

    df["betweenness"] = pd.Series(nx.betweenness_centrality(G)).reindex(df.index).astype(float)
    df["betweenness_pct"] = pct_rank(df.betweenness, df.has_edges)

    # Кольца: размер сильно связной компоненты. > 1 — деньги могут вернуться к отправителю.
    scc = {v: len(c) for c in nx.strongly_connected_components(G) for v in c}
    df["core_size"] = pd.Series(scc).reindex(df.index).fillna(1).astype(int)
    df["in_cycle"] = df.core_size > 1
    df["in_core"] = df.in_cycle & (df.core_size == df.core_size.max())   # главное ядро-кольцо
    return df


def add_temporal(df: pd.DataFrame, temporal: pd.DataFrame) -> pd.DataFrame:
    """Приклеивает метрики temporal.node_temporal (участник B) и проверяет их форму."""
    need = ["hold_median_days", "fast_pass_share", "sync_max_payers"]
    lost = [c for c in need if c not in temporal.columns]
    if lost:
        raise ValueError(f"temporal.node_temporal не вернул колонки {lost} (контракт §6.8)")
    out = df.copy()
    t = temporal[need].reindex(out.index)
    out["hold_median_days"] = t.hold_median_days.astype(float)
    out["fast_pass_share"] = t.fast_pass_share.astype(float)
    out["sync_max_payers"] = t.sync_max_payers.fillna(0).astype(int)
    return out


def add_cluster_features(df: pd.DataFrame, G: nx.DiGraph, cluster_id: pd.Series) -> pd.DataFrame:
    """cluster_id и clusters_touched — сколько разных кластеров среди соседей (в обе стороны)."""
    out = df.copy()
    cid = cluster_id.reindex(out.index)
    if cid.isna().any():
        raise ValueError(f"clusters.assign_clusters не дал cluster_id для {int(cid.isna().sum())} узлов")
    out["cluster_id"] = cid.astype(int)
    cmap = out.cluster_id.to_dict()
    out["clusters_touched"] = pd.Series(
        {v: len({cmap[u] for u in nx.all_neighbors(G, v)}) for v in out.index}).astype(int)
    return out


def pct_rank(values: pd.Series, mask: pd.Series) -> pd.Series:
    """Перцентильный ранг 0–1 среди узлов mask (узлы с рёбрами); остальным 0.

    Нулевое значение даёт 0, а не средний ранг среди нулей: у ~1 500 узлов cut_kzt = 0 и
    betweenness = 0, и без этого «ничего» получало бы ранг ≈ 0.34.
    """
    r = values[mask].rank(pct=True, method="average")
    r = r.where(values[mask] > 0, 0.0)
    return r.reindex(values.index).fillna(0.0).astype(float)


def upstream_seeds(G: nx.DiGraph, gid: int, seeds: set) -> list[int]:
    """Курьеры, чьи деньги по цепочке доходят до узла (для карточки и ИИ)."""
    return sorted(s for s in nx.ancestors(G, gid) if s in seeds)


def safe_float(x) -> float | None:
    """NaN → None (для JSON)."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f
