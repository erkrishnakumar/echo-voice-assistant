"""
The voice loop — this is what makes Echo "listen and act".

Conversation mode:
    say the wake word ONCE -> Echo enters a conversation -> after each reply it
    keeps listening for your next command without the wake word -> if you stay
    silent for `conversation_timeout` seconds, it goes back to sleep.

Resilience:
    Every stage (record, transcribe, agent, speak) is wrapped so a failure
    speaks a friendly message, logs full details, and returns to listening
    instead of crashing the whole session. The LLM call itself retries on
    transient failures (see agent._post_with_retry).
"""

from __future__ import annotations

import random
import threading
from pathlib import Path

from echo.agent import AgentError, handle
from echo.db import init_db
from echo.logging_conf import get_logger, setup_logging, timed
from echo.voice.config import load_voice_settings
from echo.voice.mic import Microphone
from echo.voice.stt import WhisperSTT
from echo.voice.tts import PiperTTS
from echo.voice.wake import WakeWord

log = get_logger("echo.voice")

# spoken fallbacks when a stage fails
MSG_STT_FAIL = "Sorry, I had trouble understanding the audio."
MSG_AGENT_FAIL = "Sorry, I'm having trouble reaching my brain right now. Please try again."
MSG_GENERIC_FAIL = "Something went wrong on my end. Let's try that again."

# varied greetings so the wake-word acknowledgement feels natural, not robotic.
# {name} and {title} are filled from config (USER_NAME / USER_TITLE).
GREETING_TEMPLATES = [
    "Hello {name}! Jarvis here. What can I do for you?",
    "At your service, {title}. What do you need?",
    "Hi {name}! I'm listening — how can I help?",
    "Yes, {title}? Jarvis, ready to assist.",
    "Good to hear from you, {name}. What's on your mind?",
    "Welcome back, {name}. How can I help today?",
]

# whisper tends to emit these when fed silence or background noise
_HALLUCINATIONS = {
    "", "you", "thank you", "thanks", "thanks for watching",
    "thank you for watching", "bye", "you're welcome", "so", "uh", "um",
    "please subscribe", "the", "yeah",
}

# saying any of these ends the conversation and returns to the wake word
_SLEEP_PHRASES = {
    "goodbye", "good bye", "bye", "bye bye", "that's all", "thats all",
    "that is all", "thanks that's all", "stop", "go to sleep", "sleep now",
    "nothing else", "that will be all", "see you", "see you later",
    "goodnight", "good night", "dismiss", "quit", "exit",
}

# saying any of these cancels the current request and re-prompts (does NOT sleep)
_CANCEL_PHRASES = {
    "cancel", "never mind", "nevermind", "hang up", "hold on",
    "wait wait", "forget it", "scratch that", "no wait", "stop stop",
}

# formal acknowledgements when the user cancels/corrects
CANCEL_ACKS = [
    "Of course, Sir. Go ahead — what did you mean?",
    "No problem. Go ahead, Krishna.",
    "Sure, Sir. I'm listening — what would you like?",
    "Understood. Go ahead whenever you're ready.",
]

# spoken farewells when the user dismisses Jarvis ({name}/{title} filled in)
FAREWELL_TEMPLATES = [
    "Goodbye, {name}! Just say the wake word when you need me.",
    "Alright {title}, going to sleep. Call me anytime.",
    "See you later, {name}! I'll be here when you need me.",
    "Rest well, {title}. Say the wake word to bring me back.",
]


