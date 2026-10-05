from __future__ import annotations

import importlib.util
import inspect
import logging
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BaseSkill(ABC):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        ...

    def to_openrouter_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": [k for k, v in self.parameters.items() if v.get("required")],
                },
            },
        }


class SkillRegistry:
    def __init__(self, root: Path) -> None:
        self._skills: dict[str, BaseSkill] = {}
        self._skill_names: dict[str, str] = {}
        self._load_skills(root)

    def _load_skills(self, root: Path) -> None:
        skills_dir = root / "skills"
        if not skills_dir.is_dir():
            return
        for d in sorted(skills_dir.iterdir()):
            if not d.is_dir():
                continue

            self._check_requirements(d)

            tool_path = d / "tool.py"
            if not tool_path.exists():
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"skills.{d.name}.tool", tool_path)
                if not spec or not spec.loader:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for _name, obj in inspect.getmembers(mod, inspect.isclass):
                    if issubclass(obj, BaseSkill) and obj is not BaseSkill:
                        instance = obj()
                        self._skills[instance.name] = instance
                        self._skill_names[instance.name] = d.name
                        logger.info("loaded skill: %s from %s", instance.name, d.name)
            except ImportError as e:
                logger.warning("skill %s missing dependency: %s", d.name, e)
            except Exception as e:
                logger.warning("failed to load skill from %s: %s", d, e)

    @staticmethod
    def _check_requirements(d: Path) -> None:
        req_path = d / "requirements.txt"
        if not req_path.exists():
            return
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_path), "--dry-run", "--quiet"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0 and "already satisfied" not in result.stderr:
                logger.warning("skill %s has unmet requirements: %s", d.name, result.stderr.strip())
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

    def get_tools(self) -> list[dict[str, Any]]:
        return [s.to_openrouter_tool() for s in self._skills.values()]

    def get_tools_for_agent(self, agent_skills: list[str]) -> list[dict[str, Any]]:
        return [
            s.to_openrouter_tool()
            for s in self._skills.values()
            if self._skill_names.get(s.name, "") in agent_skills
        ]

    def execute(self, skill_name: str, **kwargs: Any) -> Any:
        skill = self._skills.get(skill_name)
        if not skill:
            raise ValueError(f"unknown skill: {skill_name}")
        return skill.execute(**kwargs)

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())
