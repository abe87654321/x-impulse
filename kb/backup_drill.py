"""本机可跑的备份轮转 + 恢复演练。

用法：
    .venv/bin/python -m kb.backup_drill --manifest /tmp/kb-backup-drill/manifest.jsonl

流程：
1. 生成一份本地 manifest（含一个已到期代次、一个未到期代次、一个他租户代次）。
2. 通过管理面设置服务写入备份策略，再按策略构造目录驱动的轮转。
3. 在隔离租户内造一份含 PII 的文档，写入抑制清单并销毁一个假 KMS 密钥。
4. 运行 RestoreGuard：必须同时命中文档抑制、主体抑制与 KMS 销毁状态并阻断。
5. 清理演练租户数据；默认保留 manifest 供检查。

安全：租户必须 drill_ 前缀，清理只删除该租户的数据。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kb.compliance.backup_adapters import CatalogDependencies, FileManifestSource
from kb.compliance.backup_policy import (
    build_rotation_from_policy,
    resolve_backup_policy,
)
from kb.compliance.kms import (
    FakeKmsClient,
    KmsClient,
    KmsKeyManager,
    PostgresSubjectKeyStore,
)
from kb.compliance.kms_vault import KmsConfigError, build_kms_from_env
from kb.compliance.pii import subject_key
from kb.compliance.restore import (
    PostgresRestoreCatalog,
    RestoreGuard,
    RestoreViolation,
)
from kb.database import Database, dsn_from_env
from kb.governance.settings_service import ROLE_SYSTEM_ADMIN, BackupSettingsService
from kb.governance.settings_store import PostgresSettingsStore
from kb.indexing.suppression import PostgresSuppressionChecker, document_scope_key

DEFAULT_TENANT = "drill_restore"
SOURCE = "src_drill"
EXT = "ext_drill"
DOC = "doc_drill"
CHUNK = "chk_drill"
PHONE = "13800138000"
PEPPER = "drill-pepper"


def _hasher(value: str) -> str:
    return subject_key(value, PEPPER)


def _seed(db: Database, tenant_id: str) -> None:
    content = f"演练联系人电话{PHONE}"
    db.execute(
        "INSERT INTO tenants (tenant_id, display_name) VALUES ($1,'备份演练') "
        "ON CONFLICT (tenant_id) DO NOTHING",
        tenant_id,
    )
    db.execute(
        "INSERT INTO sources (source_id, tenant_id, source_type, display_name) "
        "VALUES ($1,$2,'upload','备份演练源') ON CONFLICT (source_id) DO NOTHING",
        SOURCE,
        tenant_id,
    )
    db.execute(
        "INSERT INTO documents "
        "(doc_id, tenant_id, source_id, external_id, title, content_hash, parser_version, status) "
        "VALUES ($1,$2,$3,$4,'演练文档','drill-hash','text-v1','indexed') "
        "ON CONFLICT (doc_id) DO NOTHING",
        DOC,
        tenant_id,
        SOURCE,
        EXT,
    )
    db.execute(
        "INSERT INTO chunks "
        "(chunk_id, tenant_id, doc_id, seq, content, content_hash, token_count, "
        " char_start, char_end, chunker_version) "
        "VALUES ($1,$2,$3,0,$4,'drill-chunk-hash',1,0,$5,'chunk-v1') "
        "ON CONFLICT (chunk_id) DO NOTHING",
        CHUNK,
        tenant_id,
        DOC,
        content,
        len(content),
    )
    db.execute(
        "INSERT INTO suppression_list "
        "(suppression_id, tenant_id, scope_type, scope_key, reason) "
        "VALUES ('sup_drill_doc',$1,'document',$2,'drill'),"
        "       ('sup_drill_subject',$1,'subject',$3,'drill') "
        "ON CONFLICT (tenant_id, scope_type, scope_key) DO NOTHING",
        tenant_id,
        _hasher(document_scope_key(SOURCE, EXT)),
        _hasher(PHONE),
    )


def _cleanup(db: Database, tenant_id: str) -> None:
    db.execute("DELETE FROM service_settings WHERE tenant_id = $1", tenant_id)
    db.execute("DELETE FROM suppression_list WHERE tenant_id = $1", tenant_id)
    db.execute("DELETE FROM subject_keys WHERE tenant_id = $1", tenant_id)
    db.execute("DELETE FROM chunks WHERE tenant_id = $1", tenant_id)
    db.execute("DELETE FROM documents WHERE tenant_id = $1", tenant_id)
    db.execute("DELETE FROM sources WHERE tenant_id = $1", tenant_id)
    db.execute("DELETE FROM tenants WHERE tenant_id = $1", tenant_id)


def build_drill_kms(source: str = "fake", environ: Mapping[str, str] | None = None) -> KmsClient:
    """选择演练用 KMS。

    默认 `fake`：演练会**销毁**密钥，绝不能默认打到真实 KMS。
    `vault` 必须显式选择，且只应指向非生产的 Vault 实例/命名空间。
    """
    if source == "fake":
        return FakeKmsClient()
    if source == "vault":
        return build_kms_from_env(environ)
    raise KmsConfigError(f"未知演练 KMS：{source}")


def _prepare_manifest(
    raw: str,
    tenant_id: str,
    now: datetime,
    *,
    regenerate: bool = False,
    key_ref: str = "fake-kms://drill",
) -> Path:
    path = Path(raw).expanduser().resolve()
    if path.exists() and not regenerate:
        return path
    _write_manifest(path, tenant_id, now, key_ref=key_ref)
    return path


def _write_manifest(
    path: Path, tenant_id: str, now: datetime, *, key_ref: str = "fake-kms://drill"
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "backup_id": "drill-due",
            "tenant_id": tenant_id,
            "created_at": (now - timedelta(days=100)).isoformat(),
            "expires_at": (now - timedelta(days=1)).isoformat(),
            "scope": "tenant",
            "encryption_key_ref": key_ref,
            "status": "available",
        },
        {
            "backup_id": "drill-future",
            "tenant_id": tenant_id,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=30)).isoformat(),
            "scope": "tenant",
            "encryption_key_ref": key_ref,
            "status": "available",
        },
        {
            "backup_id": "drill-other-tenant",
            "tenant_id": "other_tenant",
            "created_at": now.isoformat(),
            "expires_at": (now - timedelta(days=1)).isoformat(),
            "scope": "tenant",
        },
        {
            "backup_id": "drill-shared",
            "tenant_id": None,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=15)).isoformat(),
            "scope": "shared",
        },
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


async def run(args: argparse.Namespace) -> int:
    tenant_id = args.tenant
    if not tenant_id.startswith("drill_"):
        raise SystemExit("为安全起见，演练租户必须以 drill_ 开头")
    manifest_tenant = getattr(args, "manifest_tenant", None) or tenant_id
    kms_source = getattr(args, "kms", "fake")
    key_ref = "vault-transit://drill" if kms_source == "vault" else "fake-kms://drill"

    dsn = args.dsn or dsn_from_env()
    now = datetime.now(UTC)
    manifest = await asyncio.to_thread(
        _prepare_manifest,
        args.manifest,
        tenant_id,
        now,
        regenerate=getattr(args, "regenerate", False),
        key_ref=key_ref,
    )
    db = Database(dsn)

    try:
        await asyncio.to_thread(_seed, db, tenant_id)

        settings = BackupSettingsService(PostgresSettingsStore(dsn))
        await settings.update(
            [ROLE_SYSTEM_ADMIN],
            tenant_id,
            {
                "backup_system": "database_pitr",
                "catalog_mode": "manifest",
                "manifest_uri": manifest.as_uri(),
                "tenant_isolation": "per_tenant",
                "rotation_days": args.rotation_days,
            },
            updated_by="drill",
        )
        resolved = await settings.snapshot(tenant_id)
        policy = resolve_backup_policy(dict(resolved.values))
        rotation = build_rotation_from_policy(
            policy,
            dependencies=CatalogDependencies(manifest_source=FileManifestSource(str(manifest))),
        )

        print("== 备份策略 ==")
        print(json.dumps(resolved.values, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"manifest: {manifest}")

        print("\n== 轮转 ==")
        if manifest_tenant != tenant_id:
            print(f"manifest 租户：{manifest_tenant}；恢复演练租户：{tenant_id}")
        notice = await rotation.notify_erasure(
            manifest_tenant, scope_type="document", scope_key="drill", now=now
        )
        before = await rotation.rotation_status(manifest_tenant, now=now)
        expired = await rotation.rotate(manifest_tenant, now=now)
        after = await rotation.rotation_status(manifest_tenant, now=now)
        print(f"删除登记：{notice.status} / {notice.detail}")
        print(f"轮转前待到期：{before.pending}，本次到期：{expired}，轮转后待到期：{after.pending}")
        print("说明：manifest 模式只报告到期；真实介质删除由存储生命周期执行。")

        print("\n== 恢复演练 ==")
        key_manager = KmsKeyManager(build_drill_kms(kms_source), PostgresSubjectKeyStore(dsn))
        await key_manager.register(tenant_id, _hasher(PHONE))
        await key_manager.destroy(tenant_id, _hasher(PHONE), "drill")
        guard = RestoreGuard(
            PostgresRestoreCatalog(db),
            PostgresSuppressionChecker(_hasher, dsn=dsn),
            tenant_id=tenant_id,
            subject_hasher=_hasher,
            key_authority=key_manager,
        )
        report = await guard.scan()
        for finding in report.findings:
            print(f"- [{finding.kind}] {finding.object_id}: {finding.detail}")
        try:
            await guard.verify()
        except RestoreViolation:
            print("结果：恢复被阻断（符合预期）")
        else:
            print("结果：未阻断（不符合预期）")
            return 1
        return 0
    finally:
        await asyncio.to_thread(_cleanup, db, tenant_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="备份轮转 + 恢复演练")
    parser.add_argument(
        "--manifest", default="~/.kb/backup-drill/manifest.jsonl", help="manifest 路径"
    )
    parser.add_argument("--tenant", default=DEFAULT_TENANT, help="演练租户（须 drill_ 前缀）")
    parser.add_argument("--dsn", default=None, help="数据库 DSN；默认读 DATABASE_URL")
    parser.add_argument(
        "--manifest-tenant",
        default=None,
        help="manifest 中的租户；默认与演练租户相同",
    )
    parser.add_argument("--rotation-days", type=int, default=90)
    parser.add_argument(
        "--kms",
        choices=("fake", "vault"),
        default="fake",
        help="演练 KMS；vault 会销毁真实密钥，只允许指向非生产实例",
    )
    parser.add_argument(
        "--regenerate", action="store_true", help="覆盖已存在的 manifest（默认复用现有文件）"
    )
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
