"""Per-request LLM cost in USD from reported token usage.

Claude prices are USD per million tokens from
https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-09-15). Bedrock bills
Anthropic models through AWS Marketplace at list price for global inference profiles; geo
profiles (`us.`, `eu.`, ...) carry the 10% regional premium that page documents for Claude 4.5
and later. Amazon's own models have one price per profile, so the premium is Anthropic-only.

Amazon Nova and open-weight (OpenAI gpt-oss, Qwen) prices come from the AWS Price List API
(`AmazonBedrock`, on-demand, checked 2026-09-17), which is the same source AWS bills from.
Flex and priority tiers are deliberately not modelled: the provider calls the standard tier.

Unknown models cost `None`, never a silent $0.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.llm.base import Usage

MILLION = Decimal(1_000_000)
REGIONAL_PREMIUM = Decimal("1.10")
_GEO_PREFIXES = ("us.", "eu.", "au.", "jp.", "apac.")


@dataclass(frozen=True)
class Price:
    input: Decimal
    output: Decimal
    cache_write: Decimal  # 5-minute cache writes
    cache_read: Decimal


PRICES: dict[str, Price] = {
    "claude-haiku-4-5": Price(Decimal("1"), Decimal("5"), Decimal("1.25"), Decimal("0.10")),
    "claude-sonnet-5": Price(Decimal("2"), Decimal("10"), Decimal("2.50"), Decimal("0.20")),
    "claude-opus-5": Price(Decimal("5"), Decimal("25"), Decimal("6.25"), Decimal("0.50")),
    "amazon.nova-pro": Price(Decimal("0.80"), Decimal("3.20"), Decimal(0), Decimal("0.20")),
    "amazon.nova-lite": Price(Decimal("0.06"), Decimal("0.24"), Decimal(0), Decimal("0.015")),
    "amazon.nova-micro": Price(Decimal("0.035"), Decimal("0.14"), Decimal(0), Decimal("0.009")),
    # Open-weight models on Bedrock, same Price List API source, us-west-2, checked 2026-09-17.
    # None of them publishes a prompt-cache dimension, so both cache prices are zero and
    # unreachable: the Converse call sends no cachePoint and usage reports no cached tokens.
    "openai.gpt-oss-20b": Price(Decimal("0.07"), Decimal("0.30"), Decimal(0), Decimal(0)),
    "openai.gpt-oss-120b": Price(Decimal("0.15"), Decimal("0.60"), Decimal(0), Decimal(0)),
    "qwen.qwen3-coder-30b-a3b": Price(Decimal("0.15"), Decimal("0.60"), Decimal(0), Decimal(0)),
    "qwen.qwen3-coder-480b-a35b": Price(Decimal("0.45"), Decimal("1.80"), Decimal(0), Decimal(0)),
    "qwen.qwen3-32b": Price(Decimal("0.15"), Decimal("0.60"), Decimal(0), Decimal(0)),
}


def canonical_model(model_id: str) -> str:
    """Strips Bedrock profile prefixes and version/date suffixes.

    `us.anthropic.claude-haiku-4-5-20251001-v1:0` and `claude-haiku-4-5-20251001` both map to
    `claude-haiku-4-5`.
    """
    name = model_id.lower()
    name = re.sub(r"^(global|us|eu|au|jp|apac)\.", "", name)
    name = name.removeprefix("anthropic.")
    # Anthropic and Amazon version ids carry the `v`: `-v1:0`. The open-weight ids on Bedrock
    # drop it (`openai.gpt-oss-120b-1:0`), so the bare form must end in `:0` to be stripped --
    # matching a trailing `-<digits>` unconditionally would turn `claude-opus-5` into
    # `claude-opus`.
    name = re.sub(r"-v?\d+:\d+$|-v\d+$", "", name)
    return re.sub(r"-\d{8}$", "", name)


def add_costs(*costs: Decimal | None) -> Decimal | None:
    """Total of several calls; unknown if any part is unknown, never undercounted as $0."""
    if any(cost is None for cost in costs):
        return None
    return sum((cost for cost in costs if cost is not None), Decimal(0))


def cost_usd(model_id: str, usage: Usage) -> Decimal | None:
    price = PRICES.get(canonical_model(model_id))
    if price is None:
        return None
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_write_tokens * price.cache_write
        + usage.cache_read_tokens * price.cache_read
    ) / MILLION
    if canonical_model(model_id).startswith("claude-") and model_id.lower().startswith(
        _GEO_PREFIXES
    ):
        total *= REGIONAL_PREMIUM
    return total.quantize(Decimal("0.00000001"))
