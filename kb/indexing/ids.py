"""可读、稳定的主键生成。

- 文档 ID 由 (source_id, external_id) 决定：源系统重命名不影响（R-8）。
- chunk ID 由 (doc_id, seq) 决定：同一位置重切分时 ID 不变、version 自增，
  历史 citation 仍能指回这个位置。
- ACL ID 由条目内容决定，重复同步幂等。
"""

from __future__ import annotations

import hashlib

from kb.ingestion.contract import AclEntry


def _digest(*parts: str) -> str:
    raw = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def document_id(source_id: str, external_id: str) -> str:
    return f"doc_{_digest(source_id, external_id)}"


def chunk_id(doc_id: str, seq: int) -> str:
    return f"chk_{_digest(doc_id, str(seq))}"


def acl_id(tenant_id: str, object_id: str, entry: AclEntry) -> str:
    return "acl_" + _digest(
        tenant_id,
        object_id,
        entry.principal_type,
        entry.principal_id,
        entry.effect,
        entry.permission,
        entry.scope_level,
    )
