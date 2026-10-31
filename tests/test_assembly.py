"""组合根：环境配置到服务对象的装配。"""

from __future__ import annotations

import pytest

from kb.api.chat_service import ChatService
from kb.assembly import (
    build_app_from_env,
    build_chat_service_from_env,
    build_document_erasure_service_from_env,
    build_key_manager_from_env,
    build_subject_indexer_from_env,
    build_vector_store_from_env,
)
from kb.compliance.erasure import DocumentErasureService
from kb.compliance.kms import KmsKeyManager
from kb.compliance.subject_index import PostgresSubjectIndexer
from kb.indexing.vector_store import InMemoryVectorStore, QdrantVectorStore

DSN = "postgresql://kb:dev@localhost/kb"


class _Answers:
    async def answer(self, ctx, question):  # noqa: ANN001, ANN201
        raise NotImplementedError


def test_subject_indexer_requires_pepper():
    assert build_subject_indexer_from_env({}) is None
    indexer = build_subject_indexer_from_env({"KB_SUPPRESSION_PEPPER": "p"})
    assert isinstance(indexer, PostgresSubjectIndexer)


def test_key_manager_only_when_kms_provider_is_explicit():
    assert build_key_manager_from_env(DSN, {}) is None
    manager = build_key_manager_from_env(
        DSN,
        {
            "KB_KMS_PROVIDER": "vault",
            "KB_VAULT_ADDR": "http://vault:8200",
            "KB_VAULT_TOKEN": "s.test",
        },
    )
    assert isinstance(manager, KmsKeyManager)


def test_vector_store_selection():
    assert isinstance(build_vector_store_from_env({}), InMemoryVectorStore)
    assert isinstance(
        build_vector_store_from_env(
            {"KB_VECTOR_STORE": "qdrant", "KB_QDRANT_URL": "http://qdrant:6333"}
        ),
        QdrantVectorStore,
    )
    with pytest.raises(ValueError):
        build_vector_store_from_env({"KB_VECTOR_STORE": "unknown"})


def test_chat_service_and_app_are_wired():
    service = build_chat_service_from_env(
        _Answers(),
        dsn=DSN,
        environ={"KB_SUPPRESSION_PEPPER": "p"},
    )
    app = build_app_from_env(_Answers(), dsn=DSN, environ={})

    assert isinstance(service, ChatService)
    assert service._subject_indexer is not None
    assert callable(app)


def test_document_erasure_service_requires_pepper():
    with pytest.raises(ValueError):
        build_document_erasure_service_from_env(dsn=DSN, environ={})
    service = build_document_erasure_service_from_env(
        dsn=DSN,
        environ={
            "KB_SUPPRESSION_PEPPER": "p",
            "KB_VECTOR_STORE": "memory",
            "KB_RAW_OBJECT_STORE": "none",
        },
    )
    assert isinstance(service, DocumentErasureService)
