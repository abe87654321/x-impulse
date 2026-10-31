"""S3 备份目录：前缀隔离、到期删除与 SigV4 签名。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from kb.compliance.backup_adapters import CatalogDependencies, default_backup_registry
from kb.compliance.backup_domain import BackupCatalogError, CatalogBackupRotation
from kb.compliance.backup_s3 import (
    S3BackupCatalog,
    S3NotFoundError,
    S3ObjectSummary,
    _parse_object_lock,
    _parse_uploads,
    _parse_versions,
    build_s3_transport_from_env,
    sign_v4_request,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)


class FakeS3Transport:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.deleted: list[str] = []

    async def list_objects(
        self,
        bucket: str,
        *,
        prefix: str,
        delimiter: str | None = None,
        continuation_token: str | None = None,
    ) -> tuple[Sequence[S3ObjectSummary], Sequence[str], str | None]:
        del bucket, continuation_token
        objects: list[S3ObjectSummary] = []
        common: set[str] = set()
        for key in self.objects:
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            if delimiter is not None and delimiter in rest:
                common.add(f"{prefix}{rest.split(delimiter, 1)[0]}{delimiter}")
            else:
                objects.append(S3ObjectSummary(key=key))
        return objects, sorted(common), None

    async def get_object(self, bucket: str, key: str) -> bytes:
        del bucket
        if key not in self.objects:
            raise S3NotFoundError(key)
        return self.objects[key]

    async def delete_objects(self, bucket: str, keys: Sequence[str]) -> int:
        del bucket
        for key in keys:
            self.objects.pop(key, None)
            self.deleted.append(key)
        return len(keys)


def _manifest(backup_id: str, tenant_id: str, days: int = 30) -> bytes:
    return json.dumps(
        {
            "backup_id": backup_id,
            "tenant_id": tenant_id,
            "created_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(days=days)).isoformat(),
            "scope": "tenant",
            "encryption_key_ref": "kms://backup-key",
        }
    ).encode()


async def test_lists_only_own_tenant_and_ignores_folders_without_manifest():
    transport = FakeS3Transport(
        {
            "kb/t1/b1/kb-manifest.json": _manifest("b1", "t1"),
            "kb/t1/b1/data.bin": b"payload",
            "kb/t1/not-a-backup/readme": b"x",
            "kb/t2/b2/kb-manifest.json": _manifest("b2", "t2"),
        }
    )
    catalog = S3BackupCatalog(transport, bucket="bucket", prefix="kb")

    generations = await catalog.list_generations("t1", now=NOW)

    assert [generation.backup_id for generation in generations] == ["b1"]
    assert generations[0].tenant_id == "t1"


async def test_expire_deletes_only_selected_backup_under_tenant():
    transport = FakeS3Transport(
        {
            "kb/t1/b1/kb-manifest.json": _manifest("b1", "t1"),
            "kb/t1/b1/data.bin": b"payload",
            "kb/t1/b2/kb-manifest.json": _manifest("b2", "t1"),
            "kb/t2/b1/kb-manifest.json": _manifest("b1", "t2"),
        }
    )
    catalog = S3BackupCatalog(transport, bucket="bucket", prefix="kb")

    deleted = await catalog.expire(["b1"], tenant_id="t1", evidence="rotation")

    assert deleted == 2
    assert sorted(transport.deleted) == [
        "kb/t1/b1/data.bin",
        "kb/t1/b1/kb-manifest.json",
    ]
    assert "kb/t1/b2/kb-manifest.json" in transport.objects
    assert "kb/t2/b1/kb-manifest.json" in transport.objects


@pytest.mark.parametrize("tenant", ["", "../t2", "t1/../t2", "t1\\..\\t2"])
async def test_tenant_traversal_is_rejected(tenant):
    catalog = S3BackupCatalog(FakeS3Transport(), bucket="bucket", prefix="kb")

    with pytest.raises(BackupCatalogError):
        await catalog.list_generations(tenant, now=NOW)


async def test_backup_id_traversal_is_rejected_on_expire():
    catalog = S3BackupCatalog(FakeS3Transport(), bucket="bucket", prefix="kb")

    with pytest.raises(BackupCatalogError):
        await catalog.expire(["../t2/b1"], tenant_id="t1", evidence="rotation")


async def test_manifest_tenant_mismatch_is_rejected():
    transport = FakeS3Transport({"kb/t1/b1/kb-manifest.json": _manifest("b1", "t2")})
    catalog = S3BackupCatalog(transport, bucket="bucket", prefix="kb")

    with pytest.raises(BackupCatalogError):
        await catalog.list_generations("t1", now=NOW)


async def test_s3_catalog_plugs_into_rotation():
    transport = FakeS3Transport(
        {
            "kb/t1/due/kb-manifest.json": _manifest("due", "t1", days=-1),
            "kb/t1/due/data.bin": b"payload",
            "kb/t1/later/kb-manifest.json": _manifest("later", "t1", days=30),
        }
    )
    catalog = S3BackupCatalog(transport, bucket="bucket", prefix="kb")
    rotation = CatalogBackupRotation(catalog, rotation_days=90)

    expired = await rotation.rotate("t1", now=NOW)

    assert expired == 2
    assert "kb/t1/later/kb-manifest.json" in transport.objects


def test_registry_builds_s3_catalog_from_dependencies():
    transport = FakeS3Transport()
    catalog = default_backup_registry().create(
        "object_storage",
        "s3_api",
        {"catalog_bucket": "bucket", "catalog_prefix": "kb"},
        CatalogDependencies(s3_transport=transport),
    )

    assert isinstance(catalog, S3BackupCatalog)


def test_build_transport_from_env_requires_credentials():
    with pytest.raises(BackupCatalogError):
        build_s3_transport_from_env({"KB_S3_ENDPOINT": "https://s3.example.com"})


def test_sigv4_matches_aws_documented_example():
    headers = sign_v4_request(
        method="GET",
        url="https://examplebucket.s3.amazonaws.com/test.txt",
        body=b"",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        now=datetime(2013, 5, 24, tzinfo=UTC),
        extra_headers={"range": "bytes=0-9"},
    )

    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 "
        "Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_parse_versions_includes_delete_markers_and_pagination():
    raw = b"""<ListVersionsResult>
      <Version><Key>kb/t1/b1/data</Key><VersionId>v1</VersionId></Version>
      <DeleteMarker><Key>kb/t1/b1/old</Key><VersionId>v2</VersionId></DeleteMarker>
      <IsTruncated>true</IsTruncated>
      <NextKeyMarker>kb/t1/b1/data</NextKeyMarker>
      <NextVersionIdMarker>v1</NextVersionIdMarker>
    </ListVersionsResult>"""

    versions, truncated, key_marker, version_marker = _parse_versions(raw)

    assert versions == [
        ("kb/t1/b1/data", "v1"),
        ("kb/t1/b1/old", "v2"),
    ]
    assert truncated is True
    assert key_marker == "kb/t1/b1/data"
    assert version_marker == "v1"


def test_parse_uploads_and_object_lock():
    uploads_xml = b"""<ListMultipartUploadsResult>
      <Upload><Key>kb/t1/doc/a</Key><UploadId>u1</UploadId></Upload>
      <Upload><Key>kb/t1/doc/b</Key><UploadId>u2</UploadId></Upload>
      <IsTruncated>true</IsTruncated>
      <NextKeyMarker>kb/t1/doc/b</NextKeyMarker>
      <NextUploadIdMarker>u2</NextUploadIdMarker>
    </ListMultipartUploadsResult>"""

    uploads, truncated, key_marker, upload_marker = _parse_uploads(uploads_xml)

    assert uploads == [("kb/t1/doc/a", "u1"), ("kb/t1/doc/b", "u2")]
    assert truncated is True
    assert key_marker == "kb/t1/doc/b"
    assert upload_marker == "u2"
    assert _parse_object_lock(
        b"<ObjectLockConfiguration><ObjectLockEnabled>Enabled</ObjectLockEnabled>"
        b"</ObjectLockConfiguration>"
    )
    assert not _parse_object_lock(
        b"<ObjectLockConfiguration><ObjectLockEnabled>Disabled</ObjectLockEnabled>"
        b"</ObjectLockConfiguration>"
    )
