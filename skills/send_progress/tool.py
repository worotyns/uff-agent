from __future__ import annotations

from typing import Any

from src.tools.skill import BaseSkill
from src.thread_ctx import get_request_context


class SendProgress(BaseSkill):
    name = "send_progress"
    description = (
        "Send an email with progress information to the user. "
        "Use when you know the task will take longer than ~30 seconds "
        "(e.g. indexing a document into RAG, researching many sources, crawling a page). "
        "The 'message' parameter should describe what you are currently doing."
    )
    parameters = {
        "message": {
            "type": "string",
            "description": "Progress message — what I am currently doing",
        },
    }

    def execute(self, **kwargs: Any) -> str:
        kwargs.pop("_thread_id", None)
        kwargs.pop("_thread_subject", None)
        message = kwargs.get("message", "")

        email_ctx = get_request_context()
        if not email_ctx:
            return "(ignored – no email context)"

        if email_ctx.timer:
            email_ctx.timer.cancel()

        try:
            email_ctx.smtp.send(
                to=email_ctx.sender,
                subject=f"Re: {email_ctx.subject}",
                body_text=message,
                in_reply_to=email_ctx.in_reply_to,
            )
            return "ok"
        except Exception as e:
            return f"Error: {e}"
