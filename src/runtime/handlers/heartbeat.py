from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.agent import AgentProfile
from src.event import Event

if TYPE_CHECKING:
    from src.runtime.app import Runtime

logger = logging.getLogger(__name__)


def handle_heartbeat(event: Event, agent: AgentProfile, rt: "Runtime") -> None:
    if rt.background_tasks:
        for task in rt.background_tasks.get_due():
            rt.queue.push(Event(
                type="task.check",
                agent=task["agent"],
                thread=task["thread_id"],
                metadata={"task_id": task["id"]},
            ))
