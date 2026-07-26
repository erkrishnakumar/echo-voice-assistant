# Latency Optimization

How we cut Echo's per-turn response time, what changed, and the measured
before/after numbers.

**Hardware context:** Intel i5-1335U, 7.6 GB RAM, **CPU-only (no GPU)**.
Model: `qwen2.5:3b`. All numbers below are from real run logs, not estimates.

---

## The problem

The first working voice loop was slow on *every* turn — 60–77 seconds per
response. Because there was no visibility into where the time went, it looked
like the assistant was "stuck." Adding per-stage timing logs revealed the truth:
the LLM stage dominated, and it was paying a full model-load cost on every
single turn.

---

## What we changed

Two changes, both about keeping the model **warm** (resident in memory) instead
of letting it reload from disk on each request.

### 1. `keep_alive` on every Ollama request

```python
payload = {..., "keep_alive": settings.keep_alive}   # e.g. "30m"
```

Tells Ollama to keep the model loaded in RAM for 30 minutes between requests.
Without this, Ollama can evict the model after each turn, forcing a fresh
(expensive) load every time.

### 2. Warm-up call at startup

```python
def warm_up():
    _chat([{"role": "user", "content": "hi"}])  # trivial request, loads model
```

Sends one throwaway request when the assistant boots, so the one-time model load
happens *during startup* rather than during the user's first real command.

Neither change speeds up the model's actual computation. They remove **redundant
repeated loading** — which was the bulk of the old delay. It's an amortization
win, not a faster core operation.

---

## Measured results

### LLM stage: before vs. after

| | Old version (cold every turn) | New version (warm) |
|---|---|---|
| First turn | ~59–65 s | ~65 s (unavoidable one-time load) |
| Every turn after the first | **59–77 s** | **4–11 s** |

The decisive comparison is turn 2 onward, from one real session:

| Turn | LLM time | State |
|------|----------|-------|
| 1 — "How are you?"            | 65.58 s | cold (first load) |
| 2 — "I want to learn"        | 6.14 s  | warm |
| 3 — float question           | 11.42 s | warm |
| "goodbye"                    | 3.61 s  | warm |
| "thanks, have a good day"    | 4.98 s  | warm |
| "thanks, quit"               | 3.91 s  | warm |

### Reduction, in seconds and percent

Comparing the old per-turn cost to a warmed turn:

- **Turn 2 specifically:** 65.58 s → 6.14 s = **59.44 s saved, a 90.6% reduction**
- **Typical warm turn:** ~65 s → ~5–11 s = **~54–60 s saved, ~85–90% reduction**

So on the LLM stage — which was the bottleneck — we cut roughly **85–90%** of
the time off every turn after the first.

---

## Full-turn breakdown (warmed state)

Once the LLM was fast, the remaining stages became visible:

| Stage | Time (warm) | Notes |
|-------|-------------|-------|
| Record utterance | 2–3 s | waits for you to stop speaking |
| Transcribe (whisper) | 1.5–4 s | `base.en` model on CPU |
| **LLM (warm)** | **4–11 s** | was the bottleneck, now tamed |
| **Speak (Piper)** | **6–23 s** | now the biggest cost on *long* replies |

End-to-end, a warmed turn dropped from roughly **~75 s** to **~15–25 s**.

---

## What's still slow, and why

- **Piper (TTS)** is now the top cost on long replies (hit 22.85 s once). It
  scales with reply *length*, so shorter replies + a lighter voice
  (`en_US-amy-low` vs `medium`) will cut it. **This is the next thing to fix.**
- **Raw LLM inference speed** (~5 tokens/sec) is a **hardware ceiling**. No
  software change beats CPU-only inference at this rate. A GPU turns a ~5 s warm
  turn into ~1–2 s with zero code changes.
- **The first turn** will always pay a one-time load cost. Warm-up moves it to
  startup, but something must load the model once.

---

## Key terms

- **Cold** — model not yet in memory; the request must load ~2 GB from disk and
  set up compute structures before answering. Expensive (the ~65 s first turn).
- **Warm** — model already resident in RAM; the request skips loading and goes
  straight to answering. Cheap (the ~5 s turns).

The whole optimization in one sentence: **pay the cold-start cost once, then
stay warm.**

---

## Config knobs (in `.env`)

| key | default | effect |
|-----|---------|--------|
| `OLLAMA_KEEP_ALIVE` | `30m` | how long the model stays warm between requests |
| `ECHO_MODEL` | `qwen2.5:3b` | smaller model = faster, less accurate |
| `ECHO_MAX_TOOL_ROUNDS` | `4` | fewer rounds = fewer LLM calls per turn |
