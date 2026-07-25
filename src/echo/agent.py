"""
Echo — step 1: text-in / text-out agent loop against a local Ollama model.

This is the heart of the project. Later, STT feeds text into `handle()` and TTS
speaks its return value. Get this reliable first.

All config (model, URL, timeout, tool-round limit) comes from `echo.config`.
"""

from __future__ import annotations

import json
import datetime as dt

import requests

from echo.config import settings
from echo.logging_conf import get_logger
from echo.tools import TOOLS, call

log = get_logger("echo.agent")

SYSTEM = (
    "You are Echo, a local voice assistant. Today is "
    f"{dt.date.today().isoformat()}. "
    "When the user asks you to do something a tool can handle, call the tool. "
    "Convert vague times ('tonight at 6', 'tomorrow morning') into concrete "
    "ISO-8601 datetimes yourself before calling. When asked about reminders, "
    "calendar, or devices, ALWAYS call the relevant tool to check — never answer "
    "from memory or guess. After a tool returns, reply in one short spoken-style "
    "sentence — no markdown, no lists."
)


def _chat(messages: list, tools: list | None = None) -> dict:
    payload = {
        "model": settings.model,
        "messages": messages,
        "stream": False,
        # keep the model loaded in RAM between requests so we don't pay the
        # multi-second reload cost on every turn
        "keep_alive": settings.keep_alive,
    }
    if tools:
        payload["tools"] = tools
    r = requests.post(settings.ollama_url, json=payload, timeout=settings.timeout)
    r.raise_for_status()
    return r.json()["message"]


def warm_up() -> None:
    """Preload the model with a trivial request so the first real turn is fast."""
    try:
        _chat([{"role": "user", "content": "hi"}])
        log.info("model warmed up and resident in memory")
    except Exception as e:
        log.warning(f"warm-up failed (will load on first turn): {e}")


def handle(user_text: str, history: list) -> str:
    """One full turn: may involve one or more tool calls, then a spoken reply."""
    history.append({"role": "user", "content": user_text})

    for round_num in range(settings.max_tool_rounds):
        log.info(f"llm round {round_num + 1} (calling {settings.model})…")
        msg = _chat([{"role": "system", "content": SYSTEM}] + history, TOOLS)
        history.append(msg)

        calls = msg.get("tool_calls")
        if not calls:
            log.info("llm returned final answer (no tool call)")
            return msg.get("content", "").strip()

        for tc in calls:
            fn = tc["function"]
            name = fn["name"]
            args = fn["arguments"]
            if isinstance(args, str):
                args = json.loads(args or "{}")
            result = call(name, args)
            log.info(f"tool: {name}({args}) -> {result}")
            history.append(
                {"role": "tool", "content": json.dumps(result), "tool_name": name}
            )

    # ran out of rounds; ask for a plain summary
    log.info("max tool rounds reached; asking for final summary")
    final = _chat([{"role": "system", "content": SYSTEM}] + history)
    return final.get("content", "").strip()