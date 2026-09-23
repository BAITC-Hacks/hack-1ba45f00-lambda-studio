"""Форма ответов API = docs/mock (рекурсивно: ключи, вложенность, типы); все id — строки."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.shape import diff_shape

MOCK = Path(__file__).resolve().parent.parent / "docs" / "mock"


def mock(name: str):
    return json.loads((MOCK / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def client():
    from mycelium.serve import create_app
    return TestClient(create_app())


@pytest.fixture(scope="module")
def ids(client):
    """gid берём из ответа API, а не из кода (правило: не хардкодить gid)."""
    top = client.get("/api/top?kind=check").json()
    nodes = client.get("/api/graph").json()["nodes"]
    return {"top": top[0]["id"], "second": top[1]["id"],
            "seed": next(n["id"] for n in nodes if n["is_seed"]),
            "truncated": next(n["id"] for n in nodes if n["truncated"])}


def _ids_are_strings(obj, path="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("id", "center", "source", "target") and not isinstance(v, str):
                raise AssertionError(f"{path}.{k} не строка: {v!r}")
            if k in ("removed", "upstream_seeds", "top_ids", "nodes", "gids") and isinstance(v, list):
                for x in v:
                    if not isinstance(x, (str, dict)):
                        raise AssertionError(f"{path}.{k} содержит не строку: {x!r}")
            _ids_are_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:300]):
            _ids_are_strings(v, f"{path}[{i}]")


GETS = {
    "health": "/api/health", "meta": "/api/meta", "graph": "/api/graph",
    "top_check": "/api/top?kind=check", "top_block": "/api/top?kind=block", "clusters": "/api/clusters",
    "sankey": "/api/sankey", "resilience": "/api/resilience", "next_requests": "/api/next-requests",
}


@pytest.mark.parametrize("name", list(GETS))
def test_get_shape(client, name):
    r = client.get(GETS[name])
    assert r.status_code == 200
    errs = diff_shape(mock(name), r.json())
    assert not errs, "\n".join(errs[:10])
    _ids_are_strings(r.json())


@pytest.mark.parametrize("which", ["top", "seed", "truncated"])
def test_node_shape(client, ids, which):
    r = client.get(f"/api/node/{ids[which]}")
    assert r.status_code == 200
    body = r.json()
    assert not diff_shape(mock("node"), body)
    _ids_are_strings(body)
    matched = [x["role"] for x in body["rule_trace"] if x["matched"]]
    assert matched == [body["role"]], "rule_trace должен объяснять именно назначенную роль"
    assert body["stability_text"].startswith("роль устойчива в ")


def test_ego_and_search(client, ids):
    for hops in (1, 2):
        r = client.get(f"/api/node/{ids['top']}/ego?hops={hops}")
        assert r.status_code == 200 and not diff_shape(mock("ego"), r.json())
        assert r.json()["center"] == ids["top"]
    r = client.get(f"/api/search?q={ids['top'][-6:]}&limit=5")
    assert r.status_code == 200 and not diff_shape(mock("search"), r.json())
    assert ids["top"] in [x["id"] for x in r.json()]


def test_block(client, ids):
    r = client.post("/api/block", json={"ids": [ids["top"], ids["second"]]})
    assert r.status_code == 200
    assert not diff_shape(mock("block_response"), r.json())
    assert r.json()["removed"] == [ids["top"], ids["second"]]
    assert r.json()["after"]["reach_kzt"] <= r.json()["baseline"]["reach_kzt"]


def test_errors(client, ids):
    r = client.post("/api/block", json={"ids": [ids["seed"]]})
    assert r.status_code == 400 and r.json()["error"] == "seed_blocked"
    r = client.get("/api/node/1")
    assert r.status_code == 404 and set(r.json()) == {"error", "message"}
    assert client.get("/api/node/abc").status_code == 400
    assert client.get("/api/top?kind=nope").status_code == 400
    assert client.get("/docs").status_code == 200
