from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.agent import AgentProfile
from src.context import build_context
from src.event import Event
from src.prompts.summarizer import summarize_thread
from src.runtime.attachments import (
    collect_generated_attachments,
    move_sent_attachments,
    pre_analyze_documents,
    process_attachments,
    search_rag_for_email,
)
from src.runtime.mailout import send_email_reply
from src.runtime.server.progress import ProgressTimer
from src.runtime.sources import format_sources_block
from src.thread_ctx import RequestContext, activate
from src.tokenizer import trim_to_budget

if TYPE_CHECKING:
    from src.runtime.app import Runtime

logger = logging.getLogger(__name__)

RECOVERY_PROMPT = (
    "You have run out of tool rounds. "
    "If you have gathered enough data — write the final response.\n"
    "If you need more data — write a partial summary (what you have established so far) "
    "and do NOT ask the user for permission — just outline what still needs to be done. "
    "The user will receive the summary and can continue.\n"
    "Do NOT use tools — just write."
)


def _is_quiet_hours(config) -> bool:
    qh = config.raw.get("user", {}).get("quiet_hours")
    if not qh or not qh.get("start") or not qh.get("end"):
        return False
    now = datetime.now(timezone.utc).strftime("%H:%M")
    start, end = qh["start"], qh["end"]
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


_QUIET_HOURS_BODY = {
    "pl": "Jestem poza godzinami pracy. Odpowiem w godzinach pracy.",
    "en": "I am outside my working hours. I will reply during working hours.",
}


def _handle_quiet_hours(config, smtp, memory, sender, subject, thread) -> bool:
    if not _is_quiet_hours(config) or not smtp:
        return False
    logger.info("quiet hours: deferring %s", sender)
    body = _QUIET_HOURS_BODY.get(config.user_language, _QUIET_HOURS_BODY["en"])
    smtp.send(
        to=sender, subject=f"Re: {subject}",
        body_text=body,
        with_signature=True,
    )
    if memory:
        memory.append("daily", f"Quiet hours: deferred email from {sender}: {subject}")
    return True


def _maybe_summarize_thread(event, thread_store, llm) -> None:
    msg_count = len(thread_store.get_messages(event.thread))
    if msg_count < 10 or msg_count % 5 != 0:
        return
    logger.info("thread %s: %d msgs, summarizing...", event.thread[:12], msg_count)
    summary = summarize_thread(llm, thread_store.get_messages(event.thread, limit=10))
    if summary:
        thread_store.save_summary(event.thread, summary)


