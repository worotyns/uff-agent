from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.agent import AgentProfile
from src.context import build_context
from src.event import Event
from src.llm.openrouter import InsufficientCreditsError
from src.runtime.handlers import EVENT_HANDLERS
from src.tokenizer import trim_to_budget

if TYPE_CHECKING:
    from src.runtime.app import Runtime

logger = logging.getLogger(__name__)


def notify_admin_credits(smtp, agent_name: str, event: Event, to: str = "") -> None:
    if not to or not smtp:
        logger.error("CREDITS EXCEEDED for agent %s — no admin notify address configured", agent_name)
        return
    user = (event.metadata or {}).get("from", "unknown")
    smtp.send(
        to=to,
        subject=f"Agent {agent_name} – przekroczono limit LLM",
        body_text=(
            f"Agent {agent_name} przekroczył limit OpenRouter ($9/miesiąc).\n\n"
            f"Ostatnie zdarzenie: {event.type}\n"
            f"Użytkownik: {user}\n"
            f"Wątek: {event.thread}\n\n"
            f"Konieczne: zwiększenie limitu lub kontakt z klientem."
        ),
    )
    logger.info("admin notified about credit limit for agent %s", agent_name)


def handle_default(event: Event, agent: AgentProfile, rt: "Runtime") -> None:
    logger.info("context: building with message='%.80s'", event.message or "")
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

    rt.queue.push(Event(
        type=f"{event.type}.response",
        agent=agent.name,
        thread=event.thread,
        message=result.content,
        metadata={"model": result.model, "usage": result.usage, "took_s": result.took_s},
    ))


def process_event(event: Event, rt: "Runtime") -> None:
    agent = rt.agents.get(event.agent) if event.agent else None
    if not agent and event.agent:
        logger.warning("no agent profile for '%s', skipping event %s", event.agent, event.type)
        rt.queue.mark_done(event)
        return
    if not agent and rt.agents:
        agent = next(iter(rt.agents.values()))
        logger.info("event %s: using default agent '%s'", event.type, agent.name)

    if event.type != "heartbeat.tick":
        logger.info("=== event: %s | agent: %s | thread: %.12s ===", event.type, agent.name, event.thread)

    try:
        handler = EVENT_HANDLERS.get(event.type, handle_default)
        handler(event, agent, rt)
    except InsufficientCreditsError:
        notify_admin_credits(rt.smtp, agent.name, event, to=rt.config.admin_notify_email)
    finally:
        rt.queue.mark_done(event)
