"""Координаты узлов для схемы (масштаб ±1000).

Силовая раскладка Фрюхтермана — Рейнгольда на numpy (без scipy), отдельно по каждой слабосвязной
компоненте; компоненты раскладываются «полками» по убыванию размера, узлы без рёбер — рядом внизу.
Детерминирована: начальные позиции от SEED.
"""
from __future__ import annotations

import math

import networkx as nx
import numpy as np
import pandas as pd

from mycelium import config

SCALE = 1000.0
ITERATIONS = 80


def _fruchterman_reingold(n: int, edges: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Позиции n узлов в квадрате ~[-1, 1] по списку рёбер (индексы 0…n-1)."""
    if n == 1:
        return np.zeros((1, 2))
    pos = rng.uniform(-1, 1, size=(n, 2))
    k = math.sqrt(4.0 / n)                     # идеальное расстояние для площади 2×2
    t = 0.2
    dt = t / (ITERATIONS + 1)
    for _ in range(ITERATIONS):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt((delta ** 2).sum(-1))
        np.fill_diagonal(dist, 1.0)
        dist = np.maximum(dist, 0.01)
        disp = (delta * (k * k / dist ** 2)[:, :, None]).sum(1)          # отталкивание всех от всех
        if len(edges):
            d = pos[edges[:, 0]] - pos[edges[:, 1]]
            ln = np.maximum(np.sqrt((d ** 2).sum(1)), 0.01)
            f = d * (ln / k)[:, None]                                    # притяжение по рёбрам
            np.add.at(disp, edges[:, 0], -f)
            np.add.at(disp, edges[:, 1], f)
        length = np.maximum(np.sqrt((disp ** 2).sum(1)), 0.01)
        pos += disp * (np.minimum(length, t) / length)[:, None]
        t -= dt
    pos -= pos.mean(0)
    r = np.abs(pos).max()
    return pos / r if r > 0 else pos


def compute_layout(G: nx.DiGraph, df: pd.DataFrame) -> pd.DataFrame:
    """index=gid; колонки x, y (±1000)."""
    rng = np.random.default_rng(config.SEED)
    UG = G.to_undirected(as_view=True)
    comps = [sorted(c) for c in nx.connected_components(UG) if len(c) > 1]
    comps.sort(key=lambda c: (-len(c), c[0]))
    isolates = sorted(v for v in df.index if UG.degree(v) == 0)

    # каждая компонента — квадрат со стороной ~ sqrt(размер), раскладка «полками»
    blocks = []
    for c in comps:
        idx = {v: i for i, v in enumerate(c)}
        e = np.array([(idx[u], idx[v]) for u, v in UG.subgraph(c).edges()], dtype=int).reshape(-1, 2)
        side = math.sqrt(len(c))
        blocks.append((c, _fruchterman_reingold(len(c), e, rng) * side / 2, side))
    width = max(b[2] for b in blocks) * 1.6 if blocks else 1.0
    xs, ys, gids = [], [], []
    cx, cy, shelf_h = 0.0, 0.0, 0.0
    for c, p, side in blocks:
        gap = max(side * 0.08, 0.6)
        if cx > 0 and cx + side > width:
            cx, cy, shelf_h = 0.0, cy + shelf_h, 0.0
        xs += list(p[:, 0] + cx + side / 2)
        ys += list(p[:, 1] + cy + side / 2)
        gids += c
        cx += side + gap
        shelf_h = max(shelf_h, side + gap)
    # узлы без рёбер — строкой под всеми компонентами
    cy += shelf_h + 1.0
    per_row = max(1, int(width // 1.2))
    for i, v in enumerate(isolates):
        xs.append((i % per_row) * 1.2 + 0.6)
        ys.append(cy + (i // per_row) * 1.2)
        gids.append(v)

    x, y = np.array(xs), np.array(ys)
    span = max(x.max() - x.min(), y.max() - y.min(), 1e-9)
    x = (x - (x.max() + x.min()) / 2) / span * 2 * SCALE
    y = (y - (y.max() + y.min()) / 2) / span * 2 * SCALE
    out = pd.DataFrame({"x": np.round(x, 1), "y": np.round(y, 1)}, index=pd.Index(gids, name="gid"))
    return out.reindex(df.index)
