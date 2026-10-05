from __future__ import annotations

from typing import Any

from src.storage.index import DocumentIndex
from src.thread_ctx import get_data_root
from src.tools.skill import BaseSkill


def _get_index() -> DocumentIndex:
    return DocumentIndex(get_data_root())


class IndexDocument(BaseSkill):
    name = "index_document"
    description = "Chunk and index a document for semantic search"
    parameters = {
        "path": {"type": "string", "description": "Document path (relative to data dir)"},
        "content": {"type": "string", "description": "Document content to index"},
    }

    def execute(self, **kwargs: Any) -> str:
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")
        idx = _get_index()
        count = idx.add_document(path, content)
        return f"Indexed {count} chunks from {path}"


class SearchDocuments(BaseSkill):
    name = "search_documents"
    description = "Semantic search over indexed documents. Use this to find information in documents. Set scope='thread' to search only documents in the current email thread, or scope='global' to search all documents (including memory and globally indexed files)."
    parameters = {
        "query": {"type": "string", "description": "Search query"},
        "top_k": {"type": "integer", "description": "Number of results", "default": 5},
        "scope": {"type": "string", "description": "Search scope: 'thread' (current thread attachments only), 'global' (all files, memory), or 'all' (both)", "default": "thread"},
    }

    def execute(self, **kwargs: Any) -> str:
        query = kwargs.get("query", "")
        top_k = int(kwargs.get("top_k", 5))
        scope = kwargs.get("scope", "thread")
        thread_id = kwargs.get("_thread_id", "")
        idx = _get_index()
        if scope == "thread" and thread_id:
            results = idx.search(query, top_k, thread_id=thread_id)
        elif scope == "thread":
            results = idx.search(query, top_k, thread_id=None) if thread_id else idx.search(query, top_k)
        elif scope == "global":
            results = idx.search(query, top_k, thread_id=None)
        else:
            thread_results = idx.search(query, top_k, thread_id=thread_id) if thread_id else []
            global_results = idx.search(query, top_k, thread_id=None)
            seen = set(r.get("path") for r in thread_results)
            results = thread_results + [r for r in global_results if r.get("path") not in seen]
            results = results[:top_k]
        if not results:
            return "No relevant documents found."
        lines = [f"Found {len(results)} results:"]
        for r in results:
            lines.append(f"\n[{r['path']}] (score: {r['score']})\n{r['text']}\n---")
        return "\n".join(lines)


class RemoveDocument(BaseSkill):
    name = "remove_document"
    description = "Remove a document from the search index"
    parameters = {
        "path": {"type": "string", "description": "Document path to remove"},
    }

    def execute(self, **kwargs: Any) -> str:
        path = kwargs.get("path", "")
        idx = _get_index()
        count = idx.remove_document(path)
        return f"Removed {count} chunks for {path}"
