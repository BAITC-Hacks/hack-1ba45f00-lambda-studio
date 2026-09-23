"""Смысловые тесты: выходы пайплайна против независимого пересчёта из data/*.parquet (tests/independent.py).

Быстрые, без ключа OpenAI. Номера — как в постановке задачи.
"""
from __future__ import annotations

import json
import random
import re

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from mycelium import config
from tests.independent import OUT, graph, hold_median, kzt_close, metrics, parse_kzt, raw, reach, role_by_rules

TOTAL_KZT = 365_890_012.01
TH = config.ROLE_THRESHOLDS


@pytest.fixture(scope="module")
def nr() -> pd.DataFrame:
    return pd.read_csv(OUT / "nodes_roles.csv").set_index("gid")


@pytest.fixture(scope="module")
def web():
    load = lambda n: json.loads((OUT / "web" / f"{n}.json").read_text(encoding="utf-8"))
    return {n: load(n) for n in ("graph", "cards", "clusters", "resilience", "sankey", "top_block", "top_check")}


@pytest.fixture(scope="module")
def client():
    from mycelium.serve import create_app
    return TestClient(create_app())


# ── Роли ──────────────────────────────────────────────────────────────────────

def test_00_roles_match_independent_rules(nr):
    """Роль каждого узла = правила CLAUDE.md §8.2 на независимо пересчитанных метриках."""
    m = metrics()
    mine = m.apply(lambda r: role_by_rules(r, TH), axis=1)
    diff = mine[mine != nr.role.reindex(mine.index)]
    assert diff.empty, f"{len(diff)} узлов с другой ролью, например {diff.head(3).to_dict()}"


def test_01_terminal_seed_passthrough(nr):
    m = metrics()
    term = nr.index[nr.role == "terminal"]
    assert (m.loc[term, "out_deg"] == 0).all(), "у terminal есть исходящие"
    assert (m.loc[term, "depth"] != 4).all(), "terminal на 4-м колене (граница выборки)"
    seeds = m.index[m.is_seed]
    assert not (nr.loc[seeds, "role"] == "transit").any(), "курьер получил роль transit"
    assert nr.loc[seeds, "pass_through"].isna().all(), "у курьера посчитан pass_through"


def test_02_orphans_peripheral_cluster0(nr):
    m = metrics()
    orphans = set(m.index[(m.in_deg == 0) & (m.out_deg == 0)])
    assert len(orphans) == 19
    assert (nr.loc[list(orphans), "role"] == "peripheral").all()
    assert set(nr.index[nr.cluster_id == 0]) == orphans, "кластер 0 ≠ ровно узлы без рёбер"


LABEL_RULES = [  # метка проверки → (метрика, оператор, ключ порога или константа)
    (r"^плательщиков ≥ (\d+)$", "in_deg", ">=", None),
    (r"^получателей ≥ (\d+)$", "out_deg", ">=", None),
    (r"^есть плательщики \(≥ 1\)$", "in_deg", ">=", 1),
    (r"^есть получатели \(≥ 1\)$", "out_deg", ">=", 1),
    (r"^отдал дальше ≥ (\d+)% полученного$", "pass_through", ">=%", None),
    (r"^медиана удержания ≤ (\d+) дн\.$", "hold", "<=", None),
    (r"^получено ≥ (\d+) тыс\. ₸$", "in_kzt", ">=k", None),
    (r"^получателей = 0", "out_deg", "==", 0),
    (r"^не курьер", "is_seed", "not", None),
    (r"^не граница выборки", "truncated", "not", None),
    (r"^узел раскрыт обходом", "truncated", "not", None),
]
ALLOWED_THRESHOLDS = {"in_deg": {TH["COORD_MIN_IN"], TH["CONS_MIN_IN"]},
                      "out_deg": {TH["COORD_MIN_OUT"], TH["DIST_MIN_OUT"]},
                      "pass_through": {TH["TR_MIN_PASS"]}, "hold": {TH["TR_MAX_HOLD"]},
                      "in_kzt": {TH["TERM_MIN_KZT"]}}


def _recheck(label: str, m: pd.Series):
    for pat, metric, op, const in LABEL_RULES:
        mt = re.match(pat, label)
        if not mt:
            continue
        v = m[metric]
        if op == "not":
            return not bool(v), None
        t = const if const is not None else float(mt.group(1))
        if op == ">=%":
            t /= 100
        if op == ">=k":
            t *= 1000
        if const is None:
            assert t in ALLOWED_THRESHOLDS[metric], f"порог «{label}» не из config"
        if v != v:  # NaN не проходит
            return False, t
        return {">=": v >= t, ">=%": v >= t, ">=k": v >= t, "<=": v <= t, "==": v == t}[op], t
    return None, None


