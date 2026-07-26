"""Tests for the tool implementations and dispatch."""

from echo.tools import call


def test_set_reminder_valid():
    r = call("set_reminder", {"text": "call mom", "due": "2026-07-26T09:00"})
    assert r["ok"] is True
    assert r["id"]
    assert r["text"] == "call mom"


def test_set_reminder_bad_datetime():
    r = call("set_reminder", {"text": "x", "due": "tomorrow morning"})
    assert "error" in r


def test_get_calendar_empty():
    r = call("get_calendar_events", {})
    assert r["count"] == 0
    assert r["events"] == []


def test_control_device_valid():
    r = call("control_smart_device", {"device": "living room light", "action": "on"})
    assert r["ok"] is True
    assert r["state"] == "on"


def test_control_device_unknown():
    r = call("control_smart_device", {"device": "garage door", "action": "on"})
    assert "error" in r
    assert "known" in r


def test_control_device_bad_action():
    r = call("control_smart_device", {"device": "living room light", "action": "blink"})
    assert "error" in r


def test_unknown_tool():
    r = call("no_such_tool", {})
    assert "error" in r


def test_bad_arguments():
    # missing required 'due'
    r = call("set_reminder", {"text": "x"})
    assert "error" in r


def test_get_current_time():
    r = call("get_current_time", {})
    assert "date" in r and "time" in r and "spoken" in r
    assert len(r["date"]) == 10  # YYYY-MM-DD