from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.thread_ctx import get_data_root
from src.tools.skill import BaseSkill

logger = logging.getLogger(__name__)


def _generate(prompt: str, n: int = 1, size: str = "1K", thread_id: str | None = None) -> str:
    api_key = os.environ.get("OPENROUTER_KEY", "")
    if not api_key:
        return "Brak OPENROUTER_KEY."

    model = os.environ.get("IMAGE_GEN_MODEL", "google/gemini-3.1-flash-lite-image")

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": min(n, 4),
    }
    if size:
        payload["size"] = size

    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                "https://openrouter.ai/api/v1/images",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return "Generowanie obrazu przekroczyło limit czasu (120s)."
    except httpx.HTTPStatusError as e:
        return f"Blad API: {e.response.status_code} {e.response.text[:200]}"
    except Exception as e:
        return f"Blad: {e}"

    images = data.get("data", [])
    if not images:
        return "Nie otrzymano obrazów w odpowiedzi."

    saved = []
    out_dir = _output_dir(thread_id)

    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    for i, img in enumerate(images):
        b64 = img.get("b64_json") or img.get("data") or _extract_b64(img.get("url", ""))
        if not b64:
            saved.append(f"Obraz {i+1}: brak danych (pominieto)")
            continue

        filename = f"gen_{now}_{i}.png"
        out_path = out_dir / filename
        try:
            import base64
            out_path.write_bytes(base64.b64decode(b64))
        except Exception as e:
            saved.append(f"Obraz {i+1}: blad zapisu - {e}")
            continue

        saved.append(f"Obraz {i+1}: {filename} (zapisano w attachments/)")

    if not saved:
        return "Nie udalo sie zapisac zadnego obrazu."

    result = "\n".join(saved)
    logger.info("image_gen: %s", result)
    return result


def _output_dir(thread_id: str | None) -> Path:
    if thread_id:
        d = get_data_root() / "threads" / thread_id[:16] / "attachments"
    else:
        d = get_data_root() / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _extract_b64(url: str) -> str | None:
    if url and url.startswith("data:image"):
        import base64
        try:
            _, encoded = url.split(",", 1)
            return encoded
        except (ValueError, IndexError):
            pass
    return None


class GenerateImage(BaseSkill):
    name = "generate_image"
    description = "Generate an image from a text description. The image is saved and can be sent as an attachment. Use for: 'draw a logo', 'make an infographic', 'generate an illustration'."
    parameters = {
        "prompt": {"type": "string", "description": "Image description (in English or Polish)"},
        "n": {"type": "integer", "description": "Number of images to generate (1-4)", "default": 1},
        "size": {"type": "string", "description": "Resolution: 1K (default), 2K, 4K", "default": "1K"},
    }

    def execute(self, **kwargs: Any) -> str:
        prompt = kwargs.get("prompt", "")
        n = int(kwargs.get("n", 1))
        size = kwargs.get("size", "1K")
        thread_id = kwargs.get("_thread_id") or kwargs.get("thread_id")

        if not prompt:
            return "Podaj prompt (opis obrazu)."

        return _generate(prompt, n=n, size=size, thread_id=thread_id)