def test_03_rule_trace_matches(nr, web):
    """Выбранная роль в rule_trace = роль в CSV; каждый ok = пересчёт «значение против порога из config»."""
    m = metrics()
    bad = []
    for gid, card in web["cards"].items():
        g = int(gid)
        matched = [r["role"] for r in card["rule_trace"] if r["matched"]]
        if matched != [nr.at[g, "role"]]:
            bad.append(f"{gid}: rule_trace {matched} ≠ CSV {nr.at[g, 'role']}")
        for rule in card["rule_trace"]:
            for ch in rule["checks"]:
                ok, _ = _recheck(ch["label"], m.loc[g])
                if ok is not None and ok != ch["ok"]:
                    bad.append(f"{gid}: «{ch['label']}» ok={ch['ok']}, пересчёт {ok}")
    assert not bad, f"{len(bad)} расхождений: {bad[:5]}"


def test_04_stability_threshold_nodes_less_stable(nr):
    cons = nr[nr.role == "consolidator"]
    hi, lo = cons[cons.in_deg >= 10].role_score, cons[cons.in_deg == 6].role_score
    assert len(hi) and len(lo)
    assert hi.mean() > lo.mean(), f"in_deg ≥ 10: {hi.mean():.2f}, in_deg = 6: {lo.mean():.2f}"


# ── Обоснования ───────────────────────────────────────────────────────────────

def _evidence_sums(ev: str, role: str, m: pd.Series) -> list[str]:
    """Каждая сумма в evidence — в своём контексте — сверяется с метрикой узла."""
    errs = []
    base = ev.split("; блокировка отсекает")[0]
    ctx = [  # (регулярка суммы в контексте, какая метрика)
        (r"собирает от \d+ \w+ \(([^)]*₸)\)", "in_kzt"), (r"раздаёт \d+ \w+ \(([^)]*₸)\)", "out_kzt"),
        (r"вход ([\d,]+ (?:млн |тыс\. )?₸)", "in_kzt"), (r"получено ([\d,]+ (?:млн |тыс\. )?₸)", "in_kzt"),
        (r"пришло ([\d,]+ (?:млн |тыс\. )?₸)", "in_kzt"), (r"видимом входе ([\d,]+ (?:млн |тыс\. )?₸)", "in_kzt"),
        (r"ушло ([\d,]+ (?:млн |тыс\. )?₸)", "out_kzt"), (r"отправлено ([\d,]+ (?:млн |тыс\. )?₸)", "out_kzt"),
        (r"оборот ([\d,]+ (?:млн |тыс\. )?₸)", "flow"),
    ]
    for pat, metric in ctx:
        for mt in re.finditer(pat, base):
            (val,) = parse_kzt(mt.group(1))
            true = max(m.in_kzt, m.out_kzt) if metric == "flow" else m[metric]
            if not kzt_close(val, true):
                errs.append(f"«{mt.group(0)}» ≠ {metric}={true}")
    return errs


def test_05_evidence_numbers_equal_metrics(nr):
    m = metrics()
    bad = []
    for g, r in nr.iterrows():
        ev, mm = r.evidence, m.loc[g]
        for mt in re.finditer(r"(\d+) плательщик\w*", ev):
            if int(mt.group(1)) != mm.in_deg:
                bad.append(f"{g}: «{mt.group(0)}» ≠ in_deg {mm.in_deg}")
        for mt in re.finditer(r"(\d+) получ(?:ател\w*|\.)", ev):
            if int(mt.group(1)) != mm.out_deg:
                bad.append(f"{g}: «{mt.group(0)}» ≠ out_deg {mm.out_deg}")
        mt = re.search(r"связи (\d+)(?:→| вх\./)(\d+)", ev)
        if mt and (int(mt.group(1)), int(mt.group(2))) != (mm.in_deg, mm.out_deg):
            bad.append(f"{g}: «{mt.group(0)}» ≠ {mm.in_deg}/{mm.out_deg}")
        bad += [f"{g}: {e}" for e in _evidence_sums(ev, r.role, mm)]
    assert not bad, f"{len(bad)} расхождений: {bad[:5]}"


