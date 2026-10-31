"""端到端演示：本地文件夹 → 同步/索引 → 混合检索 → 生成 + 引用。

用法：
    .venv/bin/python -m kb.demo ./docs "报销时限是多久？"

默认用抽取式生成器（无幻觉、无模型）；设置 KB_LLM_BASE_URL 后改用
OpenAI 兼容接口（KB_LLM_MODEL / KB_LLM_API_KEY 可选）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kb.assembly import build_raw_store, build_subject_indexer_from_env
from kb.database import Database
from kb.generation.answer_service import AnswerService, HybridRetrievalAdapter
from kb.generation.citations import Answer, Generator
from kb.generation.extractive import ExtractiveGenerator
from kb.generation.llm import (
    LLMConfig,
    OpenAICompatibleGenerator,
    build_generator_from_env,
)
from kb.identity.permissions import FULL_SCOPE, DelegationContext, Principal
from kb.indexing.deletion import DocumentDeletionService
from kb.indexing.embedding import Embedder, HashingEmbedder, build_embedder_from_env
from kb.indexing.pipeline import IndexingPipeline
from kb.indexing.store import PostgresDocumentStore
from kb.indexing.suppression import PostgresSuppressionChecker
from kb.indexing.sync import PostgresSyncRunRepository, SourceSyncService, SyncResult
from kb.indexing.vector_index import VectorIndexer
from kb.indexing.vector_store import InMemoryVectorStore
from kb.ingestion.filesystem_source import FilesystemFolderSource
from kb.ingestion.local_folder import LocalFolderConnector
from kb.parsing.chunking import ChunkingConfig
from kb.retrieval.cache import InMemoryQueryCache
from kb.retrieval.pg_adapters import HybridRetrievalService


def _seed_source(db: Database, tenant_id: str, source_id: str, display_name: str) -> None:
    db.execute(
        """
        INSERT INTO sources (source_id, tenant_id, source_type, display_name)
        VALUES ($1, $2, 'upload', $3)
        ON CONFLICT (source_id) DO UPDATE SET display_name = EXCLUDED.display_name
        """,
        source_id,
        tenant_id,
        display_name,
    )


def _context(principal_id: str) -> DelegationContext:
    now = datetime.now(UTC)
    return DelegationContext(
        principal=Principal("user", principal_id),
        actor=None,
        scope=FULL_SCOPE,
        assurance_level="L2",
        expires_at=now + timedelta(hours=1),
        evidence={"channel": "demo"},
    )


def _suppression_checker() -> PostgresSuppressionChecker | None:
    pepper = os.environ.get("KB_SUPPRESSION_PEPPER")
    if not pepper:
        return None
    from kb.compliance.pii import subject_key

    return PostgresSuppressionChecker(lambda value: subject_key(value, pepper))


def _generator() -> Generator:
    if os.environ.get("KB_GENERATOR"):
        return build_generator_from_env()
    base_url = os.environ.get("KB_LLM_BASE_URL")
    if base_url:
        return OpenAICompatibleGenerator(
            LLMConfig(
                base_url=base_url,
                model=os.environ.get("KB_LLM_MODEL", "local"),
                api_key=os.environ.get("KB_LLM_API_KEY", ""),
            )
        )
    return ExtractiveGenerator()


def _embedder() -> Embedder:
    if os.environ.get("KB_EMBEDDER"):
        return build_embedder_from_env()
    return HashingEmbedder(dimension=64)


async def run_demo(
    folder: str | Path,
    query: str,
    *,
    tenant_id: str = "default",
    principal_id: str = "alice",
    source_id: str = "src_demo",
    collection: str = "demo_chunks",
) -> tuple[Answer, SyncResult]:
    db = Database()
    await asyncio.to_thread(_seed_source, db, tenant_id, source_id, f"演示源：{folder}")

    source = FilesystemFolderSource(folder, source_id=source_id)
    connector = LocalFolderConnector(source)
    store = PostgresDocumentStore()
    vectors = InMemoryVectorStore()
    embedder = _embedder()

    pipeline = IndexingPipeline(
        connector,
        store,
        tenant_id=tenant_id,
        source_id=source_id,
        chunking=ChunkingConfig(),
        suppression=_suppression_checker(),
        vector_indexer=VectorIndexer(embedder, vectors, collection=collection),
        subject_indexer=build_subject_indexer_from_env(),
        raw_store=build_raw_store(),
    )
    deletion = DocumentDeletionService(store, vectors, InMemoryQueryCache(), collection=collection)
    sync = SourceSyncService(
        connector,
        pipeline,
        deletion,
        store,
        PostgresSyncRunRepository(),
        tenant_id=tenant_id,
        source_id=source_id,
    )
    sync_result = await sync.run_full()

    retrieval = await asyncio.to_thread(
        HybridRetrievalService,
        db,
        vectors,
        embedder,
        tenant_id=tenant_id,
        collection=collection,
    )
    answer_service = AnswerService(HybridRetrievalAdapter(retrieval), _generator())
    answer = await answer_service.answer(_context(principal_id), query)
    return answer, sync_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="知识库端到端演示")
    parser.add_argument("folder", help="要索引的本地目录（txt/md）")
    parser.add_argument("query", help="问题")
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--principal", default="alice")
    parser.add_argument("--source-id", default="src_demo")
    parser.add_argument("--collection", default="demo_chunks")
    args = parser.parse_args(argv)

    answer, sync_result = asyncio.run(
        run_demo(
            args.folder,
            args.query,
            tenant_id=args.tenant,
            principal_id=args.principal,
            source_id=args.source_id,
            collection=args.collection,
        )
    )

    stats = sync_result.stats
    print(
        f"同步：扫描 {stats.scanned}，新增/更新 {stats.added}，"
        f"跳过 {stats.suppressed}，失败 {stats.failed}，状态 {sync_result.status}"
    )
    if answer.refused:
        print(f"拒答（{answer.refusal_reason}）：{answer.text}")
        return 0

    print("\n答案：")
    print(answer.text)
    print("\n引用：")
    for citation in answer.citations:
        print(
            f"  sentence={citation.sentence_index} chunk={citation.chunk_id} "
            f"doc={citation.doc_id} hash={citation.chunk_content_hash[:12]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
