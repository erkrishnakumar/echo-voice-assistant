# Interruption & Cancellation ("Hang up" / "Stop")

How Echo lets you cancel or interrupt, why it's built the way it is, and the
path to full barge-in later.

---

## The core constraint

While Echo is **processing** (running the LLM, ~5–25 s on this CPU-only machine)
or **speaking** (Piper TTS), the main loop is busy — it isn't reading the
microphone during those blocks. So there are two very different kinds of
"interrupt", with very different difficulty:

1. **Interrupt while SPEAKING** — easy. We can stop audio playback the moment a
   cancel phrase is heard (or just cut it short). Low risk.
2. **Interrupt while THINKING (mid-LLM)** — hard. The LLM call is a blocking
   request; to abort it we'd need it running in a background thread while the
   mic keeps listening in parallel. That's real concurrency + more memory.

---

## Stage 1 — what's implemented now (production-safe)

**Behaviour:**
- You can say a cancel phrase to **discard the current exchange and start over**.
- If Echo is **speaking**, the cancel phrase stops the speech immediately
  (barge-in on TTS).
- Echo then replies formally — *"Of course, Sir. Go ahead — what did you mean?"*
  — and listens fresh for your real request.

**Cancel phrases:**
`stop`, `cancel`, `never mind`, `hang up`, `hold on`, `wait wait`,
`forget it`, `scratch that`.
(Deliberately excludes bare `wait` and `no` — too common in normal speech,
would cause false cancels.)

**Why this design:**
- Covers the most common real case: you realise you misspoke *while Echo is
  replying*, say "stop", and it halts + resets.
- No background threads, no extra memory pressure — safe on a 16 GB /
  CPU-only machine.
- Reliable and simple: fewer moving parts, less to break.

**Limitation:** if Echo is mid-*thinking* (the LLM call is running), it can't
hear you yet. You have to wait for that call to finish, then cancel. Stage 2
removes this limit.

---

## Stage 2 — full barge-in (future)

**Behaviour:** say "stop" at ANY time — even while the LLM is mid-generation —
and Echo aborts immediately, like Alexa/Siri.

**What it requires:**
- Run the LLM call in a **background thread/task** so the main thread stays free.
- Keep a **lightweight wake/cancel listener** running on the mic during
  processing.
- A **cancellation signal** (e.g. `threading.Event`) the LLM worker checks, plus
  the ability to drop the in-flight Ollama request.
- Careful audio-device handling so the listener and the recorder don't fight
  over the mic.

**Costs / risks:**
- More memory (a second listener + threading overhead) — a real concern on this
  machine, which already hits RAM limits.
- More complexity → more failure modes (deadlocks, mic contention).
- Ollama request cancellation is best-effort; a partly-generated response may
  still arrive and must be discarded.

**When to build it:** once there's RAM headroom (or a GPU box), and after the
core feature set is stable. It's the "correct" production behaviour, but it's an
enhancement, not a foundation.

---

## Summary

| | Stage 1 (now) | Stage 2 (later) |
|---|---|---|
| Interrupt while speaking | ✅ yes | ✅ yes |
| Interrupt while thinking | ❌ wait for it | ✅ yes |
| Concurrency / threads | none | required |
| Extra memory | none | yes |
| Risk | low | higher |
| Matches Alexa/Siri feel | mostly | fully |

Stage 1 is the reliable foundation that fits the current hardware. Stage 2 is
the aspirational upgrade for when resources allow.
