from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BackgroundTaskStore:
    def __init__(self, root: Path) -> None:
        self.path = root / "memory" / "tasks" / "background.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text().strip().split("\n")
            return [json.loads(line) for line in lines if line.strip()]
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("failed to load background tasks: %s", e)
            return []

    def _save_all(self, tasks: list[dict[str, Any]]) -> None:
        lines = [json.dumps(t, ensure_ascii=False) for t in tasks]
        self.path.write_text("\n".join(lines) + "\n")

    def create(
        self,
        agent: str,
        thread_id: str,
        thread_subject: str,
        description: str,
        check_interval_minutes: int,
        completion_criteria: str,
    ) -> str:
        now = datetime.now(timezone.utc)
        next_check = now + timedelta(minutes=check_interval_minutes)
        task: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "agent": agent,
            "thread_id": thread_id,
            "thread_subject": thread_subject,
            "description": description,
            "check_interval_minutes": check_interval_minutes,
            "completion_criteria": completion_criteria,
            "status": "active",
            "created": now.isoformat(),
            "last_check": None,
            "next_check": next_check.isoformat(),
            "last_result": None,
            "context": None,
            "completed_at": None,
        }
        tasks = self._load_all()
        tasks.append(task)
        self._save_all(tasks)
        logger.info(
            "background task created: id=%s desc='%s' interval=%dmin next=%s",
            task["id"], description[:60], check_interval_minutes, next_check.isoformat(),
        )
        return task["id"]

    def get(self, task_id: str) -> dict[str, Any] | None:
        for t in self._load_all():
            if t["id"] == task_id:
                return t
        return None

    def get_due(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        tasks = self._load_all()
        return [
            t for t in tasks
            if t.get("status") == "active"
            and t.get("next_check") is not None
            and t["next_check"] <= now
        ]

    def update(self, task_id: str, **kwargs: Any) -> bool:
        tasks = self._load_all()
        for t in tasks:
            if t["id"] == task_id:
                for k, v in kwargs.items():
                    t[k] = v
                self._save_all(tasks)
                logger.info("background task %s updated: %s", task_id, list(kwargs.keys()))
                return True
        return False

    def complete(self, task_id: str, result_summary: str = "") -> bool:
        return self.update(
            task_id,
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            last_result=result_summary,
        )

    def list_active(self) -> list[dict[str, Any]]:
        return [t for t in self._load_all() if t.get("status") == "active"]
