"""审计 payload 的 PII 字段加密 —— 删除设计 §5.7 / §6。

审计日志不能硬删（防篡改 + 留存义务），因此 PII 用主体级 DEK 加密后存储：
密钥销毁后密文不可读，但记录结构、哈希链与留存义务均不受影响。

只加密识别出的 PII 片段，其余内容保持明文可审计。多个主体出现在同一
payload 时，每个片段各用其主体 DEK 加密 —— 不能整段多密钥封装，否则
销毁其中一个主体的密钥后，另一个密钥仍能解开整段。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Protocol

_TOKEN_RE = re.compile(r"\[enc:v1:([0-9a-f]{64}):([A-Za-z0-9+/=]+)\]")
_REDACTED = "[已加密擦除]"


class AuditPayloadError(Exception):
    """payload 含 PII 但未配置加密，或加密服务不可用。"""


class FieldKeyManager(Protocol):
    async def register(self, tenant_id: str, subject_key: str) -> None: ...

    async def encrypt(self, tenant_id: str, subject_key: str, plaintext: str) -> str: ...

    async def decrypt(self, tenant_id: str, subject_key: str, ciphertext: str) -> str: ...


async def protect_payload(
    payload: Mapping[str, object],
    *,
    tenant_id: str,
    key_manager: FieldKeyManager | None,
    subject_hasher,
) -> dict[str, object]:
    """返回可 JSON 序列化的副本；PII 片段替换为密文标记。"""
    if key_manager is None:
        if contains_pii(payload):
            raise AuditPayloadError("审计 payload 含 PII 但未配置加密，拒绝明文写入")
        return dict(payload)
    protected = await _protect_value(dict(payload), tenant_id, key_manager, subject_hasher)
    assert isinstance(protected, dict)
    return protected


async def open_payload(
    payload: Mapping[str, object],
    *,
    tenant_id: str,
    key_manager: FieldKeyManager,
) -> dict[str, object]:
    """审计读取用；已销毁密钥的位置显示 `[已加密擦除]`，不抛断整条记录。"""
    opened = await _open_value(dict(payload), tenant_id, key_manager)
    assert isinstance(opened, dict)
    return opened


def contains_pii(payload: Mapping[str, object]) -> bool:
    return _contains_pii(payload)


async def _protect_value(
    value: object,
    tenant_id: str,
    key_manager: FieldKeyManager,
    subject_hasher,
) -> object:
    if isinstance(value, str):
        return await _protect_text(value, tenant_id, key_manager, subject_hasher)
    if isinstance(value, Mapping):
        return {
            str(key): await _protect_value(item, tenant_id, key_manager, subject_hasher)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            await _protect_value(item, tenant_id, key_manager, subject_hasher) for item in value
        ]
    return value


async def _protect_text(
    text: str,
    tenant_id: str,
    key_manager: FieldKeyManager,
    subject_hasher,
) -> str:
    from kb.compliance.pii import detect_pii_spans

    spans = detect_pii_spans(text)
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end, detected in spans:
        parts.append(text[cursor:start])
        key = subject_hasher(detected.normalized)
        await key_manager.register(tenant_id, key)
        ciphertext = await key_manager.encrypt(tenant_id, key, detected.value)
        parts.append(f"[enc:v1:{key}:{ciphertext}]")
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


async def _open_value(value: object, tenant_id: str, key_manager: FieldKeyManager) -> object:
    if isinstance(value, str):
        return await _open_text(value, tenant_id, key_manager)
    if isinstance(value, Mapping):
        return {
            str(key): await _open_value(item, tenant_id, key_manager) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [await _open_value(item, tenant_id, key_manager) for item in value]
    return value


async def _open_text(text: str, tenant_id: str, key_manager: FieldKeyManager) -> str:
    if "[enc:v1:" not in text:
        return text
    result: list[str] = []
    cursor = 0
    for match in _TOKEN_RE.finditer(text):
        result.append(text[cursor : match.start()])
        key, ciphertext = match.group(1), match.group(2)
        try:
            result.append(await key_manager.decrypt(tenant_id, key, ciphertext))
        except Exception:  # noqa: BLE001
            result.append(_REDACTED)
        cursor = match.end()
    result.append(text[cursor:])
    return "".join(result)


def _contains_pii(value: object) -> bool:
    if isinstance(value, str):
        from kb.compliance.pii import detect_pii

        return bool(detect_pii(value))
    if isinstance(value, Mapping):
        return any(_contains_pii(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_pii(item) for item in value)
    return False


__all__ = [
    "AuditPayloadError",
    "FieldKeyManager",
    "contains_pii",
    "open_payload",
    "protect_payload",
]
