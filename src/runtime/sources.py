from __future__ import annotations

import re


def strip_tool_artifacts(text: str) -> str:
    return re.sub(r'\n*\[Tool \w+ returned:.*?\]', '', text, flags=re.DOTALL).strip()


def format_sources_block(sources: list[dict], language: str = "en") -> str:
    if not sources:
        return ""
    tool_counts: dict[str, int] = {}
    tool_extras: dict[str, list[str]] = {}

    for s in sources:
        tool = s["tool"]
        tool_counts[tool] = tool_counts.get(tool, 0) + 1

        info = extract_tool_info(s)
        if info:
            if info not in tool_extras.get(tool, []):
                tool_extras.setdefault(tool, []).append(info)

    header = "**Źródła:**" if language == "pl" else "**Sources:**"
    lines = ["---", header]
    for tool, count in tool_counts.items():
        label = f"`{tool}`" if count == 1 else f"`{tool}` ×{count}"
        if tool in tool_extras:
            extras = ", ".join(tool_extras[tool][:5])
            if len(tool_extras[tool]) > 5:
                extras += f" (+{len(tool_extras[tool]) - 5})"
            label += f" — {extras}"
        lines.append(f"- {label}")
    return "\n".join(lines)


def extract_tool_info(s: dict) -> str:
    tool = s["tool"]
    args = s.get("args", {})
    snippet = s.get("result_snippet", "")

    if tool == "search_documents":
        paths = re.findall(r"^\s*\[([^\]]+)\]\s*\(score:", snippet, re.MULTILINE)
        if paths:
            return ", ".join(p.replace("attachments/", "").split("/")[-1] for p in paths[:3])
        q = args.get("query", "")
        return q[:60] if q else tool

    if tool in ("web_search", "web_fetch", "browser_screenshot", "browser_markdown", "browser_crawl"):
        urls = re.findall(r"\*\*URL:\*\*\s*(\S+)", snippet)
        if urls:
            return ", ".join(u[:60] for u in urls[:3])
        url = args.get("url", "")
        if url:
            return url[:60]
        q = args.get("query", "")
        return q[:60] if q else tool

    if tool == "entity_create":
        e_type = args.get("type", "")
        props = args.get("properties", {})
        name = props.get("name") or props.get("title") or props.get("statement", "")
        if name:
            return f"{e_type}: {str(name)[:50]}"
        return e_type if e_type else tool

    if tool == "entity_query":
        e_type = args.get("type", "")
        filters = args.get("filters", {})
        if filters:
            f_str = ", ".join(f"{k}={v}" for k, v in list(filters.items())[:2])
            return f"{e_type}({f_str})" if e_type else f_str
        return e_type if e_type else tool

    if tool == "entity_relate":
        relation = args.get("relation", "")
        if relation:
            return relation
        return tool

    if tool in ("entity_get", "entity_graph", "entity_remove", "entity_update"):
        eid = args.get("id", "")
        return eid if eid else tool

    if tool == "create_attachment":
        filename = args.get("filename", "")
        return filename if filename else tool

    if tool == "generate_image":
        prompt = args.get("prompt", "")
        return prompt[:50] if prompt else tool

    if tool == "text_to_speech":
        text = args.get("text", "")
        return text[:50] if text else tool

    if tool == "index_document":
        path = args.get("path", "")
        return path.split("/")[-1] if path else tool

    if tool == "send_progress":
        msg = args.get("message", "")
        return msg[:50] if msg else tool

    return ""
