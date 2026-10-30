"""最小 PostgreSQL 访问层（asyncpg）。

集成测试的仓储/服务接口是同步的，因此这里提供同步门面：每次调用用
`asyncio.run` 打开一个短连接。生产部署应换成共享连接池并保持服务异步 ——
本模块只负责让持久层先接上，不负责性能。
"""

from __future__ import annotations

import asyncio
import os

import asyncpg

DEFAULT_DSN = "postgresql://kb:dev@localhost/kb"


def dsn_from_env() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DSN)


class Database:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or dsn_from_env()

    def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        return asyncio.run(self._fetch(sql, *args))

    def execute(self, sql: str, *args: object) -> str:
        return asyncio.run(self._execute(sql, *args))

    async def _fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        conn = await asyncpg.connect(self._dsn)
        try:
            rows = await conn.fetch(sql, *args)
        finally:
            await conn.close()
        return [dict(row) for row in rows]

    async def _execute(self, sql: str, *args: object) -> str:
        conn = await asyncpg.connect(self._dsn)
        try:
            return str(await conn.execute(sql, *args))
        finally:
            await conn.close()
