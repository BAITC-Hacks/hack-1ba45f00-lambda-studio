"""Сверка ответа ИИ с графом (CLAUDE.md §14).

1. Каждый [gid:N] существует в графе и встречался в результатах инструментов этого диалога.
2. Каждое число из ответа («3,2 млн», «850 тыс.», «33,7 %», «19») совпадает с каким-то числом из
   результатов: крупные — с допуском 1 % или точностью округления, мелкие счётчики — точно.
3. Точечно: число рядом со словом «курьер» — это 81 или число курьеров из результатов (кластер, узел);
   число рядом с названием роли — счётчик именно этой роли (сеть, ядро, кластер, список из результатов).
   Так ловится «курьеров 228», когда 228 — число транзитных узлов: такое число в фактах есть, но не про курьеров.
4. Нет запрещённых слов (язык гипотез).
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


# ── Точечная проверка: число рядом с «курьер» или названием роли ─────────────
SUBJECTS = {
    "courier": r"курьер\w*",
    "coordinator": r"координатор\w*",
    "consolidator": r"точ\w*\s+сбора|консолидатор\w*",
    "distributor": r"распределител\w*",
    "transit": r"транзит\w*",
    "terminal": r"конечн\w*\s+получател\w*|терминал\w*",
    "peripheral": r"перифери\w*",
}
SUBJECT_NAMES = {"courier": "курьеров"} | {r: f"«{config.ROLE_LABELS[r]}»" for r in config.ROLES}
_NUM = r"(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)"
_PREP = r"(?:\s+(?:в|во|у|из|на|среди)\s+(?!кластер|колен|шаг)[а-яё-]+)?"   # «в кластере 3» — номер, не счётчик   # «курьеров в ядре 36», «координаторов в кластере 3»
_ADJ = r"(?:[а-яё]+(?:ых|их|ые|ие)\s+)?"                  # «5 известных курьеров»
# после числа не должна идти другая единица: «Точка сбора — 8 плательщиков» — это про плательщиков
_NOT_UNIT = r"(?![\d,.]*\s*(?:плат|получ|млн|тыс|%|₸|дн|пер|узл|клиент|связ|кластер|курьер|колен|раз))"
COURIER_KEYS = {"n_seeds_couriers", "n_seeds", "n_seed", "n_couriers", "seed_reach", "seed_reach_couriers",
                "n_seed_payers", "n_direct", "n_upstream_seeds"}
COURIER_LISTS = {"couriers", "direct_couriers", "upstream_seeds"}
_ROLE_BY_NAME = {**{r: r for r in config.ROLES}, **{v: k for k, v in config.ROLE_LABELS.items()}}


def subject_mentions(text: str) -> list[tuple[str, float, str]]:
    """[(субъект, число, фрагмент)]: «6 координаторов», «курьеров: 11», «Транзит — 228»."""
    out = []
    for subj, pat in SUBJECTS.items():
        for m in re.finditer(rf"(?<![\d,.]){_NUM}\s+{_ADJ}(?:{pat})", text, re.I):
            out.append((subj, float(re.sub(r"\s", "", m.group(1))), m.group(0)))
        for m in re.finditer(rf"(?:{pat}){_PREP}\s*[:—–-]?\s*«?\s*{_NUM}(?![\d,.]\d){_NOT_UNIT}", text, re.I):
            out.append((subj, float(re.sub(r"\s", "", m.group(1))), m.group(0)))
    return out


def collect_subject_numbers(obj, out: dict | None = None, key: str = "") -> dict:
    """Какие числа результаты инструментов называют про курьеров и про каждую роль."""
    out = {s: set() for s in SUBJECTS} if out is None else out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        if key in COURIER_KEYS:
            out["courier"].add(float(obj))
        if key in _ROLE_BY_NAME:
            out[_ROLE_BY_NAME[key]].add(float(obj))
    elif isinstance(obj, str):
        for subj, v, _ in subject_mentions(obj):
            out[subj].add(v)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            collect_subject_numbers(v, out, str(k))
    elif isinstance(obj, (list, tuple)):
        if key in COURIER_LISTS:
            out["courier"].add(float(len(obj)))
        roles = [_ROLE_BY_NAME.get(x.get("role") or x.get("role_label") or "") for x in obj if isinstance(x, dict)]
        for r in set(filter(None, roles)):
            out[r].add(float(roles.count(r)))              # «в списке 4 координатора»
        for v in obj:
            collect_subject_numbers(v, out, key)
    return out


def subject_issues(text: str, seen_results: list) -> list[str]:
    allowed = collect_subject_numbers(seen_results)
    allowed["courier"].add(float(config.EXPECTED_COUNTS["seeds"]))
    issues = []
    for subj, v, frag in subject_mentions(text):
        if v not in allowed[subj]:
            known = ", ".join(f"{x:g}" for x in sorted(allowed[subj])[:6]) or "нет"
            issues.append(f"«{frag.strip()}»: {v:g} — не число {SUBJECT_NAMES[subj]} в результатах "
                          f"(известны: {known})")
    return issues


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

    issues += subject_issues(plain, seen_results)

    low = text.lower()
    hits = [w for w in sorted(set(config.FORBIDDEN_WORDS) | set(EXTRA_FORBIDDEN_STEMS)) if w in low]
    for w in hits:
        if not any(o != w and w.startswith(o) for o in hits):     # «преступник» уже покрыт «преступн»
            issues.append(f"запрещённая формулировка «{w}…» — нужен язык гипотез")

    return {"verified": not issues, "issues": list(dict.fromkeys(issues)), "gids": gids}
