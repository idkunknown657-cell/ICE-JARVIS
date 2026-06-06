"""
Speech-to-Text engines for MARK XL.

Whisper  – offline transcription via faster-whisper (VAD-buffered)
Vosk     – offline streaming transcription (lighter)
"""
import json
import numpy as np


class WhisperSTT:
    """Offline transcription using faster-whisper."""

    def __init__(self, model_name: str = "base", language: str | None = None):
        from faster_whisper import WhisperModel
        print(f"[STT] Loading Whisper '{model_name}'…")
        self._model    = WhisperModel(model_name, device="cpu", compute_type="int8")
        # None → auto-detect; explicit code (e.g. "tr", "de") forces that language
        self._language = None if (not language or language.strip().lower() == "auto") else language.strip().lower()
        print("[STT] Whisper ready.")

    def transcribe(self, audio: np.ndarray) -> str:
        """
        Transcribe a float32 mono 16 kHz numpy array.
        Returns transcript string (may be empty).
        """
        segments, _ = self._model.transcribe(
            audio,
            language=self._language,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        return " ".join(s.text for s in segments).strip()


class VoskSTT:
    """Streaming transcription using Vosk."""

    def __init__(self, model_path: str | None = None, language: str = "en-us"):
        from vosk import Model, KaldiRecognizer
        print("[STT] Loading Vosk model…")
        if model_path:
            model = Model(model_path)
        else:
            lang  = language.strip().lower() if language and language.strip().lower() != "auto" else "en-us"
            model = Model(lang=lang)
        self._rec = KaldiRecognizer(model, 16000)
        print("[STT] Vosk ready.")

    def process_chunk(self, audio_bytes: bytes) -> tuple[str, bool]:
        """
        Feed raw int16 LE PCM bytes.
        Returns (text, is_final).
        """
        if self._rec.AcceptWaveform(audio_bytes):
            result = json.loads(self._rec.Result())
            return result.get("text", ""), True
        partial = json.loads(self._rec.PartialResult())
        return partial.get("partial", ""), False
