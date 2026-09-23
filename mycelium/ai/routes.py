"""Роуты ИИ-аналитика: GET /api/brief и POST /api/ask (подключаются в serve.py через include_router)."""
from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mycelium import config
from mycelium.ai import analyst
from mycelium.ai.brief import BRIEF_CHECK, BRIEF_MD

router = APIRouter()
MAX_QUESTION_LEN = 1000


class AskRequest(BaseModel):
    question: str


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


@router.get("/api/brief")
def brief():
    md, chk = config.OUT_DIR / BRIEF_MD, config.OUT_DIR / BRIEF_CHECK
    if not md.exists():
        return _error(404, "no_brief", "Справки нет: выполните python -m mycelium.ai.brief")
    meta = json.loads(chk.read_text(encoding="utf-8")) if chk.exists() else {}
    return {"markdown": md.read_text(encoding="utf-8"), "verified": bool(meta.get("verified", False)),
            "issues": meta.get("issues", ["справка не сверялась"]), "cached": True,
            "generated_at": meta.get("generated_at", "")}


@router.post("/api/ask")
def ask(body: AskRequest):
    q = body.question.strip()
    if not q:
        return _error(400, "empty_question", "Задайте вопрос")
    if len(q) > MAX_QUESTION_LEN:
        return _error(400, "too_long", f"Вопрос длиннее {MAX_QUESTION_LEN} символов")
    fixed = analyst.guilt_answer(q)            # вопросы о виновности — без модели, работают и без ключа
    if fixed is not None:
        return fixed
    if not analyst.enabled():
        return _error(503, "ai_disabled", "ИИ-аналитик выключен: нет OPENAI_API_KEY и OPENAI_MODEL. "
                                          "Сохранённая справка — GET /api/brief")
    try:
        return analyst.ask(q)
    except analyst.AiDisabled as e:
        return _error(503, "ai_disabled", str(e))
    except Exception as e:  # noqa: BLE001 — сеть, квота, неверная модель
        return _error(503, "ai_error", f"ИИ временно недоступен: {type(e).__name__}")
