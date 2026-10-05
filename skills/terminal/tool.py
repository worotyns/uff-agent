from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any

from src.thread_ctx import get_data_root
from src.tools.skill import BaseSkill

logger = logging.getLogger(__name__)

BLOCKED_COMMANDS = [
    "rm -rf /", "mkfs", "dd if=", "> /dev/", ":(){ :|:& };:", "wget http://", "curl http://",
    "nc -e", "bash -i", "python -c 'import os; os.system", "chmod 777",
    "/.env", " .env",
    "config.yaml", "config.yml",
    "/proc/self/environ",
]

BLOCKED_PREFIXES = [
    "sudo", "su ", "passwd", "useradd", "usermod", "groupadd",
    "iptables", "ufw", "systemctl", "service ",
    "docker ", "podman ",
]


class RunCommand(BaseSkill):
    name = "run_command"
    description = "Execute a shell command in the agent's workspace. Returns stdout+stderr. Use for installing Python libraries (pip install), creating files, running tools."
    parameters = {
        "command": {"type": "string", "description": "Shell command to execute"},
        "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)", "default": 30},
    }

    def execute(self, **kwargs: Any) -> str:
        command = kwargs.get("command", "")
        timeout = int(kwargs.get("timeout", 30))
        timeout = min(timeout, 60)

        if not command:
            return "No command provided."

        if violation := self._check_blocked(command):
            return f"Command blocked: {violation}"

        logger.info("command: %.200s (timeout=%ds)", command, timeout)
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=get_data_root(),
            )
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s."
        except Exception as e:
            return f"Command failed: {e}"

        output = result.stdout or ""
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"

        output = output[:5000]
        logger.info("command exit=%d output=%d chars", result.returncode, len(output))
        return f"exit code: {result.returncode}\n{output}" if output else f"exit code: {result.returncode}"

    @staticmethod
    def _check_blocked(command: str) -> str | None:
        lower = command.lower().strip()
        for blocked in BLOCKED_COMMANDS:
            if blocked in lower:
                return f"matches blocked pattern: {blocked}"
        for prefix in BLOCKED_PREFIXES:
            if lower.startswith(prefix):
                return f"starts with blocked prefix: {prefix}"
        return None


class RunPython(BaseSkill):
    name = "run_python"
    description = "Execute Python code and return the result. Useful for data processing, file manipulation, etc."
    parameters = {
        "code": {"type": "string", "description": "Python code to execute"},
        "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)", "default": 30},
    }

    def execute(self, **kwargs: Any) -> str:
        code = kwargs.get("code", "")
        timeout = int(kwargs.get("timeout", 30))
        timeout = min(timeout, 60)

        if not code:
            return "No code provided."

        if "import os" in code and ("system(" in code or "popen(" in code):
            return "Code blocked: use run_command instead of os.system/os.popen."

        logger.info("python: %.200s", code)
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=timeout, cwd=get_data_root(),
            )
        except subprocess.TimeoutExpired:
            return f"Python timed out after {timeout}s."
        except Exception as e:
            return f"Python failed: {e}"

        output = result.stdout or ""
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        output = output[:5000]
        return output if output else "(no output)"
