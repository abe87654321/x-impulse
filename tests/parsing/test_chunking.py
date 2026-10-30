"""切分与字符锚点测试 —— FR-02-05/06/09/13 与 I-7。

核心断言：chunk.content 必须恒等于 normalized_text[char_start:char_end]。
这条不成立，则 citation 的字符偏移量全部不可信。
"""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kb.parsing.chunking import (
    CHUNKER_VERSION,
    ChunkingConfig,
    chunk_document,
    estimate_tokens,
)
from kb.parsing.normalize import normalize


def _paragraphs(n: int = 6) -> str:
    return "\n\n".join(
        f"第{i}段。" + "这是用于测试语义切分与字符锚点的正文内容，" * 18 for i in range(1, n + 1)
    )


def test_empty_and_whitespace_only_produce_no_chunks():
    assert chunk_document("") == []
    assert chunk_document("   \n\n\t ") == []


def test_spans_and_hashes_are_exact():
    text = _paragraphs()
    cfg = ChunkingConfig(target_tokens=120, max_tokens=180, min_tokens=30, overlap_ratio=0.1)
    chunks = chunk_document(text, cfg)

    assert chunks
    for i, c in enumerate(chunks):
        assert c.seq == i
        assert c.content == text[c.char_start : c.char_end]
        assert c.content_hash == hashlib.sha256(c.content.encode("utf-8")).hexdigest()
        assert c.token_count == estimate_tokens(c.content)
        assert c.char_end > c.char_start
        assert c.token_count <= cfg.max_tokens
        assert c.chunker_version == CHUNKER_VERSION


def test_spans_cover_the_document_in_order():
    text = _paragraphs()
    cfg = ChunkingConfig(target_tokens=100, max_tokens=160, min_tokens=20, overlap_ratio=0.0)
    chunks = chunk_document(text, cfg)

    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(text)
    for prev, cur in zip(chunks, chunks[1:], strict=False):
        assert prev.char_end <= cur.char_start


def test_overlap_when_configured():
    text = _paragraphs()
    cfg = ChunkingConfig(target_tokens=100, max_tokens=160, min_tokens=20, overlap_ratio=0.2)
    chunks = chunk_document(text, cfg)

    assert any(
        cur.char_start < prev.char_end for prev, cur in zip(chunks, chunks[1:], strict=False)
    )


def test_is_deterministic():
    text = _paragraphs()
    cfg = ChunkingConfig(target_tokens=90, max_tokens=140, min_tokens=20, overlap_ratio=0.1)
    assert chunk_document(text, cfg) == chunk_document(text, cfg)


def test_prefers_semantic_boundaries():
    # 每段约 40 token；目标 80 应尽量在段末（\n\n）结束。
    paras = ["甲" * 40, "乙" * 40, "丙" * 40, "丁" * 40]
    text = "\n\n".join(paras)
    cfg = ChunkingConfig(target_tokens=80, max_tokens=120, min_tokens=10, overlap_ratio=0.0)
    chunks = chunk_document(text, cfg)

    for c in chunks[:-1]:
        assert text[c.char_end - 2 : c.char_end] == "\n\n" or text[
            c.char_start : c.char_end
        ].endswith("。")


def test_hard_split_respects_max_tokens():
    # 单个超长无标点块，只能硬切。
    text = "甲" * 1000
    cfg = ChunkingConfig(target_tokens=100, max_tokens=120, min_tokens=10, overlap_ratio=0.0)
    chunks = chunk_document(text, cfg)

    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= cfg.max_tokens
        assert c.content == text[c.char_start : c.char_end]
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(text)


def test_section_path_and_clause_anchor():
    text = (
        "# 员工手册\n\n"
        "## 报销制度\n\n"
        "第一条 " + "员工应如实填写报销单。" * 8 + "\n\n"
        "第二条 " + "报销需在三十日内提交。" * 8
    )
    cfg = ChunkingConfig(target_tokens=20, max_tokens=30, min_tokens=5, overlap_ratio=0.0)
    chunks = chunk_document(text, cfg)

    assert chunks[0].section_path == "员工手册"
    h2 = text.index("## 报销制度")
    later = [c for c in chunks if c.char_start > h2]
    assert later
    assert all(c.section_path == "员工手册 > 报销制度" for c in later)
    assert chunks[-1].clause_no == "第二条"


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=600))
def test_property_spans_are_always_exact(raw):
    """I-7：任意输入下，chunk.content 必须等于规范化正文的字符切片。"""
    text = normalize(raw)
    cfg = ChunkingConfig(target_tokens=50, max_tokens=80, min_tokens=10, overlap_ratio=0.1)
    chunks = chunk_document(text, cfg)

    if text.strip() == "":
        assert chunks == []
        return

    assert chunks
    for c in chunks:
        assert c.content == text[c.char_start : c.char_end]
        assert c.char_end > c.char_start
        assert c.token_count <= cfg.max_tokens
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(text)


def test_config_validation():
    with pytest.raises(ValueError):
        ChunkingConfig(target_tokens=100, max_tokens=50)
    with pytest.raises(ValueError):
        ChunkingConfig(min_tokens=500, target_tokens=100, max_tokens=200)
    with pytest.raises(ValueError):
        ChunkingConfig(overlap_ratio=1.0)
