from __future__ import annotations

from pathlib import Path
from typing import Any

from src.thread_ctx import get_data_root
from src.tools.skill import BaseSkill

SENSITIVE_FILES = {
    ".env",
    "config.yaml",
    "config.yml",
    "*.pem",
    "*.key",
    "*.secret",
    "id_rsa",
    "id_ed25519",
}


def _is_sensitive(path: str | Path) -> bool:
    name = Path(path).name
    if name in SENSITIVE_FILES:
        return True
    for pattern in SENSITIVE_FILES:
        if pattern.startswith("*") and name.endswith(pattern[1:]):
            return True
    return False


def _safe_path(path: str) -> Path | None:
    root = get_data_root()
    target = (root / path).resolve()
    if not str(target).startswith(str(root)):
        return None
    if _is_sensitive(target):
        return None
    return target


def _find_file(path: str) -> Path | None:
    allowed = get_data_root()
    target = (allowed / path).resolve()
    if str(target).startswith(str(allowed)) and target.exists():
        if _is_sensitive(target):
            return None
        return target

    name = Path(path).name
    if _is_sensitive(name):
        return None
    for att_dir in sorted(allowed.glob("threads/*/attachments")):
        match = att_dir / name
        if match.exists():
            return match
        txt = att_dir / f"{name}.txt"
        if txt.exists():
            return txt

    return None


class ReadFile(BaseSkill):
    name = "read_file"
    description = "Read a file. Checks data directory first, then searches email thread attachments."
    parameters = {
        "path": {"type": "string", "description": "Filename or relative path (e.g. 'file.pdf' or 'subdir/file.txt')"},
    }

    TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log"}

    def execute(self, **kwargs: Any) -> str:
        path = kwargs.get("path", "")
        full = _find_file(path)
        if not full:
            return f"File not found: {path}"

        if full.suffix.lower() not in self.TEXT_EXTS:
            txt = full.with_name(f"{full.name}.txt")
            if txt.exists():
                full = txt

        try:
            return full.read_text()
        except Exception as e:
            return f"Error reading {full.name}: {e}"


class WriteFile(BaseSkill):
    name = "write_file"
    description = "Write content to a file in the agent's data directory"
    parameters = {
        "path": {"type": "string", "description": "Relative path within data directory"},
        "content": {"type": "string", "description": "Content to write"},
    }

    def execute(self, **kwargs: Any) -> str:
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")
        full = _safe_path(path)
        if not full:
            return f"Access denied: {path}"
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
            return f"Written {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error writing {path}: {e}"


class ListDir(BaseSkill):
    name = "list_dir"
    description = "List contents of a directory"
    parameters = {
        "path": {"type": "string", "description": "Relative path within data directory"},
    }

    def execute(self, **kwargs: Any) -> str:
        path = kwargs.get("path", ".")
        full = _safe_path(path)
        if not full:
            return f"Access denied: {path}"
        try:
            entries = [str(e.relative_to(full)) + ("/" if e.is_dir() else "") for e in sorted(full.iterdir())]
            return "\n".join(entries) if entries else "(empty)"
        except Exception as e:
            return f"Error listing {path}: {e}"
