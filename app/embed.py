"""Embedding 提供方抽象与向量存取（设计 4.3.1，M2 落地）。

- Provider：OpenAI 兼容 /embeddings 端点（vLLM 本地模型/LiteLLM 网关），
  未配置 GNR_EMBED_BASE_URL 时聚类整体跳过；测试注入 CallableEmbedder 离线运行。
- 存储：article_embeddings.embedding 为 float32 序列化 blob（array('f')），
  不引 numpy——单轮窗口内文章量级为百级，纯 Python cosine 足够。
"""
from __future__ import annotations

import math
from array import array
from typing import Protocol

import httpx

from .db import WRITE_LOCK, connect


class Embedder(Protocol):
    """embedding 提供方协议：texts → 等长向量列表。"""
    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbedder:
    """OpenAI 兼容 /embeddings（vLLM 的 bge-m3 / LiteLLM 代理的商用模型等）。"""

    def __init__(self, base_url: str, model: str, *, api_key_env: str | None,
                 timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import os
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.api_key_env or "", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/embeddings",
                                     json={"model": self.model,
                                           "input": texts},
                                     headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return [item["embedding"] for item in data["data"]]


class CallableEmbedder:
    """测试用：可调用对象产生确定性向量（离线）。"""

    def __init__(self, fn, model: str = "callable-embed"):
        self._fn = fn
        self.model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._fn(t) for t in texts]


def embedder_from_settings(settings) -> Embedder | None:
    base_url = getattr(settings, "embed_base_url", None)
    if not base_url:
        return None
    return OpenAICompatibleEmbedder(
        base_url, getattr(settings, "embed_model", None) or "bge-m3",
        api_key_env=getattr(settings, "embed_api_key_env", None),
        timeout=getattr(settings, "llm_timeout", 60.0))


# ---- 向量序列化与相似度 ----


def vec_to_blob(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def blob_to_vec(blob: bytes) -> list[float]:
    a = array("f")
    a.frombytes(blob)
    return list(a)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def save_embedding(db_path: str, article_id: int, vec: list[float],
                   model: str, now: str) -> None:
    with WRITE_LOCK, connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO article_embeddings"
            " (article_id, embedding, model_version, created_at)"
            " VALUES (?,?,?,?)",
            (article_id, vec_to_blob(vec), model, now),
        )


def article_text(row: dict) -> str:
    """聚类输入文本：标题 + 摘要（不用全文——成本与合规双重考虑）。"""
    return f"{row.get('title') or ''}\n{row.get('summary') or ''}".strip()
