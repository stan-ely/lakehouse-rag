"""LLM providers behind one interface, selected by configuration."""

from app.llm.base import LLMProvider
from app.llm.resilience import CircuitBreaker
from app.settings import Settings

DEFAULT_MODELS = {
    "fake": "fake-grounded",
    "anthropic": "claude-haiku-4-5",
    "bedrock": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}


def build_provider(settings: Settings) -> LLMProvider:
    provider = _build_provider(settings)
    if settings.llm_breaker_failures == 0:
        return provider
    return CircuitBreaker(
        provider,
        failure_threshold=settings.llm_breaker_failures,
        reset_seconds=settings.llm_breaker_reset_seconds,
    )


def _build_provider(settings: Settings) -> LLMProvider:
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
            model,
            region=settings.bedrock_region,
            timeout_seconds=settings.llm_timeout_seconds,
            endpoint_url=settings.bedrock_endpoint,
        )
    from app.llm.fake import FakeProvider

    return FakeProvider(model)
