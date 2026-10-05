from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from src.event import Event, EventQueue
from src.storage.housekeeping import prune_old_processed_events
from src.storage.script_runner import ScriptStore
from src.storage.tasks import TaskStore

logger = logging.getLogger(__name__)

DREAM_WINDOW_MINUTES = 30


class Scheduler:
    def __init__(
        self,
        queue: EventQueue,
        heartbeat_interval: int,
        dream_hour: str,
        memory_root: Path | None = None,
    ) -> None:
        self.queue = queue
        self.heartbeat_interval = heartbeat_interval
        self.dream_hour = dream_hour
        self.memory_root = memory_root
        self._last_tick: float = 0.0
        self._dream_ran_today: set[str] = set()
        self._task_store = TaskStore(memory_root) if memory_root else None
        self._script_store = ScriptStore(memory_root) if memory_root else None

    def tick(self) -> None:
        now = time.time()

        self._check_reminders()

        if now - self._last_tick < self.heartbeat_interval:
            return
        self._last_tick = now

        today = datetime.now().strftime("%Y-%m-%d")

        self._check_dream(today)
        self._check_housekeeping()
        self._check_scripts()
        self._emit_heartbeat()

    def _check_dream(self, today: str) -> None:
        if not self.dream_hour:
            return
        if today in self._dream_ran_today:
            return

        now = datetime.now()
        dream = datetime.strptime(self.dream_hour, "%H:%M").replace(
            year=now.year, month=now.month, day=now.day
        )

        if now < dream or now > dream + timedelta(minutes=DREAM_WINDOW_MINUTES):
            return

        self.queue.push(Event(type="dream.trigger", agent=""))
        self._dream_ran_today.add(today)
        logger.info("scheduled dream cycle")

    def _check_housekeeping(self) -> None:
        if not self.memory_root:
            return

        prune_old_processed_events(self.memory_root)

        daily_dir = self.memory_root / "memory"
        if daily_dir.exists():
            file_count = len(list(daily_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md")))
            if file_count > 30:
                self.queue.push(
                    Event(
                        type="memory.compact",
                        agent="",
                        metadata={"file_count": file_count},
                    )
                )

        threads_dir = self.memory_root / "threads"
        if threads_dir.exists():
            thread_count = len([d for d in threads_dir.iterdir() if d.is_dir()])
            if thread_count > 100:
                logger.info("high thread count: %d", thread_count)

    def _emit_heartbeat(self) -> None:
        if self.queue.pending_count() < 5:
            self.queue.push(Event(type="heartbeat.tick", agent=""))

    def reset_dream(self, date: str | None = None) -> None:
        if date:
            self._dream_ran_today.discard(date)
        else:
            self._dream_ran_today.clear()

    def _check_reminders(self) -> None:
        if not self._task_store:
            return
        due = self._task_store.get_due()
        for task in due:
            self.queue.push(
                Event(
                    type="reminder.fire",
                    agent=task.get("agent", "assistant"),
                    thread=task.get("thread_id", ""),
                    message=task["title"],
                    metadata={
                        "task_id": task["id"],
                        "thread_id": task.get("thread_id", ""),
                        "thread_subject": task.get("thread_subject", ""),
                        "script": task.get("script", ""),
                        "mode": task.get("mode", "thread"),
                    },
                )
            )
            self._task_store.complete_and_reschedule(task["id"])
            logger.info("fired reminder: %s (script=%s)", task["title"], task.get("script", "-"))

    def _check_scripts(self) -> None:
        if not self._script_store:
            return
        due = self._script_store.get_due()
        for meta in due:
            name = meta.get("name", "")
            self.queue.push(
                Event(
                    type="script.run",
                    agent="assistant",
                    thread=meta.get("thread_id", ""),
                    message=name,
                    metadata={
                        "script_name": name,
                        "owner": meta.get("owner", ""),
                        "thread_id": meta.get("thread_id", ""),
                        "thread_subject": meta.get("thread_subject", ""),
                    },
                )
            )
            logger.info("queued script run: %s", name)
