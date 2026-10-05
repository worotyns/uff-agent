from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

WELCOME_TEMPLATES = {"pl": "welcome.pl.md", "en": "welcome.en.md"}
WELCOME_FALLBACK = "welcome.en.md"

DATA_DIRS = ["events/pending", "events/processed", "threads", "memory", "index"]


def load_dotenv(path: str | Path | None = None) -> None:
    path = Path(path or ".env")
    if not path.exists():
        return
    logger.info("loading env: %s", path.resolve())
    for line in path.read_text().split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("\"'")
        os.environ.setdefault(key, val)


def bootstrap(data_root: Path, root: Path) -> None:
    for d in DATA_DIRS:
        (data_root / d).mkdir(parents=True, exist_ok=True)

    if data_root != root:
        _copy_default(data_root, root, "config.yaml")
        _copy_default(data_root, root, "SOUL.md")
        _copy_default(data_root, root, "mcp_servers.default.json")
        _copy_default(data_root, root, "entities/schema.yaml", dst_rel="memory/entities/schema.yaml")
        for name in ("welcome.pl.md", "welcome.en.md"):
            _copy_default(data_root, root, name)
        for agent_dir in sorted((root / "agents").iterdir()):
            if agent_dir.is_dir():
                for f in ("SOUL.md", "config.yaml"):
                    _copy_default(data_root, root, f"agents/{agent_dir.name}/{f}")

    logger.info("bootstrapped data directories under %s", data_root)


def _copy_default(data_root: Path, fallback: Path, rel: str, dst_rel: str | None = None) -> None:
    dst = data_root / (dst_rel or rel)
    src = fallback / rel
    if not src.exists():
        return
    if dst.exists() and dst.read_text() == src.read_text():
        return
    action = "overwritten" if dst.exists() else "copied"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text())
    logger.info("  %s default: %s", action, dst_rel or rel)


def welcome_lock(data_root: Path) -> Path:
    return data_root / ".welcome_sent"


def send_welcome_email(smtp, owner_email: str, data_root: Path, root: Path, language: str = "en") -> None:
    candidates = [
        data_root / WELCOME_TEMPLATES.get(language, WELCOME_FALLBACK),
        data_root / WELCOME_FALLBACK,
        root / WELCOME_TEMPLATES.get(language, WELCOME_FALLBACK),
        root / WELCOME_FALLBACK,
    ]
    template: Path | None = None
    for p in candidates:
        if p.exists():
            template = p
            break
    if not template:
        logger.warning("no welcome template found (language=%s)", language)
        return
    agent_email = smtp.user
    agent_handle = agent_email.split("@")[0] if "@" in agent_email else agent_email
    body = (
        template.read_text()
        .replace("{{agent_email}}", agent_email)
        .replace("{{agent}}", agent_handle)
        .strip()
    )
    subjects = {"pl": "Twój asystent AI – pierwsze uruchomienie", "en": "Your AI Assistant – First Launch"}
    subject = subjects.get(language, subjects["en"])
    smtp.send(to=owner_email, subject=subject, body_text=body, with_signature=False)
    welcome_lock(data_root).write_text(datetime.now(timezone.utc).isoformat())
    logger.info("welcome email sent to %s (as %s, lang=%s)", owner_email, agent_email, language)
