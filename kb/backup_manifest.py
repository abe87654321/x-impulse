"""备份 manifest 的生成与校验工具。

manifest 是备份系统与轮转/恢复演练之间的契约文件：一行一个 JSON 对象，
描述一个备份代次。本工具负责初始化、追加和校验，避免手写格式出错。

用法：
    .venv/bin/python -m kb.backup_manifest init --path config/backup_manifest.jsonl \
        --tenant default --count 3 --rotation-days 90
    .venv/bin/python -m kb.backup_manifest append --path config/backup_manifest.jsonl \
        --tenant default --rotation-days 90
    .venv/bin/python -m kb.backup_manifest validate --path config/backup_manifest.jsonl
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_PATH = "config/backup_manifest.jsonl"
_SCOPES = ("tenant", "shared", "global")


def generation_row(
    *,
    backup_id: str,
    tenant_id: str | None,
    created_at: datetime,
    rotation_days: int,
    scope: str = "tenant",
    encryption_key_ref: str | None = None,
) -> dict[str, object]:
    if rotation_days <= 0:
        raise ValueError("rotation_days 必须 > 0")
    if scope not in _SCOPES:
        raise ValueError(f"scope 必须属于 {_SCOPES}")
    if scope == "tenant" and not tenant_id:
        raise ValueError("scope=tenant 时必须提供 tenant_id")
    expires_at = created_at + timedelta(days=rotation_days)
    return {
        "backup_id": backup_id,
        "tenant_id": tenant_id,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "scope": scope,
        "encryption_key_ref": encryption_key_ref,
        "status": "available",
    }


def init_manifest(
    path: str | Path,
    *,
    tenant_id: str,
    count: int = 3,
    rotation_days: int = 90,
    now: datetime | None = None,
    backup_prefix: str = "backup",
) -> list[dict[str, object]]:
    if count <= 0:
        raise ValueError("count 必须 > 0")
    moment = now or datetime.now(UTC)
    rows: list[dict[str, object]] = []
    for index in range(count):
        if index == 0:
            created_at = moment - timedelta(days=rotation_days * 2)
        elif index == 1:
            created_at = moment - timedelta(days=rotation_days // 2)
        else:
            created_at = moment
        rows.append(
            generation_row(
                backup_id=f"{backup_prefix}-{created_at.date().isoformat()}",
                tenant_id=tenant_id,
                created_at=created_at,
                rotation_days=rotation_days,
                scope="tenant",
            )
        )
    _write_rows(path, rows)
    return rows


def append_generation(
    path: str | Path,
    *,
    tenant_id: str,
    rotation_days: int,
    backup_id: str | None = None,
    created_at: datetime | None = None,
    scope: str = "tenant",
    encryption_key_ref: str | None = None,
) -> dict[str, object]:
    moment = created_at or datetime.now(UTC)
    row = generation_row(
        backup_id=backup_id or f"backup-{moment.strftime('%Y%m%dT%H%M%SZ')}",
        tenant_id=tenant_id if scope == "tenant" else None,
        created_at=moment,
        rotation_days=rotation_days,
        scope=scope,
        encryption_key_ref=encryption_key_ref,
    )
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def validate_manifest(path: str | Path) -> list[str]:
    errors: list[str] = []
    target = Path(path).expanduser()
    if not target.exists():
        return [f"文件不存在：{target}"]
    for line_number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"第 {line_number} 行不是合法 JSON：{exc}")
            continue
        errors.extend(_validate_row(row, line_number))
    return errors


def _validate_row(row: object, line_number: int) -> list[str]:
    if not isinstance(row, dict):
        return [f"第 {line_number} 行不是 JSON 对象"]
    errors: list[str] = []
    for key in ("backup_id", "created_at", "expires_at", "scope"):
        if not row.get(key):
            errors.append(f"第 {line_number} 行缺少 {key}")
    scope = row.get("scope")
    if scope not in _SCOPES:
        errors.append(f"第 {line_number} 行 scope 不合法：{scope}")
    if scope == "tenant" and not row.get("tenant_id"):
        errors.append(f"第 {line_number} 行 scope=tenant 但缺少 tenant_id")
    for key in ("created_at", "expires_at"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"第 {line_number} 行 {key} 不是 ISO 时间")
    return errors


def _write_rows(path: str | Path, rows: list[dict[str, object]]) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="备份 manifest 生成与校验")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化示例 manifest")
    init.add_argument("--path", default=DEFAULT_PATH)
    init.add_argument("--tenant", required=True)
    init.add_argument("--count", type=int, default=3)
    init.add_argument("--rotation-days", type=int, default=90)
    init.add_argument("--backup-prefix", default="backup")

    append = sub.add_parser("append", help="追加一个备份代次")
    append.add_argument("--path", default=DEFAULT_PATH)
    append.add_argument("--tenant", required=True)
    append.add_argument("--rotation-days", type=int, default=90)
    append.add_argument("--backup-id", default=None)
    append.add_argument("--created-at", default=None, help="ISO 时间；默认当前时间")
    append.add_argument("--scope", default="tenant", choices=_SCOPES)
    append.add_argument("--encryption-key-ref", default=None)

    validate = sub.add_parser("validate", help="校验 manifest")
    validate.add_argument("--path", default=DEFAULT_PATH)

    args = parser.parse_args(argv)
    if args.command == "init":
        rows = init_manifest(
            args.path,
            tenant_id=args.tenant,
            count=args.count,
            rotation_days=args.rotation_days,
            backup_prefix=args.backup_prefix,
        )
        print(f"已写入 {len(rows)} 条备份代次：{Path(args.path).expanduser()}")
        return 0
    if args.command == "append":
        created_at = (
            datetime.fromisoformat(args.created_at.replace("Z", "+00:00"))
            if args.created_at
            else None
        )
        row = append_generation(
            args.path,
            tenant_id=args.tenant,
            rotation_days=args.rotation_days,
            backup_id=args.backup_id,
            created_at=created_at,
            scope=args.scope,
            encryption_key_ref=args.encryption_key_ref,
        )
        print(f"已追加：{row['backup_id']} -> {row['expires_at']}")
        return 0
    errors = validate_manifest(args.path)
    if errors:
        for error in errors:
            print(f"✗ {error}")
        return 1
    print(f"✓ manifest 合法：{Path(args.path).expanduser()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
