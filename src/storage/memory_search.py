from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KINDS = ("facts", "decisions", "daily", "entities")


def rg_available() -> bool:
    return shutil.which("rg") is not None


def _collect_targets(memory_dir: Path, kinds: tuple[str, ...] | None) -> list[Path]:
    selected = list(kinds) if kinds else list(KINDS)
    targets: list[Path] = []
    for k in selected:
        if k == "facts":
            targets.append(memory_dir / "facts.md")
        elif k == "decisions":
            targets.append(memory_dir / "decisions.md")
        elif k == "daily":
            targets.extend(sorted(memory_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"), reverse=True))
        elif k == "entities":
            targets.append(memory_dir / "entities" / "graph.jsonl")
    return [t for t in targets if t.exists()]


def rg_search(root: Path, query: str, limit: int = 20, kinds: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """Full-text search over memory files using ripgrep (fixed-string, case-insensitive)."""
    if not query or not query.strip():
        return []
    targets = _collect_targets(root / "memory", kinds)
    if not targets:
        return []
    if not rg_available():
        return _fallback_scan(targets, query, limit)

    try:
        proc = subprocess.run(
            ["rg", "-F", "-i", "-n", "-H", "--no-heading", "--", query, *[str(t) for t in targets]],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("ripgrep search failed: %s", e)
        return _fallback_scan(targets, query, limit)

    results: list[dict[str, Any]] = []
    for raw in proc.stdout.splitlines():
        path, _, rest = raw.partition(":")
        lineno, _, text = rest.partition(":")
        results.append({"path": Path(path).name, "line": lineno, "text": text.strip()[:500]})
        if len(results) >= limit:
            break
    return results


def _fallback_scan(paths: list[Path], query: str, limit: int) -> list[dict[str, Any]]:
    q = query.lower()
    results: list[dict[str, Any]] = []
    for p in paths:
        try:
            for i, line in enumerate(p.read_text().splitlines(), 1):
                if q in line.lower():
                    results.append({"path": p.name, "line": str(i), "text": line.strip()[:500]})
                    if len(results) >= limit:
                        return results
        except OSError:
            continue
    return results
