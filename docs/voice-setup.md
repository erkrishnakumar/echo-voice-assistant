# Voice setup (Windows)

Echo's voice layer uses three offline engines. Two are **binaries** you download
(whisper.cpp, Piper); one is a **pip package** (openWakeWord). This guide gets
them into place. Do it once.

Target layout when you're done:

    echo/
    └── bin/
        ├── whisper/
        │   └── main.exe               (or whisper-cli.exe — see step 1)
        ├── piper/
        │   ├── piper.exe
        │   └── (piper's dll/espeak files)
        └── models/
            ├── ggml-base.en.bin       whisper model
            ├── en_US-amy-medium.onnx  piper voice
            └── en_US-amy-medium.onnx.json

---

## 0. Install the pip packages

With your venv active:

    pip install -r requirements.txt

This pulls `numpy`, `sounddevice`, and `openwakeword`. The first time you run a
wake-word test, openWakeWord downloads its small pretrained models automatically.

> If `sounddevice` errors about PortAudio on Windows, it usually still works via
> the bundled binary. If not: `pip install sounddevice --force-reinstall`.

---

## 1. whisper.cpp (speech-to-text)

1. Go to the whisper.cpp releases page on GitHub:
   https://github.com/ggerganov/whisper.cpp/releases
2. Download the prebuilt **Windows** zip (look for a `whisper-bin-x64` asset).
3. Unzip it into `echo/bin/whisper/`.
4. Check the executable name inside. Newer builds call it `whisper-cli.exe`,
   older ones `main.exe`. Set `WHISPER_BIN` in `.env` to match, e.g.:

       WHISPER_BIN=bin/whisper/whisper-cli.exe

5. Download a model (start small for speed on CPU):
   https://huggingface.co/ggerganov/whisper.cpp/tree/main
   Grab `ggml-base.en.bin` (~140MB) and put it in `echo/bin/models/`.

   > On your hardware, `base.en` is the sweet spot. `tiny.en` is faster but less
   > accurate; `small.en` is more accurate but slower. Change `WHISPER_MODEL`
   > in `.env` if you switch.

**Test it:**

    python voice.py --test-stt

Speak when prompted; it should print what you said.

---

## 2. Piper (text-to-speech)

1. Go to Piper releases:
   https://github.com/rhasspy/piper/releases
2. Download the **Windows** zip (`piper_windows_amd64.zip`).
3. Unzip into `echo/bin/piper/` (keep all the `.dll` and espeak-ng data files
   alongside `piper.exe` — it won't run without them).
4. Download a voice from:
   https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US
   Grab both files for a voice, e.g. `en_US-amy-medium.onnx` and
   `en_US-amy-medium.onnx.json`, into `echo/bin/models/`.

**Test it:**

    python voice.py --test-tts

You should hear Echo speak a test line.

---

## 3. Wake word (openWakeWord)

Already installed via pip. The default wake word is `hey_jarvis` (a pretrained
model that ships with openWakeWord). Other built-ins include `alexa` and
`hey_mycroft`.

**Test it:**

    python voice.py --test-wake

Say "hey jarvis" — it should print when it fires. Adjust `WAKE_THRESHOLD` in
`.env` (0.3 = more sensitive, 0.7 = stricter) if it triggers too easily or not
enough.

> **"Hey Echo" specifically:** there's no pretrained "hey echo" model. To get a
> literal "Hey Echo", train a custom model with openWakeWord's training notebook
> (produces a `.onnx` you point `WAKE_WORD` at). Until then, `hey_jarvis` is a
> fitting stand-in for a Jarvis-style assistant.

---

## 4. Run the full assistant

Make sure Postgres and Ollama are up (`docker compose up -d`) and a model is
pulled, then:

    python voice.py

Say the wake word, wait for "Yes?", then speak your command. Echo transcribes it,
runs the agent, and speaks the reply.

---

## Testing order (important)

On CPU-only hardware, test each piece alone before the full loop — it's far
easier to debug:

    python voice.py --test-tts     # 1. can it speak?
    python voice.py --test-mic     # 2. can it hear? (saves a WAV)
    python voice.py --test-stt     # 3. can it transcribe?
    python voice.py --test-wake    # 4. does the wake word fire?
    python voice.py                # 5. everything together

If a step fails, you know exactly which component to fix.

---

## Latency expectations

On a CPU-only laptop, each turn has real delay: recording + whisper
transcription (~1-3s) + LLM (~several seconds at ~5 tok/s) + Piper synthesis
(~1s). Expect 5-15 seconds from finishing your sentence to hearing a reply. This
is a hardware ceiling, not a bug — it drops sharply on a machine with a GPU.

To keep it as fast as possible on this machine: use `ggml-tiny.en` or
`ggml-base.en` for whisper, and `qwen2.5:3b` for the LLM.
