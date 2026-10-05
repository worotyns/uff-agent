from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from src.thread_ctx import get_data_root
from src.tools.skill import BaseSkill

logger = logging.getLogger(__name__)
CF_BASE = "https://api.cloudflare.com/client/v4"


def _headers() -> dict[str, str]:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _account_id() -> str:
    return os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")


def _check_config() -> str | None:
    if not _account_id():
        return "Brak CLOUDFLARE_ACCOUNT_ID w zmiennych środowiskowych."
    if not os.environ.get("CLOUDFLARE_API_TOKEN"):
        return "Brak CLOUDFLARE_API_TOKEN w zmiennych środowiskowych."
    return None


def _save_result(data: bytes, prefix: str, ext: str, thread_id: str | None = None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_{now}.{ext}"
    if thread_id:
        out_dir = get_data_root() / "threads" / thread_id[:16] / "attachments"
    else:
        out_dir = get_data_root() / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_bytes(data)
    size_kb = len(data) / 1024
    logger.info("browser: saved %s (%.1f KB)", filename, size_kb)
    return filename


class BrowserScreenshot(BaseSkill):
    name = "browser_screenshot"
    description = "Take a screenshot of a web page. The resulting image is saved and can be sent as an attachment. Use for: 'show me what page X looks like', 'screenshot page Y'."
    parameters = {
        "url": {"type": "string", "description": "URL of the page to screenshot"},
    }

    def execute(self, **kwargs: Any) -> str:
        err = _check_config()
        if err:
            return err
        url = kwargs.get("url", "")
        thread_id = kwargs.get("_thread_id")
        if not url:
            return "Podaj URL."

        payload: dict[str, Any] = {"url": url}

        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{CF_BASE}/accounts/{_account_id()}/browser-rendering/screenshot",
                    headers=_headers(),
                    json=payload,
                )
                resp.raise_for_status()
                ct = resp.headers.get("content-type", "")
                if ct.startswith("image/"):
                    img_data = resp.content
                else:
                    data = resp.json()
                    if isinstance(data, str):
                        return "Otrzymano niespodziewany format odpowiedzi (string zamiast JSON)."
                    result = data.get("result", {}) if isinstance(data, dict) else {}
                    img_b64 = result.get("screenshot") or result.get("image") or result.get("data")
                    if not img_b64:
                        return "Brak danych obrazu w odpowiedzi."
                    import base64
                    img_data = base64.b64decode(img_b64)
        except httpx.TimeoutException:
            return "Przekroczono limit czasu (60s) na pobranie screena."
        except httpx.HTTPStatusError as e:
            return f"Blad Cloudflare API: {e.response.status_code} {e.response.text[:200]}"
        except Exception as e:
            return f"Blad: {e}"

        filename = _save_result(img_data, "screenshot", "png", thread_id)
        return f"Zrzut ekranu zapisany: {filename} (znajduje się w attachments/)."


class BrowserMarkdown(BaseSkill):
    name = "browser_markdown"
    description = "Fetch a web page's content as Markdown. Use for: 'read the content of page X', 'summarize an article'."
    parameters = {
        "url": {"type": "string", "description": "URL of the page to fetch"},
    }

    def execute(self, **kwargs: Any) -> str:
        err = _check_config()
        if err:
            return err
        url = kwargs.get("url", "")
        thread_id = kwargs.get("_thread_id")
        if not url:
            return "Podaj URL."

        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{CF_BASE}/accounts/{_account_id()}/browser-rendering/markdown",
                    headers=_headers(),
                    json={"url": url},
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, str):
                    md = data
                elif isinstance(data, dict):
                    result = data.get("result", "")
                    if isinstance(result, str):
                        md = result
                    else:
                        md = (result.get("markdown") if isinstance(result, dict) else "") or ""
                else:
                    md = ""
                if not md:
                    return "Nie udało się pobrać treści."
        except httpx.TimeoutException:
            return "Przekroczono limit czasu (60s)."
        except httpx.HTTPStatusError as e:
            return f"Blad Cloudflare API: {e.response.status_code} {e.response.text[:200]}"
        except Exception as e:
            return f"Blad: {e}"

        filename = _save_result(md.encode(), "page", "md", thread_id)

        preview = md[:2000]
        return f"Treść strony zapisana w attachments/ jako {filename}.\n\n{preview}"


class BrowserCrawl(BaseSkill):
    name = "browser_crawl"
    description = "Crawl a website and return its content. Use for: 'scan page X', 'find all links on page X'."
    parameters = {
        "url": {"type": "string", "description": "Start URL to crawl"},
        "depth": {"type": "integer", "description": "Crawl depth (default 1)", "default": 1},
        "max_pages": {"type": "integer", "description": "Max number of pages (default 10)", "default": 10},
    }

    def execute(self, **kwargs: Any) -> str:
        err = _check_config()
        if err:
            return err
        url = kwargs.get("url", "")
        depth = int(kwargs.get("depth", 1))
        max_pages = int(kwargs.get("max_pages", 10))
        thread_id = kwargs.get("_thread_id")
        if not url:
            return "Podaj URL."

        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    f"{CF_BASE}/accounts/{_account_id()}/browser-rendering/crawl",
                    headers=_headers(),
                    json={
                        "url": url,
                        "depth": depth,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, str):
                    return "Otrzymano niespodziewany format odpowiedzi (string zamiast JSON)."
                result = data.get("result", {})
                job_id = result.get("jobId") or result.get("id")
                if job_id:
                    return f"Zadanie crawl rozpoczęte (ID: {job_id}). Sprawdź wyniki za kilka minut w dashboardzie Cloudflare lub użyj API."
                pages = result.get("pages", []) or result.get("data", [])
                if not pages:
                    return "Nie znaleziono stron."
        except httpx.TimeoutException:
            return "Przekroczono limit czasu (120s)."
        except httpx.HTTPStatusError as e:
            return f"Blad Cloudflare API: {e.response.status_code} {e.response.text[:200]}"
        except Exception as e:
            return f"Blad: {e}"

        combined = []
        for page in pages:
            page_url = page.get("url", "?")
            page_md = page.get("markdown", "") or page.get("content", "")
            combined.append(f"## {page_url}\n\n{page_md[:3000]}")

        result_text = f"Scrawlowano {len(pages)} stron.\n\n" + "\n\n---\n\n".join(combined)
        filename = _save_result(result_text.encode(), "crawl", "md", thread_id)
        return f"Scrawlowano {len(pages)} stron. Wynik zapisany w attachments/ jako {filename}.\n\n{result_text[:2000]}"
