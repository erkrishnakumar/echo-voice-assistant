"""Tests for the reminder scheduler and the message-channel dispatcher."""

import datetime as dt
from unittest.mock import patch

from echo.db import session_scope
from echo.models import Reminder
from echo.scheduler import ReminderScheduler


def _add_reminder(text, due):
    with session_scope() as s:
        r = Reminder(text=text, due=due)
        s.add(r)
        s.flush()
        return r.id


def _fired(reminder_id):
    with session_scope() as s:
        return s.get(Reminder, reminder_id).fired


# ---- scheduler ------------------------------------------------------------

def test_due_reminder_is_claimed_and_marked_fired():
    rid = _add_reminder("take medicine", dt.datetime.now() - dt.timedelta(minutes=1))
    sched = ReminderScheduler()

    claimed = sched._due_now()

    assert [c["id"] for c in claimed] == [rid]
    assert _fired(rid) is True


def test_future_reminder_is_not_claimed():
    rid = _add_reminder("later", dt.datetime.now() + dt.timedelta(hours=1))
    sched = ReminderScheduler()

    assert sched._due_now() == []
    assert _fired(rid) is False


def test_reminder_fires_only_once():
    _add_reminder("once", dt.datetime.now() - dt.timedelta(minutes=1))
    sched = ReminderScheduler()

    assert len(sched._due_now()) == 1
    assert sched._due_now() == []  # already claimed


def test_stale_reminder_is_retired_without_delivering():
    """A reminder missed while Echo was off should be suppressed, not shouted."""
    rid = _add_reminder("last week", dt.datetime.now() - dt.timedelta(days=7))
    sched = ReminderScheduler(catchup_window=dt.timedelta(hours=1))

    sched._sweep_stale()

    assert _fired(rid) is True
    assert sched._due_now() == []  # nothing left to deliver


def test_recently_overdue_reminder_survives_stale_sweep():
    rid = _add_reminder("just now", dt.datetime.now() - dt.timedelta(minutes=5))
    sched = ReminderScheduler(catchup_window=dt.timedelta(hours=1))

    sched._sweep_stale()

    assert _fired(rid) is False
    assert len(sched._due_now()) == 1


def test_delivery_uses_channel_and_on_fire_callback():
    spoken = []
    sched = ReminderScheduler(channel="desktop", on_fire=spoken.append)

    with patch("echo.channels.send", return_value={"ok": True}) as sent:
        sched._deliver({"id": 1, "text": "drink water"})

    assert sent.call_args.kwargs["text"] == "drink water"
    assert sent.call_args.kwargs["channel"] == "desktop"
    assert spoken == ["drink water"]


def test_delivery_survives_a_failing_on_fire_callback():
    """TTS blowing up must not stop the reminder from being delivered."""
    def boom(_text):
        raise RuntimeError("tts died")

    sched = ReminderScheduler(on_fire=boom)
    with patch("echo.channels.send", return_value={"ok": True}):
        sched._deliver({"id": 1, "text": "still fine"})  # must not raise


# ---- channels -------------------------------------------------------------

def test_unknown_channel_returns_error():
    from echo.channels import send
    r = send(channel="smoke-signal", text="hello")
    assert "error" in r


def test_empty_message_is_rejected():
    from echo.channels import send
    r = send(channel="desktop", text="   ")
    assert "error" in r


def test_channel_failure_is_caught_not_raised():
    from echo.channels import _channels, send

    chans = _channels()
    if "desktop" not in chans:
        return  # channel unavailable on this OS; nothing to assert

    with patch.object(chans["desktop"], "send", side_effect=RuntimeError("nope")):
        r = send(channel="desktop", text="hi")
    assert "error" in r
