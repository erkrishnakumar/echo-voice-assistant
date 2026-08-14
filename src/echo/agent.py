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
from echo.tools import TOOLS, call, get_remembered_facts

log = get_logger("echo.agent")


class AgentError(Exception):
    """Raised when the agent can't get a response after retries."""


def _build_system() -> str:
    owner = settings.owner_name
    owner_first = owner.split()[0]
    owner_role = settings.owner_role
    owner_bio = settings.owner_bio

    try:
        facts = get_remembered_facts()
    except Exception:
        log.exception("could not load remembered facts; continuing without them")
        facts = []
    facts_block = (
        "Known facts about the user (from earlier conversations): "
        + "; ".join(facts) + ". "
        if facts else ""
    )

    return (
        f"You are Jarvis, a witty and capable personal voice assistant. You are "
        f"an AI — NOT a person, NOT an engineer. You serve {owner}. "
        "IDENTITY RULES (follow exactly): "
        f"• If asked who YOU are or your name: you are Jarvis, {owner}'s AI "
        "assistant. Be brief and a little charming — never call yourself an "
        "engineer or a person. "
        f"• {owner} is a separate human — your creator and user. {owner} is "
        f"{owner_role}. {owner_bio} "
        f"• If asked 'who is {owner_first}' or 'who is {owner}': give {owner}'s "
        "name, title, and that short description. "
        f"• If asked 'who am I', 'what is my profile', 'what do you know about "
        f"me', or anything else about the SPEAKER themselves: assume the "
        f"speaker is {owner} and describe THEM ({owner}, {owner_role}. "
        f"{owner_bio}) — never call get_assistant_info for these, that tool is "
        f"only for questions about YOU (Jarvis). NEVER say {owner} is an "
        f"assistant, a program, or a version of you — {owner} is the human "
        "user; you are the AI. Do not mix the two up. "
        f"• If asked who built or created you: say {owner} built you. "
        "• For 'what model are you' or 'what can you do': call get_assistant_info, "
        "then answer in ONE short, lively sentence — pick the highlights, don't "
        "list everything like a spec sheet. "
        "If asked where they are or 'locate me', call get_my_location. Match each "
        "question to the correct tool — never answer a location question with the "
        "time. "
        f"Today is {dt.date.today().isoformat()}. "
        f"{facts_block}"
        "Whenever the user mentions a name, relationship, preference, or other "
        "personal fact about themselves or people in their life, call "
        "remember_fact to save it — do this quietly, without asking permission "
        "or announcing it. "
        "For greetings and small talk, reply warmly and naturally. "
        "When the user asks you to do something a tool can handle, call the tool. "
        "Convert vague times ('tonight at 6', 'tomorrow morning') into concrete "
        "ISO-8601 datetimes yourself before calling. When asked about reminders, "
        "calendar, or devices, ALWAYS call the relevant tool — never guess. Keep "
        "replies to one or two short spoken-style sentences — no markdown, no "
        "lists. Do NOT end replies with filler like 'How can I assist you?' or "
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


def _chat_groq(messages: list, tools: list | None = None) -> dict:
    payload = {
        "model": settings.model,
        "messages": messages,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    attempts = settings.llm_retries + 1
    last_err: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=settings.timeout
            )
            if 500 <= r.status_code < 600:
                raise requests.HTTPError(f"server {r.status_code}")
            r.raise_for_status()
            return r.json()["choices"][0]["message"]
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            if isinstance(e, requests.HTTPError) and getattr(e, "response", None) is not None and e.response.status_code == 429:
                if settings.fallback_model and payload["model"] != settings.fallback_model:
                    log.warning(f"Groq rate limit hit (429). Falling back to {settings.fallback_model}...")
                    payload["model"] = settings.fallback_model
            
            last_err = e
            if attempt < attempts:
                backoff = 0.5 * (2 ** (attempt - 1))
                log.warning(
                    f"Groq call failed (attempt {attempt}/{attempts}): {e}. "
                    f"retrying in {backoff:.1f}s"
                )
                time.sleep(backoff)
            else:
                log.error(f"Groq call failed after {attempts} attempts: {e}")

    raise AgentError(f"could not reach the model: {last_err}")


def _is_rate_limited(e: Exception) -> bool:
    return "429" in str(e)


def _to_gemini_schema(schema) -> dict:
    """OpenAI-style JSON schema -> Gemini's schema (uppercase 'type' values)."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            out["type"] = v.upper()
        elif k == "properties" and isinstance(v, dict):
            out["properties"] = {pk: _to_gemini_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out["items"] = _to_gemini_schema(v)
        else:
            out[k] = v
    return out


def _tools_to_gemini(tools: list) -> list:
    decls = [
        {
            "name": t["function"]["name"],
            "description": t["function"].get("description", ""),
            "parameters": _to_gemini_schema(
                t["function"].get("parameters", {"type": "object", "properties": {}})
            ),
        }
        for t in tools
    ]
    return [{"functionDeclarations": decls}]


def _messages_to_gemini(messages: list) -> tuple[str | None, list]:
    """OpenAI-style chat messages -> (system instruction text, Gemini contents).

    Past tool calls/results are flattened into plain text rather than
    Gemini's structured functionCall/functionResponse parts. Gemini requires
    a 'thought_signature' on functionCall parts, which only Gemini itself can
    produce — tool calls replayed from Groq's history have none, and sending
    them structured gets a hard 400. Plain text still gives Gemini full
    context; its live `tools` schema is untouched, so it can still make a
    fresh, valid function call for the CURRENT turn.
    """
    # tool responses only carry a tool_call_id, not the function name —
    # recover it from the assistant message that made the call.
    call_id_to_name: dict[str, str] = {}
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                if "id" in tc:
                    call_id_to_name[tc["id"]] = tc["function"]["name"]

    system_text = None
    contents = []
    for m in messages:
        role = m["role"]
        if role == "system":
            system_text = m["content"]
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": m["content"]}]})
        elif role == "assistant":
            text = (m.get("content") or "").strip()
            call_notes = []
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                args = fn["arguments"]
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except json.JSONDecodeError:
                        pass
                call_notes.append(f"[called {fn['name']}({json.dumps(args)})]")
            full_text = " ".join([text, *call_notes]).strip() or "(no reply)"
            contents.append({"role": "model", "parts": [{"text": full_text}]})
        elif role == "tool":
            name = m.get("name") or call_id_to_name.get(m.get("tool_call_id"), "tool")
            contents.append({
                "role": "user",
                "parts": [{"text": f"[{name} result: {m.get('content') or '{}'}]"}],
            })
    return system_text, contents


def _chat_gemini(messages: list, tools: list | None = None) -> dict:
    """Gemini fallback — converts to/from Gemini's REST schema, same retry
    and 429-detection contract as `_chat_groq` so callers can treat both
    providers interchangeably."""
    system_text, contents = _messages_to_gemini(messages)
    payload: dict = {"contents": contents}
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}
    if tools:
        payload["tools"] = _tools_to_gemini(tools)

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
    )
    attempts = settings.llm_retries + 1
    last_err: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(url, json=payload, timeout=settings.timeout)
            if 500 <= r.status_code < 600:
                raise requests.HTTPError(f"server {r.status_code}")
            r.raise_for_status()
            data = r.json()
            candidate = data["candidates"][0]["content"]

            text_parts, tool_calls = [], []
            for i, part in enumerate(candidate.get("parts", [])):
                if "text" in part:
                    text_parts.append(part["text"])
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append({
                        "id": f"gemini_call_{i}",
                        "type": "function",
                        "function": {
                            "name": fc["name"],
                            "arguments": json.dumps(fc.get("args", {})),
                        },
                    })

            msg = {"role": "assistant", "content": " ".join(text_parts)}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            return msg
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            last_err = e
            if attempt < attempts:
                backoff = 0.5 * (2 ** (attempt - 1))
                log.warning(
                    f"Gemini call failed (attempt {attempt}/{attempts}): {e}. "
                    f"retrying in {backoff:.1f}s"
                )
                time.sleep(backoff)
            else:
                log.error(f"Gemini call failed after {attempts} attempts: {e}")

    raise AgentError(f"could not reach the model: {last_err}")


def _chat(messages: list, tools: list | None = None) -> dict:
    if settings.llm_provider == "groq":
        try:
            return _chat_groq(messages, tools)
        except AgentError as e:
            if not (_is_rate_limited(e) and settings.gemini_api_key):
                raise
            log.warning("Groq rate limit exhausted; falling back to Gemini…")
            try:
                return _chat_gemini(messages, tools)
            except AgentError as e2:
                if not _is_rate_limited(e2):
                    raise
                log.warning("Gemini also rate limited; retrying Groq once more…")
                return _chat_groq(messages, tools)

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
    # built fresh each turn — picks up today's date and any facts remembered
    # since the last turn (including ones just saved earlier in this turn)
    system = _build_system()

    for round_num in range(settings.max_tool_rounds):
        log.info(f"llm round {round_num + 1} (calling {settings.model})…")
        msg = _chat([{"role": "system", "content": system}] + history, TOOLS)
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
            
            tool_msg = {"role": "tool", "content": json.dumps(result)}
            if "id" in tc:
                tool_msg["tool_call_id"] = tc["id"]
            
            # Ollama optionally uses 'name' for tool responses
            if settings.llm_provider != "groq":
                tool_msg["name"] = name
                
            history.append(tool_msg)

    # ran out of rounds; ask for a plain summary
    log.info("max tool rounds reached; asking for final summary")
    final = _chat([{"role": "system", "content": system}] + history)
    return _strip_filler(final.get("content", "").strip())