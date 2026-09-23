"""Сверка ответа ИИ с графом (CLAUDE.md §14).

1. Каждый [gid:N] существует в графе и встречался в результатах инструментов этого диалога.
2. Каждое число из ответа («3,2 млн», «850 тыс.», «33,7 %», «19») совпадает с каким-то числом из
   результатов: крупные — с допуском 1 % или точностью округления, мелкие счётчики — точно.
3. Нет запрещённых слов (язык гипотез).
"""
from __future__ import annotations

import re
from typing import Iterable

from mycelium import config

GID_TAG = re.compile(r"\[gid:(\d+)\]")
BARE_GID = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
NUMBER = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,](\d+))?\s*"
    r"(млн|тыс\.?|%|процент\w*)?", re.IGNORECASE)

# Числа, которые не нужно искать в результатах: параметры выгрузки и методики
KNOWN_CONSTANTS = {0, 1, 2, 3, 4, 5, 20, 50, 81, 100, 2026, config.MIN_TX_KZT,
                   config.EXPECTED_COUNTS["nodes"], config.EXPECTED_COUNTS["edges"]} | {
    v for v in config.ROLE_THRESHOLDS.values() if float(v).is_integer()}
EXTRA_FORBIDDEN_STEMS = ["преступн", "винов", "украл", "организатор", "отмыва"]


def collect_numbers(obj, out: set | None = None) -> set:
    """Все числа из результатов инструментов (рекурсивно), включая числа внутри строк."""
    out = set() if out is None else out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for v, _ in parse_numbers(GID_TAG.sub(" ", BARE_GID.sub(" ", obj))):
            out.add(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect_numbers(v, out)
    return out


def collect_gids(obj, out: set | None = None) -> set:
    out = set() if out is None else out
    if isinstance(obj, str):
        out.update(BARE_GID.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_gids(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect_gids(v, out)
    return out


def parse_numbers(text: str) -> list[tuple[float, float]]:
    """[(значение, допуск округления)] для каждого числа в тексте."""
    res = []
    for m in NUMBER.finditer(text):
        whole = re.sub(r"[   ]", "", m.group(1))
        frac = m.group(2) or ""
        unit = (m.group(3) or "").lower()
        v = float(f"{whole}.{frac}" if frac else whole)
        step = 10 ** (-len(frac)) if frac else 1.0
        mult = 1_000_000 if unit.startswith("млн") else 1_000 if unit.startswith("тыс") else 1
        res.append((v * mult, 0.5 * step * mult))
    return res


def _matches(value: float, tol: float, seen: Iterable[float], percent: bool) -> bool:
    for s in seen:
        cands = (s, s * 100) if percent else (s,)
        for c in cands:
            if abs(value - c) <= max(tol, 0.01 * abs(c)) + 1e-9:
                return True
    return False


def verify(text: str, seen_results: list, graph_gids: set) -> dict:
    """{"verified": bool, "issues": [..], "gids": [..]} — проверка ответа по результатам инструментов."""
    issues: list[str] = []
    seen_numbers = collect_numbers(seen_results)
    seen_gids = collect_gids(seen_results)

    tagged = GID_TAG.findall(text)
    bare = [g for g in BARE_GID.findall(GID_TAG.sub(" ", text))]
    gids = list(dict.fromkeys(tagged + bare))
    for g in gids:
        if int(g) not in graph_gids:
            issues.append(f"узла {g} нет в графе")
        elif g not in seen_gids:
            issues.append(f"узел {g} не встречался в результатах запросов к графу")

    plain = BARE_GID.sub(" ", GID_TAG.sub(" ", text))
    for m in NUMBER.finditer(plain):
        (value, tol), = parse_numbers(m.group(0))
        unit = (m.group(3) or "").lower()
        percent = unit.startswith("%") or unit.startswith("процент")
        if not unit and value in KNOWN_CONSTANTS:
            continue
        if not _matches(value, tol, seen_numbers, percent):
            issues.append(f"число «{m.group(0).strip()}» не найдено в результатах запросов")

    low = text.lower()
    hits = [w for w in sorted(set(config.FORBIDDEN_WORDS) | set(EXTRA_FORBIDDEN_STEMS)) if w in low]
    for w in hits:
        if not any(o != w and w.startswith(o) for o in hits):     # «преступник» уже покрыт «преступн»
            issues.append(f"запрещённая формулировка «{w}…» — нужен язык гипотез")

    return {"verified": not issues, "issues": list(dict.fromkeys(issues)), "gids": gids}
