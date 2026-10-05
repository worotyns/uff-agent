from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.thread_ctx import get_data_root
from src.tools.skill import BaseSkill

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCHEMA_FALLBACK = _REPO_ROOT / "entities" / "schema.yaml"


def _graph_file() -> Path:
    return get_data_root() / "memory" / "entities" / "graph.jsonl"


def _schema_file() -> Path:
    return get_data_root() / "memory" / "entities" / "schema.yaml"


def _ensure_dirs() -> None:
    _graph_file().parent.mkdir(parents=True, exist_ok=True)


def _load_schema() -> dict[str, Any]:
    sc = _schema_file()
    path = sc if sc.exists() else _SCHEMA_FALLBACK
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _validate(entity_type: str, properties: dict) -> str | None:
    schema = _load_schema()
    type_def = schema.get("types", {}).get(entity_type, {})
    required = type_def.get("required", [])
    for field in required:
        if field not in properties or not properties.get(field):
            return f"Pole '{field}' jest wymagane dla typu '{entity_type}'."
    prop_defs = type_def.get("properties", {})
    for k, v in properties.items():
        if k in prop_defs and "enum" in prop_defs[k]:
            if v not in prop_defs[k]["enum"]:
                return f"Pole '{k}' dla '{entity_type}' musi być jednym z: {', '.join(prop_defs[k]['enum'])}"
    return None


def _validate_relation(relation: str, from_type: str | None, to_type: str | None) -> str | None:
    schema = _load_schema()
    rel_def = schema.get("relations", {}).get(relation)
    if not rel_def:
        return None
    if from_type and rel_def.get("from_types") and from_type not in rel_def["from_types"]:
        return f"Relacja '{relation}' nie może mieć źródła typu '{from_type}'."
    if to_type and rel_def.get("to_types") and to_type not in rel_def["to_types"]:
        return f"Relacja '{relation}' nie może mieć celu typu '{to_type}'."
    return None


_GRAPH_CACHE: dict[str, Any] = {"path": None, "state": None, "mtime": None, "size": None}


def _new_state() -> dict[str, Any]:
    return {"entities": {}, "relations": [], "outgoing": {}, "incoming": {}}


def _apply_op(state: dict[str, Any], op: dict) -> None:
    if op.get("op") == "create":
        ent = op["entity"]
        state["entities"][ent["id"]] = ent
    elif op.get("op") == "update":
        ent = op["entity"]
        if ent["id"] in state["entities"]:
            old = state["entities"][ent["id"]]
            for k, v in ent.items():
                if k != "id":
                    old[k] = v
    elif op.get("op") == "relate":
        rel = op["relation"]
        state["relations"].append(rel)
        fid, tid = rel["from_id"], rel["to_id"]
        state["outgoing"].setdefault(fid, []).append(rel)
        state["incoming"].setdefault(tid, []).append(rel)
    elif op.get("op") == "delete":
        del_id = op["id"]
        state["entities"].pop(del_id, None)


def _load_graph() -> dict[str, Any]:
    gf = _graph_file()
    try:
        st = gf.stat()
    except FileNotFoundError:
        if _GRAPH_CACHE["path"] == str(gf) and _GRAPH_CACHE["state"] is not None:
            return _GRAPH_CACHE["state"]
        _GRAPH_CACHE.update(path=str(gf), state=_new_state(), mtime=None, size=None)
        return _GRAPH_CACHE["state"]

    if (
        _GRAPH_CACHE["path"] == str(gf)
        and _GRAPH_CACHE["state"] is not None
        and _GRAPH_CACHE["mtime"] == st.st_mtime_ns
        and _GRAPH_CACHE["size"] == st.st_size
    ):
        return _GRAPH_CACHE["state"]

    state = _new_state()
    for line in gf.read_text().strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            op = json.loads(line)
        except json.JSONDecodeError:
            continue
        _apply_op(state, op)

    _GRAPH_CACHE.update(path=str(gf), state=state, mtime=st.st_mtime_ns, size=st.st_size)
    return state


def _save_op(op: dict) -> None:
    gf = _graph_file()
    _ensure_dirs()
    with open(gf, "a") as f:
        f.write(json.dumps(op, ensure_ascii=False) + "\n")

    if _GRAPH_CACHE["state"] is not None:
        _apply_op(_GRAPH_CACHE["state"], op)
    try:
        st = gf.stat()
        _GRAPH_CACHE.update(mtime=st.st_mtime_ns, size=st.st_size, path=str(gf))
    except OSError:
        _GRAPH_CACHE["mtime"] = None
        _GRAPH_CACHE["size"] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())[:8]


