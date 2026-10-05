from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.agent import AgentProfile
from src.event import Event
from src.runtime.mailout import send_email_reply
from src.runtime.sources import strip_tool_artifacts
from src.storage.script_runner import ScriptStore

if TYPE_CHECKING:
    from src.runtime.app import Runtime

logger = logging.getLogger(__name__)


def handle_script(event: Event, agent: AgentProfile, rt: "Runtime") -> None:
    meta = event.metadata or {}
    name = meta.get("script_name", event.message or "?")
    logger.info("SCRIPT RUN >>> %s", name)

    store = ScriptStore(rt.data_root)
    output = store.run(name)
    store.update_next_run(name)

    if rt.memory:
        rt.memory.append("daily", f"Script '{name}' ran: {output[:200]}")

    if not output or output.startswith("Error") or output.startswith("Script timed out"):
        logger.warning("script %s produced no output or error", name)
        return

    prompt = (
        f"The following data was produced by the '{name}' script:\n\n"
        f"{output[:4000]}\n\n"
        f"Write a friendly message about this data."
    )

    try:
        resp = rt.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            model=agent.model,
            max_tokens=1024,
        )
    except Exception as e:
        logger.error("LLM call for script %s failed: %s", name, e)
        return

    if not resp or not resp.content:
        logger.warning("LLM returned empty response for script %s", name)
        return

    body = strip_tool_artifacts(resp.content)

    if rt.thread_store and event.thread:
        tmeta = rt.thread_store.get_meta(event.thread)
        sender = tmeta.get("participants", "")
        subject = tmeta.get("subject", "")
        if sender:
            mid = rt.thread_store.get_last_message_id(event.thread)
            send_email_reply(rt.smtp, sender, subject, body, {"message_id": mid, "in_reply_to": mid}, event, rt.thread_store, rt.memory)
            return

    if not rt.smtp:
        logger.warning("script %s produced output but no SMTP is configured", name)
        return

    rt.smtp.send(
        to=rt.smtp.notify_to,
        subject=meta.get("thread_subject", "") or f"Script: {name}",
        body_text=body,
    )
    logger.info("SCRIPT RUN <<< %s: email sent to %s", name, rt.smtp.notify_to)
