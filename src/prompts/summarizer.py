from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.llm.openrouter import OpenRouterClient
from src.storage.housekeeping import prune_old_daily_logs, prune_old_processed_events

logger = logging.getLogger(__name__)

COMPRESS_PROMPT = """Summarize the following conversation concisely.
Focus on: decisions made, key facts established, action items.
Return a markdown summary under 300 words."""


def summarize_thread(llm: OpenRouterClient, messages: list[dict[str, str]]) -> str:
    try:
        response = llm.chat(
            messages=[
                {"role": "system", "content": COMPRESS_PROMPT},
                *messages[-10:],
            ],
            max_tokens=1024,
            temperature=0.3,
        )
        return response.content.strip()
    except Exception as e:
        logger.error("thread summarization failed: %s", e)
        return ""


DREAM_PROMPT = """You are a memory consolidation system.

Given:
1. Existing long-term facts (## Existing Facts)
2. Existing long-term decisions (## Existing Decisions)
3. New daily logs (## Daily Logs)

Your task:
1. Merge the new information into the existing facts — add new facts, update superseded ones, remove outdated or contradicted ones.
2. Merge the new information into the existing decisions the same way.
3. Keep everything that is still relevant and correct, even if it appears only in the existing facts/decisions and not in the recent logs. Do NOT drop a fact just because it is old.

Return updated:
## Facts
(list)

## Decisions
(list)"""


def run_dream_cycle(llm: OpenRouterClient, memory_root: Path) -> dict[str, str]:
    prune_old_processed_events(memory_root)

    daily_dir = memory_root / "memory"
    if not daily_dir.exists():
        return {"facts": "", "decisions": ""}

    recent_logs = []
    for f in sorted(daily_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"), reverse=True)[:30]:
        content = f.read_text().strip()
        if content:
            recent_logs.append(f"# {f.stem}\n{content}")

    if not recent_logs:
        prune_old_daily_logs(daily_dir, keep=30)
        return {"facts": "", "decisions": ""}

    facts_path = memory_root / "memory" / "facts.md"
    decisions_path = memory_root / "memory" / "decisions.md"

    existing_facts = facts_path.read_text().strip() if facts_path.exists() else ""
    existing_decisions = decisions_path.read_text().strip() if decisions_path.exists() else ""

    combined = "\n\n".join(recent_logs)
    user_prompt = (
        f"## Existing Facts\n{existing_facts or '(none)'}\n\n"
        f"## Existing Decisions\n{existing_decisions or '(none)'}\n\n"
        f"## Daily Logs\n{combined}"
    )
    try:
        response = llm.chat(
            messages=[
                {"role": "system", "content": DREAM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=2048,
            temperature=0.3,
        )
        result = response.content.strip()
    except Exception as e:
        logger.error("dream cycle failed: %s", e)
        prune_old_daily_logs(daily_dir, keep=30)
        return {"facts": "", "decisions": ""}

    facts_section = ""
    decisions_section = ""
    current_section = ""
    for line in result.split("\n"):
        if line.startswith("## Facts"):
            current_section = "facts"
            continue
        elif line.startswith("## Decisions"):
            current_section = "decisions"
            continue
        if current_section == "facts":
            facts_section += line + "\n"
        elif current_section == "decisions":
            decisions_section += line + "\n"

    if facts_section.strip():
        facts_path.write_text(facts_section.strip())
    if decisions_section.strip():
        decisions_path.write_text(decisions_section.strip())

    prune_old_daily_logs(daily_dir, keep=30)

    logger.info("dream cycle completed: facts=%d chars, decisions=%d chars", len(facts_section), len(decisions_section))
    return {"facts": facts_section.strip(), "decisions": decisions_section.strip()}
