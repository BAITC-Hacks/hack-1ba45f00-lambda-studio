"""Разбор узла для жюри: роль, почему (rule_trace), метрики, связи — за минуту.

    python -m mycelium.explain <gid | часть gid>

Читает out/web/cards.json (результат пайплайна) — те же данные, что карточка на экране.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mycelium import config
from mycelium.evidence import fmt_kzt


def _load_cards(out_dir: Path) -> dict:
    path = out_dir / "web" / "cards.json"
    if not path.exists():
        raise SystemExit(f"Нет {path}. Сначала запустите: python -m mycelium.pipeline")
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(query: str, cards: dict) -> str:
    q = query.strip()
    if q in cards:
        return q
    hits = sorted((i for i in cards if q in i), key=lambda i: -(cards[i]["priority"] or 0))
    if not hits:
        raise SystemExit(f"Узел «{q}» не найден")
    if len(hits) > 1:
        print(f"Под «{q}» подходят {len(hits)} узлов, показываю самый приоритетный. Остальные: "
              + ", ".join(hits[1:6]) + (" …" if len(hits) > 6 else ""))
    return hits[0]


def _v(x, kzt: bool = False) -> str:
    if x is None:
        return "—"
    if kzt:
        return fmt_kzt(x)
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def render(c: dict) -> str:
    m = c["metrics"]
    lines = []
    flags = [f for f, on in (("курьер", c["is_seed"]), ("граница выборки", c["truncated"]),
                             ("в ядре-кольце", c["in_core"])) if on]
    lines.append(f"Узел {c['id']}" + (f"  [{', '.join(flags)}]" if flags else ""))
    rank = f", место в топе проверки {c['rank']}" if c["rank"] else ""
    lines.append(f"Роль: {c['role_label']} ({c['role']}) — {c['stability_text']}")
    lines.append(f"Приоритет проверки: {c['priority']:.3f}{rank}; кластер {c['cluster']}; колено {c['depth']}")
    lines.append("")
    lines.append(f"Обоснование: {c['evidence']}")
    lines.append("")
    lines.append("Почему эта роль (правила по порядку, первое совпадение побеждает):")
    for rule in c["rule_trace"]:
        label = config.ROLE_LABELS[rule["role"]]
        if rule.get("skipped"):
            lines.append(f"  · {label}: не проверялось — роль уже определена")
            continue
        mark = "✓ СОВПАЛО" if rule["matched"] else "✗"
        lines.append(f"  {mark} {label}")
        for ch in rule["checks"]:
            lines.append(f"      {'✓' if ch['ok'] else '✗'} {ch['label']}: {_v(ch['value'])}")
    lines.append("")
    p = m["pass_through"]
    if p is None:
        pt = "—  (у курьеров входящие занижены выгрузкой)" if c["is_seed"] else "—  (нет входящих)"
    elif p > 1.2:
        pt = f"{p:.0%}  (отдал больше, чем видно входящих: приток вне выборки)"
    else:
        pt = f"{p:.0%}"
    lines.append("Метрики:")
    lines.append(f"  плательщиков {m['in_deg']}, получателей {m['out_deg']}; "
                 f"вход {_v(m['in_kzt'], True)}, выход {_v(m['out_kzt'], True)}")
    lines.append(f"  отдал дальше (доля от полученного): {pt}; медиана удержания, дн.: {_v(m['hold_median_days'])}")
    lines.append(f"  сюда доходят деньги курьеров: {m['seed_reach']}; синхронный сбор: {m['sync_max_payers']} плат. в 1 день")
    lines.append(f"  блокировка только этого узла отсекает: {_v(m['cut_kzt'], True)}")
    for title, key in (("Главные плательщики", "payers"), ("Главные получатели", "recipients")):
        rows = c[key][:5]
        if rows:
            lines.append(f"{title}:")
            for r in rows:
                lines.append(f"  {r['id']}  {fmt_kzt(r['sum_kzt']):>12}  {r['n_tx']} пер.  {config.ROLE_LABELS[r['role']]}")
    if c["upstream_seeds"]:
        lines.append(f"Курьеры, чьи деньги доходят сюда ({len(c['upstream_seeds'])}): "
                     + ", ".join(c["upstream_seeds"][:8]) + (" …" if len(c["upstream_seeds"]) > 8 else ""))
    return "\n".join(lines)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Разбор узла: роль, правила, метрики, связи")
    ap.add_argument("gid", help="gid или его часть")
    ap.add_argument("--out", type=Path, default=config.OUT_DIR)
    a = ap.parse_args(argv)
    cards = _load_cards(a.out)
    print(render(cards[_resolve(a.gid, cards)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
