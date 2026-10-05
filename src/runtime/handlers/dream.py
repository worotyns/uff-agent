from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.agent import AgentProfile
from src.event import Event
from src.prompts.summarizer import run_dream_cycle

if TYPE_CHECKING:
    from src.runtime.app import Runtime

logger = logging.getLogger(__name__)


def handle_dream(event: Event, agent: AgentProfile, rt: "Runtime") -> None:
    run_dream_cycle(rt.llm, rt.data_root)
