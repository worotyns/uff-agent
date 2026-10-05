from __future__ import annotations

from typing import Any

from src.tools.skill import BaseSkill
from src.thread_ctx import get_request_context


class CreateBackgroundTask(BaseSkill):
    name = "create_background_task"
    description = (
        "Create a background task monitored by the LLM. "
        "USE THIS for NON-DETERMINISTIC tasks — where the agent must "
        "understand a page, make a decision, judge whether a condition is met. "
        "On each check the agent analyzes the page (via browser/web_fetch) "
        "and decides itself whether to finish the task. "
        "Examples: monitor a parcel until it reaches a parcel locker, "
        "monitor a product price until it drops below X, "
        "monitor a page with match results."
    )
    parameters = {
        "description": {
            "type": "string",
            "description": "Task description — what to monitor",
        },
        "check_interval_minutes": {
            "type": "integer",
            "description": "How often to check (minutes). For parcels ~60, for prices ~1440 (daily), for match results ~60.",
        },
        "completion_criteria": {
            "type": "string",
            "description": "Completion criteria — when to consider the task done. E.g. 'parcel status is Ready for pickup', 'price below 100 PLN', 'all of today's match results collected'.",
        },
    }

    def execute(self, **kwargs: Any) -> str:
        thread_id = kwargs.pop("_thread_id", "")
        thread_subject = kwargs.pop("_thread_subject", "")
        description = kwargs.get("description", "")
        interval = kwargs.get("check_interval_minutes", 60)
        criteria = kwargs.get("completion_criteria", "")

        task_ctx = get_request_context()
        if not task_ctx:
            return "Error: no background task store available"

        store = task_ctx.bg_store
        agent = task_ctx.agent
        if not store:
            return "Error: no background task store"

        task_id = store.create(
            agent=agent,
            thread_id=thread_id,
            thread_subject=thread_subject,
            description=description,
            check_interval_minutes=interval,
            completion_criteria=criteria,
        )
        return (
            f"Background task created (id={task_id}). "
            f"Next check in {interval} minute(s). "
            f"Criteria: {criteria}"
        )


class TaskDone(BaseSkill):
    name = "task_done"
    description = (
        "Finish a background task. Call when the completion criteria are met. "
        "Sends a final summary email to the user."
    )
    parameters = {
        "result_summary": {
            "type": "string",
            "description": "Summary of the result — what was established, final state",
        },
    }

    def execute(self, **kwargs: Any) -> str:
        kwargs.pop("_thread_id", None)
        kwargs.pop("_thread_subject", None)
        result = kwargs.get("result_summary", "")

        ctx = get_request_context()

        if ctx:
            if ctx.bg_store and ctx.task_id:
                ctx.bg_store.complete(ctx.task_id, result)

        if ctx and ctx.smtp:
            try:
                ctx.smtp.send(
                    to=ctx.sender,
                    subject=f"Re: {ctx.subject}",
                    body_text=result,
                    in_reply_to=ctx.in_reply_to,
                )
            except Exception as e:
                return f"Task completed but email failed: {e}"

        return f"Task completed. Final email sent: {result[:100]}"
