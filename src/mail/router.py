from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

PLUS_ALIAS_RE = re.compile(r"^([^+]+)\+(.+)@(.+)$")

DEFAULT_AGENT = "assistant"


def resolve_agent(to_addr: str) -> str:
    for addr in to_addr.split(","):
        addr = addr.strip()
        m = PLUS_ALIAS_RE.match(addr)
        if m:
            persona = m.group(2).lower()
            logger.info("resolved agent '%s' from alias '%s'", persona, addr)
            return persona
    logger.info("no +alias found in '%s', falling back to '%s'", to_addr, DEFAULT_AGENT)
    return DEFAULT_AGENT
