from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.thread_ctx import get_data_root
from src.tools.skill import BaseSkill

logger = logging.getLogger(__name__)


class TranscribeAudio(BaseSkill):
    name = "transcribe_audio"
    description = "Transcribe an audio file to text via the OpenRouter Whisper API. Supports formats: mp3, wav, m4a, ogg, flac."
    parameters = {
        "path": {"type": "string", "description": "Path to the audio file (e.g. from an email attachment)"},
    }

    SENSITIVE = {".env", "config.yaml", "config.yml"}

    def execute(self, **kwargs: Any) -> str:
        path_str = kwargs.get("path", "")
        if not path_str:
            return "Nie podano ścieżki do pliku."

        path = Path(path_str).resolve()
        if not str(path).startswith(str(get_data_root())):
            return f"Access denied: {path_str}"
        if path.name in self.SENSITIVE:
            return f"Access denied: {path_str}"
        if not path.exists():
            return f"Plik nie istnieje: {path_str}"

        api_key = os.environ.get("OPENROUTER_KEY", "")
        if not api_key:
            return "Brak OPENROUTER_KEY w zmiennych środowiskowych."

        max_size = 25 * 1024 * 1024
        size = path.stat().st_size
        if size > max_size:
            return f"Plik za duży: {size / 1024 / 1024:.1f} MB (max 25 MB)"

        try:
            with httpx.Client(timeout=120.0) as client:
                with open(path, "rb") as f:
                    resp = client.post(
                        "https://openrouter.ai/api/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        files={"file": (path.name, f, self._mime(path))},
                        data={"model": "openai/whisper-1"},
                        timeout=120.0,
                    )
                resp.raise_for_status()
                data = resp.json()
                text = data.get("text", "").strip()
                return text[:5000] if text else "(brak tekstu w odpowiedzi)"
        except httpx.TimeoutException:
            return "Transkrypcja przekroczyła limit czasu (120s)."
        except Exception as e:
            return f"Błąd transkrypcji: {e}"

    @staticmethod
    def _mime(path: Path) -> str:
        ext = path.suffix.lower()
        return {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4a": "audio/mp4",
            ".ogg": "audio/ogg",
            ".flac": "audio/flac",
            ".webm": "audio/webm",
        }.get(ext, "application/octet-stream")


class TextToSpeech(BaseSkill):
    name = "text_to_speech"
    description = "Generate an audio file (speech) from text. The resulting .mp3 file is saved and can be sent as an email attachment. Use for: 'read this text', 'make a voice note', 'generate a podcast'."
    parameters = {
        "text": {"type": "string", "description": "Text to synthesize into speech"},
        "voice": {"type": "string", "description": "Voice: nova, alloy, echo, fable, onyx, shimmer (default nova)", "default": "nova"},
        "response_format": {"type": "string", "description": "Format: mp3 or pcm", "default": "mp3"},
    }

    def execute(self, **kwargs: Any) -> str:
        text = kwargs.get("text", "")
        voice = kwargs.get("voice", "nova")
        fmt = kwargs.get("response_format", "mp3")
        thread_id = kwargs.get("_thread_id")

        if not text:
            return "Podaj tekst do syntezy."

        api_key = os.environ.get("OPENROUTER_KEY", "")
        if not api_key:
            return "Brak OPENROUTER_KEY."

        model = os.environ.get("TTS_MODEL", "openai/gpt-4o-mini-tts-2025-12-15")

        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    "https://openrouter.ai/api/v1/audio/speech",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "input": text,
                        "voice": voice,
                        "response_format": fmt,
                    },
                )
                resp.raise_for_status()
                audio_data = resp.content
        except httpx.TimeoutException:
            return "Generowanie mowy przekroczyło limit czasu (120s)."
        except httpx.HTTPStatusError as e:
            return f"Blad API: {e.response.status_code}"
        except Exception as e:
            return f"Blad: {e}"

        now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"tts_{now}.{fmt}"

        if thread_id:
            out_dir = get_data_root() / "threads" / thread_id[:16] / "attachments"
        else:
            out_dir = get_data_root() / "generated"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        out_path.write_bytes(audio_data)

        size_kb = len(audio_data) / 1024
        logger.info("tts: generated %s (%.1f KB, voice=%s)", filename, size_kb, voice)
        return f"Wygenerowano plik audio: {filename} ({size_kb:.0f} KB, głos: {voice}). Plik znajduje się w attachments/."
