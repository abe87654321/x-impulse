"""norm-v1 规则集测试 —— TD-01 §2·5.1。

这些规则决定 I-7 字符偏移量的基准，改动会静默改变全部历史引用。
"""

from __future__ import annotations

from kb.parsing.normalize import (
    NORMALIZATION_VERSION,
    content_hash,
    normalize,
)


def test_version_is_the_literal_norm_v1():
    assert NORMALIZATION_VERSION == "norm-v1"


def test_uses_nfc_not_nfkc():
    # 全角字符在 NFC 下保持原形；NFKC 会折成半角并改变字符数。
    fullwidth = "ＡＢ１"
    assert normalize(fullwidth) == fullwidth

    # NFC 仍会合并组合字符序列（这是 NFC 的职责，不是 NFKC）。
    assert normalize("e\u0301") == "\u00e9"


def test_normalizes_line_endings_to_lf():
    assert normalize("a\r\nb\rc\nd") == "a\nb\nc\nd"


def test_removes_zero_width_and_invisible_controls():
    raw = "a\u200bb\u200cc\u200dd\ufeffe\u00adf\x00g\x0bh\x0ci\x1fj"
    assert normalize(raw) == "abcdefghij"


def test_keeps_tab_and_lf():
    assert normalize("a\tb\nc") == "a\tb\nc"


def test_does_not_fold_whitespace():
    raw = "a  b\t\tc\n\n\nd"
    assert normalize(raw) == raw


def test_is_idempotent():
    raw = "Ａ\r\nB\u200b  C\ufeff"
    once = normalize(raw)
    assert normalize(once) == once


def test_content_hash_is_sha256_of_normalized_text():
    text = normalize("a  b")
    assert content_hash(text) == content_hash("a  b")
    assert len(content_hash(text)) == 64
