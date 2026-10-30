"""语义切分与字符锚点 —— FR-02-05 / FR-02-06 / FR-02-09 / FR-02-13。

输入必须是 `norm-v1` 规范化正文（见 kb/parsing/normalize.py）。
每个 chunk 的 `content` 恒等于 `normalized_text[char_start:char_end]`，
这是 I-7 可验证性的最低要求。

本模块是确定性的纯函数：同一 (text, config) 必产出同一结果。
格式解析（PDF/OCR/表格）不在这里 —— 它负责把原始文档变成文本。
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from kb.parsing.normalize import hash_text

CHUNKER_VERSION = "chunk-v1"

TokenCounter = Callable[[str], int]

_SENTENCE_DELIMS = frozenset("。！？!?；;")
_HEADING_RE = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*$")
_CLAUSE_RE = re.compile(r"第[一二三四五六七八九十百千零〇两0-9]+条")


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x3000 <= cp <= 0x303F
        or 0xFF00 <= cp <= 0xFFEF
    )


def estimate_tokens(text: str) -> int:
    """确定性近似计数：CJK 字符按 1 token，其余按 4 字符 1 token。

    真实模型分词器由调用方注入（TokenCounter），本函数只做默认兜底，
    保证切分结果可复现，不依赖外部依赖。
    """
    cjk = 0
    other = 0
    for ch in text:
        if ch.isspace():
            continue
        if _is_cjk(ch):
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4


@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int = 400
    max_tokens: int = 600
    min_tokens: int = 80
    overlap_ratio: float = 0.15
    chunker_version: str = CHUNKER_VERSION

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("max_tokens 必须 > 0")
        if self.target_tokens <= 0:
            raise ValueError("target_tokens 必须 > 0")
        if self.min_tokens < 0:
            raise ValueError("min_tokens 必须 >= 0")
        if not (0 <= self.overlap_ratio < 1):
            raise ValueError("overlap_ratio 必须在 [0, 1)")
        if self.min_tokens > self.target_tokens:
            raise ValueError("min_tokens 不得大于 target_tokens")
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens 不得大于 max_tokens")


@dataclass(frozen=True)
class Chunk:
    seq: int
    content: str
    content_hash: str
    token_count: int
    char_start: int
    char_end: int
    page_no: int | None
    section_path: str | None
    clause_no: str | None
    chunker_version: str
    version: int = 1


def chunk_document(
    normalized_text: str,
    config: ChunkingConfig | None = None,
    *,
    token_counter: TokenCounter = estimate_tokens,
) -> list[Chunk]:
    """把规范化正文切成带字符锚点的 chunk。"""
    cfg = config or ChunkingConfig()
    text = normalized_text
    n = len(text)
    if n == 0 or text.strip() == "":
        return []

    boundaries = _candidate_boundaries(text)
    sections = _section_events(text)
    clauses = _clause_events(text)
    overlap_tokens = int(cfg.target_tokens * cfg.overlap_ratio)

    specs: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = _choose_end(text, start, n, cfg, token_counter, boundaries)
        if end <= start:
            end = n
        specs.append((start, end))
        if end >= n:
            break
        next_start = _overlap_start(text, start, end, overlap_tokens, token_counter)
        start = next_start if next_start > start else start + 1

    specs = _merge_short_tail(text, specs, cfg, token_counter)

    chunks: list[Chunk] = []
    for seq, (char_start, char_end) in enumerate(specs):
        content = text[char_start:char_end]
        chunks.append(
            Chunk(
                seq=seq,
                content=content,
                content_hash=hash_text(content),
                token_count=token_counter(content),
                char_start=char_start,
                char_end=char_end,
                page_no=None,
                section_path=_section_at(sections, char_start),
                clause_no=_clause_at(clauses, char_start),
                chunker_version=cfg.chunker_version,
            )
        )
    return chunks


# ── 边界 ────────────────────────────────────────────────────


def _candidate_boundaries(text: str) -> list[int]:
    """语义候选切点：段末、行末、句末。始终包含 0 与 len(text)。"""
    n = len(text)
    points = {0, n}
    for i, ch in enumerate(text):
        if ch == "\n":
            # 把连续换行（段落分隔）当作一个切点，避免切在 "\n\n" 中间。
            if i + 1 == n or text[i + 1] != "\n":
                points.add(i + 1)
        elif ch in _SENTENCE_DELIMS:
            points.add(i + 1)
        elif ch == "." and (i + 1 == n or text[i + 1] in " \t\n"):
            points.add(i + 1)
    return sorted(points)


def _choose_end(
    text: str,
    start: int,
    n: int,
    cfg: ChunkingConfig,
    counter: TokenCounter,
    boundaries: Sequence[int],
) -> int:
    idx = bisect.bisect_right(boundaries, start)
    best_under_max: int | None = None
    for end in boundaries[idx:]:
        tokens = counter(text[start:end])
        if tokens > cfg.max_tokens:
            break
        best_under_max = end
        if tokens >= cfg.target_tokens:
            return end

    if best_under_max is not None and counter(text[start:best_under_max]) >= cfg.min_tokens:
        return best_under_max

    return _hard_split(text, start, n, cfg.max_tokens, counter)


def _hard_split(text: str, start: int, n: int, max_tokens: int, counter: TokenCounter) -> int:
    """没有合适语义边界时，按 max_tokens 硬切（保底，不是首选）。"""
    lo, hi = start + 1, n
    best = start + 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if counter(text[start:mid]) <= max_tokens:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _overlap_start(
    text: str, start: int, end: int, overlap_tokens: int, counter: TokenCounter
) -> int:
    if overlap_tokens <= 0:
        return end
    lo, hi = start + 1, end
    best = end
    while lo <= hi:
        mid = (lo + hi) // 2
        if counter(text[mid:end]) <= overlap_tokens:
            best = mid
            hi = mid - 1
        else:
            lo = mid + 1
    return best


def _merge_short_tail(
    text: str,
    specs: list[tuple[int, int]],
    cfg: ChunkingConfig,
    counter: TokenCounter,
) -> list[tuple[int, int]]:
    if len(specs) < 2:
        return specs
    last_start, last_end = specs[-1]
    if counter(text[last_start:last_end]) >= cfg.min_tokens:
        return specs
    prev_start, _ = specs[-2]
    merged = text[prev_start:last_end]
    if counter(merged) > cfg.max_tokens:
        return specs
    return [*specs[:-2], (prev_start, last_end)]


# ── 结构锚点 ────────────────────────────────────────────────


def _section_events(text: str) -> list[tuple[int, str]]:
    """(标题行之后的位置, 章节路径)。用于 FR-02-07 / FR-02-09。"""
    events: list[tuple[int, str]] = []
    stack: list[tuple[int, str]] = []
    for match in _HEADING_RE.finditer(text):
        level = len(match.group(1))
        title = match.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        events.append((match.start(), " > ".join(t for _, t in stack)))
    return events


def _section_at(events: Sequence[tuple[int, str]], start: int) -> str | None:
    path: str | None = None
    for pos, value in events:
        if pos > start:
            break
        path = value
    return path


def _clause_events(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)) for m in _CLAUSE_RE.finditer(text)]


def _clause_at(events: Sequence[tuple[int, str]], start: int) -> str | None:
    if not events:
        return None
    positions = [pos for pos, _ in events]
    idx = bisect.bisect_right(positions, start) - 1
    return events[idx][1] if idx >= 0 else None


__all__ = [
    "CHUNKER_VERSION",
    "Chunk",
    "ChunkingConfig",
    "chunk_document",
    "estimate_tokens",
]
