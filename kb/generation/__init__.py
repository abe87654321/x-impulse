"""生成与引用绑定 —— 强制引用、逐句校验、证据快照。"""

from kb.generation.answer_service import (
    AnswerService,
    EvidenceRetriever,
    RetrievalServiceAdapter,
)
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
from kb.generation.llm import (
    GenerationError,
    JsonTransport,
    LLMConfig,
    OpenAICompatibleGenerator,
)
from kb.generation.pipeline import AnswerPipeline

__all__ = [
    "DEFAULT_REFUSAL",
    "Answer",
    "AnswerPipeline",
    "AnswerService",
    "BoundSentence",
    "Citation",
    "CitationBinder",
    "EvidenceRetriever",
    "GenerationError",
    "Generator",
    "JsonTransport",
    "LLMConfig",
    "OpenAICompatibleGenerator",
    "RetrievalServiceAdapter",
    "StreamEvent",
    "build_prompt",
    "split_sentences",
]
