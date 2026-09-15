from decimal import Decimal
from typing import Any

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from pydantic import SecretStr

from app.generation.prompt import build_prompt
from app.llm import build_provider
from app.llm.base import LLMError, LLMTimeout, Usage
from app.llm.bedrock import BedrockProvider
from app.llm.cost import canonical_model, cost_usd
from app.llm.fake import FakeProvider
from app.settings import Settings
from tests.unit.factories import make_chunk

HAIKU_US = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        (HAIKU_US, "claude-haiku-4-5"),
        ("global.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5"),
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("us.anthropic.claude-sonnet-5", "claude-sonnet-5"),
        ("claude-opus-5", "claude-opus-5"),
    ],
)
def test_canonical_model(model_id: str, expected: str) -> None:
    assert canonical_model(model_id) == expected


def test_first_party_cost_uses_list_prices() -> None:
    assert cost_usd("claude-sonnet-5", Usage(1_000_000, 100_000)) == Decimal("3")


def test_bedrock_geo_profile_carries_regional_premium() -> None:
    assert cost_usd(HAIKU_US, Usage(1_000_000, 0)) == Decimal("1.1")
    assert cost_usd("global.anthropic.claude-haiku-4-5-20251001-v1:0", Usage(1_000_000)) == 1


def test_cache_tokens_are_priced_separately() -> None:
    usage = Usage(cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    assert cost_usd("claude-haiku-4-5", usage) == Decimal("1.35")


def test_unknown_model_has_unknown_cost_not_zero() -> None:
    assert cost_usd("fake-grounded", Usage(10, 10)) is None


class StubBedrock:
    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.kwargs: dict[str, Any] = {}

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


def test_bedrock_provider_parses_converse_output_and_usage() -> None:
    stub = StubBedrock(
        {
            "output": {"message": {"content": [{"text": "Refunds take 5 days "}, {"text": "[1]"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 10, "outputTokens": 3, "cacheReadInputTokens": 4},
        }
    )

    completion = BedrockProvider(HAIKU_US, client=stub).complete(
        system="rules", prompt="question", max_tokens=5
    )

    assert completion.text == "Refunds take 5 days [1]"
    assert completion.usage == Usage(10, 3, 4, 0)
    assert stub.kwargs["modelId"] == HAIKU_US
    assert stub.kwargs["system"] == [{"text": "rules"}]
    assert stub.kwargs["inferenceConfig"] == {"maxTokens": 5}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse"), LLMError),
        (ReadTimeoutError(endpoint_url="https://bedrock"), LLMTimeout),
    ],
)
def test_bedrock_errors_map_to_provider_errors(error: Exception, expected: type[Exception]) -> None:
    provider = BedrockProvider(HAIKU_US, client=StubBedrock(error=error))
    with pytest.raises(expected):
        provider.complete(system="s", prompt="p", max_tokens=5)


def test_fake_provider_answers_from_and_cites_the_first_source() -> None:
    prompt = build_prompt("How long do refunds take?", [make_chunk(3), make_chunk(4)])

    completion = FakeProvider().complete(system="s", prompt=prompt, max_tokens=200)

    assert "Refunds post within 3 days." in completion.text
    assert completion.text.endswith("[1]")


def test_fake_provider_refuses_without_sources() -> None:
    completion = FakeProvider().complete(system="s", prompt="<sources>\n</sources>", max_tokens=50)
    assert completion.text == "INSUFFICIENT_EVIDENCE"


def test_build_provider_uses_per_provider_default_models() -> None:
    assert build_provider(Settings(llm_provider="fake")).model == "fake-grounded"
    assert build_provider(Settings(llm_provider="bedrock")).model == HAIKU_US
    anthropic = build_provider(
        Settings(llm_provider="anthropic", anthropic_api_key=SecretStr("sk-test"))
    )
    assert anthropic.model == "claude-haiku-4-5"


def test_anthropic_provider_requires_a_key() -> None:
    with pytest.raises(ValueError, match="API_KEY"):
        build_provider(Settings(llm_provider="anthropic", anthropic_api_key=None))
