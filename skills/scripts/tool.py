from __future__ import annotations

from typing import Any

from src.storage.script_runner import ScriptStore
from src.thread_ctx import get_data_root
from src.tools.skill import BaseSkill


def _store() -> ScriptStore:
    return ScriptStore(get_data_root())


class CreateScript(BaseSkill):
    name = "create_script"
    description = "CREATES a Python script with a schedule. The script result automatically goes to the LLM which sends a reply in the current email thread. USE THIS for DETERMINISTIC tasks (fixed logic, predictable output, pure math/data processing). Do NOT use it to monitor prices/parcels/results — use `create_background_task` for that (the LLM makes decisions)."
    parameters = {
        "name": {"type": "string", "description": "Script name (letters, digits, underscores only)"},
        "code": {"type": "string", "description": "Python code. Use print() for output. json, datetime, urllib.request are in the standard library. If you need another library (e.g. requests, beautifulsoup4), first use run_command with 'pip install package_name'."},
        "schedule": {"type": "string", "description": "When to run: 'hourly :MM', 'daily HH:MM', 'weekdays HH:MM', 'interval N'"},
    }

    def execute(self, **kwargs: Any) -> str:
        name = kwargs.get("name", "")
        code = kwargs.get("code", "")
        schedule = kwargs.get("schedule", "")
        thread_id = kwargs.pop("_thread_id", "")
        thread_subject = kwargs.pop("_thread_subject", "")
        try:
            meta = _store().add(name, code, schedule, owner="", thread_id=thread_id, thread_subject=thread_subject)
            return (
                f"Skrypt '{name}' utworzony (harmonogram: {schedule}, "
                f"następne uruchomienie: {meta['next_run']}). "
                f"Użyj `run_script(name='{name}')` aby uruchomić ręcznie."
            )
        except ValueError as e:
            return f"Błąd: {e}"


class RunScript(BaseSkill):
    name = "run_script"
    description = "Run a saved script and return its result. The result goes to you as a tool response for analysis."
    parameters = {
        "name": {"type": "string", "description": "Nazwa skryptu do uruchomienia"},
    }

    def execute(self, **kwargs: Any) -> str:
        name = kwargs.get("name", "")
        output = _store().run(name)
        _store().update_next_run(name)
        return output


class ListScripts(BaseSkill):
    name = "list_scripts"
    description = "List all saved scripts with their schedules"
    parameters = {}

    def execute(self, **kwargs: Any) -> str:
        scripts = _store().list()
        if not scripts:
            return "Brak zapisanych skryptów."
        lines = ["Zapisane skrypty:"]
        for s in scripts:
            last = s.get("last_run", "")[:16] if s.get("last_run") else "nigdy"
            next_r = s.get("next_run", "")[:16] if s.get("next_run") else "?"
            lines.append(
                f"  [{s['name']}] harmonogram={s.get('schedule', '?')} "
                f"uruchomień={s.get('run_count', 0)} ostatnie={last} następne={next_r}"
            )
        return "\n".join(lines)


class DeleteScript(BaseSkill):
    name = "delete_script"
    description = "Delete a saved script"
    parameters = {
        "name": {"type": "string", "description": "Script name to delete"},
    }

    def execute(self, **kwargs: Any) -> str:
        name = kwargs.get("name", "")
        if _store().delete(name):
            return f"Skrypt '{name}' usunięty."
        return f"Skrypt '{name}' nie znaleziony."
