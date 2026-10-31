"""Hashicorp Vault Transit KMS 适配器 —— FR-11-04 / A-20。

密钥材料与销毁状态只存在于 Vault（或其后端的 HSM）。业务库只保存
`key_ref` 与被 Vault 包裹的 DEK；Vault 侧删除密钥后，从数据库备份恢复
的那一行再也解不开密文 —— 这是 A-20 的核心前提。

凭据只从部署环境注入（`KB_VAULT_TOKEN`），不读设置表、不写入代码。
未配置或厂商不可用一律报错，不回退到软件 KEK（I-10）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Protocol

from kb.compliance.kms import (
    KmsClient,
    KmsError,
    KmsKeyDestroyedError,
)

_DEFAULT_MOUNT = "transit"
_DEFAULT_KEY_PREFIX = "kb-subject-"


class KmsConfigError(KmsError):
    """KMS 配置缺失或不合法；启动加密路径时必须失败而不是降级。"""


class VaultTransportError(KmsError):
    """Vault HTTP 调用失败。`status` 为 None 表示连接层失败。"""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class VaultTransport(Protocol):
    """最小 Vault HTTP 接缝。认证头由传输实现从部署环境构造。"""

    async def request(
        self, method: str, path: str, *, body: Mapping[str, object] | None = None
    ) -> Mapping[str, object] | None: ...


class UrllibVaultTransport:
    """基于 urllib 的 Vault 传输；只负责 HTTP，不解释 Transit 语义。"""

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        namespace: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not token:
            raise KmsConfigError("Vault token 不能为空")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._namespace = namespace
        self._timeout = timeout

    async def request(
        self, method: str, path: str, *, body: Mapping[str, object] | None = None
    ) -> Mapping[str, object] | None:
        return await asyncio.to_thread(self._request, method, path, body)

    def _request(
        self, method: str, path: str, body: Mapping[str, object] | None
    ) -> Mapping[str, object] | None:
        url = f"{self._base_url}{path}"
        headers = {"X-Vault-Token": self._token, "Accept": "application/json"}
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        data: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise VaultTransportError(
                f"Vault 返回 HTTP {exc.code}: {_error_message(exc)}", status=exc.code
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VaultTransportError(f"Vault 不可用：{exc}") from exc
        if not raw:
            return None
        try:
            payload: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VaultTransportError("Vault 返回不是合法 JSON") from exc
        if not isinstance(payload, Mapping):
            raise VaultTransportError("Vault 返回不是 JSON 对象")
        return payload


class VaultTransitKmsClient:
    """Vault Transit 的 `KmsClient` 实现。

    `key_ref` 是 Vault 中的密钥名，由 `(tenant_id, subject_key)` 确定性派生，
    因此重复注册不会产生第二把同名密钥。删除用 `DELETE /keys/<name>`，
    Vault 侧密钥一旦删除，旧密文永久不可解。
    """

    def __init__(
        self,
        transport: VaultTransport,
        *,
        mount: str = _DEFAULT_MOUNT,
        key_prefix: str = _DEFAULT_KEY_PREFIX,
    ) -> None:
        self._transport = transport
        self._mount = mount.strip("/")
        if not self._mount:
            raise KmsConfigError("Vault Transit mount 不能为空")
        self._key_prefix = key_prefix

    async def create_key(self, tenant_id: str, subject_key: str) -> str:
        key_ref = self._key_name(tenant_id, subject_key)
        await self._request(
            "POST",
            self._keys_path(key_ref),
            body={
                "type": "aes256-gcm96",
                "exportable": False,
                "deletion_allowed": True,
            },
        )
        return key_ref

    async def wrap(self, key_ref: str, plaintext: bytes) -> str:
        payload = await self._request(
            "POST",
            self._operation_path("encrypt", key_ref),
            body={"plaintext": base64.b64encode(plaintext).decode("ascii")},
        )
        return _data_str(payload, "ciphertext")

    async def unwrap(self, key_ref: str, wrapped: str) -> bytes:
        try:
            payload = await self._request(
                "POST",
                self._operation_path("decrypt", key_ref),
                body={"ciphertext": wrapped},
            )
        except VaultTransportError as exc:
            if _looks_missing(exc):
                raise KmsKeyDestroyedError(key_ref) from exc
            raise
        encoded = _data_str(payload, "plaintext")
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise KmsError("Vault 返回的明文不是合法 base64") from exc

    async def destroy_key(self, key_ref: str) -> str:
        await self._request("DELETE", self._keys_path(key_ref))
        return f"vault-transit:key-destroyed:{key_ref}"

    async def key_state(self, key_ref: str) -> str:
        try:
            payload = await self._request("GET", self._keys_path(key_ref))
        except VaultTransportError as exc:
            if exc.status == 404:
                return "MISSING"
            raise
        if payload is not None:
            data = payload.get("data")
            if isinstance(data, Mapping) and data.get("deleted") is True:
                return "MISSING"
        return "ENABLED"

    def _key_name(self, tenant_id: str, subject_key: str) -> str:
        digest = hashlib.sha256(f"{tenant_id}\x00{subject_key}".encode()).hexdigest()
        return f"{self._key_prefix}{digest}"

    def _keys_path(self, key_ref: str) -> str:
        return f"/v1/{self._mount}/keys/{key_ref}"

    def _operation_path(self, operation: str, key_ref: str) -> str:
        return f"/v1/{self._mount}/{operation}/{key_ref}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None = None,
    ) -> Mapping[str, object] | None:
        payload = await self._transport.request(method, path, body=body)
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise KmsError("Vault Transit 返回不是 JSON 对象")
        return payload


def build_kms_from_env(environ: Mapping[str, str] | None = None) -> KmsClient:
    """按环境装配真实 KMS。未配置或未知厂商一律报错，不隐式降级。"""
    env = os.environ if environ is None else environ
    provider = env.get("KB_KMS_PROVIDER", "").strip().lower()
    if not provider:
        raise KmsConfigError("未配置 KB_KMS_PROVIDER，拒绝回退到软件 KEK")
    if provider in {"vault", "vault-transit", "vault_transit"}:
        address = env.get("KB_VAULT_ADDR", "").strip()
        token = env.get("KB_VAULT_TOKEN", "").strip()
        if not address or not token:
            raise KmsConfigError("Vault KMS 需配置 KB_VAULT_ADDR 与 KB_VAULT_TOKEN")
        transport = UrllibVaultTransport(
            address,
            token=token,
            namespace=env.get("KB_VAULT_NAMESPACE") or None,
        )
        return VaultTransitKmsClient(
            transport,
            mount=env.get("KB_VAULT_TRANSIT_MOUNT") or _DEFAULT_MOUNT,
            key_prefix=env.get("KB_VAULT_KEY_PREFIX") or _DEFAULT_KEY_PREFIX,
        )
    raise KmsConfigError(f"未知 KB_KMS_PROVIDER：{provider}")


def _data_str(payload: Mapping[str, object] | None, field: str) -> str:
    if payload is None:
        raise KmsError("Vault Transit 返回空响应")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise KmsError("Vault Transit 响应缺少 data")
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise KmsError(f"Vault Transit 响应缺少 {field}")
    return value


def _looks_missing(exc: VaultTransportError) -> bool:
    if exc.status == 404:
        return True
    return exc.status == 400 and "not found" in str(exc).lower()


def _error_message(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read()
    except OSError:
        return exc.reason or "unknown"
    if not raw:
        return exc.reason or "unknown"
    try:
        payload: object = json.loads(raw)
    except json.JSONDecodeError:
        return exc.reason or "unknown"
    if isinstance(payload, Mapping):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors)
    return exc.reason or "unknown"


__all__ = [
    "KmsConfigError",
    "UrllibVaultTransport",
    "VaultTransitKmsClient",
    "VaultTransport",
    "VaultTransportError",
    "build_kms_from_env",
]
