"""Тексты обоснований и форматирование сумм (CLAUDE.md §8.4).

Evidence ≤ 200 символов, обязательно с числами, только язык гипотез.
Короткие хвосты (ядро-кольцо, синхронный сбор, отсекаемый поток) дописываются, если влезают.
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from mycelium import config


# ── Форматирование ────────────────────────────────────────────────────────────

def fmt_kzt(x: Optional[float]) -> str:
    """< 1 млн → «850 тыс. ₸», иначе «3,2 млн ₸»."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    x = float(x)
    if abs(x) < 1_000:
        return f"{x:.0f} ₸"
    thousands = round(x / 1_000)
    if abs(thousands) < 1_000:
        return f"{thousands:.0f} тыс. ₸"
    return f"{x / 1_000_000:.1f}".replace(".", ",") + " млн ₸"


def fmt_pct(x: float) -> str:
    return f"{x:.0%}"


def fmt_days(x: float) -> str:
    return f"{x:.0f}" if float(x).is_integer() else f"{x:.1f}".replace(".", ",")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское согласование: 1 плательщик, 3 плательщика, 5 плательщиков."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def n_payers(n: int) -> str:
    return f"{int(n)} {plural(n, 'плательщика', 'плательщиков', 'плательщиков')}"   # «от N плательщиков»


def n_recipients(n: int) -> str:
    return f"{int(n)} {plural(n, 'получатель', 'получателя', 'получателей')}"


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip(" ,;:") + "…"


def with_tails(base: str, tails: list[str], limit: int = config.EVIDENCE_MAX_LEN) -> str:
    """Дописывает хвосты по порядку, пока влезают; основу жёстко обрезает с «…»."""
    text = clip(base, limit)
    for t in tails:
        cand = f"{text}; {t}"
        if len(cand) <= limit:
            text = cand
    return text


# ── Evidence по роли ──────────────────────────────────────────────────────────

