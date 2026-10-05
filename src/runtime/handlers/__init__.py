from __future__ import annotations

from src.runtime.handlers.dream import handle_dream
from src.runtime.handlers.email import handle_email
from src.runtime.handlers.heartbeat import handle_heartbeat
from src.runtime.handlers.reminder import handle_reminder
from src.runtime.handlers.script import handle_script
from src.runtime.handlers.task import handle_task_check

EVENT_HANDLERS = {
    "heartbeat.tick": handle_heartbeat,
    "dream.trigger": handle_dream,
    "memory.compact": handle_dream,
    "reminder.fire": handle_reminder,
    "script.run": handle_script,
    "task.check": handle_task_check,
    "email.received": handle_email,
}