def handle_email(event: Event, agent: AgentProfile, rt: "Runtime") -> None:
    meta = event.metadata
    sender = meta.get("from", "?")
    subject = meta.get("subject", "(no subject)")
    attachments = meta.get("attachments", [])

    logger.info("EMAIL >>> from=%s subject='%s' thread=%.12s att=%d",
                 sender, subject, event.thread, len(attachments))

    rt.thread_store.append_message(
        thread_id=event.thread, role="user", content=event.message,
        agent=agent.name, subject=subject, participants=sender,
    )

    if _handle_quiet_hours(rt.config, rt.smtp, rt.memory, sender, subject, event.thread):
        return

    recipient_type = meta.get("recipient_type", "to")
    _silent = recipient_type in ("cc", "bcc")
    if _silent:
        logger.info("silent processing: recipient_type=%s, will process but NOT reply", recipient_type)

    attachment_notes = process_attachments(attachments, agent, rt.skills, rt.data_root, rt.config, event)

    history = rt.thread_store.get_messages(event.thread, limit=20)
    summary = rt.thread_store.get_summary(event.thread)
    logger.info("context: history=%d msgs summary=%s att_notes=%d",
                 len(history), bool(summary), len(attachment_notes))

    if attachment_notes and history:
        block = "\n\n".join(attachment_notes)
        last_user = history[-1] if history and history[-1]["role"] == "user" else None
        if last_user:
            last_user["content"] += f"\n\n[Attached documents:]\n\n{block}"
        else:
            history.append({"role": "user", "content": f"[Attached documents:]\n\n{block}"})
    else:
        last_user = None

    rag_sources: list[dict] = []
    if "rag" in agent.skills and any(len(a.get("filename", "")) > 0 for a in attachments):
        rag_context, rag_sources = search_rag_for_email(rt.data_root, event.message, attachments, thread_id=event.thread)
        if rag_context:
            if last_user:
                last_user["content"] += f"\n\n[Relevant RAG results:]\n\n{rag_context}"
            else:
                history.append({"role": "user", "content": f"[Relevant RAG results:]\n\n{rag_context}"})

    analysis_block = pre_analyze_documents(attachments, event.thread, rt.llm, agent.model, rt.data_root, language=rt.config.user_language)
    if analysis_block:
        if last_user:
            last_user["content"] += f"\n\n{analysis_block}"
        else:
            history.append({"role": "user", "content": analysis_block})

    timer = ProgressTimer(rt.smtp if not _silent else None, sender, subject, meta.get("message_id"), language=rt.config.user_language)
    timer.start()
    root = rt.data_root
    ctx = RequestContext(
        smtp=rt.smtp if not _silent else None,
        sender=sender,
        subject=subject,
        in_reply_to=meta.get("message_id"),
        data_root=root,
        thread_id=event.thread,
        thread_attachments_dir=root / "threads" / event.thread[:16] / "attachments",
        timer=timer,
        bg_store=rt.background_tasks,
        agent=agent.name,
    )
    try:
        with activate(ctx):
            context_messages = build_context(agent=agent, config=rt.config, messages=history, thread_summary=summary, memory=rt.memory)
            context_messages = trim_to_budget(context_messages, agent.model, reserve_tokens=2048)

            tools_list = rt.runner.tools_for(agent)
            if tools_list:
                logger.info("LLM tools: %d available", len(tools_list))

            max_rounds = (agent.config.get("max_tool_rounds", rt.config.max_tool_rounds)) + 1
            result = rt.runner.run(
                agent, context_messages, tools_list,
                max_rounds=max_rounds,
                thread_id=event.thread,
                thread_subject=subject,
                recovery_prompt=RECOVERY_PROMPT,
            )
            if result.failed:
                return

            all_sources: list[dict] = list(rag_sources) + result.sources
            sent_new_email = any(s.get("tool") == "send_email" for s in result.sources)
            reply_content = result.content
            if not reply_content:
                logger.warning("LLM empty response — dropping email for %s", sender)
                rt.thread_store.append_message(event.thread, role="assistant", content="(empty response)")
                return

            if all_sources:
                rt.thread_store.record_tool_calls(event.thread, all_sources)
                if agent.config.get("show_sources", False):
                    src_block = format_sources_block(all_sources, language=rt.config.user_language)
                    if src_block:
                        reply_content = reply_content + "\n\n" + src_block

            rt.thread_store.append_message(event.thread, role="assistant", content=reply_content)

            if rt.memory:
                if _silent:
                    rt.memory.append("daily", f"CC/BCC processed from {sender}: {reply_content[:200]}")
                elif sent_new_email:
                    rt.memory.append("daily", f"Sent new email to {sender}: {reply_content[:200]}")
                else:
                    rt.memory.append("daily", f"Email reply to {sender}: {reply_content[:200]}")

            _maybe_summarize_thread(event, rt.thread_store, rt.llm)

            timer.cancel()
            if _silent:
                logger.info("skipping email reply (recipient_type=%s)", recipient_type)
            elif sent_new_email:
                logger.info("skipping email reply — agent sent a new email via send_email")
            else:
                generated = collect_generated_attachments(
                    event, rt.data_root,
                    original_filenames={a.get("filename", "") for a in attachments} if attachments else set(),
                )
                mid = send_email_reply(rt.smtp, sender, subject, reply_content, meta, event, rt.thread_store, rt.memory, attachments=generated)
                if mid and generated:
                    move_sent_attachments(event, rt.data_root, generated)
    finally:
        timer.cancel()
