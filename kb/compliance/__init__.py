"""compliance —— 数据主体权利与贯穿式删除。"""

from kb.compliance.crypto import (
    KeyDestroyedError,
    KeyManager,
    SoftwareKeyManager,
    kek_from_passphrase,
)
from kb.compliance.erasure import (
    DocumentErasureService,
    ErasureRepository,
    ErasureResult,
    LocationOutcome,
    PostgresErasureRepository,
    request_id_for,
)
from kb.compliance.pii import DetectedPII, detect_pii, normalize_pii, subject_key
from kb.compliance.subject_erasure import SubjectErasureService
from kb.compliance.subject_index import PostgresSubjectIndexer

__all__ = [
    "KeyDestroyedError",
    "KeyManager",
    "SoftwareKeyManager",
    "kek_from_passphrase",
    "DetectedPII",
    "PostgresSubjectIndexer",
    "SubjectErasureService",
    "detect_pii",
    "normalize_pii",
    "subject_key",
    "DocumentErasureService",
    "ErasureRepository",
    "ErasureResult",
    "LocationOutcome",
    "PostgresErasureRepository",
    "request_id_for",
]
