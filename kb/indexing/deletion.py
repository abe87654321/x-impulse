"""源端删除传播 —— 文档从存储层、向量库与检索缓存中移除。

顺序：先删 PostgreSQL（权威），再删向量库，最后失效缓存。
- DB 成功、向量失败：留下孤儿向量，检索回表时查不到 chunk 会被丢弃，不越权。
- 反过来的顺序会让「已删除」内容在向量库里消失但 DB 仍可被词法腿召回。

I-5：这里做物理删除，不使用软删除列。
完整的数据主体清除（FR-12-04 十处）另见合规链路，本服务只负责源端同步删除。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kb.indexing.store import DocumentStore
from kb.indexing.vector_store import VectorStore


class CacheInvalidator(Protocol):
    def invalidate_chunk(self, chunk_id: str) -> None: ...


@dataclass(frozen=True)
class DeletionResult:
    doc_id: str
    chunk_ids: tuple[str, ...]


class DocumentDeletionService:
    def __init__(
        self,
        store: DocumentStore,
        vectors: VectorStore,
        cache: CacheInvalidator,
        *,
        collection: str = "kb_chunks",
    ) -> None:
        self._store = store
        self._vectors = vectors
        self._cache = cache
        self._collection = collection

    async def delete_document(self, tenant_id: str, doc_id: str) -> DeletionResult:
        chunk_ids = await self._store.list_chunk_ids(tenant_id, doc_id)
        await self._store.delete_document(tenant_id, doc_id)
        await self._vectors.delete_document(self._collection, tenant_id=tenant_id, doc_id=doc_id)
        for chunk_id in chunk_ids:
            self._cache.invalidate_chunk(chunk_id)
        return DeletionResult(doc_id=doc_id, chunk_ids=tuple(chunk_ids))


__all__ = ["CacheInvalidator", "DeletionResult", "DocumentDeletionService"]
