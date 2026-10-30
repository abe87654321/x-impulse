"""向量化接口。

生产使用 sentence-transformers / FlagEmbedding 等真实模型（ml 可选依赖）。
`HashingEmbedder` 只用于测试与本地联调：确定性、零依赖，**不代表语义质量**。
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]")


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


__all__ = ["Embedder", "HashingEmbedder"]