def test_06_evidence_words_match_role(nr):
    m = metrics()
    want = {"coordinator": r"координатор", "transit": r"транзит", "distributor": r"веерной раздачи",
            "consolidator": r"консолидац|признаки сбора", "terminal": r"оседают"}
    bad = [f"{g} {r.role}: {r.evidence[:60]}" for g, r in nr.iterrows()
           if r.role in want and not re.search(want[r.role], r.evidence.lower())]
    trunc = nr.loc[m.index[m.truncated]]
    bad += [f"{g} граница: {e[:60]}" for g, e in trunc.evidence.items() if "граница выборки" not in e.lower()]
    assert not bad, bad[:5]


# ── Приоритет ─────────────────────────────────────────────────────────────────

def test_07_top30_no_peripheral_top4_not_seed(nr):
    top = pd.read_csv(OUT / "top_nodes.csv")
    assert len(top) == 30
    assert not (top.role == "peripheral").any()
    assert not nr.loc[top.gid[:4], "is_seed"].any()


def test_08_priority_medians(nr):
    """Среди не-курьеров: у курьеров множитель 0,5 (§10), 4 из 8 координаторов — курьеры."""
    med = nr[~nr.is_seed].groupby("role").priority_score.median()
    assert med["coordinator"] > med["transit"] > med["peripheral"], med.to_dict()


def test_09_rank_exactly_30(web):
    ranks = [n["rank"] for n in web["graph"]["nodes"] if n["rank"] is not None]
    assert sorted(ranks) == list(range(1, 31))


# ── Кластеры ──────────────────────────────────────────────────────────────────

def test_10_11_clusters(nr):
    E, N, _ = raw()
    cl = pd.read_csv(OUT / "clusters.csv").set_index("cluster_id")
    cid = nr.cluster_id
    e = E.assign(cs=E.src.map(cid), cd=E.dst.map(cid))
    internal = e[e.cs == e.cd].groupby("cs").sum_kzt.sum()
    seeds = set(N.gid[N.is_seed])
    bad = []
    for c, row in cl.iterrows():
        members = set(cid.index[cid == c])
        if abs(row.sum_kzt_internal - internal.get(c, 0.0)) > 0.05:
            bad.append(f"кластер {c}: sum_kzt_internal {row.sum_kzt_internal} ≠ {internal.get(c, 0.0)}")
        if row.n_nodes != len(members) or row.n_seed != len(members & seeds):
            bad.append(f"кластер {c}: n_nodes/n_seed {row.n_nodes}/{row.n_seed} ≠ {len(members)}/{len(members & seeds)}")
        tops = [int(x) for x in str(row.top_gids).split("|") if x]
        if not set(tops) <= members:
            bad.append(f"кластер {c}: top_gids вне кластера")
        if "координатор" in row.hypothesis and not (nr.loc[list(members), "role"] == "coordinator").any():
            bad.append(f"кластер {c}: в гипотезе координатор, а в кластере его нет")
        for g in map(int, re.findall(r"\d{18}", row.hypothesis)):
            if g not in members:
                bad.append(f"кластер {c}: узел {g} из гипотезы не в кластере")
    assert not bad, bad[:5]


# ── Блокировка ────────────────────────────────────────────────────────────────

def test_12_baseline_is_total(web):
    tot, _ = reach()
    assert abs(tot - TOTAL_KZT) < 0.05
    assert abs(web["resilience"]["baseline"]["reach_kzt"] - TOTAL_KZT) < 0.05


def test_13_cut_at_least_own_edges(nr, web):
    """cut_kzt ≥ in_kzt + out_kzt для не-seed узлов, до которых доходят деньги курьеров."""
    m = metrics()
    bad = []
    for gid, card in web["cards"].items():
        g = int(gid)
        if card["is_seed"] or card["metrics"]["seed_reach"] == 0 or m.at[g, "out_deg"] == 0:
            continue
        cut = card["metrics"]["cut_kzt"] or 0.0
        if cut + 0.05 < m.at[g, "in_kzt"] + m.at[g, "out_kzt"]:
            bad.append(f"{gid}: cut {cut} < in+out {m.at[g, 'in_kzt'] + m.at[g, 'out_kzt']}")
    assert not bad, f"{len(bad)} узлов: {bad[:5]}"


def test_14_monotonic_drop(web):
    for name, by_n in web["resilience"]["strategies"].items():
        d = [by_n[n]["drop_reach_pct"] for n in ("5", "10", "20")]
        assert d[0] <= d[1] <= d[2], f"{name}: {d}"