class VoiceAssistant:
    def __init__(self) -> None:
        setup_logging()
        self.cfg = load_voice_settings()
        log.info("initializing voice components…")
        self.mic = Microphone(self.cfg)
        self.wake = WakeWord(self.cfg)
        self.stt = WhisperSTT(self.cfg)
        self.tts = PiperTTS(self.cfg)
        self.history: list = []
        self.animation = None  # orb window; built in run() if pygame is available
        self.gesture_detector = None  # built in run() if opencv/mediapipe are available
        self.scheduler = None  # reminder scheduler; built in run()
        self._gesture_event = threading.Event()
        self._in_conversation = False
        log.info("voice components ready")

    def _speak_reminder(self, text: str) -> None:
        """Say a fired reminder aloud — but only while idle. Speaking over an
        active turn would talk across the user and bleed TTS into the mic;
        the desktop notification has already delivered it either way."""
        if self._in_conversation:
            log.info("reminder fired mid-conversation; notification only")
            return
        self._safe_speak(f"Reminder: {text}")

    def _start_scheduler(self):
        """Start the reminder scheduler. Returns it, or None if it can't run."""
        try:
            from echo.scheduler import ReminderScheduler
            sched = ReminderScheduler(on_fire=self._speak_reminder)
            sched.start()
            return sched
        except Exception:
            log.exception("reminder scheduler unavailable; reminders will be "
                          "saved but not fire")
            return None

    def _set_animation_state(self, state: str) -> None:
        if self.animation is not None:
            self.animation.set_state(state)

    def _start_animation(self):
        """Build the orb window. Returns it, or None if pygame isn't installed."""
        try:
            from echo.voice.animation import OrbAnimation
            return OrbAnimation()
        except Exception:
            log.warning("orb animation unavailable (pygame not installed?); "
                        "continuing without it")
            return None

    def _start_gesture_detector(self) -> None:
        """Start webcam gesture detection on a background thread, if available.

        A detected open-palm gesture behaves exactly like the wake word — it
        just sets a flag the main loop checks alongside `self.wake.triggered`.
        Never shows the camera feed.
        """
        try:
            from echo.voice.gesture import GestureDetector
        except Exception:
            log.warning("gesture detection unavailable (opencv/mediapipe not "
                        "installed?); continuing with wake word only")
            return

        self.gesture_detector = GestureDetector(on_gesture=self._gesture_event.set)

        def _run():
            try:
                self.gesture_detector.run()
            except Exception:
                if self.gesture_detector._running:
                    # a real failure mid-run; a stop() in progress can still
                    # race a final camera read and land here harmlessly
                    log.exception("gesture detector stopped unexpectedly")

        threading.Thread(target=_run, daemon=True).start()

    def _safe_speak(self, text: str) -> None:
        """Speak, but never let a TTS failure crash the loop."""
        try:
            with timed(log, "speak (piper)"):
                self.tts.speak(text)
        except Exception:
            log.exception("TTS failed; continuing without audio")

    def _transcribe(self, wav: Path) -> str | None:
        """Transcribe; return None on failure (caller handles gracefully)."""
        try:
            with timed(log, "transcribe (whisper)"):
                text = self.stt.transcribe(wav)
        except Exception:
            log.exception("STT failed")
            self._safe_speak(MSG_STT_FAIL)
            return None
        finally:
            Path(wav).unlink(missing_ok=True)

        # whisper commonly hallucinates these on silence/noise — treat as empty
        cleaned = text.strip().lower().strip(".!? ")
        if cleaned in _HALLUCINATIONS:
            log.info(f"ignoring likely silence hallucination: {text!r}")
            return None
        return text

    def _respond(self, text: str) -> None:
        """Run the agent and speak the reply, catching agent/LLM failures."""
        log.info(f"YOU: {text!r}")
        self._set_animation_state("active")
        try:
            with timed(log, "agent (llm + tools)"):
                reply = handle(text, self.history)
        except AgentError:
            log.exception("agent could not reach the model")
            self._safe_speak(MSG_AGENT_FAIL)
            return
        except Exception:
            log.exception("agent failed unexpectedly")
            self._safe_speak(MSG_GENERIC_FAIL)
            return

        log.info(f"ECHO: {reply!r}")
        self._safe_speak(reply)

    def _record(self, listen: bool):
        """Record an utterance. Returns wav path, or None if nobody spoke."""
        try:
            if listen:
                return self.mic.listen_for_utterance(
                    start_timeout=self.cfg.conversation_timeout
                )
            with timed(log, "record utterance"):
                return self.mic.record_utterance()
        except Exception:
            log.exception("microphone capture failed")
            self._safe_speak(MSG_GENERIC_FAIL)
            return None

    def _greeting(self) -> str:
        return random.choice(GREETING_TEMPLATES).format(
            name=self.cfg.user_name, title=self.cfg.user_title
        )

    def _farewell(self) -> str:
        return random.choice(FAREWELL_TEMPLATES).format(
            name=self.cfg.user_name, title=self.cfg.user_title
        )

    def _is_sleep_command(self, text: str) -> bool:
        cleaned = text.strip().lower().strip(".!?,")
        return cleaned in _SLEEP_PHRASES

    def _is_cancel_command(self, text: str) -> bool:
        cleaned = text.strip().lower().strip(".!?,")
        return cleaned in _CANCEL_PHRASES

    def _conversation(self) -> None:
        """Stay in a back-and-forth until the user says goodbye or goes quiet."""
        # first turn: user already triggered the wake word, record directly
        wav = self._record(listen=False)
        if wav is not None:
            text = self._transcribe(wav)
            if text and text.strip():
                if self._is_sleep_command(text):
                    self._safe_speak(self._farewell())
                    return
                if self._is_cancel_command(text):
                    log.info("cancel heard; re-prompting")
                    self._safe_speak(random.choice(CANCEL_ACKS))
                else:
                    self._respond(text)

        # subsequent turns: keep listening WITHOUT the wake word until silence
        while True:
            log.info("listening for your next command… (stay quiet to end)")
            self._set_animation_state("listening")
            wav = self._record(listen=True)
            if wav is None:
                log.info("no follow-up; going back to sleep")
                return
            text = self._transcribe(wav)
            if not text or not text.strip():
                return
            if self._is_sleep_command(text):
                log.info("sleep command heard; going back to sleep")
                self._safe_speak(self._farewell())
                return
            if self._is_cancel_command(text):
                log.info("cancel heard; discarding and re-prompting")
                self._safe_speak(random.choice(CANCEL_ACKS))
                continue  # discard, listen fresh
            self._respond(text)

    def _shutdown(self) -> None:
        """Stop the background workers. Safe to call more than once."""
        for name in ("animation", "gesture_detector", "scheduler"):
            worker = getattr(self, name, None)
            if worker is not None:
                try:
                    worker.stop()
                except Exception:
                    log.exception(f"could not stop {name} cleanly")

    def _wake_loop(self) -> None:
        """Wake word + gesture -> conversation. Runs on a background thread
        when the orb animation owns the main thread; runs inline otherwise."""
        while True:  # outer loop: a crash in one conversation won't kill Echo
            try:
                for frame in self.mic.frames():
                    if self.wake.triggered(frame) or self._gesture_event.is_set():
                        self.wake.reset()
                        self._gesture_event.clear()
                        log.info("• wake word detected — entering conversation")
                        self._set_animation_state("listening")
                        self._in_conversation = True
                        try:
                            self._safe_speak(self._greeting())
                            self._conversation()
                        finally:
                            self._in_conversation = False
                        self._set_animation_state("idle")
                        log.info("conversation ended; say the wake word again\n")
            except KeyboardInterrupt:
                log.info("goodbye")
                self._shutdown()
                return
            except MemoryError:
                log.error(
                    "out of memory — the machine is low on RAM. Recovering. "
                    "Consider closing other apps or using a smaller LLM "
                    "(ECHO_MODEL=llama3.2:1b)."
                )
                import gc
                import time
                try:
                    self.wake.reset()
                except Exception:
                    pass
                gc.collect()
                time.sleep(2)  # let the OS reclaim memory before resuming
            except Exception:
                log.exception("unexpected error in main loop; recovering")
                import time
                time.sleep(0.5)  # brief pause to avoid a tight crash loop

    def run(self) -> None:
        init_db()
        with timed(log, "warm up model"):
            from echo.agent import warm_up
            warm_up()

        self.animation = self._start_animation()
        self._start_gesture_detector()
        self.scheduler = self._start_scheduler()

        log.info(f"say the wake word ('{self.wake.key}') or show an open palm "
                  "to the camera to start talking.")
        log.info("Ctrl-C to quit.")

        if self.animation is None:
            # no window to own the main thread — run the wake loop directly
            self._wake_loop()
            return

        # pygame's event loop must run on the main thread, so the wake/gesture
        # loop moves to a background thread and the orb owns this one.
        wake_thread = threading.Thread(target=self._wake_loop, daemon=True)
        wake_thread.start()
        try:
            self.animation.run()  # blocks until the window is closed
        except KeyboardInterrupt:
            log.info("goodbye")
        self._shutdown()


def main() -> None:
    VoiceAssistant().run()


if __name__ == "__main__":
    main()