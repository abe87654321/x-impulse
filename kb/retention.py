"""T-3 保留期到期清除入口 —— 由外部调度器调用，不在进程内 sleep 轮询。

用法：
    .venv/bin/python -m kb.retention --dry-run
    .venv/bin/python -m kb.retention

共享缓存未接入时（A-16），缓存失效只作用于本进程；多副本部署必须注入
共享缓存实现，否则其它副本仍可能命中旧 chunk。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from kb.assembly import build_document_erasure_service_from_env
from kb.compliance.restriction import RestrictionService
from kb.database import dsn_from_env


def _parse_now(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"--now 不是 ISO 8601 时间：{raw}") from exc
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def run(args: argparse.Namespace) -> int:
    dsn = args.dsn or dsn_from_env()
    restrictions = RestrictionService(dsn)
    now = _parse_now(args.now) if args.now else datetime.now(UTC)

    if args.dry_run:
        holds = await restrictions.list_expired(now)
        print(
            json.dumps(
                [
                    {
                        "hold_id": hold.hold_id,
                        "tenant_id": hold.tenant_id,
                        "doc_id": hold.location_id,
                        "statutory_until": (
                            hold.statutory_until.isoformat() if hold.statutory_until else None
                        ),
                    }
                    for hold in holds
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    eraser = build_document_erasure_service_from_env(dsn=dsn)
    results = await restrictions.purge_expired(eraser, now)
    for result in results:
        print(
            json.dumps(
                {
                    "request_id": result.request_id,
                    "state": result.state,
                    "certificate_id": result.certificate_id,
                },
                ensure_ascii=False,
            )
        )
    return 1 if any(result.state == "PARTIAL_FAILED" for result in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T-3 保留期到期清除")
    parser.add_argument("--dsn", default=None, help="数据库 DSN；默认读 DATABASE_URL")
    parser.add_argument("--now", default=None, help="覆盖当前时间（ISO 8601，测试用）")
    parser.add_argument("--dry-run", action="store_true", help="只列出到期 hold，不执行删除")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
