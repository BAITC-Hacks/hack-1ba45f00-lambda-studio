"""ИИ-аналитик без сети: сверка ответов, инструменты, цикл вызовов на поддельном клиенте, роуты."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from mycelium.ai import analyst, tools
from mycelium.ai.verify import verify
from tests.test_api import mock
from tests.shape import diff_shape


@pytest.fixture(scope="module")
def ctx():
    return tools.get_context()


def test_verify_accepts_facts(ctx):
    g = ctx.top_check[0]["id"]
    r = tools.call(ctx, "get_node", {"gid": g})
    text = f"Признаки координатора у [gid:{g}]: {r['metrics']['in_deg']} плательщиков, рекомендуем проверить."
    assert verify(text, [r], set(ctx.G.nodes))["verified"]


def test_verify_rejects_invented(ctx):
    g, other = ctx.top_check[0]["id"], ctx.top_check[1]["id"]
    r = tools.call(ctx, "get_node", {"gid": g})
    res = verify(f"[gid:{g}] получил 9,9 млн ₸ от 777 плательщиков; [gid:{other}] — преступник", [r], set(ctx.G.nodes))
    assert not res["verified"]
    joined = " ".join(res["issues"])
    assert "9,9 млн" in joined and "777" in joined and other in joined and "преступн" in joined


@pytest.mark.parametrize("name,args", [
    ("network_summary", {}), ("top_nodes", {"role": "coordinator", "limit": 3}), ("blocking_plan", {"limit": 3}),
])
def test_tools_return_string_ids(ctx, name, args):
    out = tools.call(ctx, name, args)
    assert "error" not in out
    assert "NaN" not in json.dumps(out)


def test_tool_error_is_returned(ctx):
    assert "error" in tools.call(ctx, "get_node", {"gid": "123"})
    assert "error" in tools.call(ctx, "no_such_tool", {})


def test_ask_loop_with_fake_client(ctx, monkeypatch):
    g = ctx.top_check[0]["id"]

    class Completions:
        n = 0

        def create(self, **kw):
            Completions.n += 1
            if Completions.n == 1:
                tc = NS(id="c1", function=NS(name="get_node", arguments=json.dumps({"gid": g})))
                return NS(choices=[NS(message=NS(content="", tool_calls=[tc]))])
            res = json.loads(kw["messages"][-1]["content"])
            text = f"Признаки роли у [gid:{g}]: {res['metrics']['in_deg']} плательщиков, рекомендуем проверить."
            return NS(choices=[NS(message=NS(content=text, tool_calls=None))])

    monkeypatch.setattr(analyst, "client_and_model", lambda: (NS(chat=NS(completions=Completions())), "fake"))
    out = analyst.ask("Кто собирает деньги?", ctx)
    assert not diff_shape(mock("ask_response"), out)
    assert out["verified"] and out["gids"] == [g] and out["tool_calls"][0]["name"] == "get_node"


def test_routes_brief_and_ask_disabled(monkeypatch):
    from mycelium.serve import create_app
    monkeypatch.setattr(analyst, "enabled", lambda: False)
    c = TestClient(create_app())
    r = c.get("/api/brief")
    assert r.status_code == 200 and not diff_shape(mock("brief"), r.json())
    r = c.post("/api/ask", json={"question": "кто главный?"})
    assert r.status_code == 503 and r.json()["error"] == "ai_disabled"
    assert c.get("/api/health").json()["ai_enabled"] is False
