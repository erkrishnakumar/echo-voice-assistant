"""
The voice loop — this is what makes Echo "listen and act".

Conversation mode:
    say the wake word ONCE -> Echo enters a conversation -> after each reply it
    keeps listening for your next command without the wake word -> if you stay
    silent for `conversation_timeout` seconds, it goes back to sleep (re-arms
    the wake word). This feels like talking to a person, while still returning
    to a privacy-safe idle state.

Every stage is logged with timing so you can see where the time goes.
"""

from __future__ import annotations

from pathlib import Path

from echo.agent import handle
from echo.db import init_db
from echo.logging_conf import get_logger, setup_logging, timed
from echo.voice.config import load_voice_settings
from echo.voice.mic import Microphone
from echo.voice.stt import WhisperSTT
from echo.voice.tts import PiperTTS
from echo.voice.wake import WakeWord

log = get_logger("echo.voice")


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

    def _transcribe(self, wav: Path) -> str:
        try:
            with timed(log, "transcribe (whisper)"):
                return self.stt.transcribe(wav)
        finally:
            Path(wav).unlink(missing_ok=True)

    def _respond(self, text: str) -> None:
        log.info(f"YOU: {text!r}")
        with timed(log, "agent (llm + tools)"):
            reply = handle(text, self.history)
        log.info(f"ECHO: {reply!r}")
        with timed(log, "speak (piper)"):
            self.tts.speak(reply)

    def _conversation(self) -> None:
        """Stay in a back-and-forth until the user goes quiet."""
        # first turn: user already triggered the wake word, so record directly
        with timed(log, "record utterance"):
            wav = self.mic.record_utterance()
        text = self._transcribe(wav)
        if text.strip():
            self._respond(text)

        # subsequent turns: keep listening WITHOUT the wake word until silence
        while True:
            log.info("listening for your next command… (stay quiet to end)")
            wav = self.mic.listen_for_utterance(
                start_timeout=self.cfg.conversation_timeout
            )
            if wav is None:
                log.info("no follow-up; going back to sleep")
                return
            text = self._transcribe(wav)
            if not text.strip():
                return
            self._respond(text)

    def run(self) -> None:
        init_db()
        with timed(log, "warm up model"):
            from echo.agent import warm_up
            warm_up()
        log.info(f"say the wake word ('{self.wake.key}') once to start talking.")
        log.info("Ctrl-C to quit.")
        try:
            for frame in self.mic.frames():
                if self.wake.triggered(frame):
                    self.wake.reset()
                    log.info("• wake word detected — entering conversation")
                    self.tts.speak("Yes?")
                    self._conversation()
                    log.info("conversation ended; say the wake word to talk again\n")
        except KeyboardInterrupt:
            log.info("goodbye")


def main() -> None:
    VoiceAssistant().run()


if __name__ == "__main__":
    main()