"""
Message channels — pluggable ways for Jarvis to reach you or someone else.

Each channel module exposes:
    NAME          str  — the identifier the LLM passes as `channel`
    DESCRIPTION   str  — short phrase used in the tool schema
    is_available()     — False when the channel can't work here (wrong OS,
                         missing credentials), so it's hidden rather than
                         failing at call time
    send(text, subject=None, to=None) -> dict

Adding a channel is a one-file addition: write the module, then list it in
`_CHANNEL_MODULES` below.
"""

from __future__ import annotations

from importlib import import_module

from echo.logging_conf import get_logger

log = get_logger("echo.channels")

# import paths, loaded lazily so a broken/optional channel can't stop startup
_CHANNEL_MODULES = [
    "echo.channels.desktop",
]

_loaded: dict | None = None


def _channels() -> dict:
    """Load channel modules once, keeping only the available ones."""
    global _loaded
    if _loaded is not None:
        return _loaded

    _loaded = {}
    for path in _CHANNEL_MODULES:
        try:
            mod = import_module(path)
            if mod.is_available():
                _loaded[mod.NAME] = mod
            else:
                log.info(f"channel '{mod.NAME}' unavailable here; skipping")
        except Exception:
            log.exception(f"could not load channel module {path}")
    return _loaded


def available_channels() -> list[str]:
    return list(_channels())


def describe_channels() -> str:
    """One-line summary for the tool schema, e.g. "desktop (a toast ...)"."""
    return "; ".join(
        f"{name} ({mod.DESCRIPTION})" for name, mod in _channels().items()
    )


def send(channel: str, text: str, subject: str | None = None,
         to: str | None = None, **options) -> dict:
    """Dispatch a message to one channel. Never raises — returns {'error': ...}.

    Extra keyword args are passed through to the channel (e.g. `sound` for
    desktop toasts); a channel that doesn't accept one ignores it rather than
    erroring, so callers can pass hints without knowing the channel.
    """
    text = (text or "").strip()
    if not text:
        return {"error": "no message text provided"}

    chans = _channels()
    if not chans:
        return {"error": "no message channels are configured on this machine."}

    mod = chans.get((channel or "").strip().lower())
    if mod is None:
        return {
            "error": f"unknown channel '{channel}'.",
            "available": list(chans),
        }

    try:
        return mod.send(text, subject=subject, to=to, **options)
    except TypeError:
        # channel doesn't support one of the extra options — retry plain
        # rather than losing the message over a cosmetic hint
        try:
            return mod.send(text, subject=subject, to=to)
        except Exception as e:
            log.exception(f"channel '{channel}' failed to send")
            return {"error": f"couldn't send via {channel}: {e}"}
    except Exception as e:
        log.exception(f"channel '{channel}' failed to send")
        return {"error": f"couldn't send via {channel}: {e}"}
