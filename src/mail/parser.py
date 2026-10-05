from __future__ import annotations

import email as email_lib
import logging
import re
from email.message import Message
from html.parser import HTMLParser
from typing import Any

logger = logging.getLogger(__name__)


def _first_ref(references: str) -> str:
    for ref in references.split():
        ref = ref.strip("<>")
        if ref:
            return ref
    return ""


def parse_email(raw: bytes) -> dict[str, Any]:
    msg = email_lib.message_from_bytes(raw)
    refs = msg.get("References", "")
    has_forward_mime = _has_forward_mime(msg)
    _fwd_subject = msg.get("Subject", "").lower().strip()
    is_forward = (
        has_forward_mime
        or any(_fwd_subject.startswith(p) for p in ("fwd:", "fw:", "wg:", "enc:", "rv:", "tr:"))
    )
    result: dict[str, Any] = {
        "message_id": msg.get("Message-ID", "").strip("<>"),
        "in_reply_to": msg.get("In-Reply-To", "").strip("<>") if msg.get("In-Reply-To") else "",
        "references": refs,
        "thread_root": _first_ref(refs),
        "subject": msg.get("Subject", ""),
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "cc": msg.get("Cc", ""),
        "date": msg.get("Date", ""),
        "body_text": "",
        "body_html": "",
        "attachments": [],
        "is_forward": is_forward,
    }

    logger.debug("parsing email: subject=%s", result["subject"])
    _extract_parts(msg, result, depth=0)
    logger.info("parsed email: subject=%s, attachments=%d, text=%d chars, forward=%s",
                result["subject"], len(result["attachments"]), len(result["body_text"]), is_forward)
    return result


def _has_forward_mime(msg: Any) -> bool:
    if msg.get_content_type() == "message/rfc822":
        return True
    if msg.is_multipart():
        for part in msg.get_payload():
            if _has_forward_mime(part):
                return True
    return False


def _extract_parts(part: Any, result: dict[str, Any], depth: int = 0) -> None:
    ct = part.get_content_type()
    cd = (part.get_content_disposition() or "").lower()
    filename = part.get_filename()
    indent = "  " * depth

    logger.debug("%spart: ct=%s cd=%s filename=%s multipart=%s", indent, ct, cd, filename, part.is_multipart())

    if ct == "message/rfc822":
        inner = part.get_payload()
        if isinstance(inner, list) and len(inner) > 0:
            logger.info("%sembedded message (forwarded email), recursing...", indent)
            _extract_parts(inner[0], result, depth + 1)
            logger.info("%sfinished embedded message", indent)
        return

    if filename and ("attachment" in cd or ct not in ("text/plain", "text/html")):
        payload = part.get_payload(decode=True)
        if payload:
            result["attachments"].append({"filename": filename, "data": payload})
            logger.info("%sfound attachment: %s (%d bytes, ct=%s)", indent, filename, len(payload), ct)
            return
        logger.debug("%sskipped attachment %s: no payload", indent, filename)
        return

    if ct == "text/plain" and not result["body_text"]:
        payload = part.get_payload(decode=True)
        if payload:
            result["body_text"] = payload.decode(errors="replace")
            logger.debug("%sextracted text body (%d chars)", indent, len(result["body_text"]))
        return

    if ct == "text/html" and not result["body_html"]:
        payload = part.get_payload(decode=True)
        if payload:
            result["body_html"] = payload.decode(errors="replace")
            logger.debug("%sextracted html body (%d chars)", indent, len(result["body_html"]))
        return

    if part.is_multipart():
        for sub in part.get_payload():
            _extract_parts(sub, result, depth + 1)


def extract_email(address: str) -> str:
    m = re.search(r"<([^>]+)>", address)
    return m.group(1) if m else address.strip()


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._text: list[str] = []
        self._skip_tags = {"style", "script", "head"}

    def handle_data(self, data: str) -> None:
        self._text.append(data)

    def get_text(self) -> str:
        return "".join(self._text)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        return html
    text = p.get_text()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_REPLY_MARKERS = [
    r"^>",
    r"^[-=]{3,}$",
]


def _strip_reply_quotes(body: str) -> str:
    if not body:
        return ""
    lines = body.split("\n")
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if any(re.match(p, stripped) for p in _REPLY_MARKERS):
            break
        result.append(line)
    text = "\n".join(result).strip()
    if len(text) < len(body.strip()) * 0.3 and len(body) > 200:
        return body.strip()
    return text


_FORWARD_BANNER = re.compile(
    r"^[-=]{3,}.*[-=]{3,}$"
    r"|^begin\s+forwarded",
    re.IGNORECASE,
)

_IS_HEADER_LINE = re.compile(r"^\w{2,}: .+")


def _strip_forward_wrapper(body: str) -> tuple[str, str]:
    lines = body.split("\n")

    banner_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if _FORWARD_BANNER.match(stripped):
            banner_idx = i
            break

    if banner_idx < 0:
        for i in range(len(lines) - 2):
            header_count = 0
            for j in range(i, min(i + 5, len(lines))):
                if _IS_HEADER_LINE.match(lines[j].strip()):
                    header_count += 1
                else:
                    break
            if header_count >= 2:
                banner_idx = max(0, i - 1)
                break

    if banner_idx < 0:
        return body.strip(), ""

    intro = "\n".join(lines[:banner_idx]).strip()

    content_start = banner_idx + 1
    while content_start < len(lines):
        stripped = lines[content_start].strip()
        if not stripped or _IS_HEADER_LINE.match(stripped) or _FORWARD_BANNER.match(stripped):
            content_start += 1
            continue
        break

    forwarded = "\n".join(lines[content_start:]).strip()
    return intro, forwarded


def strip_email_quotes(body: str, is_forward: bool = False) -> str:
    if not body:
        return ""
    if is_forward:
        intro, forwarded = _strip_forward_wrapper(body)
        if forwarded:
            parts = []
            if intro:
                parts.append(intro)
            parts.append(f"\n[Tre\u015B\u0107 przekazanej wiadomo\u015Bci]:\n{forwarded}")
            return "\n\n".join(parts)
        return intro or body.strip()
    return _strip_reply_quotes(body)


def parse_allowed(config: dict[str, Any]) -> list[str]:
    raw = config.get("allowed_senders") or []
    result: list[str] = []
    for entry in raw:
        if not entry or not isinstance(entry, str):
            continue
        for part in entry.split(","):
            part = part.strip()
            if part:
                result.append(part)
    return result


def is_allowed(sender: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    email_addr = extract_email(sender).lower()
    for rule in allowed:
        rule = rule.lower().strip()
        if rule == "*":
            return True
        if rule.startswith("@"):
            if email_addr.endswith(rule):
                return True
        elif email_addr == rule:
            return True
    return False
