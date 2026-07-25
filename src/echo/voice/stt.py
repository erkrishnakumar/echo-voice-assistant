"""
Speech-to-text via the whisper.cpp binary.

We shell out to the compiled `whisper.cpp` executable rather than binding a
Python library — this matches the "use the real binaries" choice and keeps the
heavy C++ work out of the Python process. The binary reads a 16kHz mono WAV and
writes a transcript; we capture and clean it.

Swappable: anything that implements `transcribe(wav_path) -> str` can replace
this. That's the whole interface.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from echo.voice.config import VoiceSettings


class WhisperSTT:
    def __init__(self, cfg: VoiceSettings):
        self.cfg = cfg
        if not cfg.whisper_bin.exists():
            raise FileNotFoundError(
                f"whisper binary not found at {cfg.whisper_bin}. "
                "Run the voice setup (see docs/voice-setup.md)."
            )
        if not cfg.whisper_model.exists():
            raise FileNotFoundError(
                f"whisper model not found at {cfg.whisper_model}."
            )

    def transcribe(self, wav_path: Path) -> str:
        """Run whisper.cpp on a WAV file and return the cleaned transcript."""
        with tempfile.TemporaryDirectory() as tmp:
            out_prefix = Path(tmp) / "out"
            cmd = [
                str(self.cfg.whisper_bin),
                "-m", str(self.cfg.whisper_model),
                "-f", str(wav_path),
                "-otxt",                 # write plain-text output
                "-of", str(out_prefix),  # output file prefix
                "-nt",                   # no timestamps
                "-l", "en",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"whisper.cpp failed: {proc.stderr.strip()}")
            txt_file = out_prefix.with_suffix(".txt")
            if not txt_file.exists():
                # some builds print to stdout instead of a file
                return proc.stdout.strip()
            return txt_file.read_text(encoding="utf-8").strip()