from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.thread_ctx import get_data_root
from src.tools.mcp import McpServerStore, load_defaults, merge_servers, probe_mcp_server
from src.tools.skill import BaseSkill


def _root() -> Path:
    return get_data_root()


def _store() -> McpServerStore:
    return McpServerStore(_root())


def _defaults() -> list[dict[str, Any]]:
    return load_defaults(_root())


def _merged() -> list[dict[str, Any]]:
    return merge_servers(_defaults(), _store().list())


def _default_names() -> set[str]:
    return {d.get("name", "") for d in _defaults() if d.get("name")}


def _resolve_name(key: str) -> str:
    for s in _merged():
        if s.get("name") == key or s.get("id") == key:
            return s.get("name", "")
    return ""


def _validate_url(url: str) -> str | None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "Adres musi być pełnym URL http(s), np. https://example.com/mcp"
    return None


def _headers(bearer_token: str) -> dict[str, str]:
    token = (bearer_token or "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


class AddMcpServer(BaseSkill):
    name = "add_mcp_server"
    description = (
        "Add a remote MCP (Model Context Protocol) server by http(s) URL. The agent checks the connection "
        "before saving, and from the next message has access to that server's tools. Use when the user "
        "asks to connect a specific MCP server (e.g. GitHub, Notion, Filesystem)."
    )
    parameters = {
        "name": {"type": "string", "description": "Short, readable server name, e.g. 'github'", "required": True},
        "url": {"type": "string", "description": "MCP endpoint URL (streamable HTTP / SSE), e.g. https://api.github.com/mcp", "required": True},
        "bearer_token": {"type": "string", "description": "Optional token for the Authorization: Bearer <token> header", "default": ""},
    }

    def execute(self, **kwargs: Any) -> str:
        name = (kwargs.get("name") or "").strip()
        url = (kwargs.get("url") or "").strip()
        if not name or not url:
            return "Błąd: podaj nazwę i adres URL serwera MCP."
        err = _validate_url(url)
        if err:
            return f"Błąd: {err}"

        headers = _headers(kwargs.get("bearer_token", ""))
        ok, message, tool_count = probe_mcp_server(url, headers)
        if not ok:
            return f"Nie udało się połączyć z serwerem MCP '{name}' ({url}). Błąd: {message}. Serwer nie został zapisany."

        server = _store().add(name, url, headers=headers)
        note = " (nadpisano domyślny serwer o tej nazwie)" if name in _default_names() else ""
        return (
            f"Dodano serwer MCP '{name}' ({url}) — {tool_count} narzędzi. "
            f"Narzędzia będą dostępne od następnej wiadomości (id={server['id']}).{note}"
        )


class ListMcpServers(BaseSkill):
    name = "list_mcp_servers"
    description = "List configured MCP servers (default and user's own) with status."
    parameters = {}

    def execute(self, **kwargs: Any) -> str:
        servers = _merged()
        if not servers:
            return "Brak skonfigurowanych serwerów MCP."
        lines = ["Skonfigurowane serwery MCP:"]
        for s in servers:
            status = "włączony" if s.get("enabled", True) else "wyłączony"
            source = "domyślny" if s.get("source") == "default" else "użytkownika"
            auth = "bearer token" if s.get("headers", {}).get("Authorization") else "brak auth"
            lines.append(f"  • [{source}] {s.get('name', '')} — {s.get('url', '')} ({status}, {auth})")
        return "\n".join(lines)


class RemoveMcpServer(BaseSkill):
    name = "remove_mcp_server"
    description = "Remove an MCP server by name or id. For a default server, disables it instead of removing (can be re-enabled)."
    parameters = {
        "name_or_id": {"type": "string", "description": "Name or id of the MCP server to remove", "required": True},
    }

    def execute(self, **kwargs: Any) -> str:
        key = (kwargs.get("name_or_id") or "").strip()
        if not key:
            return "Błąd: podaj nazwę lub id serwera MCP."
        name = _resolve_name(key)
        if not name:
            return f"Serwer MCP '{key}' nie znaleziony."
        if name in _default_names():
            _store().override(name, False)
            return f"Wyłączono domyślny serwer MCP '{name}' (zamiast usunięcia — włączysz go ponownie komendą włącz)."
        _store().remove(name)
        return f"Usunięto serwer MCP '{name}'."


class EnableMcpServer(BaseSkill):
    name = "enable_mcp_server"
    description = "Enable a previously disabled MCP server by name or id."
    parameters = {
        "name_or_id": {"type": "string", "description": "Name or id of the MCP server to enable", "required": True},
    }

    def execute(self, **kwargs: Any) -> str:
        key = (kwargs.get("name_or_id") or "").strip()
        if not key:
            return "Błąd: podaj nazwę lub id serwera MCP."
        name = _resolve_name(key)
        if not name:
            return f"Serwer MCP '{key}' nie znaleziony."
        _store().override(name, True)
        return f"Włączono serwer MCP '{name}'."


class DisableMcpServer(BaseSkill):
    name = "disable_mcp_server"
    description = "Disable an MCP server by name or id (without deleting its config)."
    parameters = {
        "name_or_id": {"type": "string", "description": "Name or id of the MCP server to disable", "required": True},
    }

    def execute(self, **kwargs: Any) -> str:
        key = (kwargs.get("name_or_id") or "").strip()
        if not key:
            return "Błąd: podaj nazwę lub id serwera MCP."
        name = _resolve_name(key)
        if not name:
            return f"Serwer MCP '{key}' nie znaleziony."
        _store().override(name, False)
        return f"Wyłączono serwer MCP '{name}'."
