"""Запись выходов по контрактам CLAUDE.md §6. Нарушение контракта → ContractError (громко, по-русски)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from mycelium import config

NODES_REQUIRED = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]
NODES_EXTRA = ["is_seed", "depth", "truncated", "in_deg", "out_deg", "in_kzt", "out_kzt", "in_tx", "out_tx",
               "pass_through", "hold_median_days", "seed_reach", "n_seed_payers", "betweenness",
               "sync_max_payers", "in_cycle", "inflow_outside_sample"]
CLUSTERS_COLS = ["cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"]
TOP_COLS = ["rank", "gid", "role", "priority_score", "why"]


class ContractError(RuntimeError):
    """Выход не соответствует контракту §6."""


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ContractError(msg)


def _no_forbidden(texts: pd.Series, what: str) -> None:
    low = texts.str.lower()
    for w in config.FORBIDDEN_WORDS:
        hit = low.str.contains(w, regex=False)
        _need(not hit.any(), f"{what}: запрещённое слово «{w}» ({int(hit.sum())} строк) — только язык гипотез")


# ── nodes_roles.csv ───────────────────────────────────────────────────────────

def nodes_roles_table(df: pd.DataFrame, expected_gids: pd.Index) -> pd.DataFrame:
    t = df.reset_index().rename(columns={"index": "gid"})
    t = t[NODES_REQUIRED + NODES_EXTRA].copy()
    t["gid"] = t.gid.astype("int64")
    t["cluster_id"] = t.cluster_id.astype(int)
    for c in ("role_score", "priority_score"):
        t[c] = t[c].astype(float).round(4)
    for c in ("in_kzt", "out_kzt"):
        t[c] = t[c].round(2)
    t["pass_through"] = t.pass_through.round(4)
    t["betweenness"] = t.betweenness.round(6)
    t = t.sort_values("gid").reset_index(drop=True)

    _need(len(t) == len(expected_gids),
          f"nodes_roles.csv: {len(t)} строк, а узлов в nodes.parquet {len(expected_gids)}")
    _need(t.gid.is_unique, "nodes_roles.csv: дубли gid")
    _need(set(t.gid) == set(expected_gids), "nodes_roles.csv: gid не совпадают с nodes.parquet")
    empty = [c for c in NODES_REQUIRED if t[c].isna().any() or (t[c].astype(str).str.strip() == "").any()]
    _need(not empty, f"nodes_roles.csv: пустые значения в обязательных колонках {empty}")
    bad = sorted(set(t.role) - set(config.ROLES))
    _need(not bad, f"nodes_roles.csv: роли вне словаря {bad}")
    for c in ("role_score", "priority_score"):
        _need(t[c].between(0, 1).all(), f"nodes_roles.csv: {c} вне [0, 1]")
    _need((t.cluster_id >= 0).all(), "nodes_roles.csv: отрицательный cluster_id")
    long = t.evidence.str.len() > config.EVIDENCE_MAX_LEN
    _need(not long.any(), f"nodes_roles.csv: {int(long.sum())} evidence длиннее {config.EVIDENCE_MAX_LEN} символов")
    _need(t.evidence.str.contains(r"\d").all(), "nodes_roles.csv: evidence без чисел")
    _no_forbidden(t.evidence, "nodes_roles.csv")
    wrong = t.truncated & t.role.isin(["terminal", "transit", "distributor", "coordinator"])
    _need(not wrong.any(), f"nodes_roles.csv: {int(wrong.sum())} узлов границы выборки с недопустимой ролью")
    return t


# ── clusters.csv ──────────────────────────────────────────────────────────────

def clusters_table(clusters: pd.DataFrame, nodes_roles: pd.DataFrame) -> pd.DataFrame:
    lost = [c for c in CLUSTERS_COLS if c not in clusters.columns]
    _need(not lost, f"clusters.describe_clusters не вернул колонки {lost} (контракт §6.2)")
    t = clusters[CLUSTERS_COLS].copy()
    t["top_gids"] = t.top_gids.map(lambda v: "|".join(str(int(x)) for x in v) if isinstance(v, (list, tuple)) else str(v))
    t = t.sort_values("cluster_id").reset_index(drop=True)
    _need(t.cluster_id.is_unique, "clusters.csv: дубли cluster_id")
    missing = sorted(set(nodes_roles.cluster_id) - set(t.cluster_id))
    _need(not missing, f"clusters.csv: нет строк для cluster_id {missing[:10]} из nodes_roles.csv")
    _need(int(t.n_nodes.sum()) == len(nodes_roles), "clusters.csv: сумма n_nodes не равна числу узлов")
    _no_forbidden(t.hypothesis.astype(str), "clusters.csv")
    return t


# ── top_nodes.csv ─────────────────────────────────────────────────────────────

def top_nodes_table(top: pd.DataFrame) -> pd.DataFrame:
    t = top[TOP_COLS].copy()
    t["priority_score"] = t.priority_score.astype(float).round(4)
    _need(len(t) >= config.TOP_N_MIN, f"top_nodes.csv: {len(t)} строк, нужно минимум {config.TOP_N_MIN}")
    _need(t.priority_score.is_monotonic_decreasing, "top_nodes.csv: не отсортирован по убыванию приоритета")
    _need(t.gid.is_unique, "top_nodes.csv: дубли gid")
    long = t.why.str.len() > config.WHY_MAX_LEN
    _need(not long.any(), f"top_nodes.csv: why длиннее {config.WHY_MAX_LEN} символов")
    _no_forbidden(t.why, "top_nodes.csv")
    return t


# ── Запись ────────────────────────────────────────────────────────────────────

def write_csvs(df: pd.DataFrame, clusters: pd.DataFrame, top: pd.DataFrame,
               expected_gids: pd.Index, out_dir: Path = config.OUT_DIR) -> dict[str, Path]:
    """Проверяет все три таблицы и только потом пишет — чтобы не оставить полузаписанный out/."""
    nodes_t = nodes_roles_table(df, expected_gids)
    clusters_t = clusters_table(clusters, nodes_t)
    top_t = top_nodes_table(top)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"nodes_roles": out_dir / "nodes_roles.csv", "clusters": out_dir / "clusters.csv",
             "top_nodes": out_dir / "top_nodes.csv"}
    nodes_t.to_csv(paths["nodes_roles"], index=False)
    clusters_t.to_csv(paths["clusters"], index=False)
    top_t.to_csv(paths["top_nodes"], index=False)
    return paths
