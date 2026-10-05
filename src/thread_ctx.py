from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

_current_ctx = threading.local()


@dataclass
class RequestContext:
    smtp: Any = None
    sender: str = ""
    subject: str = ""
    in_reply_to: str | None = None
    data_root: Path = field(default_factory=lambda: Path(os.environ.get("AGENT_HOME", ".")).resolve())
    thread_id: str = ""
    thread_attachments_dir: Path | None = None
    timer: Any = None
    task_id: str = ""
    bg_store: Any = None
    agent: str = "assistant"


@contextmanager
def activate(ctx: RequestContext) -> Iterator[RequestContext]:
    _current_ctx.ctx = ctx
    try:
        yield ctx
    finally:
        _current_ctx.ctx = None


def get_request_context() -> RequestContext | None:
    return getattr(_current_ctx, "ctx", None)


def get_data_root() -> Path:
    ctx = get_request_context()
    if ctx is not None and ctx.data_root is not None:
        return ctx.data_root
    return Path(os.environ.get("AGENT_HOME", ".")).resolve()
