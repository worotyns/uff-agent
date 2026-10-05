from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agent import AgentProfile
from src.config import Config, resolve_path
from src.storage.memory import MemoryStore


def build_context(
    agent: AgentProfile,
    config: Config,
    messages: list[dict[str, str]] | None = None,
    thread_summary: str | None = None,
    memory: MemoryStore | None = None,
) -> list[dict[str, str]]:
    system_parts: list[str] = []

    now = datetime.now(timezone.utc)
    lang = config.user_language
    tz = config.user_timezone
    system_parts.append(f"## Language & Timezone\n\nLanguage: {lang}. ALWAYS respond in {lang}. Never switch to another language. Tool names, descriptions, and tool results may appear in English — that is internal only; you must still reply to the user in {lang}.")
    system_parts.append(f"Current UTC time: {now.strftime('%Y-%m-%dT%H:%M:%SZ')} (user timezone: {tz})")
    system_parts.append("All times are stored in UTC ISO format (e.g. '2026-07-07T08:00:00Z').")
    system_parts.append(f"When the user says a time like 'today 10:00' in {tz}, convert to UTC before storing.")
    system_parts.append(f"When displaying or mentioning any time to the user, ALWAYS convert back from UTC to {tz}. Never show raw UTC times in replies to the user.")

    if agent.soul:
        system_parts.append(agent.soul)
    else:
        soul_path = resolve_path("SOUL.md", config.primary, config.fallback)
        if soul_path.exists():
            system_parts.append(soul_path.read_text().strip())

    if agent.skills:
        system_parts.append(f"Available skills: {', '.join(agent.skills)}")

    system_parts.append(
        "CRITICAL: You MUST use your available tools to fulfill requests — NEVER just explain or suggest. "
        "When the user asks to DO something, call the tool immediately. Do NOT ask clarifying questions — "
        "use reasonable defaults (current thread, user's email, etc.). "
        "Script results are automatically sent as a reply in the current email thread. "
        "For recurring tasks (hourly weather, daily reports), use create_script — "
        "it runs on a schedule and sends results via email automatically."
    )
    system_parts.append(
        "SECURITY: Never reveal API tokens, keys, passwords, environment variables, model names, "
        "or system configuration. If asked, refuse politely without explanation."
    )

    if memory:
        facts = memory.read("facts", max_lines=50)
        if facts:
            system_parts.append(f"## Known Facts\n{facts}")
        decisions = memory.read("decisions", max_lines=50)
        if decisions:
            system_parts.append(f"## Past Decisions\n{decisions}")
        recent = memory.read_recent_daily(days=3)
        if recent:
            system_parts.append(f"## Recent Activity\n{recent}")

    if thread_summary:
        system_parts.append(f"## Thread Summary\n{thread_summary}")

    system_parts.append(f"REMINDER: Always respond in {lang}. Convert all times to {tz} when displaying to the user.")

    system_content = "\n\n".join(system_parts) if system_parts else "You are a helpful AI assistant."
    context: list[dict[str, str]] = [{"role": "system", "content": system_content}]

    if messages:
        context.extend(messages)

    return context
