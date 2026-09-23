"""Инструменты ИИ-аналитика: чистые функции над выходами пайплайна (out/) и графом.

Каждый инструмент отдаёт JSON с gid-строками и числами. ИИ не видит ничего, кроме результатов этих
функций, поэтому каждая цифра в его ответе проверяема (verify.py).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

import networkx as nx
import pandas as pd

from mycelium import config, resilience
from mycelium.load import load_dataset

DEFAULT_LIMIT = 10
MAX_LIMIT = 30


class ToolError(ValueError):
    """Ошибка в аргументах инструмента — возвращается модели текстом, а не падает."""


class Context:
    """Выходы пайплайна и граф в памяти (грузятся один раз)."""

    def __init__(self, out_dir: Path = config.OUT_DIR, data_dir: Path = config.DATA_DIR):
        web = out_dir / "web"
        if not (web / "cards.json").exists():
            raise FileNotFoundError("Нет out/web/cards.json — сначала python -m mycelium.pipeline")
        self.cards: dict = json.loads((web / "cards.json").read_text(encoding="utf-8"))
        self.facts: dict = json.loads((out_dir / "facts.json").read_text(encoding="utf-8"))
        self.clusters: list = json.loads((web / "clusters.json").read_text(encoding="utf-8"))
        self.top_check: list = json.loads((web / "top_check.json").read_text(encoding="utf-8"))
        self.top_block: list = json.loads((web / "top_block.json").read_text(encoding="utf-8"))
        ds, _ = load_dataset(data_dir)
        self.G: nx.DiGraph = ds.graph
        self.df = ds.nodes.set_index("gid")[["is_seed"]].copy()
        self.seeds = set(self.df.index[self.df.is_seed])


@lru_cache(maxsize=2)
def get_context(out_dir: str = str(config.OUT_DIR), data_dir: str = str(config.DATA_DIR)) -> Context:
    return Context(Path(out_dir), Path(data_dir))


def _gid(ctx: Context, raw) -> int:
    s = str(raw).strip().replace("gid:", "").strip("[] ")
    if not s.isdigit() or int(s) not in ctx.G:
        raise ToolError(f"узел {raw} не найден в графе")
    return int(s)


def _limit(v, default: int = DEFAULT_LIMIT) -> int:
    try:
        return max(1, min(MAX_LIMIT, int(v)))
    except (TypeError, ValueError):
        return default


def _brief_node(ctx: Context, g: int) -> dict:
    c = ctx.cards[str(g)]
    return {"id": c["id"], "role": c["role"], "role_label": c["role_label"], "priority": c["priority"],
            "rank": c["rank"], "is_seed": c["is_seed"]}


# ── Инструменты ───────────────────────────────────────────────────────────────

def network_summary(ctx: Context) -> dict:
    """Общая картина: счётчики, роли, оборот, компоненты, ядро-кольцо, ограничения данных."""
    n = ctx.facts["network"]
    return {**n, "limitations": ctx.facts["limitations"]}


def get_node(ctx: Context, gid) -> dict:
    """Метрики, роль, устойчивость, evidence, rule_trace, кластер узла."""
    g = _gid(ctx, gid)
    c = dict(ctx.cards[str(g)])
    c["payers"] = c["payers"][:5]
    c["recipients"] = c["recipients"][:5]
    c["n_upstream_seeds"] = len(c.pop("upstream_seeds"))
    return c


def get_neighbors(ctx: Context, gid, direction: str = "in", limit: int = DEFAULT_LIMIT) -> dict:
    """Плательщики (direction=in) или получатели (out), по убыванию суммы."""
    g = _gid(ctx, gid)
    if direction not in ("in", "out"):
        raise ToolError("direction должен быть in (плательщики) или out (получатели)")
    edges = ctx.G.in_edges(g, data=True) if direction == "in" else ctx.G.out_edges(g, data=True)
    rows = sorted(((u if direction == "in" else v), d) for u, v, d in edges)
    rows.sort(key=lambda t: -t[1]["sum_kzt"])
    total = len(rows)
    return {"id": str(g), "direction": direction, "total": total,
            "neighbors": [{**_brief_node(ctx, n), "sum_kzt": round(d["sum_kzt"], 2), "n_tx": d["n_tx"]}
                          for n, d in rows[:_limit(limit)]]}


def upstream_couriers(ctx: Context, gid) -> dict:
    """Какие курьеры (seed) питают узел: у кого есть направленный путь денег до него."""
    g = _gid(ctx, gid)
    seeds = sorted(s for s in nx.ancestors(ctx.G, g) if s in ctx.seeds)
    direct = sorted(s for s in ctx.G.predecessors(g) if s in ctx.seeds)
    return {"id": str(g), "n_couriers": len(seeds), "n_direct": len(direct),
            "couriers": [str(s) for s in seeds], "direct_couriers": [str(s) for s in direct]}


def common_collectors(ctx: Context, gids: list, limit: int = DEFAULT_LIMIT) -> dict:
    """«Кто собирает деньги с этих узлов?» — узлы, до которых доходят деньги большинства данных gid."""
    if not gids:
        raise ToolError("нужен список gid")
    src = [_gid(ctx, x) for x in gids][:50]
    need = len(src) // 2 + 1
    count: dict = {}
    for s in src:
        for v in nx.descendants(ctx.G, s):
            count[v] = count.get(v, 0) + 1
    hits = [v for v, k in count.items() if k >= need and v not in src]
    hits.sort(key=lambda v: (-count[v], -(ctx.cards[str(v)]["priority"] or 0), v))
    return {"sources": [str(s) for s in src], "min_covered": need, "total_found": len(hits),
            "collectors": [{**_brief_node(ctx, v), "covered": count[v], "of": len(src),
                            "in_deg": ctx.cards[str(v)]["metrics"]["in_deg"],
                            "in_kzt": ctx.cards[str(v)]["metrics"]["in_kzt"]} for v in hits[:_limit(limit)]]}


def money_path(ctx: Context, src, dst) -> dict:
    """Кратчайший путь денег по направлению переводов, с суммами по шагам."""
    a, b = _gid(ctx, src), _gid(ctx, dst)
    for s, t, reverse in ((a, b, False), (b, a, True)):
        try:
            path = nx.shortest_path(ctx.G, s, t)
        except nx.NetworkXNoPath:
            continue
        steps = [{"from": str(u), "to": str(v), "sum_kzt": round(ctx.G[u][v]["sum_kzt"], 2),
                  "n_tx": ctx.G[u][v]["n_tx"]} for u, v in zip(path, path[1:])]
        return {"found": True, "reversed": reverse, "hops": len(steps),
                "path": [_brief_node(ctx, v) for v in path], "steps": steps,
                "note": ("деньги идут в обратную сторону: от второго узла к первому" if reverse else "")}
    return {"found": False, "note": "направленного пути денег между узлами нет ни в одну сторону"}


def top_nodes(ctx: Context, role: Optional[str] = None, limit: int = DEFAULT_LIMIT) -> dict:
    """Топ проверки (по приоритету), при желании — только одна роль."""
    if role and role not in config.ROLES:
        raise ToolError(f"роль должна быть одной из {config.ROLES}")
    if not role:
        rows = ctx.top_check[:_limit(limit)]
    else:
        ids = sorted((i for i, c in ctx.cards.items() if c["role"] == role),
                     key=lambda i: (-(ctx.cards[i]["priority"] or 0), i))[:_limit(limit)]
        rows = [{"rank": ctx.cards[i]["rank"], "id": i, "role": role, "priority": ctx.cards[i]["priority"],
                 "why": ctx.cards[i]["evidence"]} for i in ids]
    return {"role": role, "nodes": rows}


def get_cluster(ctx: Context, cluster_id) -> dict:
    """Состав и гипотеза кластера."""
    try:
        cid = int(cluster_id)
    except (TypeError, ValueError):
        raise ToolError("cluster_id должен быть числом")
    cl = next((c for c in ctx.clusters if c["cluster_id"] == cid), None)
    if cl is None:
        raise ToolError(f"кластера {cid} нет")
    members = [c for c in ctx.cards.values() if c["cluster"] == cid]
    roles = pd.Series([m["role"] for m in members]).value_counts().to_dict()
    return {**cl, "roles": roles}


def blocking_effect(ctx: Context, gids: list) -> dict:
    """Что будет с потоком денег курьеров, если заблокировать эти узлы (курьеров блокировать нельзя)."""
    if not gids:
        raise ToolError("нужен список gid")
    ids = [_gid(ctx, x) for x in gids][:50]
    seeds = [str(g) for g in ids if g in ctx.seeds]
    ids = [g for g in ids if g not in ctx.seeds]
    r = resilience.evaluate(ctx.G, ctx.df, ids)
    r["removed"] = [str(g) for g in r["removed"]]
    if seeds:
        r["skipped_couriers"] = seeds
        r["note"] = "курьеры исключены: они уже известны, их блокировка обнуляет граф по построению"
    return r


def blocking_plan(ctx: Context, limit: int = DEFAULT_LIMIT) -> dict:
    """Жадный план блокировки: сколько ₸ отсекает каждый шаг."""
    return {"steps": ctx.top_block[:_limit(limit, 10)]}


TOOLS: dict[str, Callable] = {
    "network_summary": network_summary, "get_node": get_node, "get_neighbors": get_neighbors,
    "upstream_couriers": upstream_couriers, "common_collectors": common_collectors, "money_path": money_path,
    "top_nodes": top_nodes, "get_cluster": get_cluster, "blocking_effect": blocking_effect,
    "blocking_plan": blocking_plan,
}

_GID = {"type": "string", "description": "gid узла (18 цифр, строкой)"}
_GIDS = {"type": "array", "items": _GID, "description": "список gid"}
_LIMIT = {"type": "integer", "description": "сколько строк вернуть (по умолчанию 10, максимум 30)"}


def _spec(name: str, desc: str, props: dict, required: list) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


TOOL_SPECS = [
    _spec("network_summary", "Общая картина сети: число узлов, связей, курьеров, оборот, распределение ролей, "
          "компоненты, ядро-кольцо, пороги ролей и ограничения данных.", {}, []),
    _spec("get_node", "Карточка узла: роль, устойчивость роли, приоритет, метрики, обоснование, "
          "проверки правил (rule_trace), кластер, главные плательщики и получатели.", {"gid": _GID}, ["gid"]),
    _spec("get_neighbors", "Плательщики (in) или получатели (out) узла по убыванию суммы.",
          {"gid": _GID, "direction": {"type": "string", "enum": ["in", "out"]}, "limit": _LIMIT},
          ["gid", "direction"]),
    _spec("upstream_couriers", "Какие курьеры питают узел: у кого из 81 курьера деньги по цепочке доходят до "
          "узла, и кто платит напрямую.", {"gid": _GID}, ["gid"]),
    _spec("common_collectors", "Кто собирает деньги с данных узлов: узлы, до которых доходят деньги большинства "
          "из них, по числу охваченных и приоритету.", {"gids": _GIDS, "limit": _LIMIT}, ["gids"]),
    _spec("money_path", "Кратчайший путь денег между двумя узлами по направлению переводов, с суммами по "
          "шагам.", {"src": _GID, "dst": _GID}, ["src", "dst"]),
    _spec("top_nodes", "Топ проверки по приоритету, можно отфильтровать по роли.",
          {"role": {"type": "string", "enum": config.ROLES}, "limit": _LIMIT}, []),
    _spec("get_cluster", "Кластер: размер, курьеры, внутренний оборот, главные узлы, гипотеза, роли.",
          {"cluster_id": {"type": "integer"}}, ["cluster_id"]),
    _spec("blocking_effect", "На сколько упадёт поток денег курьеров, если заблокировать эти узлы.",
          {"gids": _GIDS}, ["gids"]),
    _spec("blocking_plan", "Жадный план блокировки: какие узлы блокировать по шагам и сколько ₸ отсекает "
          "каждый шаг.", {"limit": _LIMIT}, []),
]


def call(ctx: Context, name: str, args: dict) -> dict:
    """Вызов инструмента по имени. Ошибка аргументов → {"error": …}, чтобы модель могла исправиться."""
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"инструмента {name} нет"}
    try:
        return fn(ctx, **(args or {}))
    except ToolError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"неверные аргументы: {e}"}
