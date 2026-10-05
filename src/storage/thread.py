from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _thread_dir(root: Path, thread_id: str) -> Path:
    h = hashlib.sha256(thread_id.encode()).hexdigest()[:16]
    return root / "threads" / h


class ThreadStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def ensure(self, thread_id: str, agent: str, subject: str = "", participants: str = "") -> Path:
        d = _thread_dir(self.root, thread_id)
        d.mkdir(parents=True, exist_ok=True)

        meta_path = d / "thread.yaml"
        if not meta_path.exists():
            meta = {
                "id": thread_id,
                "agent": agent,
                "subject": subject,
                "participants": participants,
                "state": "active",
                "created": datetime.now(timezone.utc).isoformat(),
                "message_count": 0,
            }
            with open(meta_path, "w") as f:
                json.dump(meta, f, indent=2)
        return d

    def append_message(self, thread_id: str, role: str, content: str, agent: str = "", subject: str = "", participants: str = "") -> None:
        d = self.ensure(thread_id, agent, subject, participants)

        msg = {
            "role": role,
            "content": content,
            "time": datetime.now(timezone.utc).isoformat(),
        }
        msgs_path = d / "messages.jsonl"
        with open(msgs_path, "a") as f:
            f.write(json.dumps(msg) + "\n")

        meta_path = d / "thread.yaml"
        with open(meta_path) as f:
            meta = json.load(f)
        meta["message_count"] = meta.get("message_count", 0) + 1
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        logger.debug("appended %s message to thread %s", role, thread_id)

    def get_messages(self, thread_id: str, limit: int = 50) -> list[dict[str, str]]:
        d = _thread_dir(self.root, thread_id)
        msgs_path = d / "messages.jsonl"
        if not msgs_path.exists():
            return []

        messages: list[dict[str, str]] = []
        with open(msgs_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    messages.append(json.loads(line))
        return messages[-limit:]

    def get_summary(self, thread_id: str) -> str | None:
        d = _thread_dir(self.root, thread_id)
        summary_path = d / "summary.md"
        if summary_path.exists():
            return summary_path.read_text().strip()
        return None

    def save_summary(self, thread_id: str, summary: str) -> None:
        d = _thread_dir(self.root, thread_id)
        (d / "summary.md").write_text(summary)

    def _mid_index_path(self) -> Path:
        return self.root / "threads" / "_mid_index.json"

    def _load_mid_index(self) -> dict[str, str]:
        p = self._mid_index_path()
        if p.exists():
            try:
                return json.loads(p.read_text())
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return {}

    def _save_mid_index(self, idx: dict[str, str]) -> None:
        self._mid_index_path().write_text(json.dumps(idx))

    def add_message_id(self, thread_id: str, message_id: str) -> None:
        if not message_id:
            return
        clean = message_id.strip("<>")
        dir_name = _thread_dir(self.root, thread_id).name
        idx = self._load_mid_index()
        idx[clean] = dir_name
        self._save_mid_index(idx)

        d = _thread_dir(self.root, thread_id)
        meta_path = d / "thread.yaml"
        if not meta_path.exists():
            return
        try:
            meta = json.loads(meta_path.read_text())
            mids = meta.get("message_ids", [])
            if clean not in mids:
                mids.append(clean)
                meta["message_ids"] = mids
                meta_path.write_text(json.dumps(meta, indent=2))
        except Exception:
            pass

    def get_last_message_id(self, thread_id: str) -> str | None:
        d = _thread_dir(self.root, thread_id)
        meta_path = d / "thread.yaml"
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text())
            mids = meta.get("message_ids", [])
            return mids[-1] if mids else None
        except Exception:
            return None

    def get_meta(self, thread_id: str) -> dict[str, str]:
        d = _thread_dir(self.root, thread_id)
        meta_path = d / "thread.yaml"
        if not meta_path.exists():
            return {}
        try:
            return json.loads(meta_path.read_text())
        except Exception:
            return {}

    def record_tool_calls(self, thread_id: str, sources: list[dict]) -> None:
        if not sources:
            return
        d = _thread_dir(self.root, thread_id)
        if not d.exists():
            return
        src_path = d / "sources.jsonl"
        ts = datetime.now(timezone.utc).isoformat()
        with open(src_path, "a") as f:
            for s in sources:
                s = dict(s)
                s["time"] = ts
                f.write(json.dumps(s) + "\n")

    def get_sources(self, thread_id: str) -> list[dict]:
        d = _thread_dir(self.root, thread_id)
        src_path = d / "sources.jsonl"
        if not src_path.exists():
            return []
        sources = []
        with open(src_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    sources.append(json.loads(line))
        return sources

    def get_agent(self, thread_id: str) -> str | None:
        meta = self.get_meta(thread_id)
        return meta.get("agent") or None


