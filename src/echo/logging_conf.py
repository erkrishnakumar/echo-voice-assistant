"""
Logging setup for Echo.

Call `setup_logging()` once at startup. Use `get_logger(__name__)` in modules.
The `timed()` context manager logs how long a stage takes — essential for a
voice assistant where you need to see which step (STT, LLM, TTS) is slow.

    from echo.logging_conf import get_logger, timed
    log = get_logger(__name__)
    with timed(log, "transcribe"):
        text = stt.transcribe(wav)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager


def setup_logging(level: str | None = None) -> None:
    lvl = (level or os.getenv("ECHO_LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, lvl, logging.INFO),
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,  # override any prior config (e.g. from a library)
    )
    # openwakeword and others can be chatty at DEBUG; keep them at WARNING
    logging.getLogger("openwakeword").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextmanager
def timed(log: logging.Logger, label: str):
    """Log the wall-clock duration of a block at INFO."""
    log.info(f"{label}…")
    start = time.perf_counter()
    try:
        yield
    finally:
        dur = time.perf_counter() - start
        log.info(f"{label} done in {dur:.2f}s")