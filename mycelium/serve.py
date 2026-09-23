"""API для экрана (CLAUDE.md §13, docs/FRONTEND.md §4) + раздача собранного фронта из web/dist.

    python -m mycelium.serve [--port 8000] [--host 127.0.0.1]

Все JSON из out/web грузятся один раз при старте и отдаются без преобразований. Граф для ego и
блокировки строится из data/*.parquet. gid на входе — строки, внутри int, на выходе — строки.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import networkx as nx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mycelium import config, resilience
from mycelium.load import load_dataset

WEB_DIST = config.ROOT / "web" / "dist"
MAX_BLOCK_IDS = 50
WEB_FILES = ["graph", "meta", "cards", "top_check", "top_block", "clusters", "sankey", "resilience",
             "next_requests"]


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str):
        self.status, self.error, self.message = status, error, message


class BlockRequest(BaseModel):
    ids: List[str]


class State:
    """Всё, что сервер держит в памяти."""

    def __init__(self, out_dir: Path, data_dir: Path):
        web = out_dir / "web"
        missing = [f"{n}.json" for n in WEB_FILES if not (web / f"{n}.json").exists()]
        if missing:
            raise SystemExit(f"Нет выходов пайплайна в {web}: {', '.join(missing)}. "
                             f"Сначала запустите: python -m mycelium.pipeline")
        self.web = {n: json.loads((web / f"{n}.json").read_text(encoding="utf-8")) for n in WEB_FILES}
        self.generated_at = datetime.fromtimestamp((web / "graph.json").stat().st_mtime).isoformat(timespec="seconds")
        ds, _ = load_dataset(data_dir)
        self.G: nx.DiGraph = ds.graph
        self.df = ds.nodes.set_index("gid")[["is_seed"]].copy()
        self.priority = {n["id"]: n["priority"] or 0.0 for n in self.web["graph"]["nodes"]}
        self.role = {n["id"]: n["role"] for n in self.web["graph"]["nodes"]}
        self.ids_sorted = sorted(self.priority, key=lambda i: (-self.priority[i], i))
        self.out_dir = out_dir


def parse_id(raw: str, st: State) -> int:
    s = str(raw).strip()
    if not s.isdigit():
        raise ApiError(400, "bad_id", f"id должен состоять из цифр: «{raw}»")
    g = int(s)
    if g not in st.G:
        raise ApiError(404, "not_found", f"Узел {s} не найден в графе")
    return g


def create_app(out_dir: Path = config.OUT_DIR, data_dir: Path = config.DATA_DIR) -> FastAPI:
    st = State(Path(out_dir), Path(data_dir))
    app = FastAPI(title="Грибница API", description="AML-анализ графа переводов: роли, кластеры, приоритеты",
                  version="1.0")
    app.state.st = st
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return JSONResponse(status_code=e.status, content={"error": e.error, "message": e.message})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, e: RequestValidationError):
        return JSONResponse(status_code=400, content={"error": "bad_request",
                                                      "message": f"Некорректный запрос: {e.errors()[0].get('msg', '')}"})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "ai_enabled": ai_enabled(), "generated_at": st.generated_at}

    @app.get("/api/meta")
    def meta():
        return st.web["meta"]

    @app.get("/api/graph")
    def graph():
        return st.web["graph"]

    @app.get("/api/node/{node_id}")
    def node(node_id: str):
        g = parse_id(node_id, st)
        return st.web["cards"][str(g)]

    @app.get("/api/node/{node_id}/ego")
    def ego(node_id: str, hops: int = Query(1, ge=1, le=2)):
        g = parse_id(node_id, st)
        UG = st.G.to_undirected(as_view=True)
        near = nx.single_source_shortest_path_length(UG, g, cutoff=hops)
        nodes = [g] + sorted(v for v in near if v != g)
        sub = st.G.subgraph(nodes)
        edges = sorted(sub.edges(data=True), key=lambda e: (-e[2]["sum_kzt"], e[0], e[1]))
        return {"center": str(g), "nodes": [str(v) for v in nodes],
                "edges": [{"source": str(u), "target": str(v), "sum_kzt": round(d["sum_kzt"], 2),
                           "n_tx": int(d["n_tx"])} for u, v, d in edges]}

    @app.get("/api/search")
    def search(q: str = "", limit: int = Query(10, ge=1, le=100)):
        q = q.strip()
        if not q:
            return []
        hits = [i for i in st.ids_sorted if q in i][:limit]
        return [{"id": i, "role": st.role[i], "priority": st.priority[i]} for i in hits]

    @app.get("/api/top")
    def top(kind: str = "check", limit: Optional[int] = Query(None, ge=1, le=100)):
        if kind not in ("check", "block"):
            raise ApiError(400, "bad_kind", "kind должен быть check (кого проверять) или block (план блокировки)")
        rows = st.web["top_check" if kind == "check" else "top_block"]
        return rows[:limit] if limit else rows

    @app.get("/api/clusters")
    def clusters():
        return st.web["clusters"]

    @app.get("/api/sankey")
    def sankey():
        return st.web["sankey"]

    @app.get("/api/resilience")
    def resilience_view():
        return st.web["resilience"]

    @app.get("/api/next-requests")
    def next_requests():
        return st.web["next_requests"]

    @app.post("/api/block")
    def block(body: BlockRequest):
        if len(body.ids) > MAX_BLOCK_IDS:
            raise ApiError(400, "too_many", f"Можно заблокировать не больше {MAX_BLOCK_IDS} узлов за раз")
        gids = list(dict.fromkeys(parse_id(i, st) for i in body.ids))
        seeds = [str(g) for g in gids if bool(st.df.at[g, "is_seed"])]
        if seeds:
            raise ApiError(400, "seed_blocked",
                           f"Курьеры уже известны, их блокировка обнуляет граф по построению: {', '.join(seeds)}")
        r = resilience.evaluate(st.G, st.df, gids)
        r["removed"] = [str(g) for g in r["removed"]]
        return r

    _include_ai(app)
    _mount_frontend(app)
    return app


def ai_enabled() -> bool:
    from mycelium.ai.analyst import enabled
    return enabled()


def _include_ai(app: FastAPI) -> None:
    """Роуты /api/brief и /api/ask живут в mycelium/ai/routes.py."""
    try:
        from mycelium.ai.routes import router
    except ModuleNotFoundError as e:
        if not (e.name or "").startswith("mycelium.ai"):
            raise
        return
    app.include_router(router)


def _mount_frontend(app: FastAPI) -> None:
    """Собранный фронт — после всех /api роутов, чтобы не перехватывать их."""
    if (WEB_DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
        return

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def placeholder():
        return ("<!doctype html><meta charset='utf-8'><title>Грибница</title>"
                "<body style='font-family:sans-serif;max-width:640px;margin:60px auto'>"
                "<h1>Грибница</h1><p>Фронт не собран (нет <code>web/dist/index.html</code>), "
                "API работает: <a href='/docs'>/docs</a>.</p></body>")


def main(argv: Optional[list] = None) -> int:
    import uvicorn
    ap = argparse.ArgumentParser(description="Грибница: API и экран")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--out", type=Path, default=config.OUT_DIR)
    ap.add_argument("--data", type=Path, default=config.DATA_DIR)
    a = ap.parse_args(argv)
    app = create_app(a.out, a.data)
    print(f"Грибница: http://localhost:{a.port}  (API: /docs)")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
