from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEDULE_RE = re.compile(
    r"^(daily|weekdays|hourly|weekly|interval)\s*(?::(\d{2})|(\d{1,2})(?::(\d{2}))?)?$", re.IGNORECASE
)


def _parse_schedule(schedule_str: str) -> dict[str, Any]:
    m = SCHEDULE_RE.match(schedule_str.strip())
    if not m:
        raise ValueError(
            f"invalid schedule: '{schedule_str}'. Use: daily 08:00, weekdays 09:30, hourly :15, interval 30"
        )
    kind = m.group(1).lower()
    result: dict[str, Any] = {"type": kind}

    col_min = m.group(2)
    hour_val = m.group(3)
    min_val = m.group(4)

    if kind in ("daily", "weekdays"):
        result["hour"] = int(hour_val) if hour_val else 8
        result["minute"] = int(min_val) if min_val else 0
    elif kind == "hourly":
        result["minute"] = int(col_min) if col_min else (int(hour_val) if hour_val else 0)
    elif kind == "interval":
        val = int(col_min) if col_min else (int(hour_val) if hour_val else 30)
        result["minutes"] = val
    elif kind == "weekly":
        result["hour"] = int(hour_val) if hour_val else 8
        result["minute"] = int(min_val) if min_val else 0
    return result


def _next_run(schedule: dict[str, Any], after: datetime | None = None) -> datetime:
    now = after or datetime.now(timezone.utc)
    t = schedule
    if t["type"] == "daily":
        candidate = now.replace(hour=t["hour"], minute=t["minute"], second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    elif t["type"] == "weekdays":
        candidate = now.replace(hour=t["hour"], minute=t["minute"], second=0, microsecond=0)
        for _ in range(8):
            if candidate > now and candidate.weekday() < 5:
                return candidate
            candidate += timedelta(days=1)
        return candidate
    elif t["type"] == "hourly":
        candidate = now.replace(minute=t["minute"], second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(hours=1)
        return candidate
    elif t["type"] == "weekly":
        candidate = now.replace(hour=t["hour"], minute=t["minute"], second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate
    elif t["type"] == "interval":
        return now + timedelta(minutes=t["minutes"])
    return now + timedelta(days=1)


class ScriptStore:
    def __init__(self, root: Path) -> None:
        self.scripts_dir = root / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, name: str) -> Path:
        return self.scripts_dir / f"{name}.json"

    def _code_path(self, name: str) -> Path:
        return self.scripts_dir / f"{name}.py"

    def add(
        self,
        name: str,
        code: str,
        schedule_str: str,
        owner: str = "",
        thread_id: str = "",
        thread_subject: str = "",
    ) -> dict[str, Any]:
        if not re.match(r"^[a-zA-Z0-9_]+$", name):
            raise ValueError(f"invalid script name: '{name}'. Use letters, digits, underscore only.")

        parsed = _parse_schedule(schedule_str)
        now = datetime.now(timezone.utc)

        meta: dict[str, Any] = {
            "name": name,
            "schedule": schedule_str,
            "schedule_parsed": parsed,
            "owner": owner,
            "thread_id": thread_id,
            "thread_subject": thread_subject,
            "created": now.isoformat(),
            "last_run": None,
            "next_run": _next_run(parsed, now).isoformat(),
            "run_count": 0,
        }

        self._code_path(name).write_text(code)
        self._meta_path(name).write_text(json.dumps(meta, indent=2))
        logger.info("script added: %s (schedule=%s)", name, schedule_str)
        return meta

    def get_meta(self, name: str) -> dict[str, Any] | None:
        p = self._meta_path(name)
        if not p.exists():
            return None
        return json.loads(p.read_text())

    def get_code(self, name: str) -> str | None:
        p = self._code_path(name)
        if not p.exists():
            return None
        return p.read_text()

    def list(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for f in sorted(self.scripts_dir.iterdir()):
            if f.suffix == ".json":
                try:
                    result.append(json.loads(f.read_text()))
                except (json.JSONDecodeError, OSError):
                    continue
        return result

    def delete(self, name: str) -> bool:
        removed = False
        for p in (self._meta_path(name), self._code_path(name)):
            if p.exists():
                p.unlink()
                removed = True
        if removed:
            logger.info("script deleted: %s", name)
        return removed

    def get_due(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        due: list[dict[str, Any]] = []
        for meta in self.list():
            if not meta.get("next_run"):
                continue
            if meta["next_run"] <= now:
                due.append(meta)
        return due

    def run(self, name: str, timeout: int = 30) -> str:
        code = self.get_code(name)
        if code is None:
            return f"Error: script '{name}' not found."

        logger.info("running script: %s (timeout=%ds)", name, timeout)
        t0 = time.time()

        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.warning("script %s timed out after %ds", name, timeout)
            return f"Script timed out after {timeout}s."
        except Exception as e:
            logger.error("script %s failed: %s", name, e)
            return f"Script error: {e}"

        dt = time.time() - t0
        output = result.stdout.strip()
        errors = result.stderr.strip()

        if result.returncode != 0:
            logger.warning("script %s exited code %d in %.1fs", name, result.returncode, dt)
            return f"Script exited code {result.returncode} ({dt:.1f}s):\n{errors}\n{output}"
        if errors:
            logger.info("script %s done (%.1fs) stderr: %s", name, dt, errors[:200])

        logger.info("script %s done (%.1fs) stdout: %s", name, dt, output[:200])
        return output

    def update_next_run(self, name: str, after: datetime | None = None) -> None:
        meta = self.get_meta(name)
        if not meta:
            return
        parsed = meta.get("schedule_parsed", {})
        if not parsed:
            return
        meta["last_run"] = datetime.now(timezone.utc).isoformat()
        meta["next_run"] = _next_run(parsed, after).isoformat()
        meta["run_count"] = meta.get("run_count", 0) + 1
        self._meta_path(name).write_text(json.dumps(meta, indent=2))
