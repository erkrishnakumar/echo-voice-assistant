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
    import numpy as np
    from echo.voice.mic import Microphone
    from echo.voice.wake import WakeWord
    cfg = _cfg()
    mic, wake = Microphone(cfg), WakeWord(cfg)
    print(f"Listening for '{wake.key}' (threshold {wake.threshold}). Say it. Ctrl-C to stop.")
    print("Showing live scores so we can see detection:\n")
    for frame in mic.frames():
        scores = wake.model.predict(frame)
        # show the max score across all models each time it's non-trivial
        if scores:
            best_key = max(scores, key=scores.get)
            best = scores[best_key]
            if best > 0.05:
                print(f"\r  {best_key}: {best:.3f}          ", end="")
            if best >= wake.threshold:
                print(f"\n  • WAKE WORD DETECTED (key='{best_key}', score={best:.3f})")
                wake.model.reset()


def test_mic():
    from echo.voice.mic import Microphone
    print("Recording one utterance — speak, then pause…")
    wav = Microphone(_cfg()).record_utterance()
    if wav is None:
        print("Nothing captured. Your voice may be below the threshold — "
              "run --mic-level to check.")
        return
    size = Path(wav).stat().st_size
    print(f"Saved {wav} ({size} bytes). Non-trivial size means audio captured.")


def mic_level():
    """Live meter: shows your mic's RMS so you can pick VOICE_SILENCE_THRESHOLD."""
    import numpy as np
    from echo.voice.mic import Microphone
    cfg = _cfg()
    mic = Microphone(cfg)
    print(f"Current VOICE_SILENCE_THRESHOLD = {cfg.silence_threshold}")
    print("Speak normally. Watch the numbers.")
    print("  - SILENCE should read LOW (below your threshold)")
    print("  - SPEECH should read HIGH (above your threshold)")
    print("Set the threshold roughly halfway between. Ctrl-C to stop.\n")
    peak_speech = 0.0
    try:
        for frame in mic.frames():
            rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
            bar = "#" * min(int(rms / 50), 40)
            marker = "  <-- above threshold" if rms >= cfg.silence_threshold else ""
            print(f"\rRMS: {rms:7.0f} {bar}{marker}          ", end="")
    except KeyboardInterrupt:
        print("\n\nDone. If your SPEECH numbers were below the threshold, "
              "lower VOICE_SILENCE_THRESHOLD in .env to match.")


def main():
    if "--test-tts" in sys.argv:
        test_tts()
    elif "--test-stt" in sys.argv:
        test_stt()
    elif "--test-wake" in sys.argv:
        test_wake()
    elif "--test-mic" in sys.argv:
        test_mic()
    elif "--mic-level" in sys.argv:
        mic_level()
    else:
        from echo.voice.assistant import VoiceAssistant
        VoiceAssistant().run()


if __name__ == "__main__":
    main()