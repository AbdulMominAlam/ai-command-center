"""Claude prices for cost estimates, in USD per million tokens.

Standard API rates, without batch discounts. Add a row when you switch
EXTRACT_MODEL or AGENT_MODEL to a model that isn't listed.

Prompt caching: input_tokens from the API is only the uncached part of the
prompt. Tokens written to the cache cost 1.25x the input price (5-minute
cache) and tokens read from it cost the model's cache read price.
"""

import re
from typing import NamedTuple

# model: (input, output, cache read)
PRICES_PER_M = {
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
}
CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute cache, the default

# Dated ids such as claude-haiku-4-5-20251001 cost the same as their alias.
_DATE_SUFFIX = re.compile(r"-\d{8}$")


class Usage(NamedTuple):
    """Token counts of one or more Claude calls."""

    input_tokens: int = 0  # uncached input only
    output_tokens: int = 0
    cache_creation_tokens: int = 0  # written to the cache
    cache_read_tokens: int = 0  # served from the cache

    @classmethod
    def of(cls, usage) -> "Usage":
        """From an API response's usage (cache fields can be missing or None)."""
        return cls(usage.input_tokens, usage.output_tokens,
                   getattr(usage, "cache_creation_input_tokens", None) or 0,
                   getattr(usage, "cache_read_input_tokens", None) or 0)

    def plus(self, other: "Usage") -> "Usage":
        return Usage(*(a + b for a, b in zip(self, other)))


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_creation_tokens: int = 0, cache_read_tokens: int = 0) -> float | None:
    """Estimated USD cost, or None if the model has no price in the table.
    Takes a Usage too: estimate_cost(model, *usage)."""
    prices = PRICES_PER_M.get(_DATE_SUFFIX.sub("", model))
    if prices is None:
        return None
    input_price, output_price, read_price = prices
    total = (input_tokens * input_price
             + cache_creation_tokens * input_price * CACHE_WRITE_MULTIPLIER
             + cache_read_tokens * read_price
             + output_tokens * output_price)
    return round(total / 1e6, 6)
