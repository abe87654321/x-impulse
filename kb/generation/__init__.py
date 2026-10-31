"""生成与引用绑定 —— 强制引用、逐句校验、证据快照。"""

from kb.generation.answer_service import (
    AnswerResult,
    AnswerService,
    EvidenceRetriever,
    PreparedStream,
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
from kb.generation.extractive import ExtractiveGenerator
from kb.generation.llm import (
    GenerationError,
    GeneratorConfigError,
    JsonTransport,
    LLMConfig,
    OpenAICompatibleGenerator,
    StreamTransport,
    build_generator_from_env,
)
from kb.generation.pipeline import AnswerPipeline

__all__ = [
    "DEFAULT_REFUSAL",
    "Answer",
    "AnswerPipeline",
    "AnswerResult",
    "AnswerService",
    "BoundSentence",
    "Citation",
    "CitationBinder",
    "EvidenceRetriever",
    "ExtractiveGenerator",
    "GenerationError",
    "GeneratorConfigError",
    "Generator",
    "JsonTransport",
    "LLMConfig",
    "OpenAICompatibleGenerator",
    "PreparedStream",
    "RetrievalServiceAdapter",
    "StreamEvent",
    "StreamTransport",
    "build_generator_from_env",
    "build_prompt",
    "split_sentences",
]
