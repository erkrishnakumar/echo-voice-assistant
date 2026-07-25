"""
Voice entry point and component tester.

    python voice.py                # full assistant: wake word -> talk
    python voice.py --test-tts     # just speak a line (checks Piper + speakers)
    python voice.py --test-stt     # record 5s, transcribe (checks mic + Whisper)
    python voice.py --test-wake    # listen and print when the wake word fires
    python voice.py --test-mic     # record one utterance, save, report size

Test each component ALONE before running the full loop — on slower hardware,
debugging the whole chain at once is painful. Build confidence piece by piece.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def _cfg():
    from echo.voice.config import load_voice_settings
    return load_voice_settings()


def test_tts():
    from echo.voice.tts import PiperTTS
    print("Speaking a test line…")
    PiperTTS(_cfg()).speak("Hello, this is Echo. Text to speech is working.")
    print("Done. Did you hear it?")


def test_stt():
    from echo.voice.mic import Microphone
    from echo.voice.stt import WhisperSTT
    cfg = _cfg()
    print("Recording — speak now (stops after you go quiet)…")
    wav = Microphone(cfg).record_utterance()
    print("Transcribing…")
    text = WhisperSTT(cfg).transcribe(wav)
    Path(wav).unlink(missing_ok=True)
    print(f"Heard: {text!r}")


def test_wake():
    from echo.voice.mic import Microphone
    from echo.voice.wake import WakeWord
    cfg = _cfg()
    mic, wake = Microphone(cfg), WakeWord(cfg)
    print(f"Listening for '{wake.key}' — say it. Ctrl-C to stop.")
    for frame in mic.frames():
        if wake.triggered(frame):
            wake.reset()
            print("  • wake word detected!")


def test_mic():
    from echo.voice.mic import Microphone
    print("Recording one utterance — speak, then pause…")
    wav = Microphone(_cfg()).record_utterance()
    size = Path(wav).stat().st_size
    print(f"Saved {wav} ({size} bytes). Non-trivial size means audio captured.")


def main():
    if "--test-tts" in sys.argv:
        test_tts()
    elif "--test-stt" in sys.argv:
        test_stt()
    elif "--test-wake" in sys.argv:
        test_wake()
    elif "--test-mic" in sys.argv:
        test_mic()
    else:
        from echo.voice.assistant import VoiceAssistant
        VoiceAssistant().run()


if __name__ == "__main__":
    main()