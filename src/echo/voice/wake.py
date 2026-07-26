"""
Wake-word detection via openWakeWord.

openWakeWord ships pretrained models (e.g. "hey_jarvis", "alexa"). It runs on
CPU via ONNX and is fully offline. We feed it audio frames and it returns a
confidence score per model; crossing the threshold means "wake".

There is no open pretrained "hey echo" model, so the default is "hey_jarvis"
(fitting for this project). To use a literal "Hey Echo", you'd train a custom
model with openWakeWord's tooling — noted in docs/voice-setup.md.

Swappable: implement `triggered(frame) -> bool` and you can drop in Porcupine
or anything else.
"""

from __future__ import annotations

import numpy as np

from echo.voice.config import VoiceSettings


class WakeWord:
    def __init__(self, cfg: VoiceSettings):
        self.cfg = cfg
        # imported lazily so the rest of the app doesn't need openwakeword
        from openwakeword.model import Model

        # onnx runtime (tflite has no Windows build)
        self.model = Model(
            wakeword_models=[cfg.wake_word], inference_framework="onnx"
        )
        self.threshold = cfg.wake_threshold
        self.key = cfg.wake_word
        # periodically reset internal buffers while idle so they don't grow
        # unbounded during long listening (guards against MemoryError on
        # low-RAM machines)
        self._frames_since_reset = 0
        self._reset_every = 1500  # ~45s at 30ms frames

    def triggered(self, frame: np.ndarray) -> bool:
        """Feed one int16 frame; return True if the wake word just fired."""
        scores = self.model.predict(frame)
        score = scores.get(self.key, 0.0)

        self._frames_since_reset += 1
        if self._frames_since_reset >= self._reset_every and score < 0.3:
            # only reset when clearly not mid-detection
            self.model.reset()
            self._frames_since_reset = 0

        return score >= self.threshold

    def reset(self) -> None:
        """Clear internal buffers after a trigger so it doesn't re-fire."""
        self.model.reset()
        self._frames_since_reset = 0