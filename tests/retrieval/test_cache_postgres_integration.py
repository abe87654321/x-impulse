"""共享缓存：跨副本读写与按 chunk 定点失效。"""

from __future__ import annotations

import asyncio

import pytest

from kb.database import Database
from kb.retrieval.cache import PostgresQueryCache

pytestmark = pytest.mark.integration

TENANT = "t_qcache_it"


def _cleanup() -> None:
    db = Database()
    db.execute("DELETE FROM query_cache_chunks WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM query_cache WHERE tenant_id = $1", TENANT)
    db.execute("DELETE FROM tenants WHERE tenant_id = $1", TENANT)


def test_postgres_cache_put_get_invalidate_and_clear():
    _cleanup()
    db = Database()
    db.execute("INSERT INTO tenants (tenant_id, display_name) VALUES ($1,'cache')", TENANT)
    try:
        first = PostgresQueryCache()
        second = PostgresQueryCache()

        asyncio.run(first.put(TENANT, "k1", ["c1", "c2", "c3"]))
        assert asyncio.run(second.get(TENANT, "k1")) == ("c1", "c2", "c3")

        asyncio.run(second.invalidate_chunk(TENANT, "c2"))
        assert asyncio.run(first.get(TENANT, "k1")) == ("c1", "c3")
        assert (
            db.fetch(
                "SELECT 1 FROM query_cache_chunks WHERE tenant_id = $1 AND chunk_id = 'c2'",
                TENANT,
            )
            == []
        )

        asyncio.run(first.invalidate_chunk(TENANT, "c1"))
        asyncio.run(first.invalidate_chunk(TENANT, "c3"))
        assert asyncio.run(second.get(TENANT, "k1")) is None

        asyncio.run(first.put(TENANT, "k2", ["c9"]))
        asyncio.run(first.clear(TENANT))
        assert asyncio.run(second.get(TENANT, "k2")) is None
    finally:
        _cleanup()
