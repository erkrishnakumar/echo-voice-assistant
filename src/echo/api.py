"""
Echo HTTP API.

    POST /chat            send a message to the agent, get a spoken-style reply
    GET  /reminders       list stored reminders
    GET  /calendar        list events (optional ?date=YYYY-MM-DD)
    GET  /health          liveness check

Run it (from the project root, with the venv active):
    uvicorn echo.api:app --reload --app-dir src

Postgres and Ollama must be running (docker compose up -d).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select

from echo.agent import handle
from echo.db import init_db, session_scope
from echo.models import Event, Reminder


@asynccontextmanager
async def lifespan(app: FastAPI):
    # create tables on startup if they don't exist
    init_db()
    yield


app = FastAPI(title="Echo API", version="0.1.0", lifespan=lifespan)


# ---- request/response schemas --------------------------------------------

class ChatRequest(BaseModel):
    message: str
    # optional prior turns so callers can keep a conversation going
    history: list[dict] = []


class ChatResponse(BaseModel):
    reply: str
    history: list[dict]


# ---- endpoints ------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    history = list(req.history)
    reply = handle(req.message, history)
    return ChatResponse(reply=reply, history=history)


@app.get("/reminders")
def list_reminders() -> dict:
    with session_scope() as s:
        rows = s.execute(select(Reminder).order_by(Reminder.due)).scalars().all()
        items = [r.as_dict() for r in rows]
    return {"count": len(items), "reminders": items}


@app.get("/calendar")
def list_calendar(date: str | None = Query(default=None)) -> dict:
    with session_scope() as s:
        rows = s.execute(select(Event).order_by(Event.start)).scalars().all()
        items = [e.as_dict() for e in rows]
    if date:
        items = [e for e in items if e["start"].startswith(date)]
    return {"count": len(items), "events": items}