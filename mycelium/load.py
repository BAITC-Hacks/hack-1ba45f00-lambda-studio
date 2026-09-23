"""Загрузка трёх parquet, sanity-check и сборка направленного графа.

Основа — starter/starter.py организаторов (load, sanity_check, build_graph). Отличия:
проверки падают громко по-русски, а не печатают предупреждения; в граф добавляются
все узлы из nodes.parquet, включая 19 узлов без рёбер.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import pandas as pd

from mycelium import config


class DataError(RuntimeError):
    """Входные данные не соответствуют ожиданиям — дальше считать нельзя."""


@dataclass
class Dataset:
    edges: pd.DataFrame         # src, dst, sum_kzt, n_tx, depth
    nodes: pd.DataFrame         # gid, depth, is_seed
    transactions: pd.DataFrame  # src, dst, date (datetime64), sum_kzt
    graph: nx.DiGraph           # все узлы; рёбра с атрибутами sum_kzt, n_tx, depth


def load_frames(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    files = {name: data_dir / f"{name}.parquet" for name in ("edges", "nodes", "transactions")}
    missing = [str(p) for p in files.values() if not p.exists()]
    if missing:
        raise DataError(f"Не найдены файлы данных: {', '.join(missing)}. Запускайте из корня репозитория "
                        f"или укажите --data.")
    edges = pd.read_parquet(files["edges"])
    nodes = pd.read_parquet(files["nodes"])
    tx = pd.read_parquet(files["transactions"])
    tx["date"] = pd.to_datetime(tx["date"])
    edges["depth"] = edges["depth"].astype(int)
    nodes["depth"] = nodes["depth"].astype(int)
    nodes["is_seed"] = nodes["is_seed"].astype(bool)
    return edges, nodes, tx


def sanity_check(edges: pd.DataFrame, nodes: pd.DataFrame, tx: pd.DataFrame) -> dict:
    """Проверки консистентности. Нарушение → DataError. Возвращает сводку для печати."""
    def need(cond: bool, msg: str) -> None:
        if not cond:
            raise DataError(msg)

    for name, df, cols in (("edges", edges, ["src", "dst", "sum_kzt", "n_tx", "depth"]),
                           ("nodes", nodes, ["gid", "depth", "is_seed"]),
                           ("transactions", tx, ["src", "dst", "date", "sum_kzt"])):
        lost = [c for c in cols if c not in df.columns]
        need(not lost, f"В {name}.parquet нет колонок: {lost}")

    need(nodes.gid.is_unique, "В nodes.parquet есть дубли gid")
    need(not edges.duplicated(["src", "dst"]).any(), "В edges.parquet есть дубли пар src→dst")
    known = set(nodes.gid)
    stray = (set(edges.src) | set(edges.dst)) - known
    need(not stray, f"{len(stray)} gid из рёбер отсутствуют в nodes.parquet")
    need((edges.src != edges.dst).all(), "В edges.parquet есть петли src == dst")

    # транзакции должны складываться в рёбра (проверка из стартового кода)
    agg = tx.groupby(["src", "dst"]).agg(s=("sum_kzt", "sum"), c=("sum_kzt", "size")).reset_index()
    m = edges.merge(agg, on=["src", "dst"], how="outer", indicator=True)
    need((m._merge == "both").all(), "edges и transactions не сходятся по парам src→dst")
    need(((m.s - m.sum_kzt).abs() <= 0.01 * m.sum_kzt.abs().clip(lower=1)).all(),
         "Сумма транзакций пары не равна sum_kzt ребра")
    need((m.c == m.n_tx).all(), "Число транзакций пары не равно n_tx ребра")

    in_edges = set(edges.src) | set(edges.dst)
    seeds = set(nodes.gid[nodes.is_seed])
    orphans = known - in_edges
    summary = {
        "nodes": len(nodes), "edges": len(edges), "transactions": len(tx), "seeds": len(seeds),
        "total_kzt": float(edges.sum_kzt.sum()),
        "period": f"{tx.date.min().date()} — {tx.date.max().date()}",
        "orphans": len(orphans), "orphan_seeds": len(orphans & seeds),
    }
    # Отличие от ожидаемых размеров — не ошибка (пайплайн работает и на других выгрузках), но видно сразу
    summary["unexpected"] = {k: (summary[k], v) for k, v in config.EXPECTED_COUNTS.items() if summary[k] != v}
    return summary


def build_graph(edges: pd.DataFrame, nodes: pd.DataFrame) -> nx.DiGraph:
    """Направленный граф: все узлы из nodes.parquet, вес ребра sum_kzt, число переводов n_tx."""
    G = nx.DiGraph()
    G.add_nodes_from(nodes.gid.tolist())
    for r in edges.itertuples(index=False):
        G.add_edge(int(r.src), int(r.dst), sum_kzt=float(r.sum_kzt), n_tx=int(r.n_tx), depth=int(r.depth))
    return G


def load_dataset(data_dir: Path = config.DATA_DIR) -> tuple[Dataset, dict]:
    edges, nodes, tx = load_frames(Path(data_dir))
    summary = sanity_check(edges, nodes, tx)
    return Dataset(edges, nodes, tx, build_graph(edges, nodes)), summary
