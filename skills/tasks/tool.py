from __future__ import annotations

from typing import Any

from src.storage.tasks import TaskStore
from src.thread_ctx import get_data_root
from src.tools.skill import BaseSkill


def _store() -> TaskStore:
    return TaskStore(get_data_root())


class AddReminder(BaseSkill):
    name = "add_reminder"
    description = (
        "Add a reminder. When the time comes, the agent checks weather/runs a script and sends a reply. "
        "Mode: 'thread' = reply in the current email thread (when the reminder relates to this conversation, e.g. 'remind me about this on the 10th'); "
        "'new' = a separate new thread (for recurring tasks or something unrelated to the current thread, e.g. daily weather, a deferred task from CC). "
        "Optionally runs a script (script parameter). Recurrence: hourly (keeps minutes), daily, weekdays, weekly."
    )
    parameters = {
        "time": {"type": "string", "description": "UTC ISO time: '2026-07-07T08:00:00Z'", "required": True},
        "title": {"type": "string", "description": "Reminder text", "required": True},
        "recurrence": {"type": "string", "description": "hourly / daily / weekdays / weekly (empty = one-off)", "default": ""},
        "script": {"type": "string", "description": "Name of the script to run at reminder time (optional)", "default": ""},
        "mode": {"type": "string", "description": "'thread' (reply in this thread) or 'new' (separate new thread)", "default": "thread"},
    }

    def execute(self, **kwargs: Any) -> str:
        title = kwargs.get("title", "")
        time = kwargs.get("time", "")
        if not time:
            from datetime import datetime, timezone
            time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = kwargs.get("recurrence", "")
        script = kwargs.get("script", "")
        mode = kwargs.get("mode", "thread")
        thread_id = kwargs.pop("_thread_id", "")
        thread_subject = kwargs.pop("_thread_subject", "")
        task = _store().add("reminder", title, time=time, recurrence=rec, script=script, thread_id=thread_id, thread_subject=thread_subject, mode=mode)
        rec_note = f" (powtarzane: {rec})" if rec else ""
        script_note = f" + skrypt '{script}'" if script else ""
        mode_note = " [nowy wątek]" if task.get("mode") == "new" else " [wątek]"
        return f"Przypomnienie ustawione: '{title}' o {time}{rec_note}{script_note}{mode_note} (id={task['id']})"


class AddTodo(BaseSkill):
    name = "add_todo"
    description = "Add a task to the list"
    parameters = {
        "title": {"type": "string", "description": "Task description"},
    }

    def execute(self, **kwargs: Any) -> str:
        title = kwargs.get("title", "")
        task = _store().add("todo", title)
        return f"Dodano zadanie: '{title}' (id={task['id']})"


class ListTasks(BaseSkill):
    name = "list_tasks"
    description = "List tasks, optionally filtered by type (reminder/todo) and status"
    parameters = {
        "type": {"type": "string", "description": "Type filter: reminder or todo", "default": ""},
        "done": {"type": "boolean", "description": "Show done instead of pending", "default": False},
    }

    def execute(self, **kwargs: Any) -> str:
        task_type = kwargs.get("type") or None
        done = kwargs.get("done", False)
        tasks = _store().list(task_type=task_type, done=done)
        if not tasks:
            return "Brak zadań."
        lines = [f"{'Zrobione' if done else 'Oczekujące'} zadania:"]
        for t in tasks:
            tag = "☑" if t.get("done") else "☐"
            time_str = f" @ {t.get('time', '')}" if t.get("time") else ""
            lines.append(f"  {tag} [{t['id']}] {t['title']}{time_str}")
        return "\n".join(lines)


class CompleteTask(BaseSkill):
    name = "complete_task"
    description = "Mark a task as done by its id"
    parameters = {
        "id": {"type": "string", "description": "Id of the task to mark"},
    }

    def execute(self, **kwargs: Any) -> str:
        task_id = kwargs.get("id", "")
        if _store().complete(task_id):
            return f"Zadanie {task_id} oznaczone jako zrobione."
        return f"Zadanie {task_id} nie znalezione."


class DeleteTask(BaseSkill):
    name = "delete_task"
    description = "Delete a task by its id"
    parameters = {
        "id": {"type": "string", "description": "Id of the task to delete"},
    }

    def execute(self, **kwargs: Any) -> str:
        task_id = kwargs.get("id", "")
        if _store().delete(task_id):
            return f"Zadanie {task_id} usunięte."
        return f"Zadanie {task_id} nie znalezione."
