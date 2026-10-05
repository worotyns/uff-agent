from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from src.agent import AgentProfile
from src.event import Event
from src.thread_ctx import RequestContext, activate

if TYPE_CHECKING:
    from src.runtime.app import Runtime

logger = logging.getLogger(__name__)


def handle_task_check(event: Event, agent: AgentProfile, rt: "Runtime") -> None:
    task_id = event.metadata.get("task_id", "")
    task = rt.background_tasks.get(task_id) if rt.background_tasks else None
    if not task:
        logger.warning("task.check: task %s not found", task_id)
        return

    logger.info("TASK CHECK >>> id=%s desc='%.60s'", task_id, task.get("description", ""))

    system = (
        "You are a monitoring assistant. Check the state of the task below.\n\n"
        f"## Task description\n{task.get('description', '')}\n\n"
        f"## Completion criteria\n{task.get('completion_criteria', '')}\n\n"
    )
    if task.get("last_result"):
        system += f"## Previous check result\n{task['last_result']}\n\n"
    if task.get("context"):
        system += f"## Context\n{task['context']}\n\n"
    system += (
        "### Your task:\n"
        "1. Use tools to check the state\n"
        "2. If the completion criteria are met → use `task_done` with a summary\n"
        "3. If not met → optionally use `send_progress` if the state changed, "
        "and at the end write a short summary of the current state"
    )

    tmeta = rt.thread_store.get_meta(task["thread_id"]) if rt.thread_store else {}
    sender = tmeta.get("participants", "")
    thread_subject = tmeta.get("subject", "") or task.get("thread_subject", "")
    mid = rt.thread_store.get_last_message_id(task["thread_id"]) if rt.thread_store else None

    root = rt.data_root
    thread_id = task.get("thread_id") or ""
    ctx = RequestContext(
        smtp=rt.smtp,
        sender=sender,
        subject=thread_subject,
        in_reply_to=mid,
        data_root=root,
        thread_id=thread_id,
        thread_attachments_dir=root / "threads" / (thread_id[:16] if thread_id else "none") / "attachments",
        task_id=task_id,
        bg_store=rt.background_tasks,
        agent=task.get("agent", "assistant"),
    )
    with activate(ctx):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Check the state of the task: {task['description']}"},
        ]

        tools_list = rt.runner.tools_for(agent)
        if tools_list:
            logger.info("TASK tools: %d available", len(tools_list))

        def should_stop() -> bool:
            current = rt.background_tasks.get(task_id) if rt.background_tasks else None
            return bool(current and current.get("status") == "completed")

        max_rounds = (agent.config.get("max_tool_rounds", rt.config.max_tool_rounds)) + 1
        result = rt.runner.run(
            agent, messages, tools_list,
            max_rounds=max_rounds,
            thread_id=thread_id,
            thread_subject=thread_subject,
            should_stop=should_stop,
            polish=False,
        )
        if result.failed:
            return

        if result.sources and rt.thread_store:
            rt.thread_store.record_tool_calls(thread_id, result.sources)

        current = rt.background_tasks.get(task_id) if rt.background_tasks else None
        if current and current.get("status") == "active":
            now = datetime.now(timezone.utc)
            next_check = now + timedelta(minutes=task["check_interval_minutes"])
            content = result.content or ""
            rt.background_tasks.update(
                task_id,
                last_result=content,
                context=content,
                last_check=now.isoformat(),
                next_check=next_check.isoformat(),
            )
            logger.info("TASK CHECK <<< id=%s next=%s", task_id, next_check.isoformat())
        else:
            logger.info("TASK CHECK <<< id=%s completed", task_id)
