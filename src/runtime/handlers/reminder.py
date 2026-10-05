from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.agent import AgentProfile
from src.context import build_context
from src.event import Event
from src.runtime.mailout import send_email_reply
from src.runtime.sources import format_sources_block, strip_tool_artifacts
from src.storage.script_runner import ScriptStore
from src.tokenizer import trim_to_budget

if TYPE_CHECKING:
    from src.runtime.app import Runtime

logger = logging.getLogger(__name__)


def handle_reminder(event: Event, agent: AgentProfile, rt: "Runtime") -> None:
    if rt.memory:
        rt.memory.append("daily", f"Reminder fired: {event.message}")

    script_name = (event.metadata or {}).get("script", "")
    if script_name and rt.data_root:
        output = ScriptStore(rt.data_root).run(script_name)
        if output and not output.startswith("Error") and not output.startswith("Script timed out"):
            event.message = f"{event.message}\n\nScript '{script_name}' output:\n{output[:3000]}"

    messages = build_context(
        agent=agent,
        config=rt.config,
        messages=[{"role": "user", "content": event.message}] if event.message else None,
        thread_summary=event.metadata.get("summary"),
        memory=rt.memory,
    )
    messages = trim_to_budget(messages, agent.model)

    tools_list = rt.runner.tools_for(agent)
    if tools_list:
        logger.info("LLM tools available: %d (%s)", len(tools_list), ", ".join(t["function"]["name"] for t in tools_list))

    max_rounds = (agent.config.get("max_tool_rounds", rt.config.max_tool_rounds)) + 1
    result = rt.runner.run(agent, messages, tools_list, max_rounds=max_rounds, polish=False)
    if result.failed:
        rt.queue.push(Event(type=f"{event.type}.failed", agent=agent.name, thread=event.thread, message=result.error))
        return

    sent_new_email = any(s.get("tool") == "send_email" for s in result.sources)
    body = strip_tool_artifacts(result.content or event.message or "")
    if result.sources and rt.thread_store and event.thread:
        rt.thread_store.record_tool_calls(event.thread, result.sources)
        if agent.config.get("show_sources", False):
            src_block = format_sources_block(result.sources, language=rt.config.user_language)
            if src_block:
                body = body + "\n\n" + src_block

    if rt.smtp and body and not sent_new_email:
        mode = (event.metadata or {}).get("mode", "thread")
        if mode == "new" or not event.thread:
            subject = event.message.strip() if event.message else "Przypomnienie"
            rt.smtp.send(to=rt.smtp.notify_to, subject=subject, body_text=body)
        else:
            tmeta = rt.thread_store.get_meta(event.thread) if rt.thread_store else {}
            sender = tmeta.get("participants", "")
            subject = tmeta.get("subject", "")
            mid = rt.thread_store.get_last_message_id(event.thread) if rt.thread_store else None
            if sender and body:
                send_email_reply(rt.smtp, sender, subject, body, {"message_id": mid, "in_reply_to": mid}, event, rt.thread_store, rt.memory)
