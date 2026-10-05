from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are a document analyst. Analyze the document below and return a concise, structured summary. Use only information present in the document — do not invent details. If a section is not applicable, write "(none)".

## Purpose
What is the document about?

## Parties / key entities
Who is involved (people, companies, organizations)?

## Key facts and figures
Dates, amounts, identifiers, and other concrete details.

## Obligations and actions
What must be done, by whom, and by when?

## Risks and notes
Anything unclear, risky, conditional, or worth flagging.

--- DOCUMENT ---

"""


def document_analysis(
    text: str,
    llm: Any,
    model: str,
    filename: str = "",
    language: str = "",
) -> str:
    if not text or len(text.strip()) < 50:
        logger.info("document_analysis: skipping %s — too short (%d chars)", filename, len(text or ""))
        return ""

    input_text = text[:15000]
    system = ANALYSIS_PROMPT
    if language:
        system += f"\nRespond in {language}."

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": input_text},
    ]

    logger.info("document_analysis >>> %s (%d chars)", filename or "?", len(input_text))
    try:
        response = llm.chat(messages, tools=None, model=model, max_tokens=2048, temperature=0.3)
        if response and response.content:
            result = response.content.strip()
            logger.info("document_analysis <<< %s: %d chars", filename or "?", len(result))
            return result
    except Exception as e:
        logger.warning("document_analysis failed for %s: %s", filename, e)

    return ""
