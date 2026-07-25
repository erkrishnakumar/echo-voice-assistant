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
        if not text.strip():
            return
        wav_path = self.synthesize(text)
        try:
            _play_wav(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)


def _play_wav(path: Path) -> None:
    """Play a WAV using sounddevice (cross-platform, no external player)."""
    import numpy as np
    import sounddevice as sd

    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16)
    sd.play(audio, rate)
    sd.wait()