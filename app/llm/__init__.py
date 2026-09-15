"""LLM providers behind one interface, selected by configuration."""

from app.llm.base import LLMProvider
from app.settings import Settings

DEFAULT_MODELS = {
    "fake": "fake-grounded",
    "anthropic": "claude-haiku-4-5",
    "bedrock": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}


def build_provider(settings: Settings) -> LLMProvider:
    model = settings.llm_model or DEFAULT_MODELS[settings.llm_provider]
    if settings.llm_provider == "anthropic":
        from app.llm.anthropic_api import AnthropicProvider

        if settings.anthropic_api_key is None:
            raise ValueError("RAG_ANTHROPIC_API_KEY is required for the anthropic provider")
        return AnthropicProvider(
            settings.anthropic_api_key.get_secret_value(),
            model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if settings.llm_provider == "bedrock":
        from app.llm.bedrock import BedrockProvider

        return BedrockProvider(
            model, region=settings.bedrock_region, timeout_seconds=settings.llm_timeout_seconds
        )
    from app.llm.fake import FakeProvider

    return FakeProvider(model)
