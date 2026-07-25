# Echo — a local-first, MCP-native voice assistant

A local LLM (via Ollama) picks and fills tools. Data lives in PostgreSQL via
SQLAlchemy. A FastAPI service exposes the agent over HTTP. Voice (STT/TTS) and
wake word wrap this later.

## Layout

    echo/
    ├── .env / .env.example      config (real is gitignored)
    ├── docker-compose.yml       Postgres + Ollama
    ├── requirements.txt
    ├── run.py                   CLI entry point
    └── src/echo/
        ├── config.py            loads .env once -> `settings`
        ├── db.py                SQLAlchemy engine + session
        ├── models.py            ORM models (Reminder, Event)
        ├── agent.py             the tool-calling loop
        ├── api.py               FastAPI app
        └── tools/registry.py    tools + JSON schemas (now ORM-backed)

## Architecture

    Docker:  Postgres  +  Ollama
    Host:    FastAPI / run.py  ->  talks to both

Infrastructure (DB, model server) runs in Docker; your app code runs on the host
so you get instant reload while developing.

## Setup

    # 1. bring up infrastructure
    docker compose up -d

    # 2. pull a model into the Ollama container (once)
    docker compose exec ollama ollama pull qwen2.5:3b

    # 3. Python env on host
    python3.11 -m venv .venv
    .venv\Scripts\activate            # Windows; mac/linux: source .venv/bin/activate
    pip install -r requirements.txt

    # 4. config
    copy .env.example .env            # mac/linux: cp .env.example .env

## Run

CLI (creates tables automatically on start):

    python run.py --demo              # scripted
    python run.py                     # interactive

API:

    uvicorn echo.api:app --reload --app-dir src

Then visit http://localhost:8000/docs for interactive API docs. Endpoints:

    POST /chat            {"message": "remind me to call mom at 6pm"}
    GET  /reminders
    GET  /calendar?date=2026-07-26
    GET  /health

## Config keys (.env)

| key                    | default                         | meaning                     |
|------------------------|---------------------------------|-----------------------------|
| `OLLAMA_URL`           | http://localhost:11434/api/chat | Ollama chat endpoint        |
| `ECHO_MODEL`           | qwen2.5:3b                      | model name                  |
| `ECHO_MAX_TOOL_ROUNDS` | 4                               | tool rounds before answer   |
| `ECHO_TIMEOUT`         | 120                             | request timeout (seconds)   |
| `POSTGRES_USER/PASSWORD/DB` | echo                       | DB credentials (compose+app)|
| `POSTGRES_HOST/PORT`   | localhost / 5432                | where the app finds the DB  |
| `ECHO_API_HOST/PORT`   | 0.0.0.0 / 8000                  | API bind address            |

`POSTGRES_*` are read by both docker-compose (to create the DB) and the app (to
connect). Keep them consistent.

## Handy commands

    docker compose ps                       # container status
    docker compose logs -f postgres         # DB logs
    docker compose exec postgres psql -U echo -d echo   # SQL shell
    docker compose down                     # stop (data persists in volume)
    docker compose down -v                  # stop AND wipe DB + models

## Notes on the DB

- SQLAlchemy is database-agnostic; the ORM code is identical whether the URL
  points at Postgres or SQLite. Only `.env` changes.
- Tables are created automatically via `init_db()` on app/CLI startup. For real
  schema migrations later, add Alembic.

## Next steps

- Fire reminders when due -> Redis + Celery (a real background-task use case).
- Voice I/O -> whisper.cpp in, Piper out, wrapping the same agent.
- Promote tools/registry.py to a proper MCP server.