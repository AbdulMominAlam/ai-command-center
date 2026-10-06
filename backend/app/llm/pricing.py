"""Claude prices for cost estimates, in USD per million tokens: (input, output).

Standard API rates, without caching or batch discounts. Add a row when you
switch EXTRACT_MODEL or AGENT_MODEL to a model that isn't listed.
"""

import re

PRICES_PER_M = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-5-5": (4.0, 20.0),
}

# Dated ids such as claude-haiku-4-5-20251001 cost the same as their alias.
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated USD cost, or None if the model has no price in the table."""
    prices = PRICES_PER_M.get(_DATE_SUFFIX.sub("", model))
    if prices is None:
        return None
    input_price, output_price = prices
    return round(input_tokens / 1e6 * input_price + output_tokens / 1e6 * output_price, 6)