def _is_stale(ent: dict) -> bool:
    sa = ent.get("stale_after")
    if not sa:
        return False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return today >= sa


def _apply_metadata(ent: dict, tags=None, status=None, stale_after=None, verified=None, sources=None) -> None:
    if tags is not None:
        ent["tags"] = tags
    if status:
        ent["status"] = status
    if stale_after:
        ent["stale_after"] = stale_after
    if verified:
        ent["verified"] = verified
    if sources:
        ent["sources"] = sources


def entity_create(e_type: str, properties: dict, tags=None, status=None, stale_after=None, verified=None, sources=None) -> str:
    err = _validate(e_type, properties)
    if err:
        return f"Blad: {err}"

    e_id = _new_id()
    now = _now()
    ent = {
        "id": e_id,
        "type": e_type,
        "properties": properties,
        "created": now,
        "updated": now,
    }
    _apply_metadata(ent, tags, status, stale_after, verified, sources)
    _save_op({"op": "create", "entity": ent})
    logger.info("ontology: created %s %s (%s)", e_type, properties.get("name", ""), e_id)
    return f"Utworzono encję [{e_id}] typu '{e_type}': {json.dumps(properties, ensure_ascii=False)}"


def entity_get(e_id: str) -> str:
    state = _load_graph()
    ent = state["entities"].get(e_id)
    if not ent:
        return f"Encja {e_id} nie znaleziona."
    rels = state["outgoing"].get(e_id, []) + state["incoming"].get(e_id, [])
    lines = [
        f"ID: {ent['id']}",
        f"Typ: {ent['type']}",
        f"Właściwości: {json.dumps(ent['properties'], ensure_ascii=False, indent=2)}",
    ]
    if ent.get("tags"):
        lines.append(f"Tagi: {', '.join(ent['tags'])}")
    if ent.get("status"):
        lines.append(f"Status: {ent['status']}")
    if ent.get("stale_after"):
        stale_note = " (PRZETERMINOWANA)" if _is_stale(ent) else ""
        lines.append(f"Ważna do: {ent['stale_after']}{stale_note}")
    if ent.get("verified"):
        lines.append(f"Zweryfikowana: {json.dumps(ent['verified'], ensure_ascii=False)}")
    if ent.get("sources"):
        lines.append(f"Źródła: {json.dumps(ent['sources'], ensure_ascii=False)}")
    lines.append(f"Utworzono: {ent['created']}")
    if rels:
        lines.append("\nRelacje:")
        for r in rels:
            direction = "->" if r["from_id"] == e_id else "<-"
            other = r["to_id"] if r["from_id"] == e_id else r["from_id"]
            other_ent = state["entities"].get(other)
            other_label = f"{other_ent['properties'].get('name', other)}" if other_ent else other
            lines.append(f"  {direction} {r['relation']} ({other_label} [{other}])")
    return "\n".join(lines)


def entity_query(e_type: str | None, filters: dict | None = None) -> str:
    state = _load_graph()
    results = []
    for ent in state["entities"].values():
        if e_type and ent["type"] != e_type:
            continue
        if filters:
            match = True
            for k, v in filters.items():
                if k not in ent["properties"]:
                    match = False
                    break
                val = ent["properties"][k]
                if isinstance(v, str) and isinstance(val, str):
                    if v.lower() not in val.lower():
                        match = False
                        break
                elif val != v:
                    match = False
                    break
            if not match:
                continue
        results.append(ent)
    if not results:
        return "Brak wyników."
    lines = [f"Znaleziono {len(results)} encji:"]
    for ent in results:
        props = ent["properties"]
        label = props.get("name") or props.get("title") or (list(props.values())[0] if props else ent["id"])
        markers = []
        if ent.get("status") and ent.get("status") != "stable":
            markers.append(ent["status"])
        if _is_stale(ent):
            markers.append("przeterminowana")
        marker_str = f" [{', '.join(markers)}]" if markers else ""
        lines.append(f"  [{ent['id']}] {ent['type']}: {label}{marker_str}")

    if len(results) <= 10:
        lines.append("\nAby poznać szczegóły, użyj entity_get(id).")
    return "\n".join(lines)


def entity_relate(from_id: str, relation: str, to_id: str, properties: dict | None = None) -> str:
    state = _load_graph()
    from_ent = state["entities"].get(from_id)
    to_ent = state["entities"].get(to_id)
    if not from_ent:
        return f"Encja źródłowa {from_id} nie istnieje."
    if not to_ent:
        return f"Encja docelowa {to_id} nie istnieje."

    err = _validate_relation(relation, from_ent["type"], to_ent["type"])
    if err:
        return f"Blad: {err}"

    now = _now()
    rel = {
        "from_id": from_id,
        "relation": relation,
        "to_id": to_id,
        "properties": properties or {},
        "created": now,
    }
    _save_op({"op": "relate", "relation": rel})

    from_label = from_ent["properties"].get("name", from_id)
    to_label = to_ent["properties"].get("name", to_id)
    logger.info("ontology: related %s --[%s]--> %s", from_label, relation, to_label)
    return f"Połączono [{from_id}] {from_label} --[{relation}]--> [{to_id}] {to_label}"


