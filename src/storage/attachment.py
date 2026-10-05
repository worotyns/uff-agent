from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}


def save_attachment(thread_dir: Path, filename: str, data: bytes, vision_config: dict[str, str] | None = None) -> dict[str, str]:
    att_dir = thread_dir / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)

    raw_path = att_dir / filename
    raw_path.write_bytes(data)

    text = _extract_text(data, filename, vision_config)
    text_path = att_dir / f"{filename}.txt"
    if text:
        text_path.write_text(text)

    meta = {
        "filename": filename,
        "size": len(data),
        "extracted": bool(text),
    }
    meta_path = att_dir / f"{filename}.meta.yaml"
    import yaml
    with open(meta_path, "w") as f:
        yaml.dump(meta, f)

    logger.info("saved attachment %s (%d bytes)", filename, len(data))
    return meta


def _extract_text(data: bytes, filename: str, vision_config: dict[str, str] | None = None) -> str | None:
    ext = Path(filename).suffix.lower()
    if ext in (".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log"):
        return data.decode(errors="replace")
    if ext == ".pdf":
        try:
            import io
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() for page in reader.pages)
        except ImportError:
            logger.warning("PyPDF2 not installed, skipping PDF extraction for %s", filename)
        except Exception as e:
            logger.warning("failed to extract PDF %s: %s", filename, e)
    if ext in (".docx",):
        try:
            import io
            import docx
            doc = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            logger.warning("python-docx not installed, skipping docx extraction for %s", filename)
        except Exception as e:
            logger.warning("failed to extract docx %s: %s", filename, e)
    if ext in IMAGE_EXTS:
        return _extract_image_text(data, filename, vision_config)
    return None


def _extract_image_text(data: bytes, filename: str, vision_config: dict[str, str] | None = None) -> str | None:
    import os
    api_key = (vision_config or {}).get("api_key") or os.environ.get("OPENROUTER_KEY", "")
    if not api_key:
        logger.warning("no OPENROUTER_KEY for OCR on %s", filename)
        return None
    model = (vision_config or {}).get("vision_model") or os.environ.get("VISION_MODEL", "google/gemini-3.1-flash-lite")

    b64 = base64.b64encode(data).decode()
    mime = _image_mime(filename)
    data_uri = f"data:{mime};base64,{b64}"

    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Extract all text visible in this image. If it is a screenshot, code, document, diagram, or chart — describe it in detail. Do not add comments, return only the content."},
                                {"type": "image_url", "image_url": {"url": data_uri}},
                            ],
                        }
                    ],
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            logger.info("OCR from %s: %d chars extracted via %s", filename, len(text), model)
            return text
    except httpx.TimeoutException:
        logger.warning("OCR timeout for %s", filename)
    except httpx.HTTPStatusError as e:
        logger.warning("OCR failed for %s: %d %s", filename, e.response.status_code, e.response.text[:300])
    except Exception as e:
        logger.warning("OCR failed for %s: %s", filename, e)
    return None


def _image_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }.get(ext, "image/png")
