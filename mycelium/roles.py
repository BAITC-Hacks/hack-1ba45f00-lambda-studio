"""Роли узлов: правила с порогами, rule_trace и устойчивость (CLAUDE.md §8).

ОДИН ИСТОЧНИК ЛОГИКИ. Правила описаны один раз — как данные (RULES). Из них строятся:
  * assign_roles()  — векторно, для всех узлов (и для 50 прогонов устойчивости);
  * rule_trace()    — по одному узлу, с реальными значениями: карточка, explain.py, ИИ-аналитик.
Поэтому объяснение роли не может разойтись с тем, как роль посчитана.

Порядок правил (первое совпадение побеждает):
  0. нет рёбер                                   → peripheral
  1. coordinator   плательщиков ≥ 8 и получателей ≥ 20       — и собирает, и раздаёт
  2. distributor   получателей ≥ 20                          — веерная раздача
  3. consolidator  плательщиков ≥ 6                          — сбор от многих
  4. transit       не курьер, не граница выборки, есть вход и выход,
                   пропуск ≥ 0.8, медиана удержания ≤ 3 дн.  — пришло и быстро ушло
  5. terminal      нет исходящих, но узел раскрыт обходом (не граница выборки),
                   есть вход, получено ≥ 200 тыс. ₸          — деньги оседают
  6. peripheral    всё остальное
Пустое значение метрики (NaN: пропуск у курьера, удержание без исходящих) проверку не проходит.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

from mycelium import config
from mycelium.features import safe_float


@dataclass(frozen=True)
class Check:
    metric: str                 # колонка таблицы узлов
    op: str                     # ">=", "<=", "==", "is", "not"
    threshold: Optional[str]    # ключ ROLE_THRESHOLDS или None (тогда const)
    label: str                  # человеческим языком; {t} → значение порога
    const: float = 0
    flag_text: tuple = ("да", "нет")   # для флагов: как показать значение True / False

    def limit(self, th: dict) -> float:
        return th[self.threshold] if self.threshold else self.const

    def text(self, th: dict) -> str:
        return self.label.format(t=_fmt_threshold(self.threshold, self.limit(th)))

    def mask(self, df: pd.DataFrame, th: dict) -> pd.Series:
        col = df[self.metric]
        if self.op == "is":
            return col.fillna(False).astype(bool)
        if self.op == "not":
            return ~col.fillna(True).astype(bool)
        # сравнение с NaN даёт False — пустая метрика правило не проходит
        return _OPS[self.op](col.astype(float), self.limit(th)).fillna(False).astype(bool)

    def evaluate(self, row: pd.Series, th: dict) -> dict:
        v = row[self.metric]
        if self.op in ("is", "not"):
            b = bool(v) if not _isnan(v) else (self.op == "not")
            ok = b if self.op == "is" else not b
            value = self.flag_text[0] if b else self.flag_text[1]
        else:
            f = safe_float(v)
            ok = f is not None and bool(_OPS[self.op](f, self.limit(th)))
            value = _json_number(f)
        return {"label": self.text(th), "value": value, "ok": bool(ok)}


_OPS: dict[str, Callable] = {">=": operator.ge, "<=": operator.le, "==": operator.eq}


@dataclass(frozen=True)
class Rule:
    role: str
    checks: tuple


SEED_TEXT = ("курьер", "не курьер")
TRUNC_TEXT = ("граница выборки", "раскрыт обходом")

NO_EDGES = Rule("peripheral", (
    Check("in_deg", "==", None, "плательщиков = 0"),
    Check("out_deg", "==", None, "получателей = 0"),
))

RULES = (
    Rule("coordinator", (
        Check("in_deg", ">=", "COORD_MIN_IN", "плательщиков ≥ {t}"),
        Check("out_deg", ">=", "COORD_MIN_OUT", "получателей ≥ {t}"),
    )),
    Rule("distributor", (
        Check("out_deg", ">=", "DIST_MIN_OUT", "получателей ≥ {t}"),
    )),
    Rule("consolidator", (
        Check("in_deg", ">=", "CONS_MIN_IN", "плательщиков ≥ {t}"),
    )),
    Rule("transit", (
        Check("is_seed", "not", None, "не курьер (у курьеров входящие занижены)", flag_text=SEED_TEXT),
        Check("truncated", "not", None, "не граница выборки (4-е колено)", flag_text=TRUNC_TEXT),
        Check("in_deg", ">=", None, "есть плательщики (≥ 1)", const=1),
        Check("out_deg", ">=", None, "есть получатели (≥ 1)", const=1),
        Check("pass_through", ">=", "TR_MIN_PASS", "отдал дальше ≥ {t} полученного"),
        Check("hold_median_days", "<=", "TR_MAX_HOLD", "медиана удержания ≤ {t} дн."),
    )),
    Rule("terminal", (
        Check("out_deg", "==", None, "получателей = 0 (исходящих ≥ 5 тыс. ₸ нет)"),
        Check("truncated", "not", None, "узел раскрыт обходом (не 4-е колено)", flag_text=TRUNC_TEXT),
        Check("in_deg", ">=", None, "есть плательщики (≥ 1)", const=1),
        Check("in_kzt", ">=", "TERM_MIN_KZT", "получено ≥ {t}"),
    )),
)


# ── Назначение ролей ──────────────────────────────────────────────────────────

def _rule_mask(rule: Rule, df: pd.DataFrame, th: dict) -> pd.Series:
    m = pd.Series(True, index=df.index)
    for c in rule.checks:
        m &= c.mask(df, th)
    return m


def assign_roles(df: pd.DataFrame, th: Optional[dict] = None) -> pd.Series:
    """Роль каждого узла по правилам RULES (первое совпадение побеждает)."""
    th = config.ROLE_THRESHOLDS if th is None else th
    role = pd.Series(pd.NA, index=df.index, dtype=object)
    role[_rule_mask(NO_EDGES, df, th)] = "peripheral"
    for rule in RULES:
        free = role.isna()
        role[free & _rule_mask(rule, df, th)] = rule.role
    return role.fillna("peripheral").astype(object)


def rule_trace(row: pd.Series, th: Optional[dict] = None) -> list[dict]:
    """Пошаговое объяснение роли одного узла: те же проверки, что в assign_roles.

    Форма (docs/mock/node.json): [{"role", "matched", "checks": [{"label","value","ok"}]}, …];
    правила после сработавшего — {"skipped": true, "checks": []}.
    """
    th = config.ROLE_THRESHOLDS if th is None else th
    trace: list[dict] = []
    no_edges = [c.evaluate(row, th) for c in NO_EDGES.checks]
    if all(c["ok"] for c in no_edges):
        trace.append({"role": "peripheral", "matched": True,
                      "checks": [{"label": "нет переводов ≥ 5 тыс. ₸ в выгрузке", "value": 0, "ok": True}]})
        trace += [{"role": r.role, "matched": False, "skipped": True, "checks": []} for r in RULES]
        return trace
    matched = False
    for rule in RULES:
        if matched:
            trace.append({"role": rule.role, "matched": False, "skipped": True, "checks": []})
            continue
        checks = [c.evaluate(row, th) for c in rule.checks]
        matched = all(c["ok"] for c in checks)
        trace.append({"role": rule.role, "matched": matched, "checks": checks})
    trace.append({"role": "peripheral", "matched": not matched, "skipped": matched,
                  "checks": [] if matched else
                  [{"label": "ни одно правило выше не выполнено", "value": "да", "ok": True}]})
    return trace


# ── Устойчивость (§8.3) ───────────────────────────────────────────────────────

def perturbed_thresholds(rng: np.random.Generator, th: Optional[dict] = None) -> dict:
    """Один вариант порогов: каждый × U[0.8, 1.2]; целые округляются, минимум 1.

    Порядок вызовов rng совпадает с explore.stability() — счётчики и role_score те же.
    """
    th = config.ROLE_THRESHOLDS if th is None else th
    lo, hi = config.STABILITY_RANGE
    return {k: (max(1, round(v * rng.uniform(lo, hi))) if k in config.INT_THRESHOLDS
                else v * rng.uniform(lo, hi))
            for k, v in th.items()}


def stability(df: pd.DataFrame, n: int = config.STABILITY_RUNS,
              seed: int = config.SEED) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(роль при базовых порогах, role_score = доля совпавших прогонов, число совпавших прогонов)."""
    base = assign_roles(df)
    rng = np.random.default_rng(seed)
    same = np.zeros(len(df), dtype=int)
    for _ in range(n):
        same += (assign_roles(df, perturbed_thresholds(rng)).values == base.values)
    k = pd.Series(same, index=df.index)
    return base, (k / n).astype(float), k


def stability_text(k: int, n: int = config.STABILITY_RUNS) -> str:
    return f"роль устойчива в {int(k)} из {n} вариантов порогов"


def add_roles(df: pd.DataFrame) -> pd.DataFrame:
    """Колонки role, role_score, stability_k."""
    out = df.copy()
    role, score, k = stability(out)
    out["role"], out["role_score"], out["stability_k"] = role, score, k.astype(int)
    return out


def role_counts(role: pd.Series) -> pd.Series:
    return role.value_counts().reindex(config.ROLES, fill_value=0)


# ── Форматирование значений в rule_trace ──────────────────────────────────────

def _isnan(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA


def _json_number(f: Optional[float]):
    if f is None:
        return None
    return int(f) if float(f).is_integer() else round(f, 2)


def _fmt_threshold(key: Optional[str], v: float) -> str:
    if key == "TR_MIN_PASS":
        return f"{v:.0%}"
    if key == "TERM_MIN_KZT":
        from mycelium.evidence import fmt_kzt
        return fmt_kzt(v)
    return f"{v:g}"
