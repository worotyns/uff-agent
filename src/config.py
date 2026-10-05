from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _interpolate(value: Any, env: dict[str, str] | None = None) -> Any:
    if env is None:
        env = os.environ
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var = m.group(1)
            if var in env:
                return env[var]
            # An unset variable used to be left as a literal `${VAR}`, which then
            # reached code expecting a real value (a port, a time, a model id)
            # and crashed it. Substitute empty and let the consumer fall back.
            logger.warning("env var %s is not set — substituting empty", var)
            return ""
        return ENV_VAR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _interpolate(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, env) for v in value]
    return value


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return _interpolate(raw)


def as_int(value: Any, default: int) -> int:
    """Parse an int from config/env, tolerating an unresolved ${VAR} placeholder."""
    try:
        if value is None:
            return default
        text = str(value).strip()
        if not text or "$" in text:
            return default
        return int(text)
    except (TypeError, ValueError):
        return default


def resolve_path(name: str, primary: Path, fallback: Path) -> Path:
    p = primary / name
    if p.exists():
        return p
    return fallback / name


class Config:
    def __init__(self, primary: str | Path, fallback: str | Path | None = None) -> None:
        self.primary = Path(primary)
        self.fallback = Path(fallback) if fallback else self.primary
        path = resolve_path("config.yaml", self.primary, self.fallback)
        if path.exists():
            logger.info("config: %s", path)
            self.root = path.parent
            self.data = load_yaml(path)
        else:
            logger.info("config: building from environment variables (no config.yaml)")
            self.root = self.primary
            self.data = self._from_env()

    @staticmethod
    def _from_env() -> dict[str, Any]:
        def _e(key: str, default: str = "") -> str:
            return os.environ.get(key, default)

        agents_str = _e("AGENTS", "assistant,researcher,sources")
        agents = [a.strip() for a in agents_str.split(",") if a.strip()]

        email_user = _e("EMAIL_USER")
        email_owner = _e("EMAIL_OWNER", email_user)
        notify_email = _e("EMAIL_NOTIFY", email_owner)
        if notify_email and "," in notify_email:
            notify_email = notify_email.split(",")[0].strip()
        return {
            "agents": agents,
            "openrouter": {
                "api_key": _e("OPENROUTER_KEY"),
                "model": _e("OPENROUTER_MODEL"),
            },
            "email": {
                "imap": {
                    "host": _e("EMAIL_IMAP_HOST"),
                    "port": int(_e("EMAIL_IMAP_PORT", "993")),
                    "user": email_user,
                    "password": _e("EMAIL_PASSWORD"),
                },
                "smtp": {
                    "host": _e("EMAIL_SMTP_HOST"),
                    "user": email_user,
                    "password": _e("EMAIL_PASSWORD"),
                    "port": int(_e("EMAIL_SMTP_PORT", "465")),
                },
                "allowed_senders": [email_owner],
                "delete_after_read": _e("EMAIL_DELETE_AFTER_READ", "true").lower() == "true",
                "welcome_email": _e("WELCOME_EMAIL", "true"),
                "notify_email": notify_email,
                "admin_notify_email": _e("ADMIN_NOTIFY_EMAIL"),
                "signature": _e("EMAIL_SIGNATURE", '\n---\nSent by your AI assistant'),
            },
            "user": {
                "language": _e("USER_LANGUAGE"),
                "timezone": _e("USER_TIMEZONE"),
            },
            "scheduler": {
                "heartbeat": int(_e("HEARTBEAT_INTERVAL", "60")),
                "dream": {"hour": _e("DREAM_HOUR", "03:00")},
            },
            "mcp": {
                "enabled": _e("MCP_ENABLED", "true").lower() == "true",
            },
        }

    @property
    def openrouter_api_key(self) -> str:
        return self.data["openrouter"]["api_key"]

    @property
    def openrouter_model(self) -> str:
        return self.data.get("openrouter", {}).get("model") or "deepseek/deepseek-v4.1-flash"

    @property
    def vision_model(self) -> str:
        return self.data.get("openrouter", {}).get("vision_model") or "google/gemini-3.1-flash-lite"

    @property
    def image_gen_model(self) -> str:
        return self.data.get("openrouter", {}).get("image_gen_model") or "google/gemini-3.1-flash-lite-image"

    @property
    def cloudflare_account_id(self) -> str:
        return self.data.get("cloudflare", {}).get("account_id", "")

    @property
    def cloudflare_api_token(self) -> str:
        return self.data.get("cloudflare", {}).get("api_token", "")

    @property
    def heartbeat_interval(self) -> int:
        val = self.data.get("scheduler", {}).get("heartbeat", 300)
        try:
            return int(val)
        except (ValueError, TypeError):
            return 300

    @property
    def dream_hour(self) -> str:
        val = (self.data.get("scheduler", {}).get("dream", {}).get("hour") or "").strip()
        if re.fullmatch(r"\d{2}:\d{2}", val):
            return val
        if val:
            logger.warning("invalid DREAM_HOUR %r — falling back to 03:00", val)
        return "03:00"

    @property
    def max_tool_rounds(self) -> int:
        return self.data.get("scheduler", {}).get("max_tool_rounds", 5)

    @property
    def agent_names(self) -> list[str]:
        return self.data.get("agents", [])

    @property
    def agent_domain(self) -> str:
        """Domain of the agent's own mailbox (used for Message-ID and HTTP-Referer)."""
        user = (
            self.data.get("email", {}).get("smtp", {}).get("user", "")
            or self.data.get("email", {}).get("imap", {}).get("user", "")
        )
        return user.split("@")[-1] if "@" in user else ""

    @property
    def welcome_email(self) -> bool:
        val = self.data.get("email", {}).get("welcome_email", "true")
        if isinstance(val, str):
            if "${" in val:  # unresolved ${VAR} left in config.yaml
                return True
            return val.strip().lower() in ("true", "1", "yes", "on")
        return bool(val)

    @property
    def admin_notify_email(self) -> str:
        val = self.data.get("email", {}).get("admin_notify_email", "") or ""
        # config.yaml keeps unresolved ${VAR} placeholders verbatim
        return "" if "${" in val else val

    @property
    def email_config(self) -> dict[str, Any]:
        cfg = dict(self.data.get("email", {}))
        if not cfg.get("allowed_senders"):
            owner = cfg.get("notify_email", "").strip()
            if owner:
                cfg["allowed_senders"] = [owner]
                logger.info("email: allowed_senders defaulted to [%s]", owner)
        return cfg

    @property
    def user_timezone(self) -> str:
        return self.data.get("user", {}).get("timezone") or "UTC"

    @property
    def user_language(self) -> str:
        return self.data.get("user", {}).get("language") or "en"

    @property
    def mcp_enabled(self) -> bool:
        val = self.data.get("mcp", {}).get("enabled", True)
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes", "on")
        return bool(val)

    @property
    def raw(self) -> dict[str, Any]:
        return self.data
