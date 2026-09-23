"""Справка по делу: out/brief.md + out/brief_check.json (та же сверка с графом).

    python -m mycelium.ai.brief              # с ключом — один вызов модели по facts.json, без ключа — шаблон
    python -m mycelium.ai.brief --template   # всегда шаблон (детерминированно, без сети)

Шаблонная справка собирается кодом из тех же фактов и проходит ту же сверку, поэтому экран без ключа
показывает честный, проверяемый текст. Сгенерированную справку коммитим.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from mycelium import config
from mycelium.ai import analyst, prompts
from mycelium.ai.verify import verify
from mycelium.evidence import fmt_kzt, plural

BRIEF_MD = "brief.md"
BRIEF_CHECK = "brief_check.json"


def _pct(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") + " %"


def template_brief(f: dict) -> str:
    """Справка из facts.json без модели: те же разделы, только посчитанные числа."""
    n = f["network"]
    roles = n["roles"]
    core = n["core"]
    lines = ["**Что видим.**",
             f"Граф переводов за июль: клиентов {n['n_nodes']}, связей {n['n_edges']}, курьеров {n['n_seeds']}, "
             f"оборот {fmt_kzt(n['total_kzt'])}. По правилам с порогами выделены признаки ролей: координаторов — "
             f"{roles['coordinator']}, точек сбора — {roles['consolidator']}, распределителей — "
             f"{roles['distributor']}, транзитных узлов — {roles['transit']}, конечных получателей — "
             f"{roles['terminal']}. Есть ядро-кольцо из {core['size']} узлов, где деньги могут вернуться к "
             f"отправителю; в нём координаторов — {core['roles'].get('coordinator', 0)}.", ""]

    lines += ["**Кого проверить первым.**"]
    for t in f["top"][:5]:
        ev = t["evidence"].split("; ")[0]     # основа обоснования, без хвостов
        lines.append(f"- [gid:{t['id']}] — {t['role_label'].lower()}: {ev}")
    lines.append("")

    lines += ["**Группы.**"]
    groups = [c for c in f["clusters"] if c["cluster_id"] != config.NO_EDGES_CLUSTER]
    for c in sorted(groups, key=lambda c: -c["sum_kzt_internal"])[:3]:
        lines.append(f"- Кластер {c['cluster_id']}: узлов {c['n_nodes']}, курьеров {c['n_seed']}, внутренний оборот "
                     f"{fmt_kzt(c['sum_kzt_internal'])}. {_tag_gids(c['hypothesis'])}.")
    lines.append("")

    req = f["next_requests"]
    out_req = [r for r in req if r["request_type"] == "outgoing"][:2]
    in_req = [r for r in req if r["request_type"] == "incoming"][:2]
    lines += ["**Что запросить дальше.**"]
    if out_req:
        lines.append("- Исходящие переводы у узлов на границе выборки (4-е колено, исходящие не видны): "
                     + "; ".join(f"[gid:{r['id']}] — получено {fmt_kzt(r['in_kzt'])}" for r in out_req) + ".")
    if in_req:
        lines.append("- Входящие переводы у узлов, которые отдали больше, чем видно входящих: "
                     + "; ".join(f"[gid:{r['id']}] ({r['reason'].split(' — ')[0]})" for r in in_req) + ".")
    s = f["resilience"]["strategies"]
    lines.append(f"- Проверять и блокировать — разные задачи: при блокировке 10 узлов план блокировки снижает поток "
                 f"денег курьеров на {_pct(s['plan']['10']['drop_reach_pct'])}, топ проверки — на "
                 f"{_pct(s['priority']['10']['drop_reach_pct'])}, 10 случайных узлов — на "
                 f"{_pct(s['random']['10']['drop_reach_pct'])}.")
    lines.append("")

    lines += ["**Ограничения.**"] + [f"- {x}" for x in f["limitations"][:4]]
    lines += ["", "_Справка собрана кодом из посчитанных фактов (без модели ИИ). Все выводы — гипотезы для "
                  "проверки, рекомендуем проверить перечисленные узлы._"]
    return "\n".join(lines)


def _tag_gids(text: str) -> str:
    """gid в тексте гипотезы → [gid:…], чтобы на экране они были кликабельны."""
    import re
    return re.sub(r"(?<![\d:])(\d{18})(?!\d)", r"[gid:\1]", text)


def semantic_issues(text: str, f: dict) -> list[str]:
    """Смысловые проверки справки поверх числовой сверки.

    1. В «Кого проверить первым» — ровно первые 5 узлов facts.top в том же порядке.
    2. Проценты стратегий при N = 10 не перепутаны (у каждого процента — своя стратегия рядом).
    """
    import re
    issues = []
    m = re.search(r"\*\*Кого проверить первым\.?\*\*(.*?)(?=\n\*\*|\Z)", text, re.S)
    want = [t["id"] for t in f["top"][:5]]
    if not m:
        issues.append("нет раздела «Кого проверить первым»")
    else:
        got = re.findall(r"\[gid:(\d+)\]", m.group(1))
        if got[:5] != want:
            issues.append("в «Кого проверить первым» не первые 5 узлов топа проверки по порядку")
    names = {"plan": r"план\w* блокировк", "priority": r"топ\w* проверк|по приоритет",
             "turnover": r"по оборот", "random": r"случайн"}
    strat = f["resilience"]["strategies"]
    for sent in re.split(r"(?<=[.!?])\s+", text):
        for key, pat in names.items():
            if not re.search(pat, sent, re.I):
                continue
            own = str(strat[key]["10"]["drop_reach_pct"]).replace(".", ",")
            others = {k: str(v["10"]["drop_reach_pct"]).replace(".", ",") for k, v in strat.items() if k != key}
            pcts = re.findall(r"(\d+(?:,\d+)?)\s*%", sent)
            mentioned = [k for k, p in names.items() if re.search(p, sent, re.I)]
            if len(mentioned) == 1 and pcts and own not in pcts and any(o in pcts for o in others.values()):
                issues.append(f"процент в предложении о стратегии «{key}» относится к другой стратегии")
    return issues


def llm_brief(f: dict) -> str:
    client, model = analyst.client_and_model()
    messages = [{"role": "system", "content": prompts.BRIEF},
                {"role": "user", "content": json.dumps(f, ensure_ascii=False)}]
    resp = analyst.complete(client, model, messages)
    return (resp.choices[0].message.content or "").strip()


def generate(out_dir: Path = config.OUT_DIR, force_template: bool = False) -> dict:
    facts_path = out_dir / "facts.json"
    if not facts_path.exists():
        raise SystemExit("Нет out/facts.json — сначала python -m mycelium.pipeline")
    f = json.loads(facts_path.read_text(encoding="utf-8"))
    graph_gids = {int(n["id"]) for n in json.loads((out_dir / "web" / "graph.json").read_text("utf-8"))["nodes"]}

    source, model = "template", None
    if not force_template and analyst.enabled():
        try:
            text = llm_brief(f)
            source, model = "openai", analyst.settings()["model"]
        except Exception as e:  # noqa: BLE001 — сеть/квота: откатываемся на шаблон, но честно пишем почему
            print(f"⚠ ИИ недоступен ({type(e).__name__}: {e}) — справка по шаблону")
            text = template_brief(f)
    else:
        text = template_brief(f)

    check = verify(text, [f], graph_gids)
    check["issues"] += semantic_issues(text, f)
    check["verified"] = not check["issues"]
    if source == "openai" and not check["verified"]:
        print("⚠ справка модели не прошла сверку: " + "; ".join(check["issues"]) + " — оставляю шаблонную")
        text, source, model = template_brief(f), "template", None
        check = verify(text, [f], graph_gids)
        check["issues"] += semantic_issues(text, f)
        check["verified"] = not check["issues"]
    meta = {"verified": check["verified"], "issues": check["issues"], "gids": check["gids"],
            "source": source, "model": model, "generated_at": datetime.now().isoformat(timespec="seconds")}
    (out_dir / BRIEF_MD).write_text(text + "\n", encoding="utf-8")
    (out_dir / BRIEF_CHECK).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Справка по делу: out/brief.md + out/brief_check.json")
    ap.add_argument("--out", type=Path, default=config.OUT_DIR)
    ap.add_argument("--template", action="store_true", help="без модели, по шаблону из facts.json")
    a = ap.parse_args(argv)
    meta = generate(a.out, a.template)
    words = len((a.out / BRIEF_MD).read_text(encoding="utf-8").split())
    src = "модель " + meta["model"] if meta["source"] == "openai" else "шаблон (без ИИ)"
    print(f"out/{BRIEF_MD}: {words} {plural(words, 'слово', 'слова', 'слов')}, источник — {src}")
    print("✓ сверено с графом" if meta["verified"] else "⚠ не сверено:\n  " + "\n  ".join(meta["issues"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
