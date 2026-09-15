"""Per-request LLM cost in USD from reported token usage.

Prices are USD per million tokens from https://platform.claude.com/docs/en/about-claude/pricing
(checked 2026-09-15). Bedrock bills Anthropic models through AWS Marketplace at list price for
global inference profiles; geo profiles (`us.`, `eu.`, ...) carry the 10% regional premium that
page documents for Claude 4.5 and later. Unknown models cost `None`, never a silent $0.
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
}


def canonical_model(model_id: str) -> str:
    """Strips Bedrock profile prefixes and version/date suffixes.

    `us.anthropic.claude-haiku-4-5-20251001-v1:0` and `claude-haiku-4-5-20251001` both map to
    `claude-haiku-4-5`.
    """
    name = model_id.lower()
    name = re.sub(r"^(global|us|eu|au|jp|apac)\.", "", name)
    name = name.removeprefix("anthropic.")
    name = re.sub(r"-v\d+(:\d+)?$", "", name)
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
    if model_id.lower().startswith(_GEO_PREFIXES):
        total *= REGIONAL_PREMIUM
    return total.quantize(Decimal("0.00000001"))
