"""service_settings 持久化：覆盖写入、版本递增与 JSON 解码。"""

from __future__ import annotations

import asyncio

import pytest

from kb.database import Database
from kb.governance.settings_store import PostgresSettingsStore

pytestmark = pytest.mark.integration

TENANT = "t_settings_it"


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM service_settings WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM tenants WHERE tenant_id = $1", TENANT)


def test_put_load_and_version_increment():
    _cleanup()
    db = Database()
    db.execute("INSERT INTO tenants (tenant_id, display_name) VALUES ($1,'settings')", TENANT)
    try:
        store = PostgresSettingsStore(db.dsn)

        first = asyncio.run(store.put(TENANT, "rotation_days", 90, "admin"))
        second = asyncio.run(store.put(TENANT, "rotation_days", 30, "admin2"))
        asyncio.run(store.put(TENANT, "backup_system", "database_pitr", "admin"))

        assert (first, second) == (1, 2)
        assert asyncio.run(store.load(TENANT)) == {
            "backup_system": "database_pitr",
            "rotation_days": 30,
        }
        row = db.fetch(
            "SELECT updated_by, version FROM service_settings "
            "WHERE tenant_id = $1 AND setting_key = 'rotation_days'",
            TENANT,
        )[0]
        assert row["updated_by"] == "admin2"
        assert row["version"] == 2
    finally:
        _cleanup()
