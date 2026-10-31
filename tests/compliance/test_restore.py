"""恢复演练闸门：抑制清单与 KMS 密钥销毁状态必须在服务放行前比对。"""

from __future__ import annotations

import pytest

from kb.compliance.restore import (
    ChunkSnapshot,
    DocumentSnapshot,
    RestoreFinding,
    RestoreGuard,
    RestoreViolation,
)
from kb.indexing.suppression import InMemorySuppressionChecker, document_scope_key


class _Catalog:
    def __init__(
        self,
        documents: list[DocumentSnapshot],
        chunks: list[ChunkSnapshot],
    ) -> None:
        self._documents = documents
        self._chunks = chunks

    async def documents(self, tenant_id: str):
        return self._documents

    async def chunks(self, tenant_id: str):
        return self._chunks


class _KeyAuthority:
    def __init__(self, destroyed: set[str]) -> None:
        self._destroyed = destroyed

    async def is_destroyed(self, tenant_id: str, subject_key: str) -> bool:
        return subject_key in self._destroyed


def _catalog(
    *,
    document_suppressed: bool = False,
    subject_suppressed: bool = False,
) -> tuple[_Catalog, InMemorySuppressionChecker]:
    documents = [DocumentSnapshot("doc_1", "src_1", "ext_1")]
    chunks = [ChunkSnapshot("chk_1", "doc_1", "联系 alice@example.com")]
    suppressed: set[tuple[str, str, str]] = set()
    if document_suppressed:
        suppressed.add(("t1", "document", document_scope_key("src_1", "ext_1")))
    if subject_suppressed:
        suppressed.add(("t1", "subject", "alice@example.com"))
    return _Catalog(documents, chunks), InMemorySuppressionChecker(suppressed)


async def test_clean_restore_passes():
    catalog, suppression = _catalog()
    guard = RestoreGuard(catalog, suppression, tenant_id="t1")

    report = await guard.verify()

    assert report.passed is True
    assert report.documents_scanned == 1
    assert report.chunks_scanned == 1
    assert report.findings == ()


async def test_suppressed_document_blocks_restore():
    catalog, suppression = _catalog(document_suppressed=True)
    guard = RestoreGuard(catalog, suppression, tenant_id="t1")

    report = await guard.scan()

    assert report.passed is False
    assert (
        RestoreFinding("document", "doc_1", "文档抑制键命中，不得重新入库或索引") in report.findings
    )


async def test_suppressed_subject_blocks_restore():
    catalog, suppression = _catalog(subject_suppressed=True)
    guard = RestoreGuard(catalog, suppression, tenant_id="t1")

    report = await guard.scan()

    assert any(finding.kind == "subject" for finding in report.findings)


async def test_destroyed_kms_key_blocks_restore_even_if_suppression_missing():
    catalog, suppression = _catalog()
    guard = RestoreGuard(
        catalog,
        suppression,
        tenant_id="t1",
        subject_hasher=lambda value: f"hashed:{value}",
        key_authority=_KeyAuthority({"hashed:alice@example.com"}),
    )

    report = await guard.scan()

    assert any(finding.kind == "destroyed_key" for finding in report.findings)


async def test_verify_raises_on_any_finding():
    catalog, suppression = _catalog(document_suppressed=True)
    guard = RestoreGuard(catalog, suppression, tenant_id="t1")

    with pytest.raises(RestoreViolation) as excinfo:
        await guard.verify()

    assert excinfo.value.report.passed is False


def test_key_authority_requires_subject_hasher():
    catalog, suppression = _catalog()

    with pytest.raises(ValueError):
        RestoreGuard(
            catalog,
            suppression,
            tenant_id="t1",
            key_authority=_KeyAuthority(set()),
        )