def entity_graph(e_id: str, depth: int = 2) -> str:
    state = _load_graph()
    root = state["entities"].get(e_id)
    if not root:
        return f"Encja {e_id} nie znaleziona."

    visited: set[str] = set()
    lines: list[str] = []
    _traverse(e_id, state, visited, lines, depth=depth, indent=0)
    return "\n".join(lines) if lines else "Brak relacji."


def _traverse(e_id: str, state: dict, visited: set[str], lines: list[str], depth: int, indent: int) -> None:
    if e_id in visited or depth < 0:
        return
    visited.add(e_id)
    ent = state["entities"].get(e_id)
    label = f"[{ent['id']}] {ent['type']}: {ent['properties'].get('name', '')}" if ent else f"[{e_id}] (usunięta)"
    if indent == 0:
        lines.append(label)
    else:
        prefix = "  " * indent + "└─"
        lines.append(f"{prefix} {label}")

    if depth == 0:
        return

    for rel in state["outgoing"].get(e_id, []):
        rel_label = f"--[{rel['relation']}]-->"
        if indent == 0:
            lines.append(f"  {rel_label}")
        else:
            prefix = "  " * (indent + 1) + rel_label
            lines.append(f"{'  ' * (indent + 1)}{rel_label}")
        _traverse(rel["to_id"], state, visited, lines, depth - 1, indent + 1)

    for rel in state["incoming"].get(e_id, []):
        rel_label = f"<--[{rel['relation']}]--"
        _traverse(rel["from_id"], state, visited, lines, depth - 1, indent + 1)


def entity_update(e_id: str, properties: dict, tags=None, status=None, stale_after=None, verified=None, sources=None) -> str:
    state = _load_graph()
    ent = state["entities"].get(e_id)
    if not ent:
        return f"Encja {e_id} nie znaleziona."
    ent["properties"].update(properties)
    _apply_metadata(ent, tags, status, stale_after, verified, sources)
    err = _validate(ent["type"], ent["properties"])
    if err:
        return f"Blad: {err}"
    now = _now()
    updated = {**ent, "updated": now}
    _save_op({"op": "update", "entity": updated})
    return f"Zaktualizowano [{e_id}]: {json.dumps(properties, ensure_ascii=False)}"


def entity_remove(e_id: str) -> str:
    state = _load_graph()
    if e_id not in state["entities"]:
        return f"Encja {e_id} nie znaleziona."
    _save_op({"op": "delete", "id": e_id})
    return f"Usunięto encję [{e_id}]."


class CreateEntity(BaseSkill):
    name = "entity_create"
    description = (
        "Create a new entity (e.g. Person, Document, Event, Fact, Asset) with properties. "
        "Use to store information about people, documents, events, facts. "
        "Optionally provide: tags (list), status (draft/stable/deprecated), stale_after (date "
        "YYYY-MM-DD when the entity becomes outdated), verified (list e.g. [{\"by\": \"human:user\", "
        "\"at\": \"2026-08-13\"}]) — set stale_after when the user says something will change in the future, "
        "and verified when the user confirms the data."
    )
    parameters = {
        "type": {"type": "string", "description": "Entity type: Person, Company, Document, Event, Fact, Note, Plan, Task, Asset, Place, Interest, Project (any other type is also accepted)"},
        "properties": {"type": "object", "description": "Entity properties as JSON, e.g. {\"name\": \"John Smith\", \"email\": \"john@example.com\"}"},
        "tags": {"type": "array", "description": "Optional tags, e.g. [\"family\", \"finance\"]", "default": []},
        "status": {"type": "string", "description": "Status: draft / stable / deprecated", "default": "stable"},
        "stale_after": {"type": "string", "description": "Expiry date YYYY-MM-DD (entity becomes outdated from that day)", "default": ""},
        "verified": {"type": "array", "description": "Verification events, e.g. [{\"by\": \"human:user\", \"at\": \"2026-08-13\"}]", "default": []},
        "sources": {"type": "array", "description": "Information sources (optional)", "default": []},
    }

    def execute(self, **kwargs: Any) -> str:
        e_type = kwargs.get("type", "")
        properties = kwargs.get("properties", {})
        if not e_type:
            return "Podaj typ encji."
        return entity_create(
            e_type, properties,
            tags=kwargs.get("tags") or None,
            status=kwargs.get("status") or None,
            stale_after=kwargs.get("stale_after") or None,
            verified=kwargs.get("verified") or None,
            sources=kwargs.get("sources") or None,
        )


