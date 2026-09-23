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
               expected_gids: pd.Index, out_dir: Path = config.OUT_DIR) -> tuple[dict, dict]:
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
    return paths, {"nodes_roles": nodes_t, "clusters": clusters_t, "top_nodes": top_t}


# ── Прочие CSV (§6.4, §6.5) ───────────────────────────────────────────────────

def write_extra_csvs(requests: pd.DataFrame, plan: pd.DataFrame, out_dir: Path = config.OUT_DIR) -> dict[str, Path]:
    _need(len(requests) <= 30, "next_requests.csv: больше 30 строк")
    _need(set(requests.request_type) <= {"outgoing", "incoming"}, "next_requests.csv: неизвестный request_type")
    _need(len(plan) > 0, "blocking_plan.csv: пустой план")
    _no_forbidden(requests.reason.astype(str), "next_requests.csv")
    out_dir = Path(out_dir)
    paths = {"next_requests": out_dir / "next_requests.csv", "blocking_plan": out_dir / "blocking_plan.csv"}
    requests.to_csv(paths["next_requests"], index=False)
    plan.to_csv(paths["blocking_plan"], index=False)
    return paths


# ── JSON для API (§6.6) и facts.json (§6.7) ───────────────────────────────────

def jsonable(x):
    """numpy → python, NaN → None, рекурсивно. gid-строки формируются вызывающим кодом."""
    import math

    import numpy as np
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else f
    if x is pd.NA or x is None:
        return None
    return x


