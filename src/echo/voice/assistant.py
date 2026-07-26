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
        log.info("voice components ready")

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
                self._respond(text)

        # subsequent turns: keep listening WITHOUT the wake word until silence
        while True:
            log.info("listening for your next command… (stay quiet to end)")
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
            self._respond(text)

    def run(self) -> None:
        init_db()
        with timed(log, "warm up model"):
            from echo.agent import warm_up
            warm_up()
        log.info(f"say the wake word ('{self.wake.key}') once to start talking.")
        log.info("Ctrl-C to quit.")
        while True:  # outer loop: a crash in one conversation won't kill Echo
            try:
                for frame in self.mic.frames():
                    if self.wake.triggered(frame):
                        self.wake.reset()
                        log.info("• wake word detected — entering conversation")
                        self._safe_speak(self._greeting())
                        self._conversation()
                        log.info("conversation ended; say the wake word again\n")
            except KeyboardInterrupt:
                log.info("goodbye")
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


def main() -> None:
    VoiceAssistant().run()


if __name__ == "__main__":
    main()