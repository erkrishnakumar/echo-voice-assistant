"""
Echo local tools.

Each tool is a plain Python function plus a JSON schema describing it. This is
the exact shape you later expose over MCP — for now they're called in-process so
you can debug the LLM's tool selection without any transport layer in the way.

Storage now lives in PostgreSQL via SQLAlchemy. Smart-device control is still a
local state dict you can later wire to Home Assistant.
"""

from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import select

from echo.db import session_scope
from echo.models import Event, Memory, Reminder
from echo.weather import get_weather, get_rain_forecast, get_local_time
from echo.places import find_nearby_places, get_my_location

# in-memory device state; swap for a Home Assistant client later
_DEVICES = {"living room light": "off", "bedroom fan": "off", "kitchen light": "off"}

# when Jarvis was "born" — used to answer "how old are you"
_BIRTH_DATE = dt.datetime(2026, 7, 25, 17, 39)

# varied phrasing so the age answer doesn't feel scripted; {age} is filled in
_AGE_TEMPLATES = [
    "I've been up and running for {age}, Sir.",
    "I've existed for {age} now.",
    "It's been {age} since I first came online.",
    "{age} and counting, Sir — feels like no time at all.",
    "I came online {age} ago.",
]


def _format_age(delta: dt.timedelta) -> str:
    """Human age string using the largest fitting unit pair: years+months if
    >=365 days, months+weeks if >=30 days, weeks+days if >=7 days, else days."""
    days = delta.days

    def _plural(n: int, unit: str) -> str:
        return f"{n} {unit}{'' if n == 1 else 's'}"

    if days >= 365:
        years, rem = divmod(days, 365)
        months = rem // 30
        parts = [_plural(years, "year")]
        if months:
            parts.append(_plural(months, "month"))
    elif days >= 30:
        months, rem = divmod(days, 30)
        weeks = rem // 7
        parts = [_plural(months, "month")]
        if weeks:
            parts.append(_plural(weeks, "week"))
    elif days >= 7:
        weeks, rem = divmod(days, 7)
        parts = [_plural(weeks, "week")]
        if rem:
            parts.append(_plural(rem, "day"))
    else:
        parts = [_plural(days, "day")]

    return " and ".join(parts)


# ---- tool implementations -------------------------------------------------

def remember_fact(fact: str) -> dict:
    """Save a short personal fact about the user for recall in later
    conversations (e.g. names, relationships, preferences)."""
    fact = (fact or "").strip()
    if not fact:
        return {"error": "no fact provided"}
    with session_scope() as s:
        m = Memory(fact=fact)
        s.add(m)
        s.flush()
        result = m.as_dict()
    return {"ok": True, **result}


def get_remembered_facts() -> list[str]:
    """All remembered facts, oldest first. Not an LLM tool — used to inject
    known facts into the system prompt every turn."""
    with session_scope() as s:
        rows = s.execute(select(Memory).order_by(Memory.created)).scalars().all()
        return [r.fact for r in rows]


def set_reminder(text: str, due: str) -> dict:
    """Store a reminder. `due` is an ISO-8601 datetime string."""
    try:
        due_dt = dt.datetime.fromisoformat(due)
    except ValueError:
        return {"error": f"'{due}' is not a valid ISO datetime (use YYYY-MM-DDTHH:MM)"}
    with session_scope() as s:
        r = Reminder(text=text, due=due_dt)
        s.add(r)
        s.flush()  # populate r.id before the session closes
        result = r.as_dict()
    return {"ok": True, **result}


def get_calendar_events(date: str | None = None) -> dict:
    """Return events. If `date` (YYYY-MM-DD) is given, filter to that day."""
    with session_scope() as s:
        rows = s.execute(select(Event).order_by(Event.start)).scalars().all()
        events = [e.as_dict() for e in rows]
    if date:
        events = [e for e in events if e["start"].startswith(date)]
    return {"count": len(events), "events": events}


def control_smart_device(device: str, action: str) -> dict:
    """Turn a known device on or off. action is 'on' or 'off'."""
    device = device.lower().strip()
    if device not in _DEVICES:
        return {"error": f"unknown device '{device}'", "known": list(_DEVICES)}
    if action not in ("on", "off"):
        return {"error": "action must be 'on' or 'off'"}
    _DEVICES[device] = action
    return {"ok": True, "device": device, "state": action}


def get_current_time(city: str | None = None) -> dict:
    """Return the current date and time — for `city` if given, else the
    machine's local time."""
    if city:
        return get_local_time(city)
    now = dt.datetime.now()
    hour12 = now.hour % 12 or 12
    ampm = "AM" if now.hour < 12 else "PM"
    spoken = f"{hour12}:{now.minute:02d} {ampm} on {now.strftime('%A, %B')} {now.day}"
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "spoken": spoken,
    }


def get_assistant_info() -> dict:
    """Report Echo/Jarvis's own identity and live configuration.

    Reads the actual runtime settings so the answer is always accurate — if you
    switch models in .env, this reflects it automatically.
    """
    from echo.config import settings

    capabilities = [
        "set reminders",
        "check your calendar",
        "control smart devices",
        "tell the time and date",
        "fetch the weather",
        "find nearby places",
    ]
    return {
        "name": "Jarvis",
        "intro": "I'm Jarvis — Krishna's personal AI assistant. Think of me as a "
        "small, private version of the assistant from the movies, running right "
        "here on this machine.",
        "description": "your personal, local-first voice assistant",
        "llm_model": settings.model,
        "runtime": "Ollama (running locally, fully offline for most tasks)",
        "speech_to_text": "whisper.cpp",
        "text_to_speech": "Piper",
        "wake_word_engine": "openWakeWord",
        "capabilities": capabilities,
        "privacy": "runs on your own machine; your voice never leaves it for "
        "the core features",
    }


