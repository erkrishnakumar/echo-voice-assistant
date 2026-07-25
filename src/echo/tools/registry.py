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

from sqlalchemy import select

from echo.db import session_scope
from echo.models import Event, Reminder

# in-memory device state; swap for a Home Assistant client later
_DEVICES = {"living room light": "off", "bedroom fan": "off", "kitchen light": "off"}


# ---- tool implementations -------------------------------------------------

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


# ---- schemas (the contract the LLM sees) ---------------------------------

TOOLS = [
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
]

DISPATCH = {
    "set_reminder": set_reminder,
    "get_calendar_events": get_calendar_events,
    "control_smart_device": control_smart_device,
}


def call(name: str, args: dict) -> dict:
    fn = DISPATCH.get(name)
    if fn is None:
        return {"error": f"no such tool '{name}'"}
    try:
        return fn(**args)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}