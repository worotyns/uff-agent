from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    MCP_AVAILABLE = True
except Exception:  # pragma: no cover - graceful degradation
    MCP_AVAILABLE = False


def _sanitize_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", (name or "").lower()).strip("_")
    return (slug or "srv")[:32]


def _content_to_string(result: Any) -> str:
    input_requests = getattr(result, "input_requests", None)
    if input_requests:
        return (
            "This tool requires additional information from the user. "
            "Stop calling tools and ask the user for the requested details instead."
        )

    parts: list[str] = []
    content = getattr(result, "content", None) or []
    for item in content:
        t = getattr(item, "type", "text")
        if t == "text":
            parts.append(getattr(item, "text", "") or "")
        elif t == "image":
            parts.append(f"[image: {getattr(item, 'mime_type', '')}]")
        elif t == "audio":
            parts.append("[audio]")
        elif t in ("resource_link", "resource"):
            parts.append(f"[{t}: {getattr(item, 'uri', '')}]")
        else:
            parts.append(str(item))
    if not content and getattr(result, "structured_content", None) is not None:
        parts.append(json.dumps(result.structured_content, default=str, ensure_ascii=False))
    if getattr(result, "is_error", False):
        parts.insert(0, "Error:")
    text = "\n".join(p for p in parts if p)
    return text or "(empty result)"


