from __future__ import annotations

from ...config import ProviderProfile
from .. import ProviderError
from .base import LLMTranslator, TranslateJob, Translator
from .llm import AnthropicTranslator, GeminiCompatTranslator, OpenAICompatTranslator
from .machine import GoogleTranslator, MicrosoftTranslator

_KINDS: dict[str, type[Translator]] = {
    "google": GoogleTranslator,
    "microsoft": MicrosoftTranslator,
    "openai": OpenAICompatTranslator,
    "gemini": GeminiCompatTranslator,
    "anthropic": AnthropicTranslator,
}

SUGGESTED_MODELS = {
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "deepseek-chat"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-flash-latest"],
    "anthropic": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
}


def create_translator(profile: ProviderProfile, model_override: str = "") -> Translator:
    cls = _KINDS.get(profile.kind)
    if cls is None:
        raise ProviderError(f"Loại provider dịch không hỗ trợ: {profile.kind}")
    return cls(profile, model_override)


__all__ = [
    "LLMTranslator",
    "SUGGESTED_MODELS",
    "TranslateJob",
    "Translator",
    "create_translator",
]
