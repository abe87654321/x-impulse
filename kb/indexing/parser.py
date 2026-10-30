"""纯文本解析器 —— 先把 TXT/Markdown 这类源接通。

PDF/Word/OCR/表格抽取属于 FR-02-01~04，需要 ml 依赖，不在本切片。
非文本格式显式抛 PermanentError，落入解析质量报告，不静默丢弃。
"""

from __future__ import annotations

from typing import Protocol

from kb.ingestion.contract import PermanentError, RawDocument

TEXT_PARSER_VERSION = "text-v1"

_TEXT_MIME_PREFIX = "text/"
_TEXT_MIME_EXACT = frozenset(
    {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
    }
)


class Parser(Protocol):
    version: str

    def parse(self, raw: RawDocument) -> str: ...


class TextParser:
    version = TEXT_PARSER_VERSION

    def parse(self, raw: RawDocument) -> str:
        if not self._looks_textual(raw.mime_type):
            raise PermanentError(f"暂不支持的格式：{raw.mime_type}")
        try:
            return raw.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PermanentError(f"文本解码失败：{exc}") from exc

    @staticmethod
    def _looks_textual(mime_type: str) -> bool:
        base = mime_type.split(";", 1)[0].strip().lower()
        return base.startswith(_TEXT_MIME_PREFIX) or base in _TEXT_MIME_EXACT
