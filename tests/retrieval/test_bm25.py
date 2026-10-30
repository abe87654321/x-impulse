"""BM25 词法腿的排序性质。"""

from __future__ import annotations

from kb.retrieval.bm25 import rank, tokenize


def test_tokenize_splits_cjk_and_latin():
    assert tokenize("报销 Standard 123") == ["报", "销", "standard", "123"]


def test_ranks_by_term_frequency():
    docs = [
        ("d1", "苹果 苹果 香蕉"),
        ("d2", "香蕉"),
        ("d3", "橙子"),
    ]

    ranked = rank("苹果 香蕉", docs, limit=10)

    assert [doc_id for doc_id, _ in ranked] == ["d1", "d2"]
    assert ranked[0][1] > ranked[1][1]


def test_no_match_returns_empty():
    assert rank("葡萄", [("d1", "苹果 香蕉")], limit=10) == []


def test_length_normalization_penalizes_longer_documents():
    docs = [
        ("short", "苹果"),
        ("long", "苹果 " + "填充 " * 50),
    ]

    ranked = rank("苹果", docs, limit=10)

    assert ranked[0][0] == "short"
    assert ranked[0][1] > ranked[1][1]


def test_empty_query_or_documents():
    assert rank("", [("d1", "苹果")], limit=10) == []
    assert rank("苹果", [], limit=10) == []
    assert rank("苹果", [("d1", "苹果")], limit=0) == []
