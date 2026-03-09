"""
channels/voice_channel.py — JARVIS Voice Interface (Phase 7)

Listens via microphone → Whisper STT → JARVIS → pyttsx3 TTS response.
Run: python main.py --voice

Wake word: "JARVIS" (configurable, 500ms cooldown after response)

Requires:
  pip install openai-whisper pyttsx3 pyaudio sounddevice numpy

Advanced:
  pip install faster-whisper  (5× faster, recommended)
"""

import io
import logging
import queue
import threading
import time
from typing import Optional

from channels.base_channel import BaseChannel

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000      # Whisper expects 16kHz
CHUNK_SECONDS = 5        # Record this many seconds per utterance
SILENCE_THRESHOLD = 500  # RMS amplitude threshold


class VoiceChannel(BaseChannel):
    CHANNEL_NAME = "voice"

    def __init__(self, config: dict, agent_loop, pairing_manager=None,
                 rate_limiter=None, jarvis_logger=None):
        super().__init__(config, agent_loop, pairing_manager, jarvis_logger)
        self.rate_limiter = rate_limiter

        vc_cfg = config.get("channels", {}).get("voice", {})
        self.wake_word: str = vc_cfg.get("wake_word", "jarvis").lower()
        self.language: str = vc_cfg.get("language", "en")
        self.model_size: str = vc_cfg.get("whisper_model", "base")
        self.tts_rate: int = vc_cfg.get("tts_rate", 175)
        self.tts_voice_index: int = vc_cfg.get("voice_index", 0)

        self._running = False
        self._audio_queue: queue.Queue = queue.Queue()
        self._whisper_model = None
        self._tts_engine = None
        self._local_sender_id = "voice_local"

    # ---------------------------------------------------------------- #
    # Lifecycle                                                         #
    # ---------------------------------------------------------------- #

    def start(self) -> None:
        self._running = True
        print("[Voice] Initializing Whisper and TTS...")
        self._init_whisper()
        self._init_tts()
        print(f"[Voice] Ready. Wake word: '{self.wake_word.upper()}'")
        self._speak("JARVIS online. How can I help you?")
        self._listen_loop()

    def stop(self) -> None:
        self._running = False

    def send_message(self, sender_id: str, text: str, **kwargs) -> None:
        self._speak(text)

    # ---------------------------------------------------------------- #
    # Whisper init                                                      #
    # ---------------------------------------------------------------- #

    def _init_whisper(self) -> None:
        try:
            # Prefer faster-whisper if available
            from faster_whisper import WhisperModel  # type: ignore
            self._whisper_model = WhisperModel(
                self.model_size, device="cpu", compute_type="int8"
            )
            self._use_faster = True
            print(f"[Voice] Using faster-whisper ({self.model_size})")
        except ImportError:
            try:
                import whisper  # type: ignore
                self._whisper_model = whisper.load_model(self.model_size)
                self._use_faster = False
                print(f"[Voice] Using openai-whisper ({self.model_size})")
            except ImportError:
                print(
                    "[Voice] Whisper not installed.\n"
                    "Run: pip install faster-whisper  OR  pip install openai-whisper"
                )
                self._whisper_model = None

    # ---------------------------------------------------------------- #
    # TTS init                                                          #
    # ---------------------------------------------------------------- #

    def _init_tts(self) -> None:
        try:
            import pyttsx3  # type: ignore
            engine = pyttsx3.init()
            engine.setProperty("rate", self.tts_rate)
            voices = engine.getProperty("voices")
            if voices and self.tts_voice_index < len(voices):
                engine.setProperty("voice", voices[self.tts_voice_index].id)
            self._tts_engine = engine
            print("[Voice] TTS (pyttsx3) ready")
        except ImportError:
            print("[Voice] pyttsx3 not installed. Run: pip install pyttsx3")
            self._tts_engine = None
        except Exception as exc:
            print(f"[Voice] TTS init failed: {exc}")
            self._tts_engine = None

    def _speak(self, text: str) -> None:
        if not text:
            return
        # Strip markdown for speech
        import re
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"\*(.+?)\*", r"\1", text)
        text = re.sub(r"`+", "", text)
        text = re.sub(r"#{1,6}\s*", "", text)
        text = text.strip()

        print(f"[Voice] JARVIS: {text}")
        if self._tts_engine:
            try:
                self._tts_engine.say(text)
                self._tts_engine.runAndWait()
            except Exception as exc:
                logger.error("TTS speak error: %s", exc)

    # ---------------------------------------------------------------- #
    # Audio listen loop                                                 #
    # ---------------------------------------------------------------- #

    def _listen_loop(self) -> None:
        try:
            import sounddevice as sd  # type: ignore
            import numpy as np
        except ImportError:
            print(
                "[Voice] sounddevice/numpy not installed.\n"
                "Run: pip install sounddevice numpy"
            )
            return

        print(f"[Voice] Listening... (speak '{self.wake_word.upper()}' to activate)")
        while self._running:
            try:
                # Record CHUNK_SECONDS of audio
                audio = sd.rec(
                    int(CHUNK_SECONDS * SAMPLE_RATE),
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                )
                sd.wait()
                audio_flat = audio.flatten()

                # Basic silence detection (skip empty audio)
                rms = float(np.sqrt(np.mean(audio_flat ** 2)))
                if rms < 0.005:
                    continue

                # Transcribe
                text = self._transcribe(audio_flat)
                if not text:
                    continue

                text_lower = text.lower()
                print(f"[Voice] Heard: {text}")

                # Check for wake word
                if self.wake_word not in text_lower:
                    continue

                # Remove wake word from command
                command = text_lower.replace(self.wake_word, "").strip(", .")
                if not command:
                    self._speak("Yes? What can I do for you?")
                    continue

                print(f"[Voice] Command: {command}")

                # Rate limit
                if self.rate_limiter:
                    allowed, msg = self.rate_limiter.check(f"voice:{self._local_sender_id}")
                    if not allowed:
                        self._speak("Please slow down, I'm processing.")
                        continue

                # Run agent
                response = self.agent_loop.run(
                    user_message=command,
                    channel=self.CHANNEL_NAME,
                    sender_id=self._local_sender_id,
                )
                if response:
                    # Truncate for speech (don't read URLs/code blocks)
                    spoken = self._summarize_for_speech(response)
                    self._speak(spoken)

            except KeyboardInterrupt:
                self._running = False
                break
            except Exception as exc:
                logger.error("Voice listen error: %s", exc)
                time.sleep(1)

    def _transcribe(self, audio_np) -> str:
        """Transcribe numpy float32 audio array to text."""
        if self._whisper_model is None:
            return ""
        try:
            if self._use_faster:
                segments, _ = self._whisper_model.transcribe(
                    audio_np, language=self.language, vad_filter=True
                )
                return " ".join(seg.text for seg in segments).strip()
            else:
                import numpy as np
                result = self._whisper_model.transcribe(
                    audio_np, language=self.language if self.language != "en" else None
                )
                return result.get("text", "").strip()
        except Exception as exc:
            logger.error("Whisper transcription error: %s", exc)
            return ""

    def _summarize_for_speech(self, text: str, max_chars: int = 500) -> str:
        """Trim long responses for voice output."""
        import re
        # Remove code blocks
        text = re.sub(r"```.*?```", "[code block]", text, flags=re.DOTALL)
        # Remove URLs
        text = re.sub(r"https?://\S+", "[link]", text)
        if len(text) > max_chars:
            text = text[:max_chars] + "... check the screen for details."
        return text.strip()
