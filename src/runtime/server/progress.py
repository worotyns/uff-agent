from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

PROGRESS_TIMEOUT = 40

_PROGRESS_BODY = {
    "pl": "Pracuję nad Twoim zadaniem.\n\nJeśli to coś bardziej złożonego, za chwilę dostaniesz odpowiedź. Dzięki za cierpliwość!",
    "en": "I am working on your request.\n\nIf this is something more complex, you will receive a reply shortly. Thanks for your patience!",
}


class ProgressTimer:
    def __init__(
        self,
        smtp: Any,
        sender: str,
        subject: str,
        in_reply_to: str | None = None,
        timeout: int = PROGRESS_TIMEOUT,
        language: str = "en",
    ) -> None:
        self._timer: threading.Timer | None = None
        self._smtp = smtp
        self._sender = sender
        self._subject = subject
        self._in_reply_to = in_reply_to
        self._timeout = timeout
        self._language = language

    def start(self) -> None:
        self._timer = threading.Timer(self._timeout, self._send_progress)
        self._timer.daemon = True
        self._timer.start()

    def cancel(self) -> None:
        if self._timer and self._timer.is_alive():
            self._timer.cancel()
            self._timer = None

    @property
    def fired(self) -> bool:
        return self._timer is not None and not self._timer.is_alive()

    def _send_progress(self) -> None:
        if not self._smtp:
            return
        body = _PROGRESS_BODY.get(self._language, _PROGRESS_BODY["en"])
        try:
            self._smtp.send(
                to=self._sender,
                subject=f"Re: {self._subject}",
                body_text=body,
                in_reply_to=self._in_reply_to,
            )
            logger.info("auto-progress email sent to %s", self._sender)
        except Exception as e:
            logger.warning("auto-progress email failed: %s", e)
