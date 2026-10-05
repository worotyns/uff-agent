from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def prune_old_daily_logs(daily_dir: Path, keep: int = 30) -> None:
    files = sorted(daily_dir.glob("*.md"))
    if len(files) <= keep:
        return
    for f in files[:-keep]:
        f.unlink()
        logger.info("pruned old daily log: %s", f.name)


def prune_old_processed_events(root: Path, days: int = 7) -> None:
    ev_dir = root / "events" / "processed"
    if not ev_dir.exists():
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    removed = 0
    for f in list(ev_dir.iterdir()):
        if f.suffix != ".json":
            continue
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info("dream cycle: removed %d old processed events (>%d days)", removed, days)

    cleanup_empty_dirs(root / "events" / "pending" / "attachments")


def cleanup_empty_dirs(dir: Path) -> None:
    if not dir.is_dir():
        return
    for child in list(dir.iterdir()):
        if child.is_dir():
            cleanup_empty_dirs(child)
            try:
                if not any(child.iterdir()):
                    child.rmdir()
                    logger.debug("removed empty dir: %s", child)
            except OSError:
                pass
