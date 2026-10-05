from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.llm.embeddings import cosine_similarity, get_embedding, get_embeddings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100


def _doc_id(path: str) -> str:
    return path.replace("/", "_").replace(".", "_").replace("\\", "_")[:64]


class DocumentIndex:
    def __init__(self, root: Path) -> None:
        self.index_dir = root / "index"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.index_dir / "manifest.json"
        self.manifest: dict[str, dict[str, Any]] = {}
        self._load_manifest()

    def _load_manifest(self) -> None:
        if self.manifest_path.exists():
            try:
                self.manifest = json.loads(self.manifest_path.read_text())
            except json.JSONDecodeError:
                logger.warning("corrupt manifest, starting fresh")
                self.manifest = {}
        logger.info("loaded index: %d documents", len(self.manifest))

    def _save_manifest(self) -> None:
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2))

    def _embed_path(self, doc_id: str) -> Path:
        return self.index_dir / f"{doc_id}.embed.json"

    def _chunks_path(self, doc_id: str) -> Path:
        return self.index_dir / f"{doc_id}.chunks.json"

    def add_document(self, path: str, content: str, thread_id: str | None = None) -> int:
        doc_id = _doc_id(path)
        chunks = self._chunk_text(content)

        embeddings = get_embeddings(chunks)
        if not embeddings:
            logger.warning("indexed %s: 0 chunks (embeddings unavailable)", path)
            return 0

        self._embed_path(doc_id).write_text(json.dumps(embeddings))
        self._chunks_path(doc_id).write_text(json.dumps(chunks))

        self.manifest[doc_id] = {
            "path": path,
            "chunks": len(chunks),
            "embed_file": self._embed_path(doc_id).name,
            "chunks_file": self._chunks_path(doc_id).name,
        }
        if thread_id:
            self.manifest[doc_id]["thread_id"] = thread_id
        self._save_manifest()
        logger.info("indexed %s: %d/%d chunks (thread=%s)", path, len(chunks), len(chunks), thread_id)
        return len(chunks)

    def search(self, query: str, top_k: int = 5, thread_id: str | None = None) -> list[dict[str, Any]]:
        if not self.manifest:
            logger.info("RAG search: no documents indexed")
            return []

        q_emb = get_embedding(query)
        if not q_emb:
            logger.info("RAG search: embeddings unavailable, returning empty")
            return []

        scored: list[tuple[float, str, dict[str, Any], int]] = []

        for doc_id, info in self.manifest.items():
            if thread_id and info.get("thread_id") != thread_id:
                continue
            embed_path = self.index_dir / info["embed_file"]
            if not embed_path.exists():
                continue
            try:
                doc_embs: list[list[float]] = json.loads(embed_path.read_text())
            except (json.JSONDecodeError, FileNotFoundError):
                continue

            for i, emb in enumerate(doc_embs):
                score = cosine_similarity(q_emb, emb)
                scored.append((score, doc_id, info, i))

        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            logger.info("RAG search: no results above score threshold")
            return []

        logger.info("RAG search: %d candidates, returning top %d (thread=%s)", len(scored), min(top_k, len(scored)), thread_id)
        results: list[dict[str, Any]] = []
        for score, doc_id, info, chunk_idx in scored[:top_k]:
            if score < 0.3:
                continue
            chunks_path = self.index_dir / info.get("chunks_file", "")
            if not chunks_path.exists():
                continue
            try:
                texts: list[str] = json.loads(chunks_path.read_text())
                if chunk_idx < len(texts):
                    results.append({
                        "path": info.get("path", doc_id),
                        "score": round(score, 3),
                        "text": texts[chunk_idx][:500],
                        "thread_id": info.get("thread_id"),
                    })
            except (json.JSONDecodeError, FileNotFoundError):
                continue

        return results

    def remove_document(self, path: str) -> int:
        doc_id = _doc_id(path)
        info = self.manifest.pop(doc_id, None)
        if info:
            for key in ("embed_file", "chunks_file"):
                f = self.index_dir / info.get(key, "")
                if f.exists():
                    f.unlink()
            self._save_manifest()
            logger.info("removed %s from index", path)
            return info.get("chunks", 0)
        return 0

    def find_document(self, path: str) -> dict[str, Any] | None:
        doc_id = _doc_id(path)
        return self.manifest.get(doc_id)

    @staticmethod
    def _chunk_text(text: str) -> list[str]:
        if len(text) <= CHUNK_SIZE:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + CHUNK_SIZE
            if end < len(text):
                boundary = text.rfind(" ", start, end)
                if boundary > start:
                    end = boundary
            chunks.append(text[start:end])
            start = end - CHUNK_OVERLAP
        return chunks