class GetEntity(BaseSkill):
    name = "entity_get"
    description = "Get an entity's details by its ID, together with relations."
    parameters = {
        "id": {"type": "string", "description": "Entity ID"},
    }

    def execute(self, **kwargs: Any) -> str:
        e_id = kwargs.get("id", "")
        if not e_id:
            return "Podaj ID encji."
        return entity_get(e_id)


class QueryEntities(BaseSkill):
    name = "entity_query"
    description = "Search entities by type and/or properties. E.g. entity_query(type=Person, filters={\"name\": \"John\"}) or entity_query(type=Skill)."
    parameters = {
        "type": {"type": "string", "description": "Entity type to filter by (optional, omit to search all)"},
        "filters": {"type": "object", "description": "Property filter as JSON, e.g. {\"name\": \"John\", \"skills\": \"Python\"}. String values are partially matched."},
    }

    def execute(self, **kwargs: Any) -> str:
        e_type = kwargs.get("type") or None
        filters = kwargs.get("filters") or None
        return entity_query(e_type, filters)


class RelateEntities(BaseSkill):
    name = "entity_relate"
    description = "Link two entities with a relation. E.g. entity_relate(from=ID_Person, relation=owns, to=ID_Asset). Use for: 'John owns an apartment', 'works at company X', 'policy refers to a car'."
    parameters = {
        "from": {"type": "string", "description": "Source entity ID"},
        "relation": {"type": "string", "description": "Relation name, e.g. works_at, owns, likes, participated, issued, refers_to, located_at, knows, relates_to"},
        "to": {"type": "string", "description": "Target entity ID"},
        "properties": {"type": "object", "description": "Additional relation properties (optional)", "default": {}},
    }

    def execute(self, **kwargs: Any) -> str:
        from_id = kwargs.get("from", "")
        relation = kwargs.get("relation", "")
        to_id = kwargs.get("to", "")
        properties = kwargs.get("properties") or {}
        if not from_id or not relation or not to_id:
            return "Podaj from, relation i to."
        return entity_relate(from_id, relation, to_id, properties)


class EntityGraph(BaseSkill):
    name = "entity_graph"
    description = "Show the connection graph of a given entity — what it is connected to and how."
    parameters = {
        "id": {"type": "string", "description": "Entity ID"},
        "depth": {"type": "integer", "description": "Graph traversal depth (default 2)", "default": 2},
    }

    def execute(self, **kwargs: Any) -> str:
        e_id = kwargs.get("id", "")
        depth = int(kwargs.get("depth", 2))
        if not e_id:
            return "Podaj ID encji."
        return entity_graph(e_id, depth)


class UpdateEntity(BaseSkill):
    name = "entity_update"
    description = (
        "Update the properties and metadata of an existing entity. Use stale_after when something stops "
        "being current from a given date, status='deprecated' for outdated decisions, verified when the "
        "user confirms data."
    )
    parameters = {
        "id": {"type": "string", "description": "Entity ID"},
        "properties": {"type": "object", "description": "New properties to overwrite as JSON"},
        "tags": {"type": "array", "description": "Optional tags", "default": []},
        "status": {"type": "string", "description": "Status: draft / stable / deprecated", "default": ""},
        "stale_after": {"type": "string", "description": "Expiry date YYYY-MM-DD", "default": ""},
        "verified": {"type": "array", "description": "Verification events", "default": []},
        "sources": {"type": "array", "description": "Information sources", "default": []},
    }

    def execute(self, **kwargs: Any) -> str:
        e_id = kwargs.get("id", "")
        properties = kwargs.get("properties", {})
        if not e_id or not properties:
            return "Podaj id i properties."
        return entity_update(
            e_id, properties,
            tags=kwargs.get("tags") or None,
            status=kwargs.get("status") or None,
            stale_after=kwargs.get("stale_after") or None,
            verified=kwargs.get("verified") or None,
            sources=kwargs.get("sources") or None,
        )


class RemoveEntity(BaseSkill):
    name = "entity_remove"
    description = "Delete an entity by its ID."
    parameters = {
        "id": {"type": "string", "description": "Entity ID"},
    }

    def execute(self, **kwargs: Any) -> str:
        e_id = kwargs.get("id", "")
        if not e_id:
            return "Podaj ID encji."
        return entity_remove(e_id)
