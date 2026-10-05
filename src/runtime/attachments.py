from __future__ import annotations

import logging
import time
from pathlib import Path

from src.prompts.document_analyzer import document_analysis
from src.storage.attachment import save_attachment
from src.storage.index import DocumentIndex

logger = logging.getLogger(__name__)

SMALL_DOC_THRESHOLD = 2000


def process_attachments(attachments, agent, skills, data_root, config, event) -> list[str]:
    notes: list[str] = []
    if not attachments or not event.thread:
        return notes
    root = data_root or config.root
    thread_dir = root / "threads" / event.thread[:16]
    for i, att in enumerate(attachments):
        data = read_attachment_data(att, i)
        if not data:
            continue
        filename = att.get("filename", f"file_{i}")
        save_attachment(thread_dir, filename, data, vision_config={
            "api_key": config.openrouter_api_key if config else None,
            "vision_model": config.vision_model if config else None,
        })
        txt_path = thread_dir / "attachments" / f"{filename}.txt"
        if not txt_path.exists():
            logger.warning("  attachment %d: no text extracted", i)
            continue
        text = txt_path.read_text()
        if len(text) <= SMALL_DOC_THRESHOLD:
            notes.append(
                f"[Attachment: {filename}]\n{text}\n\n"
                f"(also indexed for RAG — use `search_documents(query)` for semantic search. "
                f"Do NOT use `read_file` or `list_dir` for this document.)"
            )
            logger.info("  attachment %d: inline + RAG (%d chars)", i, len(text))
        else:
            notes.append(
                f"[Attachment: {filename} ({len(text)} chars, indexed in RAG)]\n"
                f"Use `search_documents(query)` to find relevant passages. "
                f"Do NOT use `read_file` or `list_dir` for this document."
            )
            logger.info("  attachment %d: RAG only (%d chars)", i, len(text))
        if "rag" in agent.skills and skills:
            index_attachment(data_root or config.root, filename, text, thread_id=event.thread)
    return notes


def pre_analyze_documents(attachments: list[dict], thread_id: str, llm, model: str, data_root: Path, language: str = "") -> str:
    if not attachments or not thread_id:
        return ""
    att_dir = data_root / "threads" / thread_id[:16] / "attachments"
    if not att_dir.is_dir():
        return ""

    results: list[str] = []
    for att in attachments:
        filename = att.get("filename", "")
        if not filename:
            continue
        txt_path = att_dir / f"{filename}.txt"
        if not txt_path.exists():
            continue
        text = txt_path.read_text()
        if len(text) < 2000:
            continue

        logger.info("document_analysis: analyzing %s (%d chars)...", filename, len(text))
        analysis = document_analysis(text, llm, model, filename=filename, language=language)
        if analysis:
            results.append(f"## {filename}\n\n{analysis}")

    if not results:
        return ""
    return "\n\n---\n\n".join(["[Pre-analysis of attachments:]"] + results)


def read_attachment_data(att, i):
    src_path = att.get("path")
    if src_path:
        src = Path(src_path)
        if src.exists():
            data = src.read_bytes()
            src.unlink()
            parent = src.parent
            if parent.exists() and not any(parent.iterdir()):
                try:
                    parent.rmdir()
                except OSError:
                    pass
            logger.info("  attachment %d: %s (%d bytes)", i, att.get("filename", "?"), len(data))
            return data
        logger.warning("  attachment %d: path missing %s", i, src_path)
        return None
    if "data" in att:
        logger.info("  attachment %d: inline (%d bytes)", i, len(att["data"]))
        return att["data"]
    return None


