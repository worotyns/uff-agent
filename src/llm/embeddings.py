from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "openai/text-embedding-3-small")
EMBEDDING_BASE = "https://openrouter.ai/api/v1"
BATCH_SIZE = 20


def get_embedding(text: str, api_key: str | None = None) -> list[float]:
    result = get_embeddings([text], api_key)
    return result[0] if result else []


def _batch_embeddings(texts: list[str], key: str) -> list[list[float]]:
    result: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = [t[:8000] for t in texts[i : i + BATCH_SIZE]]
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    f"{EMBEDDING_BASE}/embeddings",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": EMBEDDING_MODEL, "input": batch},
                )
                if resp.status_code == 404:
                    logger.warning("embedding model %s not available (404)", EMBEDDING_MODEL)
                    return result
                resp.raise_for_status()
                data = resp.json()
                if "data" not in data:
                    logger.warning("embedding API error: %s", data)
                    return result
                for item in data["data"]:
                    result.append(item["embedding"])
        except Exception as e:
            logger.warning("embedding batch failed (%s): %s", EMBEDDING_MODEL, e)
            return result
    return result


def get_embeddings(texts: list[str], api_key: str | None = None) -> list[list[float]]:
    key = api_key or os.environ.get("OPENROUTER_KEY", "")
    if not key or not texts:
        return []
    return _batch_embeddings(texts, key)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if not na or not nb:
        return 0.0
    return dot / (na * nb)
