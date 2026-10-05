from __future__ import annotations

import logging
import os
import smtplib
import ssl
import time
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any

import markdown

from src.config import as_int

logger = logging.getLogger(__name__)

SENSITIVE_ENV_KEYS = [
    "OPENROUTER_KEY",
    "EMAIL_PASSWORD",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
]


def _redact_sensitive(text: str) -> str:
    for key in SENSITIVE_ENV_KEYS:
        val = os.environ.get(key, "")
        if val and val in text:
            text = text.replace(val, "[REDACTED]")
    return text


def md_to_html(text: str) -> str:
    return markdown.markdown(text, extensions=["nl2br", "sane_lists", "tables"])


class SMTPSender:
    def __init__(self, config: dict[str, Any]) -> None:
        self.host = config["smtp"]["host"]
        self.port = as_int(config["smtp"].get("port"), 465)
        self.user = config["smtp"]["user"]
        self.password = config["smtp"]["password"]
        self.use_tls = config["smtp"].get("tls", True)
        self.notify_to = config.get("notify_email", "").strip() or self.user
        self.signature = self._build_signature(config)

    @staticmethod
    def _build_signature(config: dict[str, Any]) -> str:
        parts = []
        sig = config.get("signature", "")
        if sig:
            parts.append(sig)
        email_addr = config.get("smtp", {}).get("user", "")
        if email_addr:
            parts.append(f"Sent from {email_addr}")
        return "\n\n".join(parts) if parts else ""

    def send(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
        with_signature: bool = True,
        attachments: list[Path] | None = None,
    ) -> str:
        has_attachments = attachments and any(p.exists() for p in attachments)
        if has_attachments:
            msg = MIMEMultipart("mixed")
            alt = MIMEMultipart("alternative")
            msg.attach(alt)
        else:
            msg = MIMEMultipart("alternative")
            alt = msg

        msg["From"] = self.user
        msg["To"] = to
        msg["Subject"] = _redact_sensitive(subject)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.user.split("@")[-1] if "@" in self.user else None)

        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = references or in_reply_to

        final_text = f"{body_text}\n{self.signature}" if (with_signature and self.signature) else body_text
        final_text = _redact_sensitive(final_text)
        alt.attach(MIMEText(final_text, "plain"))
        html = body_html or md_to_html(final_text)
        alt.attach(MIMEText(html, "html"))

        if has_attachments:
            for path in attachments:
                if not path.exists():
                    continue
                try:
                    with open(path, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                    import email.encoders
                    email.encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f'attachment; filename="{path.name}"')
                    msg.attach(part)
                except Exception as e:
                    logger.warning("failed to attach %s: %s", path.name, e)

        message_id = msg["Message-ID"]

        last_err = None
        for attempt in range(3):
            try:
                self._send_internal(msg, to)
                logger.info("sent email to %s: %s (mid=%s)", to, subject, message_id)
                return message_id
            except Exception as e:
                last_err = e
                logger.warning("SMTP attempt %d failed: %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep(2 ** attempt)

        logger.error("SMTP failed after 3 retries: %s", last_err)
        return ""

    def _send_internal(self, msg: MIMEMultipart, to: str) -> None:
        context = ssl.create_default_context()
        if self.port == 587:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                server.starttls(context=context)
                server.login(self.user, self.password)
                server.sendmail(self.user, [to], msg.as_string())
        else:
            with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=30) as server:
                server.login(self.user, self.password)
                server.sendmail(self.user, [to], msg.as_string())
