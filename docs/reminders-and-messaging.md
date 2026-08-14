# Reminders & Messaging

How Jarvis reaches you: the message-channel layer, the `send_message` tool,
and the scheduler that makes `set_reminder` actually fire.

Also covers the `HOME_LOCATION` fix for nearby-place searches, built in the
same pass.

---

## The bug this started from

`set_reminder` had been marked "done" for a long time. It wasn't.

It stored a row in Postgres and Jarvis would say *"I've set a reminder for
6pm"* — but **nothing ever fired**. There was no scheduler, no background
thread, no timer. The only code that ever read the `reminders` table was a
`GET /reminders` API endpoint that listed rows on request.

So it was a write-only notepad wearing a reminder's clothing. Worse than
missing, because the assistant's confident reply implied something had
happened that hadn't.

Fixing it properly needed two pieces that didn't exist:

1. Something that watches the clock and notices when a reminder comes due.
2. Somewhere to *deliver* it — a way for Jarvis to reach you unprompted.

Piece 2 is Phase 5 (messaging), so the two were built together.

---

## Part 1 — Message channels

### The shape

A channel is a module that knows how to deliver a message one way. The
dispatcher (`src/echo/channels/__init__.py`) picks between them.

Each channel module exposes:

```python
NAME         = "desktop"                  # what the LLM passes as `channel`
DESCRIPTION  = "a toast notification..."  # shown in the tool schema
def is_available() -> bool: ...           # False = skip, don't fail
def send(text, subject=None, to=None, **options) -> dict: ...
```

Adding a channel is a one-file addition: write the module, add its import
path to `_CHANNEL_MODULES`. Nothing else changes — not the tool schema, not
the agent, not the scheduler.

### Why `is_available()` exists

Channels have preconditions. Desktop toasts are Windows-only. Email needs
SMTP credentials. Telegram needs a bot token.

Without a availability check, a channel with missing config would load fine
and then explode at call time — mid-conversation, after the user asked for
something. Instead, unavailable channels are **filtered out at load**, so:

- the LLM never sees a channel it can't use (it's absent from the schema),
- a missing token is a startup log line, not a runtime failure.

### Failure handling

`send()` never raises. Every failure comes back as `{"error": ...}` so the
agent can tell the user something useful instead of crashing a turn.

There's one deliberate subtlety — the dispatcher passes extra options
through (`sound=` for desktop toasts), but a channel that doesn't accept an
option shouldn't lose the message over it:

```python
try:
    return mod.send(text, subject=subject, to=to, **options)
except TypeError:
    # channel doesn't understand one of the extras — retry plain rather
    # than dropping the message over a cosmetic hint
    return mod.send(text, subject=subject, to=to)
```

So a caller can pass a hint speculatively without knowing which channel will
handle it.

### The desktop channel, and the silent-toast gotcha

`src/echo/channels/desktop.py` uses `winotify`, which drives Windows'
built-in toast API through PowerShell. No service, no account, no setup.

**Windows toasts are silent unless you set a sound explicitly.** The first
version didn't, and the notification appeared with no audio at all — easy to
miss entirely, which defeats the point. Now a sound is always attached:

| key        | when to use                        |
|------------|------------------------------------|
| `default`  | general notifications              |
| `reminder` | reminders (distinct chime)         |
| `mail`     | incoming-message feel             |
| `sms`      | short alert                        |
| `alarm`    | looping, hard to ignore            |
| `call`     | looping ringtone                   |
| `silent`   | deliberately quiet                 |

Unknown names fall back to `default` rather than failing — a wrong sound
must never cost you the message.

### The `send_message` tool

Registered in `tools/registry.py` like any other tool. Its schema lists the
live channels, generated at import time from `describe_channels()`, so the
LLM only ever sees what actually works on this machine.

Verified against a real model call:

    "send me a desktop notification saying the build finished"
    -> send_message(channel="desktop", text="The build has finished")

---

## Part 2 — The reminder scheduler

`src/echo/scheduler.py`. A daemon thread that wakes every 30s, finds due
reminders, delivers them, marks them fired.

Simple in outline. Four details are what make it not annoying.

### 1. Fire exactly once — `fired` column

New boolean on `Reminder` (migration `db95c8f20d0b`), defaulting to false.

The claim-and-mark happens **inside one transaction**:

```python
with session_scope() as s:
    rows = s.execute(select(Reminder).where(
        Reminder.fired.is_(False), Reminder.due <= now
    )).scalars().all()
    claimed = [r.as_dict() for r in rows]
    for r in rows:
        r.fired = True          # marked before delivery, same transaction
return claimed
```

Marking *before* delivering is deliberate. If it marked after, a slow
notification could overlap the next poll and alert twice. The tradeoff — a
crash between mark and deliver loses that one alert — is the better failure:
a missed reminder beats an endless loop of duplicates.

