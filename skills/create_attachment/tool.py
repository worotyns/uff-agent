from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.tools.skill import BaseSkill
from src.thread_ctx import get_request_context

logger = logging.getLogger(__name__)


class CreateAttachment(BaseSkill):
    name = "create_attachment"
    description = (
        "Create or update a file that will be automatically attached to the next email reply. "
        "Use this instead of write_file when you want the result to be sent as an attachment. "
        "Each file created with this tool is saved in the current thread's attachments folder "
        "and will be included in the next outgoing email. "
        "Parameters: filename (name with extension, e.g. report.pdf), content (file text content), "
        "mode ('w' to overwrite, 'a' to append)."
    )
    parameters = {
        "filename": {
            "type": "string",
            "description": "File name with extension (e.g. 'raport.pdf', 'prezentacja.md')",
        },
        "content": {
            "type": "string",
            "description": "Full content of the file",
        },
        "mode": {
            "type": "string",
            "description": "'w' (default) to overwrite if file exists, 'a' to append",
            "enum": ["w", "a"],
        },
    }

    def execute(self, **kwargs: Any) -> str:
        filename = kwargs.get("filename", "")
        content = kwargs.get("content", "")
        mode = kwargs.get("mode", "w")

        if not filename or not content:
            return "Error: filename and content are required"

        email_ctx = get_request_context()
        if not email_ctx:
            return "Error: no active email context (create_attachment can only be used during a conversation)"

        att_dir = email_ctx.thread_attachments_dir
        if not att_dir:
            if not email_ctx.data_root or not email_ctx.thread_id:
                return "Error: cannot determine thread attachments directory"
            att_dir = email_ctx.data_root / "threads" / email_ctx.thread_id[:16] / "attachments"

        att_dir = Path(att_dir)
        att_dir.mkdir(parents=True, exist_ok=True)
        dest = att_dir / filename

        try:
            if mode == "a" and dest.exists():
                with open(dest, "a") as f:
                    f.write(content)
                msg = f"Appended {len(content)} bytes to {filename}"
            else:
                dest.write_text(content)
                msg = f"Written {len(content)} bytes to {filename}"
            logger.info("create_attachment: %s in %s", msg, att_dir)
            return msg
        except Exception as e:
            return f"Error writing {filename}: {e}"
