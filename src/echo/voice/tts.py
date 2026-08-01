"""
Text-to-speech via the Piper binary.

Piper reads text on stdin and writes a WAV. We then play it. Like the STT side,
this is a thin wrapper so the engine is swappable — anything implementing
`speak(text) -> None` works.
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from pathlib import Path

from echo.voice.config import VoiceSettings


class PiperTTS:
    def __init__(self, cfg: VoiceSettings):
        self.cfg = cfg
        if not cfg.piper_bin.exists():
            raise FileNotFoundError(
                f"piper binary not found at {cfg.piper_bin}. "
                "Run the voice setup (see docs/voice-setup.md)."
            )
        if not cfg.piper_model.exists():
            raise FileNotFoundError(f"piper model not found at {cfg.piper_model}.")

    def synthesize(self, text: str) -> Path:
        """Generate a WAV file from text and return its path (caller deletes)."""
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        cmd = [
            str(self.cfg.piper_bin),
            "-m", str(self.cfg.piper_model),
            "-f", tmp.name,
        ]
        proc = subprocess.run(
            cmd, input=text.encode("utf-8"), capture_output=True
        )
        if proc.returncode != 0:
            raise RuntimeError(f"piper failed: {proc.stderr.decode().strip()}")
        return Path(tmp.name)

    def speak(self, text: str) -> None:
        """Synthesize and play through the default output device."""
    def speak(self, text: str, should_stop=None) -> bool:
        """
        Synthesize and play through the default output device.

        If `should_stop` is provided, it's called periodically during playback;
        when it returns True, playback stops early (barge-in). Returns True if
        it was interrupted, False if it finished normally.
        """
        if not text.strip():
            return False
        wav_path = self.synthesize(text)
        try:
            return _play_wav(wav_path, should_stop)
        finally:
            wav_path.unlink(missing_ok=True)


def _play_wav(path: Path, should_stop=None) -> bool:
    """
    Play a WAV using sounddevice. If `should_stop()` returns True mid-playback,
    stop immediately. Returns True if interrupted, else False.
    """
    import time

    import numpy as np
    import sounddevice as sd

    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16)

    sd.play(audio, rate)
    if should_stop is None:
        sd.wait()
        return False

    # poll for a stop signal while audio plays
    while sd.get_stream().active:
        if should_stop():
            sd.stop()
            return True
        time.sleep(0.05)
    return False