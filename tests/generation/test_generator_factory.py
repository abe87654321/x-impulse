"""生成后端工厂 —— 显式选择，缺失即报错，禁止静默降级。"""

from __future__ import annotations

import pytest

from kb.generation.extractive import ExtractiveGenerator
from kb.generation.llm import (
    GeneratorConfigError,
    OpenAICompatibleGenerator,
    build_generator_from_env,
)


def test_factory_requires_explicit_provider():
    with pytest.raises(GeneratorConfigError):
        build_generator_from_env({})


def test_factory_builds_extractive():
    generator = build_generator_from_env({"KB_GENERATOR": "extractive"})

    assert isinstance(generator, ExtractiveGenerator)


def test_factory_builds_openai_compatible():
    generator = build_generator_from_env(
        {
            "KB_GENERATOR": "openai",
            "KB_LLM_BASE_URL": "http://llm.local/v1",
            "KB_LLM_MODEL": "qwen",
        }
    )

    assert isinstance(generator, OpenAICompatibleGenerator)


def test_factory_openai_requires_base_url():
    with pytest.raises(GeneratorConfigError):
        build_generator_from_env({"KB_GENERATOR": "openai"})


def test_factory_rejects_invalid_timeout():
    with pytest.raises(GeneratorConfigError):
        build_generator_from_env(
            {
                "KB_GENERATOR": "openai",
                "KB_LLM_BASE_URL": "http://llm.local/v1",
                "KB_LLM_TIMEOUT_SEC": "0",
            }
        )


def test_factory_rejects_unknown_provider():
    with pytest.raises(GeneratorConfigError):
        build_generator_from_env({"KB_GENERATOR": "magic"})
