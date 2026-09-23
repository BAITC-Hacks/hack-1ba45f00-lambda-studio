"""Данные «лестницы денег» (Санкей): колонки — колена 0–4, внутри колена — группы по роли.

Лента = сумма переводов между группами соседних колен (depth → depth + 1). Переводы внутри колена,
назад или через колено («по кругу или вбок», в основном ядро-кольцо) в ленты не входят —
их сумма отдаётся как skipped_kzt и показывается сноской.
"""
from __future__ import annotations

import networkx as nx
import pandas as pd

GROUP = {"coordinator": "координаторы", "consolidator": "точки сбора", "distributor": "распределители",
         "transit": "транзит", "terminal": "конечные", "peripheral": "прочие"}
SEED_GROUP = "курьеры"


def node_group(df: pd.DataFrame) -> pd.Series:
    """«1 · транзит»: колено · группа. Все курьеры (колено 0) — одна группа."""
    grp = df.role.map(GROUP).where(~df.is_seed, SEED_GROUP)
    return df.depth.astype(str) + " · " + grp


def build_sankey(G: nx.DiGraph, df: pd.DataFrame) -> dict:
    """Форма sankey.json: {nodes: [{name}], links: [{source, target, value}], skipped_kzt}."""
    name = node_group(df).to_dict()
    depth = df.depth.to_dict()
    flows: dict = {}
    skipped = 0.0
    for u, v, d in G.edges(data=True):
        if depth[v] == depth[u] + 1:
            key = (name[u], name[v])
            flows[key] = flows.get(key, 0.0) + d["sum_kzt"]
        else:
            skipped += d["sum_kzt"]
    links = [{"source": s, "target": t, "value": int(round(val))}
             for (s, t), val in sorted(flows.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1]))]
    used = sorted({l["source"] for l in links} | {l["target"] for l in links},
                  key=lambda n: (int(n.split(" · ")[0]), n))
    return {"nodes": [{"name": n} for n in used], "links": links, "skipped_kzt": int(round(skipped))}
