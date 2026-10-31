"""S3 兼容对象存储的备份目录适配器 —— A-19 的真实厂商接入。

目录约定（每个租户独立前缀，per_tenant）：

    s3://<bucket>/<prefix>/<tenant_id>/<backup_id>/kb-manifest.json

`kb-manifest.json` 描述一个备份代次；同前缀下的其它对象属于该代次的
真实数据。`expire` 只删除**指定租户**前缀下的对象，租户边界由前缀强制。

凭据只从部署环境注入（`build_s3_transport_from_env`），设置表只存引用名。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote, urlsplit

from kb.compliance.backup_domain import BackupCatalogError, BackupGeneration

META_NAME = "kb-manifest.json"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class S3TransportError(BackupCatalogError):
    """S3 调用失败。"""


class S3NotFoundError(S3TransportError):
    """对象或前缀不存在。"""


@dataclass(frozen=True)
class S3ObjectSummary:
    key: str
    size: int = 0


class S3Transport(Protocol):
    """最小 S3 接缝；凭据与签名由实现从部署环境构造。"""

    async def list_objects(
        self,
        bucket: str,
        *,
        prefix: str,
        delimiter: str | None = None,
        continuation_token: str | None = None,
    ) -> tuple[Sequence[S3ObjectSummary], Sequence[str], str | None]:
        """返回 (对象, 公共前缀, 下一页令牌)。"""
        ...

    async def get_object(self, bucket: str, key: str) -> bytes: ...

    async def put_object(
        self, bucket: str, key: str, data: bytes, *, content_type: str | None = None
    ) -> None: ...

    async def delete_objects(self, bucket: str, keys: Sequence[str]) -> int: ...

    async def delete_prefix(self, bucket: str, prefix: str) -> int: ...


class S3BackupCatalog:
    def __init__(self, transport: S3Transport, *, bucket: str, prefix: str = "") -> None:
        if not bucket.strip():
            raise BackupCatalogError("S3 bucket 不能为空")
        self._transport = transport
        self._bucket = bucket
        self._prefix = _normalise_prefix(prefix)

    async def list_generations(
        self, tenant_id: str, *, now: datetime | None = None
    ) -> Sequence[BackupGeneration]:
        del now
        tenant_prefix = self._tenant_prefix(tenant_id)
        generations: list[BackupGeneration] = []
        token: str | None = None
        while True:
            _objects, common, token = await self._transport.list_objects(
                self._bucket,
                prefix=tenant_prefix,
                delimiter="/",
                continuation_token=token,
            )
            for backup_prefix in common:
                try:
                    raw = await self._transport.get_object(
                        self._bucket, f"{backup_prefix}{META_NAME}"
                    )
                except S3NotFoundError:
                    continue
                generations.append(
                    _parse_manifest(raw, expected_tenant=tenant_id, backup_prefix=backup_prefix)
                )
            if not token:
                break
        return generations

    async def expire(self, backup_ids: Sequence[str], *, tenant_id: str, evidence: str) -> int:
        del evidence
        tenant_prefix = self._tenant_prefix(tenant_id)
        deleted = 0
        for backup_id in backup_ids:
            _validate_segment(backup_id, "backup_id")
            prefix = f"{tenant_prefix}{backup_id}/"
            keys: list[str] = []
            token: str | None = None
            while True:
                objects, _common, token = await self._transport.list_objects(
                    self._bucket,
                    prefix=prefix,
                    continuation_token=token,
                )
                keys.extend(item.key for item in objects)
                if not token:
                    break
            if keys:
                deleted += await self._transport.delete_objects(self._bucket, keys)
        return deleted

    def _tenant_prefix(self, tenant_id: str) -> str:
        _validate_segment(tenant_id, "tenant_id")
        return f"{self._prefix}{tenant_id}/"


class UrllibS3Transport:
    """基于 urllib 的 S3 传输（path-style + SigV4），不引入 boto3。"""

    def __init__(
        self,
        endpoint: str,
        *,
        region: str,
        access_key: str,
        secret_key: str,
        session_token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise BackupCatalogError("S3 endpoint 必须以 http:// 或 https:// 开头")
        if not access_key or not secret_key:
            raise BackupCatalogError("S3 访问密钥不能为空")
        self._endpoint = endpoint.rstrip("/")
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._session_token = session_token
        self._timeout = timeout

    async def list_objects(
        self,
        bucket: str,
        *,
        prefix: str,
        delimiter: str | None = None,
        continuation_token: str | None = None,
    ) -> tuple[Sequence[S3ObjectSummary], Sequence[str], str | None]:
        query: dict[str, str] = {"list-type": "2", "prefix": prefix}
        if delimiter is not None:
            query["delimiter"] = delimiter
        if continuation_token:
            query["continuation-token"] = continuation_token
        body = await self._request("GET", bucket, "", query=query)
        return _parse_listing(body)

    async def get_object(self, bucket: str, key: str) -> bytes:
        return await self._request("GET", bucket, key)

    async def put_object(
        self, bucket: str, key: str, data: bytes, *, content_type: str | None = None
    ) -> None:
        await self._request(
            "PUT",
            bucket,
            key,
            body=data,
            content_type=content_type or "application/octet-stream",
        )

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        """删除前缀下所有对象，包含版本历史（S3 版本控制陷阱）。"""
        versions: list[tuple[str, str]] = []
        key_marker: str | None = None
        version_marker: str | None = None
        while True:
            query: dict[str, str] = {"versions": "", "prefix": prefix}
            if key_marker:
                query["key-marker"] = key_marker
            if version_marker:
                query["version-id-marker"] = version_marker
            raw = await self._request("GET", bucket, "", query=query)
            page, truncated, key_marker, version_marker = _parse_versions(raw)
            versions.extend(page)
            if not truncated:
                break
        if versions:
            deleted = 0
            for start in range(0, len(versions), 1000):
                deleted += await self._delete_versions(bucket, versions[start : start + 1000])
            return deleted

        keys: list[str] = []
        token: str | None = None
        while True:
            objects, _common, token = await self.list_objects(
                bucket, prefix=prefix, continuation_token=token
            )
            keys.extend(item.key for item in objects)
            if not token:
                break
        deleted = 0
        for start in range(0, len(keys), 1000):
            deleted += await self.delete_objects(bucket, keys[start : start + 1000])
        return deleted

    async def _delete_versions(self, bucket: str, versions: Sequence[tuple[str, str]]) -> int:
        body = _delete_versions_xml(versions)
        payload = await self._request(
            "POST",
            bucket,
            "",
            query={"delete": ""},
            body=body,
            content_type="application/xml",
            content_md5=base64.b64encode(hashlib.md5(body).digest()).decode("ascii"),  # noqa: S324
        )
        deleted, errors = _parse_delete_result(payload)
        if errors:
            raise S3TransportError(f"S3 删除版本部分失败：{'; '.join(errors)}")
        return deleted

    async def delete_objects(self, bucket: str, keys: Sequence[str]) -> int:
        if not keys:
            return 0
        body = _delete_xml(keys)
        payload = await self._request(
            "POST",
            bucket,
            "",
            query={"delete": ""},
            body=body,
            content_type="application/xml",
            content_md5=base64.b64encode(hashlib.md5(body).digest()).decode("ascii"),  # noqa: S324
        )
        deleted, errors = _parse_delete_result(payload)
        if errors:
            raise S3TransportError(f"S3 删除部分失败：{'; '.join(errors)}")
        return deleted

    async def _request(
        self,
        method: str,
        bucket: str,
        key: str,
        *,
        query: Mapping[str, str] | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
        content_md5: str | None = None,
    ) -> bytes:
        return await asyncio.to_thread(
            self._request_sync,
            method,
            bucket,
            key,
            dict(query or {}),
            body,
            content_type,
            content_md5,
        )

    def _request_sync(
        self,
        method: str,
        bucket: str,
        key: str,
        query: dict[str, str],
        body: bytes | None,
        content_type: str | None,
        content_md5: str | None,
    ) -> bytes:
        url = self._url(bucket, key, query)
        extra: dict[str, str] = {}
        if content_type:
            extra["content-type"] = content_type
        if content_md5:
            extra["content-md5"] = content_md5
        signed = sign_v4_request(
            method=method,
            url=url,
            body=body or b"",
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
            session_token=self._session_token,
            now=datetime.now(UTC),
            extra_headers=extra,
        )
        headers = {name: value for name, value in signed.items() if name != "host"}
        request = urllib.request.Request(  # noqa: S310
            url, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return bytes(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise S3NotFoundError(f"S3 对象不存在：{bucket}/{key}") from exc
            raise S3TransportError(f"S3 返回 HTTP {exc.code}：{exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise S3TransportError(f"S3 不可用：{exc}") from exc

    def _url(self, bucket: str, key: str, query: Mapping[str, str]) -> str:
        path = f"{self._endpoint}/{quote(bucket, safe='-_.~')}"
        if key:
            path = f"{path}/{quote(key, safe='/-_.~')}"
        if query:
            encoded = "&".join(
                f"{quote(name, safe='-_.~')}={quote(value, safe='-_.~')}"
                for name, value in sorted(query.items())
            )
            path = f"{path}?{encoded}"
        return path


def build_s3_transport_from_env(
    environ: Mapping[str, str] | None = None,
) -> UrllibS3Transport:
    """按部署环境装配 S3 传输。凭据不得来自设置表或代码。"""
    env = os.environ if environ is None else environ
    endpoint = env.get("KB_S3_ENDPOINT", "").strip()
    access_key = env.get("KB_S3_ACCESS_KEY_ID", "").strip()
    secret_key = env.get("KB_S3_SECRET_ACCESS_KEY", "").strip()
    if not endpoint or not access_key or not secret_key:
        raise BackupCatalogError(
            "S3 需配置 KB_S3_ENDPOINT / KB_S3_ACCESS_KEY_ID / KB_S3_SECRET_ACCESS_KEY"
        )
    return UrllibS3Transport(
        endpoint,
        region=env.get("KB_S3_REGION") or "us-east-1",
        access_key=access_key,
        secret_key=secret_key,
        session_token=env.get("KB_S3_SESSION_TOKEN") or None,
    )


def sign_v4_request(
    *,
    method: str,
    url: str,
    body: bytes,
    access_key: str,
    secret_key: str,
    region: str,
    now: datetime,
    service: str = "s3",
    session_token: str | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """返回带 Authorization 的完整请求头（含 host，供签名校验）。"""
    parts = urlsplit(url)
    canonical_uri = parts.path or "/"
    canonical_query = parts.query
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()

    headers: dict[str, str] = {
        "host": parts.netloc,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if session_token:
        headers["x-amz-security-token"] = session_token
    for name, value in (extra_headers or {}).items():
        headers[name.lower()] = value

    signed_names = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in signed_names)
    signed_headers = ";".join(signed_names)
    canonical_request = (
        f"{method}\n{canonical_uri}\n{canonical_query}\n"
        f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _signing_key(secret_key, datestamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers


def _signing_key(secret_key: str, datestamp: str, region: str, service: str) -> bytes:
    date_key = hmac.new(f"AWS4{secret_key}".encode(), datestamp.encode(), hashlib.sha256).digest()
    region_key = hmac.new(date_key, region.encode(), hashlib.sha256).digest()
    service_key = hmac.new(region_key, service.encode(), hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()


def _parse_manifest(raw: bytes, *, expected_tenant: str, backup_prefix: str) -> BackupGeneration:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BackupCatalogError(f"备份 manifest 不是合法 JSON：{backup_prefix}") from exc
    if not isinstance(payload, Mapping):
        raise BackupCatalogError(f"备份 manifest 不是对象：{backup_prefix}")
    tenant_id = payload.get("tenant_id")
    if tenant_id != expected_tenant:
        raise BackupCatalogError(
            f"备份 manifest 租户不匹配：{backup_prefix}（{tenant_id!r} != {expected_tenant!r}）"
        )
    folder = backup_prefix.rstrip("/").rsplit("/", 1)[-1]
    backup_id = payload.get("backup_id")
    if backup_id != folder:
        raise BackupCatalogError(f"备份 manifest 的 backup_id 与前缀不一致：{backup_prefix}")
    try:
        return BackupGeneration(
            backup_id=str(backup_id),
            created_at=_datetime(payload["created_at"]),
            expires_at=_datetime(payload["expires_at"]),
            scope=str(payload.get("scope", "tenant")),  # type: ignore[arg-type]
            tenant_id=str(tenant_id),
            encryption_key_ref=_optional_str(payload.get("encryption_key_ref")),
            status=str(payload.get("status", "available")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupCatalogError(f"备份 manifest 字段不合法：{backup_prefix}：{exc}") from exc


def _parse_listing(raw: bytes) -> tuple[Sequence[S3ObjectSummary], Sequence[str], str | None]:
    try:
        root = ET.fromstring(raw)  # noqa: S314 - 响应来自部署层配置的 S3 端点
    except ET.ParseError as exc:
        raise S3TransportError("S3 列表响应不是合法 XML") from exc
    objects: list[S3ObjectSummary] = []
    common: list[str] = []
    for node in root:
        tag = _local(node.tag)
        if tag == "Contents":
            key = _child_text(node, "Key")
            if key is not None:
                objects.append(S3ObjectSummary(key=key, size=_child_int(node, "Size")))
        elif tag == "CommonPrefixes":
            prefix = _child_text(node, "Prefix")
            if prefix is not None:
                common.append(prefix)
    token = _child_text(root, "NextContinuationToken")
    return objects, common, token


def _parse_delete_result(raw: bytes) -> tuple[int, list[str]]:
    try:
        root = ET.fromstring(raw)  # noqa: S314 - 响应来自部署层配置的 S3 端点
    except ET.ParseError as exc:
        raise S3TransportError("S3 删除响应不是合法 XML") from exc
    deleted = 0
    errors: list[str] = []
    for node in root:
        tag = _local(node.tag)
        if tag == "Deleted":
            deleted += 1
        elif tag == "Error":
            key = _child_text(node, "Key") or "?"
            message = _child_text(node, "Message") or "unknown"
            errors.append(f"{key}: {message}")
    return deleted, errors


def _delete_xml(keys: Sequence[str]) -> bytes:
    objects = "".join(f"<Object><Key>{_xml_escape(key)}</Key></Object>" for key in keys)
    return f"<Delete><Quiet>false</Quiet>{objects}</Delete>".encode()


def _delete_versions_xml(versions: Sequence[tuple[str, str]]) -> bytes:
    objects = "".join(
        f"<Object><Key>{_xml_escape(key)}</Key>"
        f"<VersionId>{_xml_escape(version)}</VersionId></Object>"
        for key, version in versions
    )
    return f"<Delete><Quiet>false</Quiet>{objects}</Delete>".encode()


def _parse_versions(
    raw: bytes,
) -> tuple[list[tuple[str, str]], bool, str | None, str | None]:
    try:
        root = ET.fromstring(raw)  # noqa: S314 - 响应来自部署层配置的 S3 端点
    except ET.ParseError as exc:
        raise S3TransportError("S3 版本列表响应不是合法 XML") from exc
    versions: list[tuple[str, str]] = []
    for node in root:
        if _local(node.tag) not in {"Version", "DeleteMarker"}:
            continue
        key = _child_text(node, "Key")
        version = _child_text(node, "VersionId")
        if key is not None and version is not None:
            versions.append((key, version))
    truncated = (_child_text(root, "IsTruncated") or "").lower() == "true"
    return (
        versions,
        truncated,
        _child_text(root, "NextKeyMarker"),
        _child_text(root, "NextVersionIdMarker"),
    )


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(node: ET.Element, name: str) -> str | None:
    for child in node:
        if _local(child.tag) == name:
            return child.text
    return None


def _child_int(node: ET.Element, name: str) -> int:
    raw = _child_text(node, name)
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError("时间字段不是 ISO 字符串")


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _normalise_prefix(prefix: str) -> str:
    cleaned = prefix.strip().strip("/")
    if not cleaned:
        return ""
    parts = cleaned.split("/")
    if any(not part or part in {".", ".."} or "\\" in part for part in parts):
        raise BackupCatalogError(f"prefix 含非法路径段：{prefix!r}")
    return f"{cleaned}/"


def _validate_segment(value: str, name: str) -> None:
    if not value or value in {".", ".."}:
        raise BackupCatalogError(f"{name} 不能为空或相对路径")
    if "/" in value or "\\" in value or ".." in value:
        raise BackupCatalogError(f"{name} 含非法字符：{value!r}")


__all__ = [
    "META_NAME",
    "S3BackupCatalog",
    "S3NotFoundError",
    "S3ObjectSummary",
    "S3Transport",
    "S3TransportError",
    "UrllibS3Transport",
    "build_s3_transport_from_env",
    "sign_v4_request",
]
