"""检索结果缓存（基础版）—— FR-04-14a / A-16。

缓存的是「问题哈希 → 已召回 chunk_id 列表」，**不按用户权限分区**。
命中后仍必须做实时权限二次校验（FR-06-03）。因此缓存里绝不放
"已过滤结果"，只放召回候选。

删除链路要求按 chunk_id 定点失效 —— 这是 FR-04-14b / 删除位置 4 的硬前置。
`PostgresQueryCache` 提供跨副本共享实现；`InMemoryQueryCache` 只适合单进程开发。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from typing import Protocol

import asyncpg

from kb.database import dsn_from_env


def make_cache_key(*, tenant_id: str, query: str, metadata_signature: str) -> str:
    """问题哈希。含租户与元数据过滤指纹，**不含权限信息**。"""
    raw = f"{tenant_id}\x00{query}\x00{metadata_signature}".encode()
    return hashlib.sha256(raw).hexdigest()


class QueryCache(Protocol):
    async def get(self, tenant_id: str, key: str) -> tuple[str, ...] | None: ...

    async def put(self, tenant_id: str, key: str, chunk_ids: Sequence[str]) -> None: ...

    async def invalidate_chunk(self, tenant_id: str, chunk_id: str) -> None: ...

    async def clear(self, tenant_id: str) -> None: ...


class InMemoryQueryCache:
    """进程内实现；生产多副本必须换 `PostgresQueryCache`。"""

    def __init__(self) -> None:
        self._forward: dict[tuple[str, str], tuple[str, ...]] = {}
        self._reverse: dict[tuple[str, str], set[tuple[str, str]]] = {}

    async def get(self, tenant_id: str, key: str) -> tuple[str, ...] | None:
        return self._forward.get((tenant_id, key))

    async def put(self, tenant_id: str, key: str, chunk_ids: Sequence[str]) -> None:
        await self._drop(tenant_id, key)
        ids = tuple(chunk_ids)
        self._forward[(tenant_id, key)] = ids
        for chunk_id in set(ids):
            self._reverse.setdefault((tenant_id, chunk_id), set()).add((tenant_id, key))

    async def invalidate_chunk(self, tenant_id: str, chunk_id: str) -> None:
        for key in self._reverse.pop((tenant_id, chunk_id), set()):
            remaining = tuple(i for i in self._forward.get(key, ()) if i != chunk_id)
            if remaining:
                self._forward[key] = remaining
            else:
                self._forward.pop(key, None)

    async def clear(self, tenant_id: str) -> None:
        for key in [key for key in self._forward if key[0] == tenant_id]:
            del self._forward[key]
        for key in [key for key in self._reverse if key[0] == tenant_id]:
            del self._reverse[key]

    async def _drop(self, tenant_id: str, key: str) -> None:
        for chunk_id in self._forward.pop((tenant_id, key), ()):
            keys = self._reverse.get((tenant_id, chunk_id))
            if keys is not None:
                keys.discard((tenant_id, key))
                if not keys:
                    self._reverse.pop((tenant_id, chunk_id), None)


_GET_SQL = """
SELECT chunk_ids FROM query_cache WHERE tenant_id = $1 AND cache_key = $2
"""

_UPSERT_SQL = """
INSERT INTO query_cache (tenant_id, cache_key, chunk_ids, updated_at)
VALUES ($1, $2, $3, now())
ON CONFLICT (tenant_id, cache_key) DO UPDATE SET
    chunk_ids = EXCLUDED.chunk_ids,
    updated_at = now()
"""

_KEYS_FOR_CHUNK_SQL = """
SELECT cache_key FROM query_cache_chunks
WHERE tenant_id = $1 AND chunk_id = $2
"""

_UPDATE_ARRAY_SQL = """
UPDATE query_cache
SET chunk_ids = array_remove(chunk_ids, $3), updated_at = now()
WHERE tenant_id = $1 AND cache_key = ANY($2::text[])
"""

_DELETE_EMPTY_SQL = """
DELETE FROM query_cache
WHERE tenant_id = $1 AND cache_key = ANY($2::text[]) AND cardinality(chunk_ids) = 0
"""

_DELETE_CHUNK_INDEX_SQL = """
DELETE FROM query_cache_chunks
WHERE tenant_id = $1 AND chunk_id = $2
"""

_INSERT_CHUNK_SQL = """
INSERT INTO query_cache_chunks (tenant_id, chunk_id, cache_key)
VALUES ($1, $2, $3)
ON CONFLICT (tenant_id, chunk_id, cache_key) DO NOTHING
"""

_DELETE_KEY_CHUNKS_SQL = """
DELETE FROM query_cache_chunks WHERE tenant_id = $1 AND cache_key = $2
"""

_CLEAR_CACHE_SQL = "DELETE FROM query_cache WHERE tenant_id = $1"
_CLEAR_CHUNKS_SQL = "DELETE FROM query_cache_chunks WHERE tenant_id = $1"


class PostgresQueryCache:
    """跨副本共享缓存；删除时同步维护反向映射。"""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or dsn_from_env()

    async def get(self, tenant_id: str, key: str) -> tuple[str, ...] | None:
        conn = await asyncpg.connect(self._dsn)
        try:
            row = await conn.fetchrow(_GET_SQL, tenant_id, key)
        finally:
            await conn.close()
        if row is None:
            return None
        return tuple(str(chunk_id) for chunk_id in row["chunk_ids"])

    async def put(self, tenant_id: str, key: str, chunk_ids: Sequence[str]) -> None:
        ids = [str(chunk_id) for chunk_id in chunk_ids]
        conn = await asyncpg.connect(self._dsn)
        try:
            async with conn.transaction():
                await conn.execute(_DELETE_KEY_CHUNKS_SQL, tenant_id, key)
                await conn.execute(_UPSERT_SQL, tenant_id, key, ids)
                for chunk_id in set(ids):
                    await conn.execute(_INSERT_CHUNK_SQL, tenant_id, chunk_id, key)
        finally:
            await conn.close()

    async def invalidate_chunk(self, tenant_id: str, chunk_id: str) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            async with conn.transaction():
                rows = await conn.fetch(_KEYS_FOR_CHUNK_SQL, tenant_id, chunk_id)
                keys = [str(row["cache_key"]) for row in rows]
                if keys:
                    await conn.execute(_UPDATE_ARRAY_SQL, tenant_id, keys, chunk_id)
                    await conn.execute(_DELETE_EMPTY_SQL, tenant_id, keys)
                await conn.execute(_DELETE_CHUNK_INDEX_SQL, tenant_id, chunk_id)
        finally:
            await conn.close()

    async def clear(self, tenant_id: str) -> None:
        conn = await asyncpg.connect(self._dsn)
        try:
            async with conn.transaction():
                await conn.execute(_CLEAR_CACHE_SQL, tenant_id)
                await conn.execute(_CLEAR_CHUNKS_SQL, tenant_id)
        finally:
            await conn.close()


def build_query_cache_from_env(
    dsn: str | None = None, environ: Mapping[str, str] | None = None
) -> QueryCache:
    env = os.environ if environ is None else environ
    provider = env.get("KB_QUERY_CACHE", "").strip().lower()
    if provider in {"", "memory"}:
        return InMemoryQueryCache()
    if provider == "postgres":
        return PostgresQueryCache(dsn)
    raise ValueError(f"未知 KB_QUERY_CACHE：{provider}")


__all__ = [
    "InMemoryQueryCache",
    "PostgresQueryCache",
    "QueryCache",
    "build_query_cache_from_env",
    "make_cache_key",
]
