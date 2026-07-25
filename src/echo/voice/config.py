"""
Voice-specific configuration. Kept separate from the core `settings` so the
text-only parts of Echo don't need any of these values to import.

Paths point at binaries and models you download during setup (see
docs/voice-setup.md). Defaults assume the layout that setup script creates.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from echo.config import ROOT


def _get(key: str, default: str) -> str:
    return os.getenv(key, default)


def _get_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(f"env var {key}={raw!r} must be a number") from e


def _resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class VoiceSettings:
    # whisper.cpp
    whisper_bin: Path
    whisper_model: Path
    # piper
    piper_bin: Path
    piper_model: Path
    # wake word (openWakeWord)
    wake_word: str
    wake_threshold: float
    # audio capture
    sample_rate: int
    silence_threshold: float   # RMS below this = silence
    silence_seconds: float     # this much silence ends an utterance
    max_utterance_seconds: float
    conversation_timeout: float  # silence before leaving conversation mode


def load_voice_settings() -> VoiceSettings:
    return VoiceSettings(
        whisper_bin=_resolve(_get("WHISPER_BIN", "bin/whisper/main")),
        whisper_model=_resolve(_get("WHISPER_MODEL", "bin/models/ggml-base.en.bin")),
        piper_bin=_resolve(_get("PIPER_BIN", "bin/piper/piper")),
        piper_model=_resolve(
            _get("PIPER_MODEL", "bin/models/en_US-amy-medium.onnx")
        ),
        wake_word=_get("WAKE_WORD", "hey_jarvis"),
        wake_threshold=_get_float("WAKE_THRESHOLD", 0.5),
        sample_rate=16000,
        silence_threshold=_get_float("VOICE_SILENCE_THRESHOLD", 500.0),
        silence_seconds=_get_float("VOICE_SILENCE_SECONDS", 1.0),
        max_utterance_seconds=_get_float("VOICE_MAX_UTTERANCE_SECONDS", 15.0),
        conversation_timeout=_get_float("VOICE_CONVERSATION_TIMEOUT", 12.0),
    )