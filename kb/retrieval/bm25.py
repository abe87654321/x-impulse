"""BM25 词法打分 —— FR-04-04 的稀疏腿。

当前实现按查询在应用层对候选语料打分（候选集已按权限/受众/元数据在 SQL
里收敛）。中文按单字 token，英文按词。这对开发规模足够，但全量扫描不适合
百万级语料；上规模时应改为倒排表 / tsvector + 中文分词。

★ 排序权重仍需在客户评测集上标定（FR-04-05）。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

_LATIN_RE = re.compile(r"[a-zA-Z0-9_]+")


def tokenize(text: str) -> list[str]:
    """CJK 逐字 + 拉丁词；统一小写。"""
    tokens: list[str] = []
    for char in text:
        if _is_cjk(char):
            tokens.append(char)
    tokens.extend(match.group(0).lower() for match in _LATIN_RE.finditer(text))
    return tokens


@dataclass(frozen=True)
class Bm25Config:
    k1: float = 1.5
    b: float = 0.75


def rank(
    query: str,
    documents: Sequence[tuple[str, str]],
    *,
    limit: int,
    config: Bm25Config | None = None,
) -> list[tuple[str, float]]:
    """返回 (doc_id, score) 按分数降序，只保留正分。"""
    cfg = config or Bm25Config()
    query_terms = list(dict.fromkeys(tokenize(query)))
    if not query_terms or not documents or limit <= 0:
        return []

    tokenized = [(doc_id, tokenize(text)) for doc_id, text in documents]
    total = len(tokenized)
    avg_len = sum(len(tokens) for _, tokens in tokenized) / total if total else 0.0
    if avg_len == 0:
        return []

    document_frequency: Counter[str] = Counter()
    for _, tokens in tokenized:
        document_frequency.update(set(tokens))

    idf = {
        term: math.log(
            1.0 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
        )
        for term in query_terms
    }

    scored: list[tuple[str, float]] = []
    for doc_id, tokens in tokenized:
        length = len(tokens)
        frequencies = Counter(tokens)
        score = 0.0
        for term in query_terms:
            tf = frequencies.get(term, 0)
            if tf == 0:
                continue
            denominator = tf + cfg.k1 * (1.0 - cfg.b + cfg.b * length / avg_len)
            score += idf[term] * (tf * (cfg.k1 + 1.0)) / denominator
        if score > 0:
            scored.append((doc_id, score))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def _is_cjk(char: str) -> bool:
    cp = ord(char)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x3000 <= cp <= 0x303F
        or 0xFF00 <= cp <= 0xFFEF
    )


__all__ = ["Bm25Config", "rank", "tokenize"]
