"""主体反向索引的写入与查询 —— FR-12-19。

索引键是 HMAC，不存明文。写入发生在索引流水线；查询供 T-1 删除使用。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import asyncpg

from kb.compliance.pii import PII_NORM_VERSION, detect_pii, subject_key
from kb.database import dsn_from_env

_DELETE_CHUNK_SQL = """
DELETE FROM subject_index
WHERE tenant_id = $1 AND location_type = 'chunk' AND location_id = ANY($2::text[])
"""

_INSERT_SQL = """
INSERT INTO subject_index
    (tenant_id, subject_key, subject_type, location_type, location_id,
     confidence, norm_version)
VALUES ($1, $2, $3, 'chunk', $4, $5, $6)
ON CONFLICT (tenant_id, subject_key, location_type, location_id) DO UPDATE SET
    confidence = EXCLUDED.confidence,
    norm_version = EXCLUDED.norm_version,
    last_seen_at = now()
"""

_LOCATIONS_SQL = """
SELECT DISTINCT location_type, location_id
FROM subject_index
WHERE tenant_id = $1 AND subject_key = ANY($2::text[])
"""


class SubjectIndexer(Protocol):
    async def index_chunk(self, tenant_id: str, chunk_id: str, text: str) -> int: ...


class PostgresSubjectIndexer:
    def __init__(self, pepper: str, dsn: str | None = None) -> None:
        if not pepper:
            raise ValueError("pepper 不能为空")
        self._pepper = pepper
        self._dsn = dsn or dsn_from_env()

    async def index_chunk(self, tenant_id: str, chunk_id: str, text: str) -> int:
        detected = detect_pii(text)
        conn = await asyncpg.connect(self._dsn)
        try:
            async with conn.transaction():
                await conn.execute(_DELETE_CHUNK_SQL, tenant_id, [chunk_id])
                for item in detected:
                    await conn.execute(
                        _INSERT_SQL,
                        tenant_id,
                        subject_key(item.normalized, self._pepper),
                        item.subject_type,
                        chunk_id,
                        item.confidence,
                        PII_NORM_VERSION,
                    )
        finally:
            await conn.close()
        return len(detected)

    async def delete_chunk_locations(self, tenant_id: str, chunk_ids: Sequence[str]) -> None:
        if not chunk_ids:
            return
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(_DELETE_CHUNK_SQL, tenant_id, list(chunk_ids))
        finally:
            await conn.close()

    async def locations(self, tenant_id: str, subject_keys: Sequence[str]) -> list[tuple[str, str]]:
        if not subject_keys:
            return []
        conn = await asyncpg.connect(self._dsn)
        try:
            rows = await conn.fetch(_LOCATIONS_SQL, tenant_id, list(subject_keys))
        finally:
            await conn.close()
        return [(str(row["location_type"]), str(row["location_id"])) for row in rows]


__all__ = ["PostgresSubjectIndexer", "SubjectIndexer"]
