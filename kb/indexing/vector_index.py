"""把文档的 chunk 写入向量库，并记录 chunk_vectors 映射。

顺序：先按文档删除旧向量（重切分后旧 chunk 可能消失），再写新向量，
最后记录映射。向量库与 PostgreSQL 之间没有分布式事务，因此这一步失败时
内容已入库但向量缺失；应通过重建任务补偿，而不是把内容回滚掉。
"""

from __future__ import annotations

from kb.indexing.embedding import Embedder
from kb.indexing.models import ChunkVectorEntry, PreparedDocument
from kb.indexing.store import DocumentStore
from kb.indexing.vector_store import VectorPoint, VectorStore


class VectorIndexer:
    def __init__(
        self,
        embedder: Embedder,
        vectors: VectorStore,
        *,
        collection: str = "kb_chunks",
        vector_store_name: str = "qdrant",
    ) -> None:
        self._embedder = embedder
        self._vectors = vectors
        self._collection = collection
        self._store_name = vector_store_name

    async def index(self, prepared: PreparedDocument, store: DocumentStore) -> None:
        await self._vectors.ensure_collection(self._collection, self._embedder.dimension)
        await self._vectors.delete_document(
            self._collection, tenant_id=prepared.tenant_id, doc_id=prepared.doc_id
        )
        if not prepared.chunks:
            return

        texts = [chunk.content for chunk in prepared.chunks]
        vectors = await self._embedder.embed(texts)
        if len(vectors) != len(prepared.chunks):
            raise ValueError("embedder 返回的向量数与 chunk 数不一致")

        points = [
            VectorPoint(
                chunk_id=chunk.chunk_id,
                doc_id=prepared.doc_id,
                tenant_id=prepared.tenant_id,
                audience=prepared.audience,
                vector=tuple(vector),
            )
            for chunk, vector in zip(prepared.chunks, vectors, strict=True)
        ]
        await self._vectors.upsert(self._collection, points)
        await store.record_chunk_vectors(
            prepared.tenant_id,
            [
                ChunkVectorEntry(
                    chunk_id=point.chunk_id,
                    model_name=self._embedder.model_name,
                    model_version=self._embedder.model_version,
                    vector_store=self._store_name,
                    collection=self._collection,
                )
                for point in points
            ],
        )


__all__ = ["VectorIndexer"]
