from __future__ import annotations

import logging

from src.runtime.sources import strip_tool_artifacts

logger = logging.getLogger(__name__)

REPLY_PREFIXES = ("re:", "odp:", "aw:", "fwd:", "bot:")


def strip_reply_prefix(subject: str) -> str:
    lower = subject.lower().strip()
    for p in REPLY_PREFIXES:
        if lower.startswith(p):
            return subject[len(p):].strip()
    return subject


def send_email_reply(smtp, sender, subject, body, meta, event, thread_store, memory, attachments=None):
    if not smtp:
        return
    body = strip_tool_artifacts(body)
    is_forward = meta.get("is_forward", False)
    if is_forward:
        base = strip_reply_prefix(subject)
        reply_subject = f"Bot: {base}"
        mid = smtp.send(
            to=sender, subject=reply_subject, body_text=body,
            attachments=attachments or [],
        )
    else:
        reply_subject = subject if subject.lower().startswith(REPLY_PREFIXES) else f"Odp: {subject}"
        mid = smtp.send(
            to=sender, subject=reply_subject, body_text=body,
            in_reply_to=meta.get("message_id"),
            references=meta.get("message_id"),
            attachments=attachments or [],
        )
    if mid:
        thread_store.add_message_id(event.thread, mid)
    if memory:
        memory.append("daily", f"Sent email to {sender}: {reply_subject}")
    logger.info("EMAIL <<< sent to %s: '%s' (%d chars) mid=%s forward=%s", sender, reply_subject, len(body), mid, is_forward)
    return mid
