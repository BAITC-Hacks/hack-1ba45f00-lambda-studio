"""Механическая проверка выходов пайплайна (CLAUDE.md §16).

    python -m mycelium.validate

Падает с понятным сообщением при нарушении контракта; предупреждения печатает, но не падает.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

from mycelium import config, explore

REQUIRED = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]
GID_LEN = 18


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.passed: list[str] = []

    def check(self, ok: bool, what: str, fail: str) -> None:
        (self.passed if ok else self.errors).append(what if ok else f"{what}: {fail}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _forbidden(texts: pd.Series) -> list[str]:
    low = texts.astype(str).str.lower()
    return [w for w in config.FORBIDDEN_WORDS if low.str.contains(w, regex=False).any()]


def _json_problems(obj, path: str = "$") -> list[str]:
    """gid числом (18-значное целое) или NaN/Infinity где-либо в JSON."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _json_problems(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5000]):
            out += _json_problems(v, f"{path}[{i}]")
            if len(out) > 5:
                break
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, int) and len(str(abs(obj))) >= GID_LEN - 2:
        out.append(f"{path}: похоже на gid числом ({obj})")
    elif isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        out.append(f"{path}: NaN/Infinity")
    return out


def validate(out_dir: Path = config.OUT_DIR, data_dir: Path = config.DATA_DIR) -> Report:
    rep = Report()
    nodes = pd.read_parquet(data_dir / "nodes.parquet")
    paths = {n: out_dir / f"{n}.csv" for n in ("nodes_roles", "clusters", "top_nodes", "next_requests", "blocking_plan")}
    missing = [str(p) for p in paths.values() if not p.exists()]
    rep.check(not missing, "все CSV на месте", f"нет {missing} — запустите python -m mycelium.pipeline")
    if missing:
        return rep

    nr = pd.read_csv(paths["nodes_roles"])
    cl = pd.read_csv(paths["clusters"])
    top = pd.read_csv(paths["top_nodes"])

    # ── nodes_roles.csv
    rep.check(len(nr) == len(nodes), f"nodes_roles.csv: {len(nodes)} строк", f"{len(nr)} строк")
    rep.check(nr.gid.is_unique, "nodes_roles.csv: gid без дублей", "есть дубли")
    rep.check(set(nr.gid) == set(nodes.gid), "nodes_roles.csv: gid = nodes.parquet", "наборы gid расходятся")
    rep.check(list(nr.columns[:len(REQUIRED)]) == REQUIRED, "nodes_roles.csv: порядок обязательных колонок",
              f"{list(nr.columns[:len(REQUIRED)])}")
    empty = [c for c in REQUIRED if c in nr and (nr[c].isna().any() or (nr[c].astype(str).str.strip() == "").any())]
    rep.check(not empty, "nodes_roles.csv: обязательные колонки заполнены", f"пусто в {empty}")
    bad = sorted(set(nr.role) - set(config.ROLES))
    rep.check(not bad, "nodes_roles.csv: роли из словаря", f"чужие роли {bad}")
    for c in ("role_score", "priority_score"):
        rep.check(nr[c].between(0, 1).all(), f"nodes_roles.csv: {c} в [0, 1]", "есть значения вне диапазона")
    rep.check((nr.evidence.str.len() <= config.EVIDENCE_MAX_LEN).all(),
              f"nodes_roles.csv: evidence ≤ {config.EVIDENCE_MAX_LEN}", f"макс. {nr.evidence.str.len().max()}")
    rep.check(nr.evidence.str.contains(r"\d").all(), "nodes_roles.csv: в evidence есть числа",
              f"{int((~nr.evidence.str.contains(r'[0-9]')).sum())} строк без чисел")
    fw = _forbidden(nr.evidence)
    rep.check(not fw, "nodes_roles.csv: язык гипотез", f"запрещённые слова {fw}")
    wrong = nr.truncated & nr.role.isin(["terminal", "transit", "distributor"])
    rep.check(not wrong.any(), "граница выборки не бывает terminal/transit/distributor", f"{int(wrong.sum())} узлов")
    for r in config.ROLES:
        if (nr.role == r).sum() == 0:
            rep.warn(f"роль {r} не получил ни один узел")

    # ── clusters.csv
    rep.check(set(nr.cluster_id) <= set(cl.cluster_id), "cluster_id из nodes_roles есть в clusters.csv",
              f"нет {sorted(set(nr.cluster_id) - set(cl.cluster_id))[:10]}")
    rep.check(not _forbidden(cl.hypothesis), "clusters.csv: язык гипотез", f"{_forbidden(cl.hypothesis)}")

    # ── top_nodes.csv
    rep.check(len(top) >= config.TOP_N_MIN, f"top_nodes.csv: ≥ {config.TOP_N_MIN} строк", f"{len(top)} строк")
    rep.check(top.priority_score.is_monotonic_decreasing, "top_nodes.csv: по убыванию приоритета", "не отсортирован")
    rep.check((top.why.str.len() <= config.WHY_MAX_LEN).all(), f"top_nodes.csv: why ≤ {config.WHY_MAX_LEN}", "длиннее")
    rep.check(not _forbidden(top.why), "top_nodes.csv: язык гипотез", f"{_forbidden(top.why)}")

    # ── роли совпадают с эталоном explore.py (правила не продублированы с ошибкой)
    _, N, T, G = explore.load(data_dir)
    ref = explore.assign(explore.features(N, T, G))
    ours = nr.set_index("gid").role.reindex(ref.index)
    rep.check((ref == ours).all(), "роли совпадают с explore.py",
              f"расходятся у {int((ref != ours).sum())} узлов")
    counts = ours.value_counts().to_dict()
    rep.check(counts == ref.value_counts().to_dict(), "счётчики ролей совпадают с explore.py", f"{counts}")

    # ── JSON: gid строками, без NaN
    jsons = sorted((out_dir / "web").glob("*.json")) + [out_dir / "facts.json"]
    for p in jsons:
        if not p.exists():
            rep.check(False, f"{p.name} на месте", "нет файла")
            continue
        text = p.read_text(encoding="utf-8")
        rep.check(not re.search(r"\bNaN\b|\bInfinity\b", text), f"{p.name}: без NaN", "есть NaN/Infinity")
        probs = _json_problems(json.loads(text))
        rep.check(not probs, f"{p.name}: gid только строками", "; ".join(probs[:3]))

    # ── в коде нет литералов gid из данных
    gids = set(map(str, nodes.gid))
    hits = []
    for folder in ("mycelium", "web/src", "frontend/app", "frontend/components", "tests"):
        root = config.ROOT / folder
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if f.suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"} and f.is_file():
                txt = f.read_text(encoding="utf-8", errors="ignore")
                found = [g for g in re.findall(r"\d{18}", txt) if g in gids]
                if found:
                    hits.append(f"{f.relative_to(config.ROOT)} ({found[0]})")
    rep.check(not hits, "в коде нет литералов gid", ", ".join(hits[:5]))
    return rep


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Проверка выходов пайплайна по контрактам CLAUDE.md §6/§16")
    ap.add_argument("--out", type=Path, default=config.OUT_DIR)
    ap.add_argument("--data", type=Path, default=config.DATA_DIR)
    a = ap.parse_args(argv)
    rep = validate(a.out, a.data)
    for p in rep.passed:
        print(f"  ✓ {p}")
    for w in rep.warnings:
        print(f"  ⚠ {w}")
    for e in rep.errors:
        print(f"  ✗ {e}")
    if rep.errors:
        print(f"\nПРОВЕРКА НЕ ПРОЙДЕНА: ошибок {len(rep.errors)}")
        return 1
    print(f"\nВсё в порядке: проверок {len(rep.passed)}, предупреждений {len(rep.warnings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