def _has(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _tails(r: pd.Series) -> list[str]:
    tails = []
    if bool(r.get("in_core", False)):
        tails.append(f"в ядре-кольце из {int(r.core_size)}")
    sync = r.get("sync_max_payers", 0)
    if _has(sync) and sync >= config.EVIDENCE_SYNC_MIN_PAYERS:
        tails.append(f"синхронный сбор: {int(sync)} плат. в 1 день")
    cut = r.get("cut_kzt", 0)
    if _has(cut) and cut >= config.EVIDENCE_CUT_MIN_KZT:
        tails.append(f"блокировка отсекает {fmt_kzt(cut)}")
    return tails


def node_evidence(r: pd.Series) -> str:
    """Обоснование роли одного узла. r — строка таблицы узлов (метрики + role)."""
    role = r.role
    in_deg, out_deg = int(r.in_deg), int(r.out_deg)
    in_kzt, out_kzt = fmt_kzt(r.in_kzt), fmt_kzt(r.out_kzt)
    pt = r.pass_through
    hold = r.get("hold_median_days")
    seed_note = " (курьер: входящие занижены выгрузкой)" if r.is_seed else ""

    if in_deg == 0 and out_deg == 0:
        return "Нет переводов ≥5 тыс. ₸ внутри банка за июль" + ("; курьер из исходного списка" if r.is_seed else "")

    if role == "coordinator":
        base = (f"Кандидат в координаторы: собирает от {n_payers(in_deg)} ({in_kzt}) "
                f"и раздаёт {out_deg} {plural(out_deg, 'получателю', 'получателям', 'получателям')} ({out_kzt})")
        return with_tails(base, _tails(r))

    if role == "consolidator":
        if r.truncated:
            base = (f"Признаки сбора: {in_deg} {plural(in_deg, 'плательщик', 'плательщика', 'плательщиков')}, вход {in_kzt}; "
                    f"исходящие не видны — граница выборки (4-е колено)")
            return with_tails(base, _tails(r))
        base = (f"Признаки консолидации: {in_deg} {plural(in_deg, 'плательщик', 'плательщика', 'плательщиков')} (курьеров: {int(r.n_seed_payers)}), "
                f"вход {in_kzt}, дальше {out_deg} получ.")
        if not _has(pt):
            base += seed_note
        elif pt <= 1:
            base += f", удержано {fmt_pct(1 - pt)}"
        else:
            base += ", отдал больше, чем видно входящих"
        return with_tails(base, _tails(r))

    if role == "transit":
        h = fmt_days(hold) if _has(hold) else "—"
        if r.inflow_outside_sample:
            base = (f"Признаки транзита: ушло {out_kzt} при видимом входе {in_kzt} за ~{h} дн. "
                    f"— приток вне выборки")
        else:
            base = f"Признаки транзита: пришло {in_kzt}, ушло {fmt_pct(pt)} за ~{h} дн.; связи {in_deg}→{out_deg}"
        return with_tails(base, _tails(r))

    if role == "distributor":
        base = f"Признаки веерной раздачи: {n_recipients(out_deg)}, отправлено {out_kzt}"
        if r.is_seed:
            base += "; курьер, вход вне выборки"
        return with_tails(base, _tails(r))

    if role == "terminal":
        base = (f"Деньги оседают: получено {in_kzt} от {n_payers(in_deg)}, "
                f"исходящих ≥5 тыс. ₸ нет (узел раскрыт обходом)")
        return with_tails(base, _tails(r))

    # peripheral
    if r.truncated:
        base = f"Граница выборки (4-е колено): исходящие не видны; получено {in_kzt} от {n_payers(in_deg)}"
        return with_tails(base, [])
    base = (f"Признаков роли нет: связи {in_deg} вх./{out_deg} исх., "
            f"оборот {fmt_kzt(max(r.in_kzt, r.out_kzt))}")
    return with_tails(base, (["курьер из исходного списка"] if r.is_seed else []) + _tails(r))


def add_evidence(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["evidence"] = [node_evidence(r) for _, r in out.iterrows()]
    return out


# ── Почему в топе (§10) ───────────────────────────────────────────────────────

def factor_text(factor: str, r: pd.Series) -> Optional[str]:
    """Человеческая формулировка вклада фактора приоритета."""
    if factor == "cut_kzt" and r.get("cut_kzt", 0) > 0:
        return f"блокировка отсекает {fmt_kzt(r.cut_kzt)}"
    if factor == "in_deg" and r.in_deg > 0:
        return f"{int(r.in_deg)} {plural(r.in_deg, 'плательщик', 'плательщика', 'плательщиков')}"
    if factor == "betweenness" and r.get("betweenness_pct", 0) > 0:
        top = max(1, round((1 - r.betweenness_pct) * 100))
        return f"посредничество — в верхних {top} % узлов"
    if factor == "flow_kzt" and r.flow_kzt > 0 and fmt_kzt(r.flow_kzt) not in r.evidence:
        return f"оборот {fmt_kzt(r.flow_kzt)}"
    if factor == "role_score":
        return f"роль устойчива в {int(r.stability_k)} из {config.STABILITY_RUNS}"
    return None


def top_why(r: pd.Series, top_factors: list[str]) -> str:
    """evidence + два главных вклада в приоритет, ≤ 300 символов."""
    # факторы, которые уже названы в evidence (хвост «блокировка отсекает…»), не повторяем
    parts = [t for t in (factor_text(f, r) for f in top_factors) if t and t not in r.evidence][:2]
    extra = "главное в приоритете: " + "; ".join(parts) if parts else ""
    if not extra:
        return clip(r.evidence, config.WHY_MAX_LEN)
    budget = config.WHY_MAX_LEN - len(extra) - 3
    return f"{clip(r.evidence, budget)}. {extra[0].upper()}{extra[1:]}" if budget > 40 else clip(extra, config.WHY_MAX_LEN)
