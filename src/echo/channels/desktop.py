"""
Desktop notification channel — a Windows toast, fully local, no setup.

Uses winotify, which shells out to PowerShell's toast API. Windows-only;
`is_available()` reports False elsewhere so the dispatcher can skip it
instead of raising.

Toasts are SILENT unless a sound is set explicitly, so we always attach one —
a notification you don't notice isn't a notification.
"""

from __future__ import annotations

import sys

NAME = "desktop"
DESCRIPTION = "a toast notification on this computer"

APP_ID = "Jarvis"

# friendly names -> winotify audio attributes, so callers (and .env) don't
# have to import winotify just to pick a sound
SOUNDS = {
    "default": "Default",
    "reminder": "Reminder",
    "mail": "Mail",
    "sms": "SMS",
    "alarm": "LoopingAlarm",
    "call": "LoopingCall",
    "silent": "Silent",
}
DEFAULT_SOUND = "default"


def is_available() -> bool:
    return sys.platform == "win32"


def send(text: str, subject: str | None = None, to: str | None = None,
         sound: str | None = None, loop: bool = False) -> dict:
    """Show a desktop toast. `to` is ignored — notifications are always local.

    `sound` is a key from SOUNDS; unknown names fall back to the default
    rather than failing, since a wrong sound shouldn't lose the message.
    """
    from winotify import Notification, audio

    note = Notification(
        app_id=APP_ID,
        title=subject or "Jarvis",
        msg=text,
    )

    key = (sound or DEFAULT_SOUND).strip().lower()
    attr = SOUNDS.get(key, SOUNDS[DEFAULT_SOUND])
    note.set_audio(getattr(audio, attr), loop=loop)

    note.show()
    return {
        "ok": True,
        "channel": NAME,
        "delivered_to": "this computer",
        "sound": key if key in SOUNDS else DEFAULT_SOUND,
    }