def test_15_plan_marginals_sum(web):
    plan = web["top_block"]
    removed = {int(p["id"]) for p in plan}
    after, _ = reach(removed)
    total_cut = sum(p["cut_kzt_marginal"] for p in plan)
    assert abs(total_cut - (TOTAL_KZT - after)) < 1.0, (total_cut, TOTAL_KZT - after)
    assert abs(plan[-1]["reach_kzt_after"] - after) < 1.0


def test_16_plan_beats_turnover_beats_random(web):
    s = web["resilience"]["strategies"]
    assert s["plan"]["10"]["drop_reach_pct"] >= s["turnover"]["10"]["drop_reach_pct"] >= s["random"]["10"]["drop_reach_pct"]


def test_17_api_block_equals_resilience(client, web):
    for n in ("5", "10", "20"):
        ref = web["resilience"]["strategies"]["plan"][n]
        r = client.post("/api/block", json={"ids": ref["removed"]}).json()
        assert abs(r["after"]["reach_kzt"] - ref["reach_kzt"]) < 0.05
        assert r["drop_reach_pct"] == ref["drop_reach_pct"] and r["drop_deep_pct"] == ref["drop_deep_pct"]
        tot, deep = reach({int(x) for x in ref["removed"]})       # и независимый пересчёт
        assert abs(tot - ref["reach_kzt"]) < 0.05 and abs(deep - ref["deep_kzt"]) < 0.05


# ── Прочее ────────────────────────────────────────────────────────────────────

def test_18_sankey_conserves_money(web):
    sk = web["sankey"]
    total = sum(l["value"] for l in sk["links"]) + sk["skipped_kzt"]
    assert abs(total - TOTAL_KZT) <= len(sk["links"]) + 1, total   # значения округлены до ₸


def test_19_next_requests(nr):
    m = metrics()
    req = pd.read_csv(OUT / "next_requests.csv")
    out, inc = req[req.request_type == "outgoing"], req[req.request_type == "incoming"]
    assert m.loc[out.gid, "truncated"].all(), "в «исходящих» не только граница выборки"
    inflow = ~m.is_seed & (m.pass_through > config.INFLOW_OUTSIDE_PASS)
    assert inflow.loc[inc.gid].all(), "во «входящих» не только приток вне выборки"
    assert out.in_kzt.is_monotonic_decreasing, "исходящие не по убыванию входа"
    # входящие — по убыванию отданного (CLAUDE.md §12: «крупным out_kzt»)
    assert m.loc[inc.gid, "out_kzt"].is_monotonic_decreasing, "входящие не по убыванию отданного"


def test_20_hold_median_direct(nr):
    rnd = random.Random(config.SEED)
    transit = sorted(nr.index[nr.role == "transit"])
    for g in rnd.sample(transit, 20):
        assert abs(hold_median(int(g)) - nr.at[g, "hold_median_days"]) < 1e-9, g


def test_21_api_node_ego_search(client, nr):
    m = metrics()
    rnd = random.Random(config.SEED)
    sample = rnd.sample(sorted(nr.index[(nr.in_deg + nr.out_deg) > 0]), 40)
    G = graph()
    bad = []
    for g in sample:
        card = client.get(f"/api/node/{g}").json()
        row = nr.loc[g]
        for k in ("in_deg", "out_deg", "seed_reach", "sync_max_payers"):
            if card["metrics"][k] != row[k]:
                bad.append(f"{g}: {k} API {card['metrics'][k]} ≠ CSV {row[k]}")
        for k in ("in_kzt", "out_kzt"):
            if abs(card["metrics"][k] - row[k]) > 0.01:
                bad.append(f"{g}: {k}")
        if card["role"] != row.role or abs(card["role_score"] - row.role_score) > 0.01:
            bad.append(f"{g}: role/role_score")
        ego = client.get(f"/api/node/{g}/ego?hops=1").json()
        # соседи = уникальные плательщики ∪ получатели: встречная пара u→v, v→u — один сосед
        want = set(G.predecessors(g)) | set(G.successors(g))
        if set(map(int, ego["nodes"])) - {g} != want:
            bad.append(f"{g}: ego {len(ego['nodes']) - 1} соседей ≠ {len(want)} уникальных")
        hits = [x["id"] for x in client.get(f"/api/search?q={str(g)[-6:]}&limit=100").json()]
        if str(g) not in hits:
            bad.append(f"{g}: не найден поиском по последним 6 цифрам")
    assert not bad, f"{len(bad)}: {bad[:6]}"