def get_assistant_age() -> dict:
    """How long Jarvis has existed, since `_BIRTH_DATE`."""
    now = dt.datetime.now()
    age_str = _format_age(now - _BIRTH_DATE)
    return {
        "born": _BIRTH_DATE.strftime("%Y-%m-%d %H:%M"),
        "age": age_str,
        "spoken": random.choice(_AGE_TEMPLATES).format(age=age_str),
    }


# ---- schemas (the contract the LLM sees) ---------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Save a short personal fact about the user for "
            "later recall — e.g. names of people in their life (partner, "
            "family, friends, pets), preferences, or other personal details "
            "they mention in passing. Call this QUIETLY whenever the user "
            "states such a fact, without asking permission or announcing it. "
            "Do not call this for the assistant's own facts, only the user's.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "a short, self-contained sentence, e.g. "
                        "'Krishna's girlfriend is named Aditi'",
                    },
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Save a reminder for the user at a specific time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "what to remind about"},
                    "due": {
                        "type": "string",
                        "description": "ISO-8601 datetime, e.g. 2026-07-25T18:30",
                    },
                },
                "required": ["text", "due"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_calendar_events",
            "description": "List the user's calendar events, optionally for one day.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "optional YYYY-MM-DD to filter to one day",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_smart_device",
            "description": "Turn a smart home device on or off.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device": {
                        "type": "string",
                        "description": "device name, e.g. 'living room light'",
                    },
                    "action": {"type": "string", "enum": ["on", "off"]},
                },
                "required": ["device", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current date and time — pass 'city' to get "
            "that city's local time (its own timezone), or omit it for the "
            "assistant's own machine time. Use this whenever the user asks what "
            "time or date it is somewhere.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "optional city name, e.g. 'New York' or "
                        "'Tokyo'; omit for local machine time",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_assistant_info",
            "description": "Get details about YOURSELF (Jarvis, the AI) — your "
            "name, the model you're running, and your capabilities. Use ONLY for "
            "'who are you', 'what model are you', 'what can you do', or your "
            "configuration. Do NOT use this for questions about the user "
            "themselves (e.g. 'what is my profile', 'who am I') — those are "
            "about the human, not you.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather for a city — current conditions, or a "
            "forecast for a specific future date, or an hourly forecast. If the user hasn't said which "
            "city, ASK them first. For forecasts, pass 'date' as YYYY-MM-DD "
            "(you convert 'tomorrow' etc. to a real date yourself).",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "the city name, e.g. 'Bengaluru' or 'London'",
                    },
                    "date": {
                        "type": "string",
                        "description": "optional forecast date as YYYY-MM-DD; "
                        "omit for current weather",
                    },
                    "hourly": {
                        "type": "boolean",
                        "description": "set to true if the user asks for the weather for the next few hours, hour-by-hour",
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_assistant_age",
            "description": "Get how long Jarvis has existed. Use this whenever "
            "the user asks 'how old are you', 'when were you created/born', or "
            "your age — never guess or say you have no age, always call this.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rain_forecast",
            "description": "Get the chance of rain for a city, hour by hour, sorted "
            "with the highest-probability hours first. Use this specifically when the "
            "user asks about rain/precipitation chances (e.g. 'will it rain today', "
            "'when is it most likely to rain', 'chances of rain this week') — it "
            "already ranks the hours so you can just report the top ones. Defaults "
            "to today if no date is given.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "the city name, e.g. 'Bengaluru' or 'London'",
                    },
                    "date": {
                        "type": "string",
                        "description": "optional YYYY-MM-DD; omit for today",
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_nearby_places",
            "description": "Find places near the user (auto-detects their "
            "location), or near a specific place if the user names one. Use "
            "when the user asks to find nearby things like restaurants, cafes, "
            "hospitals, ATMs, pharmacies, hotels, parks, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "type of place, e.g. 'restaurants', "
                        "'cafes', 'hospitals', 'ATMs'",
                    },
                    "city": {
                        "type": "string",
                        "description": "optional area/city/neighborhood name "
                        "the user asked about, e.g. 'Madhapur, Hyderabad'; omit "
                        "to use the user's own detected location",
                    },
                },
                "required": ["category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_location",
            "description": "Get the user's current approximate location (city, "
            "region, country). Use when the user asks 'where am I', 'what is my "
            "location', 'locate me', or 'what city am I in'.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

DISPATCH = {
    "remember_fact": remember_fact,
    "set_reminder": set_reminder,
    "get_calendar_events": get_calendar_events,
    "control_smart_device": control_smart_device,
    "get_current_time": get_current_time,
    "get_assistant_info": get_assistant_info,
    "get_assistant_age": get_assistant_age,
    "get_weather": get_weather,
    "get_rain_forecast": get_rain_forecast,
    "find_nearby_places": find_nearby_places,
    "get_my_location": get_my_location,
}


def call(name: str, args: dict | None) -> dict:
    fn = DISPATCH.get(name)
    if fn is None:
        return {"error": f"no such tool '{name}'"}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}