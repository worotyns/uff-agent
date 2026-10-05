from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

MODEL_LIMITS: dict[str, int] = {
    "deepseek/deepseek-v4.1-flash": 1048576,
    "deepseek/deepseek-v4-pro": 1048576,
    "google/gemini-2.5-flash-preview": 1048576,
    "openai/gpt-4o": 128000,
    "openai/text-embedding-3-small": 8191,
}

DEFAULT_LIMIT = 1048576
AVG_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return int(len(text) / AVG_CHARS_PER_TOKEN)


def count_messages_tokens(messages: list[dict[str, str]]) -> int:
    total = 0
    for msg in messages:
        total += estimate_tokens(msg.get("content", ""))
        total += estimate_tokens(msg.get("role", ""))
    return total


def trim_to_budget(
    messages: list[dict[str, str]],
    model: str,
    reserve_tokens: int = 4096,
    tool_reserve: int = 2048,
) -> list[dict[str, str]]:
    limit = MODEL_LIMITS.get(model, DEFAULT_LIMIT)
    budget = limit - reserve_tokens - tool_reserve

    current = count_messages_tokens(messages)
    if current <= budget:
        return messages

    system_messages: list[dict[str, str]] = [m for m in messages if m["role"] == "system"]
    history: list[dict[str, str]] = [m for m in messages if m["role"] != "system"]

    while count_messages_tokens(history) > budget and len(history) > 1:
        history.pop(0)

    trimmed = system_messages + history
    logger.info(
        "trimmed context: %d -> %d tokens (budget %d)",
        current, count_messages_tokens(trimmed), budget,
    )
    return trimmed


def get_model_fallback(model: str) -> str:
    fallbacks: dict[str, str] = {
        "deepseek/deepseek-v4-pro": "deepseek/deepseek-v4.1-flash",
        "deepseek/deepseek-v4.1-flash": "deepseek/deepseek-v4.1-flash",
    }
    return fallbacks.get(model, "deepseek/deepseek-v4.1-flash")
