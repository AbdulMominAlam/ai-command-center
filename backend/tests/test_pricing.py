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


def test_cache_writes_and_reads():
    # Haiku 4.5: writes at 1.25 x $1, reads at $0.10 per million tokens
    assert estimate_cost("claude-haiku-4-5", 0, 0, cache_creation_tokens=1_000_000) == 1.25
    assert estimate_cost("claude-haiku-4-5", 0, 0, cache_read_tokens=1_000_000) == 0.1
    assert estimate_cost("claude-opus-5-5", 0, 0, cache_read_tokens=1_000_000) == 0.2
    # a typical cached extraction: 300 new input, 4000 read, 150 output
    assert estimate_cost("claude-haiku-4-5", 300, 150, 0, 4000) == pytest.approx(0.00145)


def test_usage_adds_up_and_reads_api_usage():
    from types import SimpleNamespace

    from app.llm.pricing import Usage
    u = Usage.of(SimpleNamespace(input_tokens=10, output_tokens=2,
                                 cache_creation_input_tokens=None, cache_read_input_tokens=4000))
    assert u == Usage(10, 2, 0, 4000)
    assert u.plus(Usage(1, 1, 5, 0)) == Usage(11, 3, 5, 4000)
    assert Usage.of(SimpleNamespace(input_tokens=1, output_tokens=1)) == Usage(1, 1, 0, 0)
    assert estimate_cost("claude-haiku-4-5", *u) == estimate_cost("claude-haiku-4-5", 10, 2, 0, 4000)
