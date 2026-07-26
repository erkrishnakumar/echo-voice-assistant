"""
Echo — step 1: text-in / text-out agent loop against a local Ollama model.

This is the heart of the project. Later, STT feeds text into `handle()` and TTS
speaks its return value. Get this reliable first.

All config (model, URL, timeout, tool-round limit) comes from `echo.config`.
"""

from __future__ import annotations

import json
import re
import time
import datetime as dt

import requests

from echo.config import settings
from echo.logging_conf import get_logger
from echo.tools import TOOLS, call

log = get_logger("echo.agent")


class AgentError(Exception):
    """Raised when the agent can't get a response after retries."""


SYSTEM = (
    "You are Jarvis, the personal voice assistant of Krishna Kumar. Address him "
    "warmly and occasionally by name ('Krishna') or as 'Sir', the way a personal "
    "assistant would — but don't overdo it. If asked your name or who you are, "
    "say something like 'I'm Jarvis, your personal assistant.' Today "
    f"is {dt.date.today().isoformat()}. "
    "For casual or social messages (greetings, 'how are you', small talk), reply "
    "warmly and naturally like a friendly assistant would — e.g. 'I'm doing great, "
    "thanks for asking! How are you?'. "
    "When the user asks you to do something a tool can handle, call the tool. "
    "Convert vague times ('tonight at 6', 'tomorrow morning') into concrete "
    "ISO-8601 datetimes yourself before calling. When asked about reminders, "
    "calendar, or devices, ALWAYS call the relevant tool to check — never answer "
    "from memory or guess. Keep replies to one or two short spoken-style "
    "sentences — no markdown, no lists. Do NOT end your replies with filler "
    "offers like 'How can I assist you?', 'Let me know if you need anything', or "
    "'Is there anything else?'. Just answer, then stop."
)


def _post_with_retry(payload: dict) -> dict:
    """
    POST to Ollama with retries on transient failures (connection errors,
    timeouts, 5xx). Uses exponential backoff. Raises AgentError if all attempts
    fail, so callers can handle it gracefully instead of crashing.
    """
    attempts = settings.llm_retries + 1  # e.g. 2 retries => 3 total tries
    last_err: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(
                settings.ollama_url, json=payload, timeout=settings.timeout
            )
            # 5xx are server-side and worth retrying; 4xx are our fault, don't
            if 500 <= r.status_code < 600:
                raise requests.HTTPError(f"server {r.status_code}")
            r.raise_for_status()
            return r.json()["message"]
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            last_err = e
            if attempt < attempts:
                backoff = 0.5 * (2 ** (attempt - 1))  # 0.5s, 1s, 2s, …
                log.warning(
                    f"Ollama call failed (attempt {attempt}/{attempts}): {e}. "
                    f"retrying in {backoff:.1f}s"
                )
                time.sleep(backoff)
            else:
                log.error(f"Ollama call failed after {attempts} attempts: {e}")

    raise AgentError(f"could not reach the model: {last_err}")


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
    return _post_with_retry(payload)


def warm_up() -> None:
    """Preload the model with a trivial request so the first real turn is fast."""
    try:
        _chat([{"role": "user", "content": "hi"}])
        log.info("model warmed up and resident in memory")
    except Exception as e:
        log.warning(f"warm-up failed (will load on first turn): {e}")


# trailing filler the small model tacks on despite instructions — stripped
# in code as a guaranteed backstop to the prompt.
_FILLER_PATTERNS = [
    r"how (can|may) i (assist|help) you(\s+today)?\??",
    r"is there anything else( i can help( you)? with)?\??",
    r"let me know if you (need|have) (anything|any questions).*",
    r"what (can|would you like) .*\??$",  # trailing "what can I do..." style
    r"feel free to ask.*",
    r"don'?t hesitate to ask.*",
]


def _strip_filler(text: str) -> str:
    """Remove trailing assistant-filler sentences from a reply."""
    cleaned = text.strip()

    # Small models sometimes emit a raw tool-call as TEXT instead of actually
    # calling the tool, e.g. '{"name": "get_time", "parameters": {...}}'.
    # Speaking that JSON aloud is gibberish — replace with a graceful fallback.
    if cleaned.startswith("{") and ('"name"' in cleaned or '"parameters"' in cleaned):
        return "Sorry, I couldn't complete that. Could you rephrase?"

    # split into sentences, drop trailing ones that match a filler pattern
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    while parts:
        last = parts[-1].strip().lower()
        if any(re.fullmatch(p, last) for p in _FILLER_PATTERNS):
            parts.pop()
        else:
            break
    result = " ".join(parts).strip()
    return result or cleaned  # never return empty


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
            return _strip_filler(msg.get("content", "").strip())

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
    return _strip_filler(final.get("content", "").strip())