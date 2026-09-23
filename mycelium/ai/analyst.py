"""Цикл вызовов инструментов OpenAI: вопрос аналитика → инструменты → ответ → сверка с графом.

Температура 0.2, максимум 6 шагов инструментов, таймаут и до 2 повторов при сетевой ошибке.
Без OPENAI_API_KEY и OPENAI_MODEL ИИ выключен (AiDisabled), остальное работает.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from mycelium import config
from mycelium.ai import prompts, tools
from mycelium.ai.verify import verify

MAX_TOOL_STEPS = 6
TEMPERATURE = 0.2
MAX_RETRIES = 2
PREVIEW_LEN = 160
DEFAULT_BASE_URL = "https://api.openai.com/v1"


class AiDisabled(RuntimeError):
    """Нет ключа или модели OpenAI."""


def settings() -> dict:
    try:
        from dotenv import load_dotenv
        load_dotenv(config.ROOT / ".env")
    except ImportError:
        pass
    return {"api_key": os.getenv("OPENAI_API_KEY", "").strip(), "model": os.getenv("OPENAI_MODEL", "").strip(),
            "base_url": os.getenv("OPENAI_BASE_URL", "").strip() or None,
            "timeout": float(os.getenv("OPENAI_TIMEOUT", "30") or 30)}


def enabled() -> bool:
    s = settings()
    return bool(s["api_key"] and s["model"])


def client_and_model():
    s = settings()
    if not (s["api_key"] and s["model"]):
        raise AiDisabled("ИИ выключен: задайте OPENAI_API_KEY и OPENAI_MODEL в .env")
    from openai import OpenAI
    # Пустой OPENAI_BASE_URL= из .env попадает в окружение, и SDK при base_url=None берёт его оттуда —
    # получается пустой адрес (UnsupportedProtocol → «Connection error»). Поэтому адрес всегда явный.
    if not os.environ.get("OPENAI_BASE_URL", "").strip():
        os.environ.pop("OPENAI_BASE_URL", None)
    return OpenAI(api_key=s["api_key"], base_url=s["base_url"] or DEFAULT_BASE_URL, timeout=s["timeout"],
                  max_retries=MAX_RETRIES), s["model"]


def complete(client, model: str, messages: list, tool_specs: Optional[list] = None):
    """Один вызов chat completions. Если модель не принимает temperature — повтор без неё."""
    kwargs = {"model": model, "messages": messages}
    if tool_specs:
        kwargs["tools"] = tool_specs
    try:
        return client.chat.completions.create(temperature=TEMPERATURE, **kwargs)
    except Exception as e:  # noqa: BLE001 — у разных моделей разные ограничения параметров
        if "temperature" in str(e).lower():
            return client.chat.completions.create(**kwargs)
        raise


def _preview(result: dict) -> str:
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) <= PREVIEW_LEN else text[: PREVIEW_LEN - 1] + "…"


def ask(question: str, ctx: Optional[tools.Context] = None) -> dict:
    """Форма ответа POST /api/ask: {answer, verified, issues, gids, tool_calls}."""
    question = (question or "").strip()
    if not question:
        raise ValueError("пустой вопрос")
    client, model = client_and_model()
    ctx = ctx or tools.get_context()
    messages = [{"role": "system", "content": prompts.SYSTEM}, {"role": "user", "content": question}]
    results: list = []
    calls: list = []
    answer = ""
    for step in range(MAX_TOOL_STEPS + 1):
        allow_tools = step < MAX_TOOL_STEPS
        resp = complete(client, model, messages, tools.TOOL_SPECS if allow_tools else None)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            answer = (msg.content or "").strip()
            break
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": tc.id, "type": "function",
                                         "function": {"name": tc.function.name,
                                                      "arguments": tc.function.arguments or "{}"}}
                                        for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            t0 = time.perf_counter()
            result = tools.call(ctx, tc.function.name, args)
            ms = int((time.perf_counter() - t0) * 1000)
            results.append(result)
            calls.append({"name": tc.function.name, "args": args, "ms": ms, "result_preview": _preview(result)})
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})
    if not answer:
        answer = "Не удалось получить ответ за отведённое число шагов. Переформулируйте вопрос."
    check = verify(answer, results, set(ctx.G.nodes))
    return {"answer": answer, "verified": check["verified"], "issues": check["issues"],
            "gids": check["gids"], "tool_calls": calls}
