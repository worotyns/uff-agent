from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PRIORITY_MAP = {
    "email.received": 0,
    "email.sent": 1,
    "heartbeat.tick": 2,
    "dream.trigger": 3,
    "thread.compress": 2,
    "manual.test": 1,
}


@dataclass
class Event:
    type: str
    agent: str
    thread: str = ""
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    retries: int = 0
    max_retries: int = 3

    @property
    def filename(self) -> str:
        h = hashlib.md5(f"{self.type}:{self.agent}:{self.thread}:{self.timestamp}".encode()).hexdigest()[:12]
        return f"{self.timestamp:.0f}_{h}.json"

    @property
    def priority(self) -> int:
        return PRIORITY_MAP.get(self.type, 5)


class EventQueue:
    def __init__(self, root: str | Path) -> None:
        self.pending = Path(root) / "pending"
        self.processed = Path(root) / "processed"
        self.pending.mkdir(parents=True, exist_ok=True)
        self.processed.mkdir(parents=True, exist_ok=True)

    def push(self, event: Event, dedup: bool = True) -> None:
        if dedup and self._is_duplicate(event):
            logger.debug("skipped duplicate event: %s", event.type)
            return
        path = self.pending / event.filename
        with open(path, "w") as f:
            json.dump(asdict(event), f, indent=2)

    def _is_duplicate(self, event: Event) -> bool:
        prefix = f"{event.timestamp:.0f}_"
        for f in self.pending.iterdir():
            if f.suffix == ".json" and f.name.startswith(prefix):
                try:
                    with open(f) as fh:
                        existing = json.load(fh)
                    if existing.get("type") == event.type and existing.get("thread") == event.thread:
                        return True
                except (json.JSONDecodeError, KeyError):
                    continue
        return False

    def poll(self) -> Event | None:
        files = sorted(self.pending.iterdir()) if self.pending.exists() else []
        if not files:
            return None
        for f in files:
            if f.suffix == ".json" and f.is_file():
                try:
                    with open(f) as fh:
                        data = json.load(fh)
                    return Event(**data)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("corrupt event file %s: %s", f.name, e)
                    continue
        return None

    def fail(self, event: Event) -> None:
        event.retries += 1
        if event.retries >= event.max_retries:
            logger.warning("event %s exceeded max retries, moving to processed", event.type)
            self.mark_done(event)
            return
        src = self.pending / event.filename
        if src.exists():
            src.unlink()
        self.push(event, dedup=False)
        logger.info("event %s requeued (retry %d/%d)", event.type, event.retries, event.max_retries)

    def mark_done(self, event: Event) -> None:
        src = self.pending / event.filename
        dst = self.processed / event.filename
        try:
            if src.exists():
                src.rename(dst)
        except FileNotFoundError:
            logger.debug("event file already moved: %s", event.filename)
        except OSError as e:
            logger.warning("failed to move event file %s: %s", event.filename, e)

    def has_pending(self) -> bool:
        return any(f.suffix == ".json" for f in self.pending.iterdir()) if self.pending.exists() else False

    def pending_count(self) -> int:
        if not self.pending.exists():
            return 0
        return sum(1 for f in self.pending.iterdir() if f.suffix == ".json")
