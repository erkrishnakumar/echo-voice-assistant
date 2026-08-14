"""
Gesture wake trigger — webcam + MediaPipe hand tracking, no video shown.

Watches for an open palm (all fingers extended toward the camera) held up
for a moment and fires a callback, same role as the wake word but visual.
The camera feed is only ever read with cv2.VideoCapture — never displayed
(no cv2.imshow window) — per the requirement that the tracking itself stays
invisible; only the orb animation is shown.

MediaPipe's Tasks API (the only hand-tracking API this package version
ships) needs a model bundle file that isn't included in the pip package.
Download it once:

    https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task

and save it as `models/hand_landmarker.task` under the project root (or set
GESTURE_MODEL_PATH to wherever you put it).

Runs its own loop in `run()`; call it from a background thread.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[3] / "models" / "hand_landmarker.task"

# fingertip landmark ids and their corresponding knuckle (MCP) ids — same
# 21-point hand layout MediaPipe has always used, legacy or Tasks API
_TIP_IDS = [8, 12, 16, 20]
_MCP_IDS = [5, 9, 13, 17]


def _is_open_palm(landmarks) -> bool:
    extended = 0
    for tip, mcp in zip(_TIP_IDS, _MCP_IDS):
        if landmarks[tip].y < landmarks[mcp].y:
            extended += 1
    return extended >= 4  # allow one finger of slack for detection noise


class GestureDetector:
    def __init__(self, on_gesture, camera_index: int = 0, cooldown: float = 3.0,
                 hold_seconds: float = 0.4, model_path: str | Path | None = None,
                 scan_interval: float = 0.15):
        self.on_gesture = on_gesture
        self.camera_index = camera_index
        self.cooldown = cooldown
        self.hold_seconds = hold_seconds
        # minimum seconds between detection passes. MediaPipe's inference
        # call holds the GIL for the duration of each pass, which starves
        # other threads (e.g. the wake-word listener) if run on every camera
        # frame (~30/s). A held-gesture only needs a few checks per second.
        self.scan_interval = scan_interval
        self.model_path = Path(
            model_path or os.environ.get("GESTURE_MODEL_PATH") or DEFAULT_MODEL_PATH
        )
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        """Blocking capture loop. Call from a background thread."""
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"hand-tracking model not found at {self.model_path}. Download "
                "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
                "hand_landmarker/float16/latest/hand_landmarker.task and save it "
                "there (or set GESTURE_MODEL_PATH)."
            )

        import cv2
        from mediapipe import Image, ImageFormat
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import (
            HandLandmarker, HandLandmarkerOptions, RunningMode,
        )

        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(self.model_path)),
            running_mode=RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        )

        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            raise RuntimeError(f"could not open camera index {self.camera_index}")

        self._running = True
        last_trigger = 0.0
        palm_since: float | None = None
        start = time.monotonic()
        last_timestamp_ms = -1
        last_scan = 0.0

        try:
            with HandLandmarker.create_from_options(options) as landmarker:
                while self._running:
                    ok, frame = cap.read()
                    if not ok:
                        time.sleep(0.05)
                        continue

                    # drain the camera at full rate, but only run the (GIL-
                    # heavy) detection pass at scan_interval
                    since_last_scan = time.monotonic() - last_scan
                    if since_last_scan < self.scan_interval:
                        time.sleep(max(0.0, self.scan_interval - since_last_scan))
                        continue
                    last_scan = time.monotonic()

                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    mp_image = Image(image_format=ImageFormat.SRGB, data=rgb)
                    # detect_for_video requires strictly increasing timestamps;
                    # consecutive frames can round to the same millisecond, so
                    # force it forward instead of trusting the wall clock.
                    timestamp_ms = int((time.monotonic() - start) * 1000)
                    if timestamp_ms <= last_timestamp_ms:
                        timestamp_ms = last_timestamp_ms + 1
                    last_timestamp_ms = timestamp_ms
                    result = landmarker.detect_for_video(mp_image, timestamp_ms)

                    now = time.time()
                    palm_visible = any(
                        _is_open_palm(hand) for hand in result.hand_landmarks
                    )

                    if not palm_visible:
                        palm_since = None
                        continue

                    if palm_since is None:
                        palm_since = now

                    held_long_enough = (now - palm_since) >= self.hold_seconds
                    off_cooldown = (now - last_trigger) >= self.cooldown
                    if held_long_enough and off_cooldown:
                        last_trigger = now
                        palm_since = None
                        self.on_gesture()
        finally:
            cap.release()
