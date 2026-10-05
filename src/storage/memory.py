from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from src.storage.memory_search import rg_search

logger = logging.getLogger(__name__)

MemoryKind = Literal["daily", "facts", "decisions"]


class MemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        (self.root / "memory").mkdir(parents=True, exist_ok=True)

    def _path(self, kind: MemoryKind, name: str | None = None) -> Path:
        if kind == "daily":
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return self.root / "memory" / f"{today}.md"
        return self.root / "memory" / f"{kind}.md"

    def append(self, kind: MemoryKind, text: str) -> None:
        path = self._path(kind)
        timestamp = datetime.now(timezone.utc).isoformat()
        with open(path, "a") as f:
            f.write(f"- [{timestamp}] {text}\n")
        logger.debug("appended to memory %s", kind)

    def read(self, kind: MemoryKind, max_lines: int = 100) -> str:
        path = self._path(kind)
        if not path.exists():
            return ""
        lines = path.read_text().strip().split("\n")
        return "\n".join(lines[-max_lines:])

    def read_recent_daily(self, days: int = 7) -> str:
        parts: list[str] = []
        daily_dir = self.root / "memory"
        if not daily_dir.exists():
            return ""
        files = sorted(daily_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"), reverse=True)[:days]
        for f in files:
            content = f.read_text().strip()
            if content:
                parts.append(f"## {f.stem}\n{content}")
        return "\n\n".join(parts)

    def search(self, kind: MemoryKind, query: str) -> str:
        kind_map = {"daily": ("daily",), "facts": ("facts",), "decisions": ("decisions",)}
        results = rg_search(self.root, query, limit=100, kinds=kind_map.get(kind))
        if not results:
            return ""
        return "\n".join(f"[{r['path']}:{r['line']}] {r['text']}" for r in results)
