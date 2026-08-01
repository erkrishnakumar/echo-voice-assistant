# Echo — Troubleshooting Journey & Fixes

A complete record of the problems hit while getting Echo's voice assistant
working reliably, and how each was solved. Written so the reasoning — not just
the fix — is preserved.

**Hardware:** Intel i5-1335U, 16 GB RAM, CPU-only (Intel Iris Xe, no discrete GPU),
Windows 11 + Docker Desktop (WSL2).

---

## The single biggest lesson

Almost every "mysterious" failure traced back to **one root cause: memory**.
And the memory limit itself was a *misconfiguration*, not the hardware:

- The machine has **16 GB RAM**.
- But **Docker (via WSL2) was capped at ~8 GB** by the WSL2 default (50% of RAM).
- Ollama's logs reported `total="7.6 GiB"` — this was the *Docker* limit, not
  the machine's real capacity.
- Running the 3B model (~2 GB) plus the voice stack inside that 8 GB box tipped
  it over, causing crashes and forcing a switch to a weaker 1B model that
  hallucinated.

**The fix that unlocked everything:** raising the WSL2 memory cap (see below).

---

## Problems and fixes, in order

### 1. PostgreSQL "password authentication failed"

**Symptom:** every DB connection failed with `password authentication failed
for user "echo"`, even though credentials were correct.

**Root cause:** two native Windows PostgreSQL services (v17, v18) were already
listening on ports 5432/5433 and intercepting connections meant for the Docker
container. The app was talking to the wrong Postgres.

**Fix:** stopped the native services (elevated PowerShell), used `127.0.0.1`
(not `localhost`) to avoid IPv6 ambiguity, and confirmed via an inside-container
vs. from-host connection test. Full write-up in
`docs/postmortem-postgres-auth.md`.

---

### 2. Ollama 404 on `/api/chat`

**Symptom:** `404 Not Found` when the agent called Ollama.

**Root cause:** `docker compose down -v` had wiped the `ollama-models` volume
(the `-v` deletes *all* named volumes), removing the pulled model.

**Fix:** re-pull the model into the container:
`docker compose exec ollama ollama pull <model>`.

---

### 3. Per-turn latency of 60–77 seconds

**Symptom:** every voice turn took over a minute; looked "stuck".

**Root cause:** the model was reloading from disk on *every* request (cold start
each time).

**Fix:** two changes in `agent.py` —
- `keep_alive: "30m"` on each request → model stays resident in RAM.
- a `warm_up()` call at startup → the load happens once, during boot.

**Result:** warm turns dropped from ~65 s to ~4–11 s — an ~85–90 % reduction.
Full numbers in `docs/latency-optimization.md`.

---

### 4. MemoryError in openWakeWord / whisper buffer allocation

**Symptom:** `MemoryError` during wake-word processing;
`failed to allocate buffer` in whisper; app recovered (thanks to error handling)
but couldn't transcribe.

**Root cause:** the 8 GB Docker cap + everything else exhausted RAM.

**Fixes (layered):**
- **whisper low-memory mode** (`stt.py`): added `-ng` (no GPU — the log showed it
  uselessly trying `use gpu = 1` on a GPU-less machine), `-bs 1` (greedy decode
  instead of memory-hungry beam search), `-t 4` (limit threads).
- **openWakeWord periodic buffer reset** (`wake.py`): reset internal buffers
  every ~45 s while idle so they don't grow unbounded.
- **explicit MemoryError handling** (`assistant.py`): catch it, run `gc.collect()`,
  pause, and recover instead of crashing.

---

### 5. THE key fix — WSL2 memory cap

**Symptom:** everything was memory-starved despite the machine having 16 GB.

**Diagnosis:** Task Manager showed 16 GB total, but Docker only saw 7.6 GB.
WSL2's default is to use only ~50 % of system RAM.

**Fix:** created `C:\Users\<user>\.wslconfig`:
```
[wsl2]
memory=9GB
processors=6
```
Then `wsl --shutdown` and restarted Docker Desktop.

**Result:** Docker's limit rose to 9 GB — enough to run the **accurate 3B model**
(`qwen2.5:3b`) alongside the voice stack. This ended the crashes AND let us drop
the weak 1B model. The 3B correctly uses tool results (e.g. actually reports the
time from `get_current_time`), which the 1B kept hallucinating.

