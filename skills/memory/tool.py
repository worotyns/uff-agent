from __future__ import annotations

from typing import Any

from src.storage.memory_search import rg_search
from src.thread_ctx import get_data_root
from src.tools.skill import BaseSkill


class MemorySearch(BaseSkill):
    name = "memory_search"
    description = (
        "Search the agent's long-term memory (facts, decisions, daily logs, entities) by keywords. "
        "Use when the user asks about something from the past, asks to recall earlier context, "
        "or when you need to check what you already know about a topic."
    )
    parameters = {
        "query": {"type": "string", "description": "Search phrase or keywords, e.g. 'lease agreement'", "required": True},
        "limit": {"type": "integer", "description": "Max number of results", "default": 20},
    }

    def execute(self, **kwargs: Any) -> str:
        query = (kwargs.get("query") or "").strip()
        limit = int(kwargs.get("limit", 20))
        if not query:
            return "Podaj frazę do wyszukania."
        root = get_data_root()
        results = rg_search(root, query, limit=limit)
        if not results:
            return "Brak wyników w pamięci."
        lines = [f"Znaleziono {len(results)} wyników:"]
        for r in results:
            lines.append(f"[{r['path']}:{r['line']}] {r['text']}")
        return "\n".join(lines)
