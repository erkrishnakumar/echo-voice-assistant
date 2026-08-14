"""
Reminder scheduler — the piece that makes `set_reminder` actually alert you.

A background thread wakes every `poll_seconds`, finds reminders that are due
and not yet fired, delivers them through a message channel, and marks them
fired so they alert exactly once.

Stale reminders: if Echo was off when something came due, alerting about a
week-old reminder on startup is noise, not help. Anything overdue by more
than `catchup_window` is marked fired WITHOUT notifying, and logged so it's
visible rather than silently dropped.

Runs as a daemon thread — it never blocks shutdown, and a failure in here
must never take down the voice loop, so the poll body is fully guarded.
"""

from __future__ import annotations

import datetime as dt
import threading

from sqlalchemy import select

from echo.db import session_scope
from echo.logging_conf import get_logger
from echo.models import Reminder

log = get_logger("echo.scheduler")

DEFAULT_POLL_SECONDS = 30
DEFAULT_CATCHUP_WINDOW = dt.timedelta(hours=1)


class ReminderScheduler:
    def __init__(self, channel: str = "desktop",
                 poll_seconds: int = DEFAULT_POLL_SECONDS,
                 catchup_window: dt.timedelta = DEFAULT_CATCHUP_WINDOW,
                 on_fire=None):
        self.channel = channel
        self.poll_seconds = poll_seconds
        self.catchup_window = catchup_window
        # optional extra delivery (e.g. speak it aloud) called with the text
        self.on_fire = on_fire
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._sweep_stale()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info(f"reminder scheduler started (every {self.poll_seconds}s, "
                 f"via {self.channel})")

    def stop(self) -> None:
        self._stop.set()

    # ---- internals ----------------------------------------------------

    def _sweep_stale(self) -> None:
        """Retire reminders that came due while Echo was off, without alerting."""
        cutoff = dt.datetime.now() - self.catchup_window
        try:
            with session_scope() as s:
                stale = s.execute(
                    select(Reminder).where(
                        Reminder.fired.is_(False), Reminder.due < cutoff
                    )
                ).scalars().all()
                for r in stale:
                    log.info(f"skipping stale reminder #{r.id} "
                             f"(due {r.due.isoformat()}): {r.text!r}")
                    r.fired = True
                if stale:
                    log.info(f"retired {len(stale)} stale reminder(s) without "
                             "alerting — they were more than "
                             f"{self.catchup_window} overdue")
        except Exception:
            log.exception("could not sweep stale reminders")

    def _due_now(self) -> list[dict]:
        """Claim all due reminders, marking them fired inside the same
        transaction so a slow delivery can't cause a double-alert."""
        now = dt.datetime.now()
        with session_scope() as s:
            rows = s.execute(
                select(Reminder).where(
                    Reminder.fired.is_(False), Reminder.due <= now
                ).order_by(Reminder.due)
            ).scalars().all()
            claimed = [r.as_dict() for r in rows]
            for r in rows:
                r.fired = True
        return claimed

    def _deliver(self, reminder: dict) -> None:
        from echo.channels import send

        text = reminder["text"]
        result = send(
            channel=self.channel,
            text=text,
            subject="Reminder",
            sound="reminder",  # distinct chime so it stands out from chatter
        )
        if result.get("error"):
            log.error(f"reminder #{reminder['id']} could not be delivered: "
                      f"{result['error']}")
        else:
            log.info(f"• reminder fired #{reminder['id']}: {text!r}")

        if self.on_fire is not None:
            try:
                self.on_fire(text)
            except Exception:
                log.exception("reminder on_fire callback failed")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                for reminder in self._due_now():
                    self._deliver(reminder)
            except Exception:
                log.exception("reminder poll failed; continuing")
            # Event.wait doubles as the sleep and the stop signal, so
            # shutdown doesn't have to wait out a full poll interval
            self._stop.wait(self.poll_seconds)
