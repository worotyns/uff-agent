from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.config import load_yaml, resolve_path


@dataclass
class AgentProfile:
    name: str
    soul: str
    model: str
    skills: list[str]
    config: dict[str, Any]


def load_agents(primary: Path, fallback: Path, agent_names: list[str]) -> dict[str, AgentProfile]:
    agents: dict[str, AgentProfile] = {}

    for name in agent_names:
        soul = ""
        for base in (primary, fallback):
            p = base / "agents" / name / "SOUL.md"
            if p.exists():
                soul = p.read_text().strip()
                break

        if not soul:
            for base in (primary, fallback):
                p = base / "SOUL.md"
                if p.exists():
                    soul = p.read_text().strip()
                    break

        cfg: dict[str, Any] = {}
        for base in (primary, fallback):
            p = base / "agents" / name / "config.yaml"
            if p.exists():
                cfg = load_yaml(p)
                break

        agents[name] = AgentProfile(
            name=name,
            soul=soul,
            model=cfg.get("model", "deepseek/deepseek-v4.1-flash"),
            skills=cfg.get("skills", []),
            config=cfg,
        )

    return agents
