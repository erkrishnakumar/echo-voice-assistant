"""
Central config. Loads `.env` exactly once at import time and exposes a single
frozen `settings` object. Every other module imports `settings` from here —
nothing else in the codebase should call os.getenv or read .env directly.

    from echo.config import settings
    print(settings.model)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# project root = three levels up from this file: src/echo/config.py -> echo/
ROOT = Path(__file__).resolve().parents[2]

# load .env from the project root into the process environment.
# override=False means real shell env vars win over the file, which is what
# you want in production/containers.
load_dotenv(ROOT / ".env", override=False)


def _get(key: str, default: str) -> str:
    return os.getenv(key, default)


def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"env var {key}={raw!r} must be an integer") from e


@dataclass(frozen=True)
class Settings:
    # --- LLM ---
    llm_provider: str
    groq_api_key: str | None
    ollama_url: str
    model: str
    fallback_model: str | None
    gemini_api_key: str | None
    gemini_model: str
    max_tool_rounds: int
    timeout: int
    keep_alive: str
    llm_retries: int
    # --- database ---
    database_url: str
    # --- api ---
    api_host: str
    api_port: int
    # --- owner identity ---
    owner_name: str
    owner_role: str
    owner_bio: str
    # --- location ---
    home_location: str | None


def _build_database_url() -> str:
    """Prefer a full DATABASE_URL; otherwise assemble one from parts."""
    full = os.getenv("DATABASE_URL")
    if full:
        return full
    user = _get("POSTGRES_USER", "echo")
    pw = _get("POSTGRES_PASSWORD", "echo")
    host = _get("POSTGRES_HOST", "localhost")
    port = _get("POSTGRES_PORT", "5432")
    db = _get("POSTGRES_DB", "echo")
    return f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{db}"


def _load() -> Settings:
    provider = _get("LLM_PROVIDER", "ollama").lower()
    model = _get("ECHO_MODEL", "qwen2.5:3b") if provider == "ollama" else _get("GROQ_MODEL", "llama-3.3-70b-versatile")
    fallback = _get("GROQ_FALLBACK_MODEL", "llama-3.1-8b-instant") if provider == "groq" else None
    
    return Settings(
        llm_provider=provider,
        groq_api_key=_get("GROQ_API_KEY", None),
        ollama_url=_get("OLLAMA_URL", "http://localhost:11434/api/chat"),
        model=model,
        fallback_model=fallback,
        gemini_api_key=_get("GEMINI_API_KEY", None),
        gemini_model=_get("GEMINI_MODEL", "gemini-flash-latest"),
        max_tool_rounds=_get_int("ECHO_MAX_TOOL_ROUNDS", 4),
        timeout=_get_int("ECHO_TIMEOUT", 120),
        keep_alive=_get("OLLAMA_KEEP_ALIVE", "30m"),
        llm_retries=_get_int("ECHO_LLM_RETRIES", 2),
        database_url=_build_database_url(),
        api_host=_get("ECHO_API_HOST", "0.0.0.0"),
        api_port=_get_int("ECHO_API_PORT", 8000),
        owner_name=_get("OWNER_NAME", "Krishna Kumar"),
        owner_role=_get(
            "OWNER_ROLE", "an Artificial Intelligence and Machine Learning Engineer"
        ),
        owner_bio=_get(
            "OWNER_BIO",
            "Krishna Kumar built me — he is an AI/ML engineer who enjoys "
            "building intelligent systems like this one.",
        ),
        home_location=_get("HOME_LOCATION", None),
    )


settings = _load()