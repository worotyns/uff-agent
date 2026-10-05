from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from src.storage.index import DocumentIndex
from src.thread_ctx import get_data_root, get_request_context
from src.tools.skill import BaseSkill

logger = logging.getLogger(__name__)


class SendDocument(BaseSkill):
    name = "send_document"
    description = (
        "Attach an indexed document to the current email reply. "
        "Use the 'path' from search_documents results (e.g. 'attachments/Umowa.pdf'). "
        "The original file will be attached to the outgoing email."
    )
    parameters = {
        "path": {
            "type": "string",
            "description": "Document path as returned by search_documents, e.g. 'attachments/Umowa.pdf'",
        },
    }

    def execute(self, **kwargs: Any) -> str:
        path = kwargs.get("path", "")
        if not path:
            return "Error: path is required"

        email_ctx = get_request_context()
        if not email_ctx:
            return "Error: no active email context"

        data_root = get_data_root()
        idx = DocumentIndex(data_root)
        info = idx.find_document(path)
        if not info:
            return f"Error: document '{path}' not found in index"

        thread_id = info.get("thread_id")
        if not thread_id:
            return f"Error: document '{path}' has no thread_id — cannot locate original file"

        filename = Path(path).name
        source_path = data_root / "threads" / thread_id[:16] / "attachments" / filename
        if not source_path.exists():
            return f"Error: original file not found at {source_path}"

        thread_attachments_dir = email_ctx.thread_attachments_dir
        if not thread_attachments_dir:
            if not email_ctx.data_root or not email_ctx.thread_id:
                return "Error: cannot determine target attachments directory"
            thread_attachments_dir = email_ctx.data_root / "threads" / email_ctx.thread_id[:16] / "attachments"

        dest_dir = Path(thread_attachments_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename

        try:
            shutil.copy2(source_path, dest_path)
            logger.info("send_document: copied %s -> %s", source_path, dest_path)
            return f"Done. {filename} will be attached to the next email reply."
        except Exception as e:
            return f"Error copying {filename}: {e}"