> **Migration note:** `server_default="false"` matters. The column is
> `NOT NULL` and the table already had rows; without a server default the
> migration fails on existing data. Autogenerate got this right, but it's
> the kind of thing worth checking before applying — see
> [migrations.md](migrations.md).

### 2. Stale reminders are suppressed, not shouted

Echo isn't always running. If a reminder came due last Tuesday while it was
closed, alerting about it at startup is noise.

On start, anything overdue by more than `catchup_window` (default 1 hour) is
marked fired **without notifying** — and logged, so it's visible rather than
silently dropped:

```
skipping stale reminder #1 (due 2026-07-27T12:00): 'something important...'
retired 1 stale reminder(s) without alerting
```

This was found the practical way: the database already held a reminder 18
days overdue. Without the sweep, the first run of the new scheduler would
have toasted about it immediately.

Reminders overdue by *less* than the window still fire normally — you
probably do want the one you missed twenty minutes ago.

### 3. It won't talk over you

The scheduler takes an optional `on_fire` callback. The voice assistant uses
it to speak reminders aloud — but only while idle:

```python
def _speak_reminder(self, text):
    if self._in_conversation:
        log.info("reminder fired mid-conversation; notification only")
        return
    self._safe_speak(f"Reminder: {text}")
```

Speaking mid-turn would talk across the user *and* bleed TTS audio into the
microphone, corrupting whatever they were saying. The desktop notification
fires either way, so nothing is lost by staying quiet.

### 4. Nothing it does can take down the voice loop

The poll body is fully guarded, and a failing `on_fire` callback is caught
separately — TTS blowing up must not stop the notification that already
succeeded. `stop()` uses an `Event`, so shutdown doesn't wait out a full
poll interval.

### Wiring

`VoiceAssistant.run()` starts it alongside the orb and gesture detector. All
three now stop through one `_shutdown()` helper. If the scheduler can't
start, it's logged and the assistant runs without it — reminders get saved
but don't fire, which is exactly the old behavior.

### Known limit

**Reminders only fire while `voice.py` is running.** Close Echo and the
scheduler goes with it; anything that comes due meanwhile gets swept as
stale on next start. Firing while Echo is closed needs a separate always-on
process (Windows Task Scheduler, or a tray app).

---

## Part 3 — `HOME_LOCATION`

`find_nearby_places` used to locate you by IP. That's ISP-accurate, not
you-accurate — often the wrong neighbourhood, sometimes the wrong city.

Now there's a `HOME_LOCATION` setting. Resolution order:

1. A city named in the request — *"hospitals in Bengaluru"*
2. `HOME_LOCATION` from `.env`
3. IP detection (unchanged fallback if `HOME_LOCATION` is unset)

```
HOME_LOCATION=Madhapur, Hyderabad, India
```

Geocoded once and cached per process — it's fixed config, no reason to hit
the geocoder on every search.

The spoken reply names the place (*"40 restaurants near Madhapur"*) instead
of the vaguer *"near you"*, so a wrong `HOME_LOCATION` is obvious rather
than silently skewing results.

---

## Testing

    python voice.py --test-notify      # one desktop notification
    python voice.py --test-reminder    # reminder 10s out, watch it fire

`tests/test_scheduler.py` covers the logic that's awkward to check by hand:

- due reminders are claimed and marked
- future reminders are left alone
- a reminder fires only once
- stale reminders retire without delivering
- recently-overdue reminders survive the sweep
- delivery reaches the channel and the `on_fire` callback
- a failing `on_fire` doesn't break delivery
- unknown channel / empty message / channel failure all return errors

Suite: **32 passing** (was 22 — 10 new, plus 2 pre-existing failures fixed).

> Those 2 had been broken since the Groq migration: they faked
> `requests.post(url, json, timeout)`, but `_chat` had started routing to
> `_chat_groq`, which passes a `headers` kwarg the fakes didn't accept. They
> now exercise `_post_with_retry` directly and accept `**kwargs`, so they
> test retry behaviour rather than whichever provider `.env` happens to
> select.

---

## Adding the next channel

Telegram or email is now a single file. Sketch:

```python
# src/echo/channels/telegram.py
NAME = "telegram"
DESCRIPTION = "a Telegram message"

def is_available() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)

def send(text, subject=None, to=None, **_):
    ...  # POST to api.telegram.org
    return {"ok": True, "channel": NAME, "delivered_to": chat_id}
```

Then add `"echo.channels.telegram"` to `_CHANNEL_MODULES`. The tool schema
picks it up automatically; the scheduler can target it by passing
`channel="telegram"`.

Telegram is the more useful next step than email — it reaches your phone,
which is what makes reminders meaningful when you're away from the PC.
