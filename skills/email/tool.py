from __future__ import annotations

from typing import Any

from src.tools.skill import BaseSkill
from src.thread_ctx import get_request_context


class SendEmail(BaseSkill):
    name = "send_email"
    description = (
        "Send the user a new email as a NEW thread (fresh subject, not a reply to the current thread). "
        "Use when you want to start a new topic instead of replying in the current thread — e.g. a summary, "
        "proposal, reminder, or something derived from document analysis or knowledge."
    )
    parameters = {
        "subject": {"type": "string", "description": "Subject of the new email (without the 'Re:' prefix)", "required": True},
        "body": {"type": "string", "description": "Email body in markdown", "required": True},
    }

    def execute(self, **kwargs: Any) -> str:
        kwargs.pop("_thread_id", None)
        kwargs.pop("_thread_subject", None)
        subject = (kwargs.get("subject") or "").strip()
        body = kwargs.get("body", "")
        if not subject or not body:
            return "Podaj subject i body."

        email_ctx = get_request_context()
        if not email_ctx or not email_ctx.smtp:
            return "(ignored – no email context)"

        smtp = email_ctx.smtp
        to = email_ctx.sender or smtp.notify_to
        if email_ctx.timer:
            email_ctx.timer.cancel()

        try:
            mid = smtp.send(to=to, subject=subject, body_text=body)
            return f"Wysłano nowy e-mail do {to}: '{subject}' (mid={mid or '?'})"
        except Exception as e:
            return f"Error: {e}"
