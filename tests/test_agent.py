"""Tests for agent helpers: filler stripping and retry behaviour."""

from unittest.mock import MagicMock, patch

import requests

from echo import agent


def test_strip_filler_removes_assist_tail():
    out = agent._strip_filler("You are Krishna Kumar. How can I assist you today?")
    assert out == "You are Krishna Kumar."


def test_strip_filler_removes_anything_else():
    out = agent._strip_filler("The light is on. Is there anything else?")
    assert out == "The light is on."


def test_strip_filler_keeps_real_content():
    text = "I'm doing great, thanks for asking!"
    assert agent._strip_filler(text) == text


def test_strip_filler_never_empty():
    # a reply that is ONLY filler should not become empty
    out = agent._strip_filler("How can I assist you today?")
    assert out  # non-empty fallback


def _fake_response(content):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"message": {"content": content}}
    m.raise_for_status = lambda: None
    return m


def test_retry_succeeds_after_transient_failure():
    calls = {"n": 0}

    def flaky(url, json, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("blip")
        return _fake_response("ok")

    with patch("echo.agent.requests.post", side_effect=flaky), \
         patch("echo.agent.time.sleep"):
        result = agent._chat([{"role": "user", "content": "hi"}])
    assert result["content"] == "ok"
    assert calls["n"] == 3


def test_retry_raises_agent_error_when_exhausted():
    def always_fail(url, json, timeout):
        raise requests.ConnectionError("down")

    with patch("echo.agent.requests.post", side_effect=always_fail), \
         patch("echo.agent.time.sleep"):
        try:
            agent._chat([{"role": "user", "content": "hi"}])
            raised = False
        except agent.AgentError:
            raised = True
    assert raised


def test_strip_filler_suppresses_json_leak():
    leak = '{"name": "get_time", "parameters": {"date": "2026-07-26"}}'
    out = agent._strip_filler(leak)
    assert not out.startswith("{")  # gibberish JSON not returned