def search_rag_for_email(root, email_body, attachments, thread_id=None):
    if not root:
        return "", []
    try:
        idx = DocumentIndex(root)

        queries = [email_body] if email_body else []
        for att in (attachments or []):
            fn = att.get("filename", "")
            name = Path(fn).stem.replace("_", " ").replace("-", " ")
            if name:
                queries.append(name)
            txt_path = root / "threads" / "attachments" / f"{fn}.txt"
            if txt_path.exists():
                txt = txt_path.read_text()
                if len(txt) <= 500:
                    queries.append(txt)

        seen: set[str] = set()
        results: list[str] = []
        sources: list[dict] = []
        for q in queries:
            if not q.strip():
                continue
            docs = idx.search(q, top_k=5, thread_id=thread_id)
            if not docs and thread_id:
                docs = idx.search(q, top_k=5, thread_id=None)
            for d in docs:
                key = f"{d.get('path', '')}:{d.get('text', '')[:80]}"
                if key in seen:
                    continue
                seen.add(key)
                scope_tag = "[thread]" if d.get("thread_id") == thread_id else "[global]"
                results.append(f"{scope_tag} {d.get('path', '?')}: {d.get('text', '')[:500]}")
                sources.append({
                    "tool": "search_documents",
                    "args": {"query": q[:100], "scope": "auto"},
                    "result_snippet": d.get("text", "")[:500],
                    "took_s": 0,
                })
            if len(results) >= 10:
                break

        if results:
            logger.info("RAG auto-search: %d relevant passages found", len(results))
            return "\n\n".join(results[:10]), sources
        logger.info("RAG auto-search: no relevant passages found")
        return "", []
    except Exception as e:
        logger.warning("RAG auto-search failed: %s", e)
        return "", []


def index_attachment(root, filename, text, thread_id=None) -> None:
    logger.info("  RAG: indexing %s (%d chars, thread=%s)...", filename, len(text), thread_id)
    try:
        idx = DocumentIndex(root)
        n = idx.add_document(f"attachments/{filename}", text, thread_id=thread_id)
        logger.info("  RAG: indexed %s: %d chunks", filename, n)
    except Exception as e:
        logger.warning("  RAG: failed to index %s: %s", filename, e)


def collect_generated_attachments(event, root: Path, original_filenames: set[str] | None = None) -> list[Path]:
    if not event.thread or not root:
        return []
    att_dir = root / "threads" / event.thread[:16] / "attachments"
    if not att_dir.is_dir():
        return []
    if original_filenames is None:
        original_filenames = set()
    internal_stems: set[str] = set()
    for f in att_dir.iterdir():
        if f.is_file() and f.suffix == ".yaml":
            stem = f.stem.replace(".meta", "")
            internal_stems.add(stem)
    found: list[Path] = []
    for f in sorted(att_dir.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        if f.suffix in (".yaml", ".yml"):
            continue
        if f.name in original_filenames:
            continue
        stem = f.name
        for s in internal_stems:
            if stem == s or stem.startswith(s + "."):
                break
        else:
            found.append(f)
    if found:
        logger.info("found %d generated attachment(s) to include in reply: %s", len(found), [f.name for f in found])
    return found


def move_sent_attachments(event, root: Path, sent_files: list[Path]) -> None:
    if not event.thread or not root or not sent_files:
        return
    sent_dir = root / "threads" / event.thread[:16] / "attachments" / ".sent"
    sent_dir.mkdir(parents=True, exist_ok=True)
    for f in sent_files:
        if not f.exists():
            continue
        try:
            dest = sent_dir / f.name
            f.rename(dest)
            logger.info("moved sent attachment %s -> .sent/", f.name)
        except Exception as e:
            logger.warning("failed to move sent attachment %s: %s", f.name, e)


def index_in_background(root: Path, filename: str, text: str) -> None:
    logger.info("RAG background: starting index for %s (%d chars)...", filename, len(text))
    t0 = time.time()
    try:
        idx = DocumentIndex(root)
        n = idx.add_document(f"attachments/{filename}", text)
        dt = time.time() - t0
        logger.info("RAG background: indexed %s: %d chunks in %.1fs", filename, n, dt)
    except Exception as e:
        logger.error("RAG background: failed to index %s: %s", filename, e)
