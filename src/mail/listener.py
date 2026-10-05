from __future__ import annotations

import logging
import time
from imaplib import IMAP4_SSL
from pathlib import Path
from typing import Any

from src.config import as_int
from src.event import Event, EventQueue
from src.mail.parser import extract_email, html_to_text, is_allowed, parse_allowed, parse_email, strip_email_quotes
from src.mail.router import resolve_agent
from src.storage.thread import ThreadStore

logger = logging.getLogger(__name__)


class IMAPListener:
    def __init__(self, config: dict[str, Any], queue: EventQueue, thread_store: ThreadStore | None = None, data_root: Path | None = None) -> None:
        self.host = config["imap"]["host"]
        self.port = as_int(config["imap"].get("port"), 993)
        self.user = config["imap"]["user"]
        self.password = config["imap"]["password"]
        self.queue = queue
        self.thread_store = thread_store
        self.data_root = data_root
        self._seen: set[str] = set()
        self.allowed = parse_allowed(config)
        self.delete_after_read = config.get("delete_after_read", False)
        logger.info("delete_after_read=%s, allowed_senders=%s", self.delete_after_read, self.allowed)

    def _connect(self) -> IMAP4_SSL:
        if len(self._seen) > 10000:
            self._seen.clear()
        logger.debug("connecting to IMAP %s as %s", self.host, self.user)
        conn = IMAP4_SSL(self.host, self.port, timeout=20)
        conn.login(self.user, self.password)
        if conn.sock:
            conn.sock.settimeout(15)
        conn.select("INBOX")
        return conn

    def _close(self, conn: IMAP4_SSL | None) -> None:
        if not conn:
            return
        try:
            if conn.sock:
                conn.sock.settimeout(5)
            conn.logout()
        except Exception:
            pass

    def _save_attachments(self, uid: str, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        paths: list[dict[str, Any]] = []
        data_root = self.data_root or Path.cwd()
        base = data_root / "events" / "pending" / "attachments" / uid
        base.mkdir(parents=True, exist_ok=True)
        for att in attachments:
            filename = att.get("filename", "unknown")
            data = att.get("data")
            if not data:
                continue
            dest = base / filename
            dest.write_bytes(data)
            paths.append({"filename": filename, "path": str(dest)})
            logger.info("saved attachment %s (%d bytes) to %s", filename, len(data), dest)
        return paths

    def idle_loop(self, interval: float = 5.0) -> None:
        logger.info("IMAP listener started, polling every %ds", interval)
        conn: IMAP4_SSL | None = None
        backoff = 1.0
        while True:
            try:
                if conn is None:
                    conn = self._connect()
                    logger.info("IMAP connected to %s", self.host)
                self._poll_conn(conn)
                backoff = 1.0
            except Exception as e:
                logger.warning("IMAP poll failed: %s — reconnecting", e)
                self._close(conn)
                conn = None
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            time.sleep(interval)

    def _poll_conn(self, conn: IMAP4_SSL) -> None:
        deleted_uid_set: list[str] = []
        processed_count = 0
        try:
            logger.debug("searching UNSEEN emails...")
            result, data = conn.uid('SEARCH', None, 'UNSEEN')
            if result != 'OK':
                logger.warning("UID SEARCH failed: %s", result)
                return
            raw_uids = data[0] if data and data[0] else b''
            uid_list = raw_uids.decode().split() if raw_uids else []
            logger.debug("UNSEEN search returned %d email(s) (UIDs: %s)", len(uid_list), uid_list)
            if not uid_list:
                return

            for uid_str in uid_list:
                if uid_str in self._seen:
                    logger.debug("skipping UID %s (already seen)", uid_str)
                    continue
                self._seen.add(uid_str)
                processed_count += 1
                self._process_uid(conn, uid_str, deleted_uid_set)

            if deleted_uid_set:
                try:
                    conn.expunge()
                    logger.info("expunged %d deleted emails", len(deleted_uid_set))
                except Exception as e:
                    logger.warning("expunge failed: %s", e)

            logger.debug("poll finished, processed %d/%d unseen email(s)",
                        processed_count, len(uid_list))

        except Exception as e:
            logger.warning("IMAP SEARCH/poll error: %s", e)
            raise

    def _process_uid(self, conn: IMAP4_SSL, uid_str: str, deleted_uid_set: list[str]) -> None:
        try:
            _, msg_data = conn.uid('FETCH', uid_str, "(RFC822)")
        except Exception as e:
            logger.warning("failed to fetch UID %s: %s", uid_str, e)
            return

        if not msg_data or msg_data[0] is None:
            return

        raw = msg_data[0][1]
        parsed = parse_email(raw)
        sender = parsed["from"]

        agent_email = extract_email(self.user).lower()
        agent_base = self._agent_base(agent_email)

        is_in_to = any(self._matches_agent(a, agent_email, agent_base) for a in parsed["to"].split(",") if a.strip())
        is_in_cc = any(self._matches_agent(a, agent_email, agent_base) for a in parsed.get("cc", "").split(",") if a.strip())
        recipient_type = "to" if is_in_to else "cc" if is_in_cc else "bcc"
        logger.debug("recipient_type=%s for email from %s (agent=%s)", recipient_type, sender, agent_email)

        if not is_allowed(sender, self.allowed):
            logger.info("ignored email from %s (not in whitelist)", sender)
            self._mark_deleted(conn, uid_str, deleted_uid_set)
            return

        agent = self._resolve_agent(parsed)

        body = self._extract_body(parsed)

        attachment_paths = self._save_attachments(uid_str, parsed.get("attachments", []))

        event = Event(
            type="email.received",
            agent=agent,
            thread=parsed.get("thread_root", "") or parsed.get("in_reply_to", "") or parsed["message_id"] or uid_str,
            message=body,
            metadata={
                "from": sender,
                "to": parsed["to"],
                "cc": parsed.get("cc", ""),
                "recipient_type": recipient_type,
                "subject": parsed["subject"],
                "message_id": parsed["message_id"],
                "in_reply_to": parsed["in_reply_to"],
                "is_forward": parsed.get("is_forward", False),
                "attachments": attachment_paths,
            },
        )
        self.queue.push(event)
        logger.info("queued email from %s for agent %s (subject=%s, mid=%s)",
                    sender, event.agent, parsed.get("subject", "")[:60], parsed.get("message_id", "")[:20])

        if self.delete_after_read:
            self._mark_deleted(conn, uid_str, deleted_uid_set)
        else:
            try:
                conn.uid('STORE', uid_str, '+FLAGS', '\\Seen')
                logger.debug("marked UID %s as seen", uid_str)
            except Exception as e:
                logger.warning("store UID %s failed: %s", uid_str, e)

    @staticmethod
    def _agent_base(agent_email: str) -> str:
        if "@" not in agent_email:
            return agent_email
        local, _, domain = agent_email.partition("@")
        return local.split("+")[0] + "@" + domain

    @staticmethod
    def _matches_agent(addr: str, agent_email: str, agent_base: str) -> bool:
        addr = extract_email(addr).lower()
        if addr == agent_email:
            return True
        if "@" not in addr:
            return addr == agent_email
        local, _, domain = addr.partition("@")
        base = local.split("+")[0] + "@" + domain
        return base == agent_base or base == agent_email

    def _resolve_agent(self, parsed: dict[str, Any]) -> str:
        agent = resolve_agent(parsed["to"])
        if agent == "assistant" and parsed.get("cc"):
            cc_agent = resolve_agent(parsed["cc"])
            if cc_agent != "assistant":
                agent = cc_agent
                logger.debug("agent resolved from CC: %s", agent)
        resolved_via_alias = agent != "assistant" or (parsed.get("cc") and resolve_agent(parsed["cc"]) != "assistant")
        thread_root = parsed.get("thread_root", "") or parsed.get("in_reply_to", "")
        if thread_root and self.thread_store and not resolved_via_alias:
            thread_agent = self.thread_store.get_agent(thread_root)
            if thread_agent and thread_agent != "assistant":
                agent = thread_agent
                logger.debug("agent resolved from thread history: %s", agent)
        return agent

    @staticmethod
    def _extract_body(parsed: dict[str, Any]) -> str:
        body_text = parsed.get("body_text", "")
        body_html = parsed.get("body_html", "")
        is_forward = parsed.get("is_forward", False)
        if body_text:
            return strip_email_quotes(body_text, is_forward=is_forward)
        if body_html:
            return strip_email_quotes(html_to_text(body_html), is_forward=is_forward)
        return ""

    def _mark_deleted(self, conn: IMAP4_SSL, uid_str: str, deleted_uid_set: list[str]) -> None:
        if not self.delete_after_read:
            return
        try:
            conn.uid('STORE', uid_str, '+FLAGS', '\\Deleted \\Seen')
            deleted_uid_set.append(uid_str)
            logger.info("deleted UID %s", uid_str)
        except Exception as e:
            logger.warning("store UID %s failed: %s", uid_str, e)
