"""审计 —— 只追加哈希链与 PII 字段加密。"""

from kb.audit.protection import (
    AuditPayloadError,
    FieldKeyManager,
    contains_pii,
    open_payload,
    protect_payload,
)
from kb.audit.writer import (
    AuditEvent,
    AuditStore,
    PostgresAuditStore,
    build_audit_store_from_env,
)

__all__ = [
    "AuditEvent",
    "AuditPayloadError",
    "AuditStore",
    "FieldKeyManager",
    "PostgresAuditStore",
    "build_audit_store_from_env",
    "contains_pii",
    "open_payload",
    "protect_payload",
]