def _convert_tool(tool: Any, namespaced_name: str) -> dict[str, Any]:
    schema = getattr(tool, "input_schema", None)
    if not isinstance(schema, dict):
        schema = {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    return {
        "type": "function",
        "function": {
            "name": namespaced_name,
            "description": (getattr(tool, "description", "") or "")[:1024],
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


DEFAULTS_FILENAME = "mcp_servers.default.json"


def load_defaults(root: Path) -> list[dict[str, Any]]:
    """Operator-provided default MCP servers (image-baked, read-only at runtime)."""
    path = root / DEFAULTS_FILENAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def merge_servers(defaults: list[dict[str, Any]], user: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge operator defaults with the user's own servers, keyed by unique name.

    A user entry with the same name overrides the default. Sparse user entries
    (e.g. ``{name, enabled: false}``) inherit url/headers from the default so
    disabling and re-enabling a default works without duplicating its config.
    """
    defaults_by_name: dict[str, dict[str, Any]] = {}
    for d in defaults:
        name = d.get("name")
        if not name:
            continue
        entry = dict(d)
        entry["id"] = "default:" + name
        entry["source"] = "default"
        defaults_by_name[name] = entry

    user_by_name: dict[str, dict[str, Any]] = {}
    for u in user:
        name = u.get("name")
        if not name:
            continue
        user_by_name[name] = u

    merged: list[dict[str, Any]] = []
    for name in sorted(set(defaults_by_name) | set(user_by_name)):
        d = defaults_by_name.get(name)
        u = user_by_name.get(name)
        if u is not None:
            entry = dict(u)
            entry["source"] = "user"
            if d is not None:
                entry.setdefault("url", d.get("url", ""))
                entry.setdefault("headers", d.get("headers") or {})
        else:
            entry = d

        if not entry.get("url"):
            logger.warning("MCP server '%s' has no URL — skipping", name)
            continue
        merged.append(entry)
    return merged


def probe_mcp_server(url: str, headers: dict[str, str] | None = None, timeout: float = 15.0) -> tuple[bool, str, int]:
    """One-shot connectivity check against a remote MCP server. Returns (ok, message, tool_count)."""
    if not MCP_AVAILABLE:
        return False, "MCP client library not installed", 0

    async def _probe() -> int:
        http_client = create_mcp_http_client(headers=headers or None)
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return len(result.tools)

    try:
        n = asyncio.run(asyncio.wait_for(_probe(), timeout=timeout))
        return True, "OK", n
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", 0


class McpServerStore:
    """Per-user MCP server registry persisted as JSON under memory/."""

    def __init__(self, root: Path) -> None:
        self.path = root / "memory" / "mcp_servers.json"

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save(self, servers: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(servers, indent=2, ensure_ascii=False))

    def list(self) -> list[dict[str, Any]]:
        return self._load()

    def get(self, key: str) -> dict[str, Any] | None:
        for s in self._load():
            if s.get("id") == key or s.get("name") == key:
                return s
        return None

    def add(self, name: str, url: str, headers: dict[str, str] | None = None, enabled: bool = True) -> dict[str, Any]:
        servers = self._load()
        for s in servers:
            if s.get("name") == name:
                s["url"] = url
                s["headers"] = headers or {}
                s["enabled"] = enabled
                self._save(servers)
                logger.info("MCP server updated: %s (%s)", name, url)
                return s
        server: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "url": url,
            "headers": headers or {},
            "enabled": enabled,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        servers.append(server)
        self._save(servers)
        logger.info("MCP server added: %s (%s)", name, url)
        return server

    def override(self, name: str, enabled: bool) -> dict[str, Any]:
        """Toggle a server by name, creating a sparse user override for defaults."""
        servers = self._load()
        for s in servers:
            if s.get("name") == name:
                s["enabled"] = enabled
                self._save(servers)
                return s
        server: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "enabled": enabled,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        servers.append(server)
        self._save(servers)
        logger.info("MCP server override: %s enabled=%s", name, enabled)
        return server

    def remove(self, key: str) -> bool:
        servers = self._load()
        kept = [s for s in servers if s.get("id") != key and s.get("name") != key]
        if len(kept) == len(servers):
            return False
        self._save(kept)
        return True

    def set_enabled(self, key: str, enabled: bool) -> bool:
        servers = self._load()
        for s in servers:
            if s.get("id") == key or s.get("name") == key:
                s["enabled"] = enabled
                self._save(servers)
                return True
        return False


class _ServerConn:
    def __init__(self, server_id: str, name: str, url: str, headers: dict[str, str]) -> None:
        self.server_id = server_id
        self.name = name
        self.url = url
        self.headers = headers
        self.stack: AsyncExitStack | None = None
        self.session: Any = None
        self.tools: list[Any] = []
        self.connected = False
        self.last_error = ""
        self.last_attempt = 0.0


class McpManager:
    """Hosts MCP sessions on a background asyncio loop and exposes them synchronously."""

    def __init__(
        self,
        root: Path,
        enabled: bool = True,
        max_tools_per_server: int = 10,
        connect_timeout: float = 15.0,
        call_timeout: float = 60.0,
        retry_backoff: float = 60.0,
    ) -> None:
        self.store = McpServerStore(root)
        self.root = root
        self.enabled = enabled and MCP_AVAILABLE
        self.max_tools_per_server = max_tools_per_server
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self.retry_backoff = retry_backoff

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._conns: dict[str, _ServerConn] = {}
        self._tool_map: dict[str, tuple[str, str]] = {}
        self._tools_schema: list[dict[str, Any]] = []

        if not MCP_AVAILABLE:
            logger.warning("MCP client library not installed — MCP tools disabled")
        if self.enabled:
            self.start()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mcp")
        self._thread.start()
        if not self._ready.wait(timeout=5):
            logger.warning("MCP event loop did not start in time")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def _submit(self, coro: Any, timeout: float) -> Any:
        if not self._loop or not self._loop.is_running():
            raise RuntimeError("MCP event loop is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def get_tools(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            return self._submit(self._refresh(), timeout=self.connect_timeout + 5)
        except Exception as e:
            logger.warning("MCP get_tools failed: %s", e)
            return []

    def call_tool(self, namespaced_name: str, args: dict[str, Any] | None = None) -> str:
        if not self.enabled:
            raise RuntimeError("MCP is disabled")
        mapping = self._tool_map.get(namespaced_name)
        if not mapping:
            raise ValueError(f"unknown MCP tool: {namespaced_name}")
        sid, tool_name = mapping
        try:
            return self._submit(self._call(sid, tool_name, args or {}), timeout=self.call_timeout)
        except Exception as e:
            raise RuntimeError(f"MCP call failed: {e}") from e

    def status(self) -> dict[str, dict[str, Any]]:
        return {
            sid: {"connected": c.connected, "last_error": c.last_error, "tools": len(c.tools)}
            for sid, c in self._conns.items()
        }

    def shutdown(self) -> None:
        if not self._loop:
            return
        try:
            self._submit(self._shutdown(), timeout=10)
        except Exception:
            pass

    async def _refresh(self) -> list[dict[str, Any]]:
        servers = merge_servers(load_defaults(self.root), self.store.list())
        enabled = {s["id"]: s for s in servers if s.get("enabled", True)}
        now = time.monotonic()

        for sid, conn in list(self._conns.items()):
            if sid not in enabled:
                await self._close_conn(conn)
                del self._conns[sid]

        for sid, server in enabled.items():
            conn = self._conns.get(sid)
            if conn is None:
                conn = _ServerConn(sid, server.get("name", ""), server["url"], server.get("headers") or {})
                self._conns[sid] = conn
            if conn.connected:
                continue
            if conn.last_attempt and (now - conn.last_attempt) < self.retry_backoff:
                continue
            conn.last_attempt = now
            try:
                await asyncio.wait_for(self._connect(conn), timeout=self.connect_timeout)
            except Exception as e:
                conn.connected = False
                conn.last_error = f"{type(e).__name__}: {e}"
                logger.warning("MCP server %s connect failed: %s", sid, conn.last_error)

        self._rebuild_tools()
        return self._tools_schema

    async def _connect(self, conn: _ServerConn) -> None:
        await self._close_conn(conn)
        conn.stack = AsyncExitStack()
        http_client = create_mcp_http_client(headers=conn.headers or None)
        read, write = await conn.stack.enter_async_context(
            streamable_http_client(conn.url, http_client=http_client)
        )
        conn.session = await conn.stack.enter_async_context(ClientSession(read, write))
        await conn.session.initialize()
        result = await conn.session.list_tools()
        conn.tools = list(result.tools)
        conn.connected = True
        conn.last_error = ""
        logger.info("MCP server %s connected: %d tools", conn.server_id, len(conn.tools))

    async def _close_conn(self, conn: _ServerConn) -> None:
        conn.connected = False
        conn.session = None
        if conn.stack is not None:
            try:
                await conn.stack.aclose()
            except Exception:
                pass
            conn.stack = None

    def _rebuild_tools(self) -> None:
        self._tool_map = {}
        schema: list[dict[str, Any]] = []
        for sid, conn in self._conns.items():
            if not conn.connected:
                continue
            ns_prefix = f"mcp__{_sanitize_slug(conn.name)}"
            for tool in conn.tools[: self.max_tools_per_server]:
                tool_name = getattr(tool, "name", "")
                if not tool_name:
                    continue
                namespaced = f"{ns_prefix}__{tool_name}"
                self._tool_map[namespaced] = (sid, tool_name)
                schema.append(_convert_tool(tool, namespaced))
        self._tools_schema = schema

    async def _call(self, sid: str, tool_name: str, args: dict[str, Any]) -> str:
        conn = self._conns.get(sid)
        if not conn or not conn.connected or not conn.session:
            raise RuntimeError(f"MCP server {sid} is not connected")
        try:
            result = await conn.session.call_tool(tool_name, args)
        except Exception as e:
            conn.connected = False
            conn.last_error = f"{type(e).__name__}: {e}"
            raise
        return _content_to_string(result)

    async def _shutdown(self) -> None:
        for conn in list(self._conns.values()):
            await self._close_conn(conn)
        self._conns.clear()
        self._tool_map.clear()
        self._tools_schema = []
