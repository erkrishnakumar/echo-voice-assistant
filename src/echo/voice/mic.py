"""
Microphone capture.

Two responsibilities:
1. Stream raw audio frames (for the wake-word detector to consume continuously).
2. Record a single utterance — start capturing, stop when the speaker goes quiet
   (voice-activity detection by simple RMS energy) — and save it as a 16kHz mono
   WAV for whisper.cpp.

Simple energy-based VAD is enough here and adds no heavy dependencies. It can be
swapped for webrtcvad later if you want tighter turn-taking.
"""

from __future__ import annotations

import tempfile
import wave
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import sounddevice as sd

from echo.voice.config import VoiceSettings

FRAME_MS = 30  # audio frame length fed to wake word / VAD


class Microphone:
    def __init__(self, cfg: VoiceSettings):
        self.cfg = cfg
        self.frame_len = int(cfg.sample_rate * FRAME_MS / 1000)

    def frames(self) -> Iterator[np.ndarray]:
        """Yield fixed-length int16 frames forever (for wake-word listening)."""
        with sd.InputStream(
            samplerate=self.cfg.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_len,
        ) as stream:
            while True:
                data, _ = stream.read(self.frame_len)
                yield data.reshape(-1)

    def record_utterance(self) -> Path:
        """
        Record until the speaker stops (silence_seconds of quiet), or until
        max_utterance_seconds. Returns a path to a 16kHz mono WAV.
        """
        cfg = self.cfg
        silence_frames_needed = int(cfg.silence_seconds * 1000 / FRAME_MS)
        max_frames = int(cfg.max_utterance_seconds * 1000 / FRAME_MS)

        collected: list[np.ndarray] = []
        silent_run = 0
        started = False

        with sd.InputStream(
            samplerate=cfg.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_len,
        ) as stream:
            for _ in range(max_frames):
                data, _ = stream.read(self.frame_len)
                frame = data.reshape(-1)
                rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))

                if rms >= cfg.silence_threshold:
                    started = True
                    silent_run = 0
                    collected.append(frame)
                elif started:
                    silent_run += 1
                    collected.append(frame)
                    if silent_run >= silence_frames_needed:
                        break
                # if not started yet, ignore leading silence

        return _save_wav(collected, cfg.sample_rate)

    def listen_for_utterance(self, start_timeout: float) -> Path | None:
        """
        Like record_utterance, but returns None if the speaker doesn't begin
        talking within `start_timeout` seconds. Used by conversation mode to
        decide when to stop waiting and go back to the wake word.
        """
        cfg = self.cfg
        silence_frames_needed = int(cfg.silence_seconds * 1000 / FRAME_MS)
        max_frames = int(cfg.max_utterance_seconds * 1000 / FRAME_MS)
        start_deadline_frames = int(start_timeout * 1000 / FRAME_MS)

        collected: list[np.ndarray] = []
        silent_run = 0
        started = False
        waited = 0

        with sd.InputStream(
            samplerate=cfg.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_len,
        ) as stream:
            for _ in range(max_frames):
                data, _ = stream.read(self.frame_len)
                frame = data.reshape(-1)
                rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))

                if rms >= cfg.silence_threshold:
                    started = True
                    silent_run = 0
                    collected.append(frame)
                elif started:
                    silent_run += 1
                    collected.append(frame)
                    if silent_run >= silence_frames_needed:
                        break
                else:
                    waited += 1
                    if waited >= start_deadline_frames:
                        return None  # nobody spoke in time

        return _save_wav(collected, cfg.sample_rate)


def _save_wav(frames: list[np.ndarray], rate: int) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    audio = np.concatenate(frames) if frames else np.zeros(0, dtype=np.int16)
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(rate)
        wf.writeframes(audio.tobytes())
    return Path(tmp.name)