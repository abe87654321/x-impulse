"""组合根：把环境配置装配成可运行的服务对象。

所有工厂都显式接收 `environ`，不在模块导入时读取环境；未配置的能力
保持显式缺省（例如无 KMS 就不启用 KMS 路径），不静默降级安全边界。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from kb.api.app import ASGIApp, create_app
from kb.api.chat_service import ChatService
from kb.audit.writer import build_audit_store_from_env
from kb.compliance.backup import BackupRotation, build_backup_rotation_from_env
from kb.compliance.crypto import KeyManager
from kb.compliance.erasure import DocumentErasureService, PostgresErasureRepository
from kb.compliance.kms import KmsKeyManager, PostgresSubjectKeyStore
from kb.compliance.kms_vault import build_kms_from_env
from kb.compliance.pii import subject_key
from kb.compliance.restriction import RestrictionService
from kb.compliance.subject_index import PostgresSubjectIndexer
from kb.database import dsn_from_env
from kb.generation.answer_service import AnswerService
from kb.indexing.raw_store import RawObjectStore, build_raw_store_from_env
from kb.indexing.store import PostgresDocumentStore
from kb.indexing.vector_store import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorStore,
)
from kb.retrieval.cache import InMemoryQueryCache


def build_subject_indexer_from_env(
    environ: Mapping[str, str] | None = None,
) -> PostgresSubjectIndexer | None:
    """主体索引需要 pepper；未配置即返回 None（不写索引，T-1 召回下降）。"""
    env = os.environ if environ is None else environ
    pepper = env.get("KB_SUPPRESSION_PEPPER") or env.get("KB_AUDIT_PII_PEPPER")
    if not pepper:
        return None
    return PostgresSubjectIndexer(pepper)


def build_key_manager_from_env(
    dsn: str | None = None, environ: Mapping[str, str] | None = None
) -> KeyManager | None:
    """配置了 KB_KMS_PROVIDER 才启用 KMS；否则返回 None（不假装已加密）。"""
    env = os.environ if environ is None else environ
    if not env.get("KB_KMS_PROVIDER", "").strip():
        return None
    return KmsKeyManager(build_kms_from_env(env), PostgresSubjectKeyStore(dsn))


def build_vector_store_from_env(
    environ: Mapping[str, str] | None = None,
) -> VectorStore:
    env = os.environ if environ is None else environ
    provider = env.get("KB_VECTOR_STORE", "").strip().lower()
    if provider in {"", "memory"}:
        return InMemoryVectorStore()
    if provider == "qdrant":
        return QdrantVectorStore(env.get("KB_QDRANT_URL") or "http://localhost:6333")
    raise ValueError(f"未知 KB_VECTOR_STORE：{provider}")


def build_chat_service_from_env(
    answers: AnswerService,
    *,
    dsn: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ChatService:
    env = os.environ if environ is None else environ
    return ChatService(
        answers,
        audit=build_audit_store_from_env(dsn, env),
        dsn=dsn,
        subject_indexer=build_subject_indexer_from_env(env),
    )


def build_app_from_env(
    answers: AnswerService,
    *,
    dsn: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ASGIApp:
    return create_app(build_chat_service_from_env(answers, dsn=dsn, environ=environ))


def build_document_erasure_service_from_env(
    *,
    dsn: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> DocumentErasureService:
    env = os.environ if environ is None else environ
    pepper = env.get("KB_SUPPRESSION_PEPPER")
    if not pepper:
        raise ValueError("删除链路需要 KB_SUPPRESSION_PEPPER 计算抑制键")
    resolved_dsn = dsn or dsn_from_env()
    return DocumentErasureService(
        PostgresErasureRepository(resolved_dsn),
        PostgresDocumentStore(resolved_dsn),
        build_vector_store_from_env(env),
        InMemoryQueryCache(),
        scope_key_hasher=lambda value: subject_key(value, pepper),
        collection=env.get("KB_COLLECTION") or "kb_chunks",
        restrictions=RestrictionService(resolved_dsn),
        backup=build_backup_rotation_from_env(env),
        raw_store=build_raw_store_from_env(env),
    )


def build_raw_store(environ: Mapping[str, str] | None = None) -> RawObjectStore | None:
    return build_raw_store_from_env(environ)


def build_backup_rotation(environ: Mapping[str, str] | None = None) -> BackupRotation:
    return build_backup_rotation_from_env(environ)


__all__ = [
    "build_app_from_env",
    "build_backup_rotation",
    "build_chat_service_from_env",
    "build_document_erasure_service_from_env",
    "build_key_manager_from_env",
    "build_raw_store",
    "build_subject_indexer_from_env",
    "build_vector_store_from_env",
]
