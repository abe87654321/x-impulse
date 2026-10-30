"""PII 归一化与识别（开发版）—— FR-12-01 / FR-12-19。

只覆盖可被正则稳定识别的类型：手机号、18 位身份证、邮箱、16–19 位银行卡。
姓名等需要模型识别，不在本切片。

★ 识别召回率决定 T-1 删除完整性的上限（删除设计 §4.2）。这里不宣称完整。
归一化规则带版本号；改规则必须升版本并重建 subject_index。
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

PII_NORM_VERSION = "pii-norm-v1"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_BANK_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")

_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("email", _EMAIL_RE, 0.95),
    ("id_card_cn", _ID_CARD_RE, 0.9),
    ("phone_cn", _PHONE_RE, 0.9),
    ("bank_card", _BANK_CARD_RE, 0.8),
)


@dataclass(frozen=True)
class DetectedPII:
    subject_type: str
    value: str
    normalized: str
    confidence: float


def normalize_pii(subject_type: str, value: str) -> str:
    if subject_type == "email":
        return value.strip().lower()
    return re.sub(r"[\s-]+", "", value)


def detect_pii(text: str) -> list[DetectedPII]:
    """识别并去重；重叠匹配按“先长后短”保留。"""
    spans: list[tuple[int, int, DetectedPII]] = []
    for subject_type, pattern, confidence in _PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            spans.append(
                (
                    match.start(),
                    match.end(),
                    DetectedPII(
                        subject_type=subject_type,
                        value=value,
                        normalized=normalize_pii(subject_type, value),
                        confidence=confidence,
                    ),
                )
            )

    spans.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    seen: set[tuple[str, str]] = set()
    result: list[DetectedPII] = []
    covered_until = -1
    for start, end, detected in spans:
        if start < covered_until:
            continue
        covered_until = end
        key = (detected.subject_type, detected.normalized)
        if key in seen:
            continue
        seen.add(key)
        result.append(detected)
    return result


def subject_key(normalized: str, pepper: str) -> str:
    """HMAC-SHA256，不存明文（删除设计 §4.1）。"""
    return hmac.new(pepper.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()


__all__ = [
    "PII_NORM_VERSION",
    "DetectedPII",
    "detect_pii",
    "normalize_pii",
    "subject_key",
]
