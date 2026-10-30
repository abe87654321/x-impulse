"""规范化正文 `norm-v1` —— I-7 字符偏移量的唯一基准。

对应 docs/td-01-database-schema.md §2·5.1。规则按顺序执行且必须幂等。

★ 禁止 NFKC：它会把全角折成半角、折叠兼容字符，直接改变中文文档的字符数，
  使同一份文档在不同实现下产出完全不同的偏移量。
"""

from __future__ import annotations

import hashlib
import unicodedata

NORMALIZATION_VERSION = "norm-v1"

# N-3：零宽与不可见控制字符。保留 U+0009 TAB 与 U+000A LF。
_INVISIBLE = frozenset(
    {
        "\u200b",  # ZWSP
        "\u200c",  # ZWNJ
        "\u200d",  # ZWJ
        "\ufeff",  # BOM
        "\u00ad",  # SOFT HYPHEN
        *(chr(cp) for cp in range(0x00, 0x09)),
        "\u000b",
        "\u000c",
        *(chr(cp) for cp in range(0x0E, 0x20)),
    }
)


def normalize(raw: str) -> str:
    """把解析器输出规范化为 `norm-v1` 正文。幂等。"""
    # N-1 Unicode 规范化：NFC（不是 NFKC）。
    text = unicodedata.normalize("NFC", raw)

    # N-2 换行统一为 LF。
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # N-3 删除零宽与不可见控制字符。
    text = "".join(ch for ch in text if ch not in _INVISIBLE)

    # N-4 不折叠空白；N-5 不做任何内容性删除。
    return text


def content_hash(normalized_text: str) -> str:
    """R-N2：documents.content_hash = SHA-256(规范化正文)，UTF-8 编码。"""
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


def hash_text(text: str) -> str:
    """通用 SHA-256（UTF-8），用于 chunk 的内容哈希。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
