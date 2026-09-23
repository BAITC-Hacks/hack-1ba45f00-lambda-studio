"""Точка входа: parquet → out/*.csv + out/web/*.json + out/facts.json.

    python -m mycelium.pipeline [--data data] [--out out]

Модули вызываются только через интерфейсы CLAUDE.md §6.8.
"""
from __future__ import annotations

import argparse
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from mycelium import clusters as clusters_mod
from mycelium import config, explore, layout, next_requests, resilience, sankey, temporal
from mycelium.evidence import add_evidence, fmt_kzt
from mycelium.export import write_csvs, write_extra_csvs, write_web
from mycelium.features import add_cluster_features, add_temporal, node_features
from mycelium.load import load_dataset
from mycelium.priority import add_priority, add_rank, top_table
from mycelium.roles import add_roles, role_counts

@contextmanager
def step(title: str, timings: list):
    t0 = time.perf_counter()
    print(f"→ {title}…", flush=True)
    yield
    dt = time.perf_counter() - t0
    timings.append((title, dt))
    print(f"  готово за {dt:.1f} с", flush=True)


def check_against_explore(df: pd.DataFrame) -> None:
    """Правила в roles.py не должны разойтись с эталоном explore.assign (§16)."""
    ref = explore.assign(df, dict(config.ROLE_THRESHOLDS))
    diff = (ref != df.role)
    if diff.any():
        sample = df.index[diff][:5].tolist()
        raise RuntimeError(f"Роли расходятся с explore.py у {int(diff.sum())} узлов (например, {sample}) — "
                           f"правила продублированы с ошибкой")


def run(data_dir: Path = config.DATA_DIR, out_dir: Path = config.OUT_DIR) -> pd.DataFrame:
    timings: list = []
    t_all = time.perf_counter()

    with step("Загрузка и проверка данных", timings):
        ds, summary = load_dataset(data_dir)
        G = ds.graph
        print(f"  узлов {summary['nodes']}, рёбер {summary['edges']}, переводов {summary['transactions']}, "
              f"курьеров {summary['seeds']}, оборот {fmt_kzt(summary['total_kzt'])}, {summary['period']}")
        print(f"  узлов без рёбер: {summary['orphans']} (из них курьеров {summary['orphan_seeds']})")
        for k, (got, exp) in summary["unexpected"].items():
            print(f"  ⚠ {k}: {got}, в описании кейса {exp}")

    with step("Метрики узлов", timings):
        df = node_features(ds.nodes, G)

    with step("Временные метрики (temporal)", timings):
        df = add_temporal(df, temporal.node_temporal(ds.transactions))

    with step("Кластеры (Louvain)", timings):
        df = add_cluster_features(df, G, clusters_mod.assign_clusters(G, df))

    with step("Роли и устойчивость (50 вариантов порогов)", timings):
        df = add_roles(df)
        check_against_explore(df)

    with step("Зависимый поток (cut_kzt)", timings):
        cut = resilience.cut_kzt(G, df).reindex(df.index)
        df["cut_kzt"] = cut.fillna(0.0).astype(float)

    with step("Приоритет, обоснования, топ", timings):
        df = add_priority(df)
        df = add_evidence(df)
        df = add_rank(df)
        top = top_table(df)

    with step("Описание кластеров", timings):
        clusters = clusters_mod.describe_clusters(G, df)

    with step("План блокировки и сравнение стратегий", timings):
        plan = resilience.greedy_plan(G, df, n=20)
        res = resilience.strategies(G, df, plan)
        for name in ("plan", "turnover", "priority", "random"):
            e = res["strategies"][name]["10"]
            print(f"  N=10 {name:<9} поток −{e['drop_reach_pct']} %, до верхних колен −{e['drop_deep_pct']} %")

    with step("Следующий запрос, лестница денег", timings):
        requests = next_requests.build_requests(df)
        sk = sankey.build_sankey(G, df)

    with step("Раскладка схемы", timings):
        xy = layout.compute_layout(G, df)
        df["x"], df["y"] = xy.x, xy.y

    with step("Запись CSV и JSON", timings):
        paths, tables = write_csvs(df, clusters, top, pd.Index(ds.nodes.gid), out_dir)
        paths.update(write_extra_csvs(requests, plan, out_dir))
        web = write_web(df, G, summary, tables["clusters"], tables["top_nodes"], plan, res, requests, sk, out_dir)
        for p in list(paths.values()) + web:
            print(f"  {_rel(p)}")

    report(df, clusters, time.perf_counter() - t_all)
    return df


def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(p)


def report(df: pd.DataFrame, clusters: pd.DataFrame, total: float) -> None:
    counts = role_counts(df.role)
    score = df.groupby("role").role_score.mean()
    print("\nРоль → число узлов (средняя устойчивость)")
    for role, n in counts.items():
        s = f"{score[role]:.2f}" if role in score else "—"
        print(f"  {config.ROLE_LABELS[role]:<20} {role:<13} {n:>5}   {s}")
        if n == 0:
            print(f"  ⚠ роль {role} не получил ни один узел")
    print(f"  {'итого':<34} {int(counts.sum()):>5}")
    print(f"Кластеров: {len(clusters)} (с несколькими курьерами: {int((clusters.n_seed > 1).sum())}); "
          f"ядро-кольцо: {int(df.in_core.sum())} узлов")
    print(f"Пайплайн отработал за {total:.1f} с")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Грибница: parquet → роли, кластеры, топ (out/*.csv)")
    ap.add_argument("--data", type=Path, default=config.DATA_DIR, help="папка с parquet-файлами")
    ap.add_argument("--out", type=Path, default=config.OUT_DIR, help="куда писать выходы")
    a = ap.parse_args(argv)
    run(a.data, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
