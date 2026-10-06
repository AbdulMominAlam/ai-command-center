import pytest

from app.llm.pricing import estimate_cost


def test_known_model():
    # 1M input at $1 + 200k output at $5
    assert estimate_cost("claude-haiku-4-5", 1_000_000, 200_000) == 2.0


def test_dated_id_uses_the_alias_price():
    assert estimate_cost("claude-haiku-4-5-20251001", 3213, 253) == estimate_cost("claude-haiku-4-5", 3213, 253)


def test_models_have_different_prices():
    assert estimate_cost("claude-opus-5-5", 1_000_000, 0) == 4.0
    assert estimate_cost("claude-sonnet-5-5", 0, 1_000_000) == 10.0


@pytest.mark.parametrize("model", ["claude-some-future-model", "", "gpt-4o", "claude-haiku-4-5-preview"])
def test_unknown_model_returns_none(model):
    assert estimate_cost(model, 1000, 1000) is None
