"""向量库接口与 Qdrant REST 实现。

召回阶段的权限过滤必须下推到这里：`search` 的过滤条件包含租户、允许的
doc_id 与受众白名单。禁止先取回再在应用层遮蔽（I-3）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

_DEFAULT_TIMEOUT = 10.0


class VectorStoreError(Exception):
    """向量库调用失败。"""


@dataclass(frozen=True)
class VectorPoint:
    chunk_id: str
    doc_id: str
    tenant_id: str
    audience: str
    vector: tuple[float, ...]


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    doc_id: str
    score: float


class VectorStore(Protocol):
    async def ensure_collection(self, collection: str, dimension: int) -> None: ...

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None: ...

    async def delete_document(self, collection: str, *, tenant_id: str, doc_id: str) -> None: ...

    async def search(
        self,
        collection: str,
        vector: Sequence[float],
        *,
        tenant_id: str,
        allowed_doc_ids: frozenset[str],
        audience_allowlist: frozenset[str],
        limit: int,
    ) -> list[VectorHit]: ...


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, VectorPoint]] = {}
        self.dimensions: dict[str, int] = {}

    async def ensure_collection(self, collection: str, dimension: int) -> None:
        self._collections.setdefault(collection, {})
        self.dimensions[collection] = dimension

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None:
        bucket = self._collections.setdefault(collection, {})
        for point in points:
            bucket[point.chunk_id] = point

    async def delete_document(self, collection: str, *, tenant_id: str, doc_id: str) -> None:
        bucket = self._collections.get(collection, {})
        for chunk_id in [
            chunk_id
            for chunk_id, point in bucket.items()
            if point.tenant_id == tenant_id and point.doc_id == doc_id
        ]:
            bucket.pop(chunk_id, None)

    async def search(
        self,
        collection: str,
        vector: Sequence[float],
        *,
        tenant_id: str,
        allowed_doc_ids: frozenset[str],
        audience_allowlist: frozenset[str],
        limit: int,
    ) -> list[VectorHit]:
        if not allowed_doc_ids:
            return []
        hits: list[VectorHit] = []
        for point in self._collections.get(collection, {}).values():
            if point.tenant_id != tenant_id:
                continue
            if point.doc_id not in allowed_doc_ids:
                continue
            if point.audience not in audience_allowlist:
                continue
            hits.append(
                VectorHit(
                    chunk_id=point.chunk_id,
                    doc_id=point.doc_id,
                    score=_cosine(vector, point.vector),
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]


class QdrantTransport(Protocol):
    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, object]: ...


class QdrantVectorStore:
    def __init__(
        self,
        base_url: str = "http://localhost:6333",
        *,
        timeout_sec: float = _DEFAULT_TIMEOUT,
        transport: QdrantTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_sec
        self._transport = transport or _urllib_transport

    async def ensure_collection(self, collection: str, dimension: int) -> None:
        status, _ = await self._call("GET", f"/collections/{collection}", None)
        if status == 200:
            return
        if status != 404:
            raise VectorStoreError(f"读取集合失败：HTTP {status}")
        body = {"vectors": {"size": dimension, "distance": "Cosine"}}
        status, _ = await self._call("PUT", f"/collections/{collection}", body)
        if status not in (200, 201):
            raise VectorStoreError(f"创建集合失败：HTTP {status}")

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None:
        if not points:
            return
        payload = {
            "points": [
                {
                    "id": _point_id(point.chunk_id),
                    "vector": list(point.vector),
                    "payload": {
                        "chunk_id": point.chunk_id,
                        "doc_id": point.doc_id,
                        "tenant_id": point.tenant_id,
                        "audience": point.audience,
                    },
                }
                for point in points
            ]
        }
        status, _ = await self._call("PUT", f"/collections/{collection}/points?wait=true", payload)
        if status not in (200, 201):
            raise VectorStoreError(f"写入向量失败：HTTP {status}")

    async def delete_document(self, collection: str, *, tenant_id: str, doc_id: str) -> None:
        payload = {
            "filter": {
                "must": [
                    {"key": "tenant_id", "match": {"value": tenant_id}},
                    {"key": "doc_id", "match": {"value": doc_id}},
                ]
            }
        }
        status, _ = await self._call(
            "POST", f"/collections/{collection}/points/delete?wait=true", payload
        )
        if status not in (200, 201):
            raise VectorStoreError(f"删除向量失败：HTTP {status}")

    async def search(
        self,
        collection: str,
        vector: Sequence[float],
        *,
        tenant_id: str,
        allowed_doc_ids: frozenset[str],
        audience_allowlist: frozenset[str],
        limit: int,
    ) -> list[VectorHit]:
        if not allowed_doc_ids:
            return []
        payload = {
            "vector": list(vector),
            "limit": limit,
            "with_payload": True,
            "filter": {
                "must": [
                    {"key": "tenant_id", "match": {"value": tenant_id}},
                    {"key": "doc_id", "match": {"any": sorted(allowed_doc_ids)}},
                    {"key": "audience", "match": {"any": sorted(audience_allowlist)}},
                ]
            },
        }
        status, body = await self._call("POST", f"/collections/{collection}/points/search", payload)
        if status != 200:
            raise VectorStoreError(f"向量检索失败：HTTP {status}")
        return _parse_hits(body)

    async def _call(
        self, method: str, path: str, body: Mapping[str, object] | None
    ) -> tuple[int, object]:
        import asyncio

        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        try:
            return await asyncio.to_thread(
                self._transport, method, self._base + path, headers, raw, self._timeout
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VectorStoreError(f"向量库不可达：{exc}") from exc


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot: float = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm: float = sum(a * a for a in left) ** 0.5
    right_norm: float = sum(b * b for b in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def _parse_hits(body: object) -> list[VectorHit]:
    if not isinstance(body, dict):
        return []
    result = body.get("result")
    if not isinstance(result, list):
        return []
    hits: list[VectorHit] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        chunk_id = payload.get("chunk_id")
        doc_id = payload.get("doc_id")
        score = item.get("score")
        if (
            isinstance(chunk_id, str)
            and isinstance(doc_id, str)
            and isinstance(score, (int, float))
        ):
            hits.append(VectorHit(chunk_id=chunk_id, doc_id=doc_id, score=float(score)))
    return hits


def _urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, object]:
    request = urllib.request.Request(url, data=body or None, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read())


def _decode(raw: bytes) -> object:
    if not raw:
        return {}
    try:
        parsed: object = json.loads(raw)
        return parsed
    except json.JSONDecodeError:
        return {}


__all__ = [
    "InMemoryVectorStore",
    "QdrantTransport",
    "QdrantVectorStore",
    "VectorHit",
    "VectorPoint",
    "VectorStore",
    "VectorStoreError",
]
