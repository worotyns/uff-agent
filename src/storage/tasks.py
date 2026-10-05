from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _parse_time(t: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"cannot parse time: {t}")


def _next_time(current: str, recurrence: str) -> str:
    dt = _parse_time(current)
    if recurrence == "hourly":
        dt += timedelta(hours=1)
    elif recurrence == "daily":
        dt += timedelta(days=1)
    elif recurrence == "weekdays":
        dt += timedelta(days=1)
        while dt.weekday() >= 5:
            dt += timedelta(days=1)
    elif recurrence == "weekly":
        dt += timedelta(days=7)
    else:
        raise ValueError(f"unknown recurrence: {recurrence}")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TaskStore:
    def __init__(self, root: Path) -> None:
        self.path = root / "memory" / "tasks.json"

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save(self, tasks: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(tasks, indent=2))

    def add(self, task_type: str, title: str, time: str | None = None, recurrence: str = "", script: str = "", agent: str = "assistant", thread_id: str = "", thread_subject: str = "", mode: str = "thread") -> dict[str, Any]:
        tasks = self._load()
        task: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "type": task_type,
            "title": title,
            "done": False,
            "created": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
        }
        if time:
            task["time"] = time
        if recurrence:
            task["recurrence"] = recurrence
        if script:
            task["script"] = script
        if thread_id:
            task["thread_id"] = thread_id
        if thread_subject:
            task["thread_subject"] = thread_subject
        task["mode"] = mode if mode in ("thread", "new") else "thread"
        tasks.append(task)
        self._save(tasks)
        logger.info("added %s: %s (id=%s rec=%s script=%s thread=%s mode=%s)", task_type, title, task["id"], recurrence or "none", script or "-", thread_id[:16] if thread_id else "-", task["mode"])
        return task

    def list(self, task_type: str | None = None, done: bool | None = False) -> list[dict[str, Any]]:
        tasks = self._load()
        result = []
        for t in tasks:
            if task_type and t.get("type") != task_type:
                continue
            if done is not None and t.get("done", False) != done:
                continue
            result.append(t)
        return result

    def complete(self, task_id: str) -> bool:
        tasks = self._load()
        for t in tasks:
            if t["id"] == task_id:
                t["done"] = True
                t["completed_at"] = datetime.now(timezone.utc).isoformat()
                logger.info("completed task: %s (%s)", t["title"], task_id)
                self._save(tasks)
                return True
        return False

    def complete_and_reschedule(self, task_id: str) -> bool:
        tasks = self._load()
        for t in tasks:
            if t["id"] == task_id:
                rec = t.get("recurrence", "")
                if rec and t.get("time"):
                    new_time = _next_time(t["time"], rec)
                    t["time"] = new_time
                    t["done"] = False
                    logger.info("recurring task %s: next at %s", task_id, new_time)
                else:
                    t["done"] = True
                    t["completed_at"] = datetime.now(timezone.utc).isoformat()
                    logger.info("completed task: %s (%s)", t["title"], task_id)
                self._save(tasks)
                return True
        return False

    def delete(self, task_id: str) -> bool:
        tasks = self._load()
        before = len(tasks)
        tasks = [t for t in tasks if t["id"] != task_id]
        if len(tasks) < before:
            self._save(tasks)
            return True
        return False

    def get_due(self, now: str | None = None) -> list[dict[str, Any]]:
        if now is None:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        now_dt = _parse_time(now)
        tasks = self._load()
        return [
            t for t in tasks
            if not t.get("done", False)
            and t.get("type") == "reminder"
            and (not t.get("time") or _parse_time(t["time"]) <= now_dt)
        ]

    def count_pending(self) -> int:
        return len([t for t in self._load() if not t.get("done", False)])