> Note: McAfee (`ServiceShell` + `mc-fw-host`) was found using ~3.3 GB baseline —
> a permanent tax worth knowing about on this machine.

---

### 6. 1B model ignoring tool results / speaking raw JSON

**Symptom:** with the 1B model, asking the time returned
`{"name": "get_time_now", "parameters": {...}}` spoken aloud, or a wrong "date
not available" even though the tool returned the correct date.

**Root cause:** the 1B model is too weak — it emitted tool-calls as plain text
and ignored tool outputs.

**Fixes:**
- Added a real `get_current_time` tool (asking the time is basic).
- Added a **JSON-leak guard** in `agent.py`: if a reply is raw tool-call JSON,
  replace it with a graceful fallback instead of speaking gibberish.
- Ultimately, switching back to the **3B model** (via fix #5) removed the root
  cause.

---

### 7. Conversation ending while the user was still speaking

**Symptom:** it went back to sleep mid-conversation.

**Root causes (two, found in sequence):**
1. The empty-audio guard used *average* RMS, which the surrounding silence
   dragged below threshold — so real speech was wrongly discarded.
2. A single noisy frame falsely triggered "speech started", then brief quiet
   ended the capture immediately.

**Fixes (`mic.py`):**
- Judge captures by **peak / count of loud frames**, not average.
- Require a short run of **consecutive** loud frames (~0.09 s) to confirm real
  speech onset — ignores stray blips.
- Raised `VOICE_CONVERSATION_TIMEOUT` to 30 s for a more natural pace.

**Confirmed via a mic-level meter** (`voice.py --mic-level`): the user's speech
reads ~2000 RMS vs. a 400 threshold — 5× headroom, so detection was never the
issue; timing was.

---

### 8. Wake word "not listening" after a crash

**Symptom:** full app froze right after "say the wake word", no detection —
yet `--test-wake` worked fine and showed a clean 0.564 score.

**Root cause:** a previously crashed/force-killed run left the **microphone
device locked**, so the next run's audio stream couldn't open.

**Fix:** kill stray Python processes and start fresh:
```
Get-Process python | Stop-Process -Force
```
then a new terminal. The mic released, and the full app worked perfectly.

**Lesson:** on Windows, always fully close a crashed voice run before restarting —
a zombie process can hold the audio device.

---

## Whisper hallucination filtering (ongoing quality)

Whisper emits phantom words ("you", "thank you", "thanks for watching") on
silence. `assistant.py` filters a known set of these, and `stt.py` strips
bracketed markers like `[BLANK_AUDIO]`.

---

## Diagnostic tools added (in `voice.py`)

| command | what it does |
|---------|--------------|
| `python voice.py --test-tts`   | speak a line (checks Piper + speakers) |
| `python voice.py --test-stt`   | record + transcribe (checks mic + whisper) |
| `python voice.py --test-wake`  | live wake-word scores (checks detection) |
| `python voice.py --test-mic`   | record one utterance, report capture |
| `python voice.py --mic-level`  | live RMS meter to tune the threshold |

These made every problem *measurable* instead of guesswork — the single most
useful investment during debugging.

---

## The stable, working configuration

- **Model:** `qwen2.5:3b` (accurate, follows tool results)
- **Docker RAM:** 9 GB via `.wslconfig`
- **whisper:** `ggml-base.en`, low-memory flags (`-ng -bs 1 -t 4`)
- **Voice tuning:** threshold 400, silence 2.2 s, conversation timeout 30 s
- **keep_alive:** 30 m + warm-up at startup
- **Result:** stable multi-turn voice conversations, accurate answers, correct
  tool use (time, reminders), clean wake/sleep cycle.

---

## Standing truths about this hardware

- **CPU-only inference has a floor:** ~5 tokens/sec. Warm 3B turns are ~5–12 s;
  the *first* turn each session pays a one-time load cost. A GPU would cut this
  to ~1–2 s with zero code changes.
- **TTS (Piper) scales with reply length** — long answers take 15–24 s to speak.
  Shorter replies help.
- **16 GB is workable but not roomy** once McAfee (~3 GB) and Windows are
  accounted for. The 9 GB Docker cap is a deliberate balance.
