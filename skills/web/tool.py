from __future__ import annotations

from typing import Any

import httpx

from src.tools.skill import BaseSkill


class WebFetch(BaseSkill):
    name = "web_fetch"
    description = "Fetch a URL and return the text content"
    parameters = {
        "url": {"type": "string", "description": "URL to fetch"},
    }

    def execute(self, **kwargs: Any) -> str:
        url = kwargs.get("url", "")
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                text = resp.text
                return text[:5000]
        except Exception as e:
            return f"Error fetching {url}: {e}"


class WebSearch(BaseSkill):
    name = "web_search"
    description = "Search the web using OpenRouter's web search capability"
    parameters = {
        "query": {"type": "string", "description": "Search query"},
    }

    def execute(self, **kwargs: Any) -> str:
        query = kwargs.get("query", "")
        key = self._get_api_key()
        if not key:
            return "No OPENROUTER_KEY set"

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek/deepseek-v4.1-flash",
                        "messages": [
                            {
                                "role": "user",
                                "content": f"Search the web for: {query}. Return top results with brief summaries and sources.",
                            }
                        ],
                        "tools": [{"type": "web_search"}],
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"][:5000]
        except Exception as e:
            return f"Search failed: {e}"

    @staticmethod
    def _get_api_key() -> str:
        import os
        return os.environ.get("OPENROUTER_KEY", "")