def write_json(obj, path: Path) -> Path:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(jsonable(obj), f, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return path


def _r(x, nd: int = 3):
    return None if x is None or pd.isna(x) else round(float(x), nd)


def _rank(v):
    return None if pd.isna(v) else int(v)


def graph_json(df: pd.DataFrame, G) -> dict:
    nodes = [{
        "id": str(g), "x": float(r.x), "y": float(r.y), "role": r.role, "role_score": _r(r.role_score, 2),
        "cluster": int(r.cluster_id), "priority": _r(r.priority_score), "rank": _rank(r["rank"]),
        "is_seed": bool(r.is_seed), "depth": int(r.depth), "truncated": bool(r.truncated),
        "in_core": bool(r.in_core), "in_kzt": _r(r.in_kzt, 2), "out_kzt": _r(r.out_kzt, 2), "evidence": r.evidence,
    } for g, r in df.iterrows()]
    edges = [{"source": str(u), "target": str(v), "sum_kzt": round(d["sum_kzt"], 2), "n_tx": int(d["n_tx"])}
             for u, v, d in G.edges(data=True)]
    return {"nodes": nodes, "edges": edges}


def meta_json(df: pd.DataFrame, G, summary: dict, n_clusters: int) -> dict:
    counts = df.role.value_counts()
    return {
        "roles": [{"id": r, "label": config.ROLE_LABELS[r], "color": config.ROLE_COLORS[r],
                   "count": int(counts.get(r, 0))} for r in config.ROLES],
        "network": {"n_nodes": len(df), "n_edges": G.number_of_edges(), "n_seeds": int(df.is_seed.sum()),
                    "total_kzt": round(summary["total_kzt"], 2), "n_clusters": int(n_clusters),
                    "core_size": int(df.core_size.max()), "period": summary["period"]},
        "thresholds": dict(config.ROLE_THRESHOLDS),
    }


def _neighbors(G, g: int, df: pd.DataFrame, direction: str, limit: int) -> list[dict]:
    it = G.in_edges(g, data=True) if direction == "in" else G.out_edges(g, data=True)
    rows = [((u if direction == "in" else v), d) for u, v, d in it]
    rows.sort(key=lambda t: (-t[1]["sum_kzt"], t[0]))
    return [{"id": str(n), "sum_kzt": round(d["sum_kzt"], 2), "n_tx": int(d["n_tx"]), "role": df.at[n, "role"]}
            for n, d in rows[:limit]]


def card(g: int, r: pd.Series, G, df: pd.DataFrame, seeds: set, neighbors_limit: int = 10) -> dict:
    from mycelium.features import upstream_seeds
    from mycelium.roles import rule_trace, stability_text
    return {
        "id": str(g), "role": r.role, "role_label": config.ROLE_LABELS[r.role], "role_score": _r(r.role_score, 2),
        "stability_text": stability_text(r.stability_k),
        "priority": _r(r.priority_score), "rank": _rank(r["rank"]), "cluster": int(r.cluster_id),
        "is_seed": bool(r.is_seed), "depth": int(r.depth), "truncated": bool(r.truncated), "in_core": bool(r.in_core),
        "metrics": {
            "in_deg": int(r.in_deg), "out_deg": int(r.out_deg), "in_kzt": _r(r.in_kzt, 2),
            "out_kzt": _r(r.out_kzt, 2), "pass_through": _r(r.pass_through, 2),
            "hold_median_days": _r(r.hold_median_days, 1), "seed_reach": int(r.seed_reach),
            "cut_kzt": _r(r.cut_kzt, 2), "sync_max_payers": int(r.sync_max_payers),
        },
        "evidence": r.evidence,
        "rule_trace": rule_trace(r),
        "upstream_seeds": [str(s) for s in upstream_seeds(G, g, seeds)],
        "payers": _neighbors(G, g, df, "in", neighbors_limit),
        "recipients": _neighbors(G, g, df, "out", neighbors_limit),
    }


def cards_json(df: pd.DataFrame, G) -> dict:
    seeds = set(df.index[df.is_seed])
    return {str(g): card(g, r, G, df, seeds) for g, r in df.iterrows()}


def top_check_json(top: pd.DataFrame) -> list[dict]:
    return [{"rank": int(r["rank"]), "id": str(r.gid), "role": r.role, "priority": _r(r.priority_score),
             "why": r.why} for _, r in top.iterrows()]


def top_block_json(plan: pd.DataFrame) -> list[dict]:
    return [{"step": int(r.step), "id": str(r.gid), "role": r.role, "cut_kzt_marginal": float(r.cut_kzt_marginal),
             "reach_kzt_after": float(r.reach_kzt_after), "why": r.why} for _, r in plan.iterrows()]


def clusters_json(clusters_t: pd.DataFrame) -> list[dict]:
    return [{"cluster_id": int(r.cluster_id), "n_nodes": int(r.n_nodes), "n_seed": int(r.n_seed),
             "sum_kzt_internal": float(r.sum_kzt_internal),
             "top_ids": [x for x in str(r.top_gids).split("|") if x], "hypothesis": r.hypothesis}
            for _, r in clusters_t.iterrows()]


def next_requests_json(requests: pd.DataFrame) -> list[dict]:
    return [{"id": str(r.gid), "request_type": r.request_type, "reason": r.reason,
             "in_kzt": float(r.in_kzt), "in_deg": int(r.in_deg)} for _, r in requests.iterrows()]


LIMITATIONS = [
    "Выгрузка содержит только исходящие переводы от 81 курьера на 4 колена: входящие курьеров занижены, "
    "у узлов 4-го колена исходящие не видны (граница выборки).",
    "Переводы меньше 5 000 ₸ отброшены: дробление ниже порога невидимо.",
    "Только внутрибанковские переводы: межбанковские и наличные потоки не видны.",
    "Период — июль 2026: более ранние и поздние связи не учтены.",
    "Размеченных ролей нет: роли — правила с порогами, их надёжность оценена устойчивостью к сдвигу порогов.",
    "Кластеры (Louvain) считаются без учёта направления переводов.",
    "Роли — гипотезы для углублённой проверки; решение по каждому клиенту принимает человек.",
]


def facts_json(df: pd.DataFrame, G, meta: dict, top: pd.DataFrame, clusters_t: pd.DataFrame,
               res: dict, plan: pd.DataFrame, requests: pd.DataFrame) -> dict:
    """Всё, что видит ИИ-аналитик (§6.7). gid — строками."""
    comps = sorted((len(c) for c in __import__("networkx").weakly_connected_components(G) if len(c) > 1),
                   reverse=True)
    top_rows = []
    for _, t in top.head(20).iterrows():
        r = df.loc[int(t.gid)]
        top_rows.append({
            "rank": int(t["rank"]), "id": str(t.gid), "role_label": config.ROLE_LABELS[r.role],
            "priority": _r(r.priority_score), "role_score": _r(r.role_score, 2), "cluster": int(r.cluster_id),
            "is_seed": bool(r.is_seed), "in_core": bool(r.in_core), "in_deg": int(r.in_deg), "out_deg": int(r.out_deg),
            "in_kzt": _r(r.in_kzt, 2), "out_kzt": _r(r.out_kzt, 2), "cut_kzt": _r(r.cut_kzt, 2),
            "seed_reach_couriers": int(r.seed_reach), "evidence": r.evidence, "why": t.why,
        })
    strat = {name: {n: {k: v for k, v in e.items() if k != "removed"} for n, e in by_n.items()}
             for name, by_n in res["strategies"].items()}
    n = meta["network"]

    def by_label(counts) -> dict:
        """Счётчики ролей под русскими названиями, как на экране, в порядке легенды."""
        return {config.ROLE_LABELS[r]: int(counts.get(r, 0)) for r in config.ROLES if counts.get(r, 0)}

    clusters = []
    for c in clusters_json(clusters_t):
        members = df[df.cluster_id == c["cluster_id"]]
        clusters.append({"cluster_id": c["cluster_id"], "n_nodes": c["n_nodes"], "n_couriers": c.pop("n_seed"),
                         "sum_kzt_internal": c["sum_kzt_internal"], "top_ids": c["top_ids"],
                         "roles": by_label(members.role.value_counts()), "hypothesis": c["hypothesis"]})
    return {
        "glossary": FACTS_GLOSSARY,
        "network": {
            "n_nodes": n["n_nodes"], "n_edges": n["n_edges"],
            "n_seeds_couriers": n["n_seeds"],
            "total_kzt": n["total_kzt"], "period": n["period"], "n_clusters": n["n_clusters"],
            "roles": by_label(df.role.value_counts()),
            "components": {"n_with_edges": len(comps), "sizes": comps[:5],
                           "isolated_nodes": int((~df.has_edges).sum())},
            "core": {"size": int(df.core_size.max()), "roles": by_label(df[df.in_core].role.value_counts()),
                     "nodes_in_any_ring_incl_core": int(df.in_cycle.sum())},
            "thresholds": meta["thresholds"],
        },
        "top": top_rows,
        "clusters": clusters,
        "resilience": {
            "metric": "drop_reach_pct — на сколько процентов падает поток денег курьеров по сети, если "
                      "заблокировать N узлов (не курьеров) по данной стратегии; drop_deep_pct — то же для "
                      "потока до 3–4 колена",
            "baseline": res["baseline"],
            "strategies": {STRATEGY_LABELS[k]: v for k, v in strat.items()},
        },
        "blocking_plan": top_block_json(plan),
        "next_requests": next_requests_json(requests),
        "limitations": LIMITATIONS,
    }


STRATEGY_LABELS = {"plan": "блокировка по плану", "priority": "блокировка топа проверки",
                   "turnover": "блокировка топа по обороту", "random": "блокировка случайных узлов"}

FACTS_GLOSSARY = {
    "курьеры": "исходные 81 клиент (seed) из запроса правоохранителей: network.n_seeds_couriers, "
               "в кластере — n_couriers, у узла — is_seed и seed_reach (деньги скольких курьеров доходят до узла)",
    "роли": "network.roles — число узлов с каждой ролью; названия как на экране",
    "кольца": "network.core.size — главное ядро-кольцо; network.core.nodes_in_any_ring_incl_core — все узлы "
              "во всех кольцах, включая ядро (это не отдельное кольцо вокруг ядра)",
    "Точка сбора": "роль consolidator: плательщиков ≥ 6",
    "Конечный получатель": "роль terminal: деньги оседают, исходящих нет, узел раскрыт обходом",
}


def write_web(df: pd.DataFrame, G, summary: dict, clusters_t: pd.DataFrame, top: pd.DataFrame,
              plan: pd.DataFrame, res: dict, requests: pd.DataFrame, sankey: dict,
              out_dir: Path = config.OUT_DIR) -> list[Path]:
    web = Path(out_dir) / "web"
    meta = meta_json(df, G, summary, len(clusters_t))
    files = {
        "graph.json": graph_json(df, G), "meta.json": meta, "cards.json": cards_json(df, G),
        "top_check.json": top_check_json(top), "top_block.json": top_block_json(plan),
        "clusters.json": clusters_json(clusters_t), "sankey.json": sankey, "resilience.json": res,
        "next_requests.json": next_requests_json(requests),
    }
    paths = [write_json(obj, web / name) for name, obj in files.items()]
    paths.append(write_json(facts_json(df, G, meta, top, clusters_t, res, plan, requests),
                            Path(out_dir) / "facts.json"))
    return paths
