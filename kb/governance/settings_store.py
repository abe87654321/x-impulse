"""管理面设置项的持久化。

只存管理员显式覆盖的值；推荐默认值由 DAG 求值器在读取时补齐，不落库。
每次写入递增 `version`，并通过 `updated_by` 留下操作者。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

import asyncpg

from kb.database import dsn_from_env


class SettingsStore(Protocol):
    async def load(self, tenant_id: str) -> Mapping[str, object]: ...

    async def put(self, tenant_id: str, key: str, value: object, updated_by: str) -> int: ...


class InMemorySettingsStore:
    def __init__(self) -> None:
        self._values: dict[str, dict[str, object]] = {}
        self._versions: dict[tuple[str, str], int] = {}

    async def load(self, tenant_id: str) -> Mapping[str, object]:
        return dict(self._values.get(tenant_id, {}))

    async def put(self, tenant_id: str, key: str, value: object, updated_by: str) -> int:
        del updated_by
        self._values.setdefault(tenant_id, {})[key] = value
        version = self._versions.get((tenant_id, key), 0) + 1
        self._versions[(tenant_id, key)] = version
        return version


class PostgresSettingsStore:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or dsn_from_env()

    async def load(self, tenant_id: str) -> Mapping[str, object]:
        conn = await asyncpg.connect(self._dsn)
        try:
            rows = await conn.fetch(_LOAD_SQL, tenant_id)
        finally:
            await conn.close()
        return {str(row["setting_key"]): _decode(row["value"]) for row in rows}

    async def put(self, tenant_id: str, key: str, value: object, updated_by: str) -> int:
        conn = await asyncpg.connect(self._dsn)
        try:
            version = await conn.fetchval(
                _PUT_SQL,
                tenant_id,
                key,
                json.dumps(value, ensure_ascii=False),
                updated_by,
            )
        finally:
            await conn.close()
        return int(version)


_LOAD_SQL = """
SELECT setting_key, value FROM service_settings
WHERE tenant_id = $1 ORDER BY setting_key
"""

_PUT_SQL = """
INSERT INTO service_settings (tenant_id, setting_key, value, updated_by)
VALUES ($1, $2, $3::jsonb, $4)
ON CONFLICT (tenant_id, setting_key) DO UPDATE
SET value = EXCLUDED.value,
    version = service_settings.version + 1,
    updated_by = EXCLUDED.updated_by,
    updated_at = now()
RETURNING version
"""


def _decode(value: object) -> object:
    if isinstance(value, str):
        return json.loads(value)
    return value


__all__ = ["InMemorySettingsStore", "PostgresSettingsStore", "SettingsStore"]
