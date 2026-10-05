from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import httpx

from src.tokenizer import get_model_fallback

logger = logging.getLogger(__name__)


class InsufficientCreditsError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict[str, Any]]
    model: str
    usage: dict[str, int] | None
    finish_reason: str | None


class OpenRouterClient:
    BASE = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str, model: str, fallback_model: str | None = None, timeout: float = 60.0, referer: str = "") -> None:
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model or get_model_fallback(model)
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if referer:
            self.headers["HTTP-Referer"] = referer

    def chat(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        return self._chat_with_fallback(messages, tools, model, temperature, max_tokens, attempt=0)

    def _chat_with_fallback(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        attempt: int = 0,
    ) -> LLMResponse:
        current_model = model or (self.fallback_model if attempt > 0 else self.model)

        logger.info("LLM call: model=%s attempt=%d messages=%d tools=%s",
                     current_model, attempt, len(messages), bool(tools))

        payload: dict[str, Any] = {
            "model": current_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools

        for retry in range(3):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        f"{self.BASE}/chat/completions",
                        headers=self.headers,
                        json=payload,
                    )
                    if resp.status_code == 429 and retry < 2:
                        wait = 2 ** (retry + 1) * (attempt + 1)
                        logger.warning("rate limited on %s, retrying in %ds", current_model, wait)
                        time.sleep(wait)
                        continue
                    if resp.status_code == 402:
                        logger.warning("insufficient credits for %s", current_model)
                        if attempt == 0:
                            return self._chat_with_fallback(messages, tools, model, temperature, max_tokens, attempt=1)
                        raise InsufficientCreditsError("insufficient credits for all models")
                    resp.raise_for_status()
                    data = resp.json()
                    used_model = data.get("model", current_model)
                    usage = data.get("usage", {})
                    logger.info("LLM response: model=%s input_tokens=%d output_tokens=%d finish=%s",
                                 used_model,
                                 usage.get("prompt_tokens", 0),
                                 usage.get("completion_tokens", 0),
                                 data["choices"][0].get("finish_reason", "unknown"))
            except httpx.TimeoutException as e:
                if retry < 2:
                    wait = 2 ** (retry + 1)
                    logger.warning("timeout on %s, retrying in %ds", current_model, wait)
                    time.sleep(wait)
                    continue
                if attempt == 0:
                    logger.warning("falling back to %s after timeout", self.fallback_model)
                    return self._chat_with_fallback(messages, tools, model, temperature, max_tokens, attempt=1)
                raise
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt == 0:
                    logger.warning("server error on %s, falling back", current_model)
                    return self._chat_with_fallback(messages, tools, model, temperature, max_tokens, attempt=1)
                raise

            choice = data["choices"][0]
            msg = choice["message"]
            return LLMResponse(
                content=msg.get("content") or "",
                tool_calls=msg.get("tool_calls") or [],
                model=data.get("model", current_model),
                usage=data.get("usage"),
                finish_reason=choice.get("finish_reason"),
            )

        raise RuntimeError(f"OpenRouter request failed after 3 retries on {current_model}")

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        current_model = model or self.model

        payload: dict[str, Any] = {
            "model": current_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = payload

        with httpx.Client(timeout=self.timeout) as client:
            with client.stream("POST", f"{self.BASE}/chat/completions", headers=self.headers, json=payload) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
