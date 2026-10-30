"""生成与引用绑定 —— 强制引用、逐句校验、证据快照。"""

from kb.generation.citations import (
    DEFAULT_REFUSAL,
    Answer,
    BoundSentence,
    Citation,
    CitationBinder,
    Generator,
    StreamEvent,
    build_prompt,
    split_sentences,
)
from kb.generation.pipeline import AnswerPipeline

__all__ = [
    "DEFAULT_REFUSAL",
    "Answer",
    "AnswerPipeline",
    "BoundSentence",
    "Citation",
    "CitationBinder",
    "Generator",
    "StreamEvent",
    "build_prompt",
    "split_sentences",
]
