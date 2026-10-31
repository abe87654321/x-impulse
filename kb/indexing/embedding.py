"""向量化接口。

生产使用 sentence-transformers / FlagEmbedding 等真实模型（ml 可选依赖）。
`HashingEmbedder` 只用于测试与本地联调：确定性、零依赖，**不代表语义质量**。
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
from collections.abc import Mapping, Sequence
from typing import Protocol

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]")


class EmbeddingConfigError(Exception):
    """嵌入后端缺失或配置非法。禁止静默回退到更弱的实现（I-10）。"""


class Embedder(Protocol):
    model_name: str
    model_version: str
    dimension: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """特征哈希：把 token 映到固定维度并 L2 归一化。仅用于开发/测试。"""

    model_name = "hashing"
    model_version = "v1"

    def __init__(self, dimension: int = 64) -> None:
        if dimension <= 0:
            raise ValueError("dimension 必须 > 0")
        self.dimension = dimension

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest, "big") % self.dimension
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector


class SentenceTransformerEmbedder:
    """真实语义嵌入适配器。依赖 `.[ml]` 可选组，导入失败即拒绝启动。"""

    model_name = "sentence-transformers"

    def __init__(self, model: str, *, device: str | None = None, batch_size: int = 32) -> None:
        if not model.strip():
            raise EmbeddingConfigError("嵌入模型名不能为空")
        if batch_size <= 0:
            raise ValueError("batch_size 必须 > 0")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - 环境缺依赖时触发
            raise EmbeddingConfigError(
                "未安装 sentence-transformers；请安装 `.[ml]` 或改用其他嵌入后端"
            ) from exc
        self._model = SentenceTransformer(model, device=device)
        dimension = self._model.get_sentence_embedding_dimension()
        if not dimension:
            raise EmbeddingConfigError(f"无法确定嵌入模型 {model} 的维度")
        self.model_version = model
        self.dimension = int(dimension)
        self._batch_size = batch_size

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = await asyncio.to_thread(
            self._model.encode,
            list(texts),
            batch_size=self._batch_size,
            normalize_embeddings=True,
        )
        return [[float(value) for value in vector] for vector in vectors]


def build_embedder_from_env(environ: Mapping[str, str] | None = None) -> Embedder:
    """按 `KB_EMBEDDER` 选择后端。未配置或未知值一律报错，不隐式降级。

    取值：`sentence-transformers`（生产）或 `hashing`（仅开发/测试，必须显式选择）。
    """
    env = os.environ if environ is None else environ
    provider = env.get("KB_EMBEDDER", "").strip().lower()
    if not provider:
        raise EmbeddingConfigError("未配置 KB_EMBEDDER，拒绝隐式选择嵌入后端")
    if provider == "hashing":
        raw_dimension = env.get("KB_EMBEDDING_DIMENSION", "64")
        try:
            dimension = int(raw_dimension)
        except ValueError as exc:
            raise EmbeddingConfigError("KB_EMBEDDING_DIMENSION 不是整数") from exc
        return HashingEmbedder(dimension=dimension)
    if provider in {"sentence-transformers", "sentence_transformers", "st"}:
        model = env.get("KB_EMBEDDING_MODEL", "").strip()
        if not model:
            raise EmbeddingConfigError("KB_EMBEDDING_MODEL 必填")
        return SentenceTransformerEmbedder(
            model,
            device=env.get("KB_EMBEDDING_DEVICE") or None,
            batch_size=_positive_int(env, "KB_EMBEDDING_BATCH_SIZE", 32),
        )
    raise EmbeddingConfigError(f"未知 KB_EMBEDDER：{provider}")


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise EmbeddingConfigError(f"{key} 不是整数") from exc
    if value <= 0:
        raise EmbeddingConfigError(f"{key} 必须 > 0")
    return value


__all__ = [
    "Embedder",
    "EmbeddingConfigError",
    "HashingEmbedder",
    "SentenceTransformerEmbedder",
    "build_embedder_from_env",
]
