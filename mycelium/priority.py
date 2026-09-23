"""Приоритет проверки: «кого смотреть первым» (CLAUDE.md §10).

priority_score = min-max( Σ вес × компонент ) × (0.5 для курьеров), где компоненты —
перцентильные ранги 0–1 среди узлов с рёбрами, кроме веса роли и устойчивости.
Это список для ПРОВЕРКИ (кто организует), а не план блокировки — он в resilience.py (§11).
"""
from __future__ import annotations

import pandas as pd

from mycelium import config
from mycelium.evidence import top_why
from mycelium.features import pct_rank


def priority_components(df: pd.DataFrame) -> pd.DataFrame:
    """Компоненты 0–1 до взвешивания. Узлы без рёбер — нули."""
    m = df.has_edges
    comp = pd.DataFrame(index=df.index)
    comp["role_weight"] = df.role.map(config.ROLE_WEIGHT).astype(float)
    comp["cut_kzt"] = pct_rank(df.cut_kzt, m)
    comp["betweenness"] = pct_rank(df.betweenness, m)
    comp["in_deg"] = pct_rank(df.in_deg, m)
    comp["flow_kzt"] = pct_rank(df.flow_kzt, m)
    comp["role_score"] = df.role_score.where(df.role != "peripheral", 0.0).astype(float)
    comp.loc[~m] = 0.0
    return comp


def add_priority(df: pd.DataFrame) -> pd.DataFrame:
    """Колонки priority_score, priority_raw и contrib_<фактор> (вклад после веса и множителя)."""
    lost = [c for c in config.PRIORITY_WEIGHTS if c not in ("role_weight",) and c not in df.columns]
    if lost:
        raise ValueError(f"Для приоритета не хватает колонок: {lost}")
    out = df.copy()
    comp = priority_components(out)
    mult = out.is_seed.map({True: config.SEED_MULTIPLIER, False: 1.0}).astype(float)
    contrib = comp.mul(pd.Series(config.PRIORITY_WEIGHTS)).mul(mult, axis=0)
    raw = contrib.sum(axis=1)
    lo, hi = raw.min(), raw.max()
    out["priority_raw"] = raw
    out["priority_score"] = ((raw - lo) / (hi - lo) if hi > lo else raw * 0).astype(float)
    for c in contrib.columns:
        out[f"contrib_{c}"] = contrib[c]
    return out


def top_factors(r: pd.Series) -> list[str]:
    """Факторы по убыванию вклада (без веса роли — роль и так названа в evidence)."""
    names = [f for f in config.PRIORITY_WEIGHTS if f != "role_weight"]
    return sorted(names, key=lambda f: -r[f"contrib_{f}"])


def ranked(df: pd.DataFrame) -> pd.DataFrame:
    """Все узлы по убыванию приоритета; при равенстве — детерминированно по gid."""
    return df.assign(_gid=df.index).sort_values(["priority_score", "_gid"],
                                                ascending=[False, True]).drop(columns="_gid")


def add_rank(df: pd.DataFrame, n: int = config.TOP_N) -> pd.DataFrame:
    """rank 1…n для топа, остальным — NA."""
    out = df.copy()
    order = ranked(out).index[:n]
    out["rank"] = pd.Series(range(1, len(order) + 1), index=order).reindex(out.index).astype("Int64")
    return out


def top_table(df: pd.DataFrame, n: int = config.TOP_N) -> pd.DataFrame:
    """Схема top_nodes.csv: rank, gid, role, priority_score, why."""
    top = ranked(df).head(n)
    return pd.DataFrame({
        "rank": range(1, len(top) + 1),
        "gid": top.index.astype("int64"),
        "role": top.role.values,
        "priority_score": top.priority_score.values,
        "why": [top_why(r, top_factors(r)) for _, r in top.iterrows()],
    })
