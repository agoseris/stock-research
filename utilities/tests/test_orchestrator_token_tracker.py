"""
Tests for utilities/orchestrator/token_tracker.py — Stage 3.
"""

import pytest


def _make_usage(input_tokens, output_tokens, model):
    return {"input": input_tokens, "output": output_tokens, "model": model}


# ===========================================================================
# Group 1: aggregate_token_usage
# ===========================================================================

class TestAggregateTokenUsage:

    def test_all_none_returns_zeros(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        result = aggregate_token_usage()
        assert result["total_input"] == 0
        assert result["total_output"] == 0
        assert result["estimated_cost_gbp"] == 0.0

    def test_sums_all_four_layers(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        result = aggregate_token_usage(
            layer1_usage=_make_usage(100, 200, "claude-haiku-4-5-20251001"),
            layer2_usage=_make_usage(150, 250, "claude-haiku-4-5-20251001"),
            layer3_usage=_make_usage(200, 300, "claude-sonnet-4-6"),
            synthesis_usage=_make_usage(400, 500, "claude-sonnet-4-6"),
        )
        assert result["total_input"] == 850
        assert result["total_output"] == 1250

    def test_per_layer_fields_present(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        result = aggregate_token_usage(
            layer1_usage=_make_usage(10, 20, "claude-haiku-4-5-20251001"),
        )
        for key in [
            "layer1_input", "layer1_output",
            "layer2_input", "layer2_output",
            "layer3_input", "layer3_output",
            "synthesis_input", "synthesis_output",
        ]:
            assert key in result

    def test_none_layers_contribute_zero(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        result = aggregate_token_usage(
            layer1_usage=_make_usage(100, 200, "claude-haiku-4-5-20251001"),
            layer2_usage=None,
            layer3_usage=None,
        )
        assert result["layer2_input"] == 0
        assert result["layer2_output"] == 0
        assert result["layer3_input"] == 0

    def test_per_layer_token_counts_correct(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        result = aggregate_token_usage(
            layer1_usage=_make_usage(111, 222, "claude-haiku-4-5-20251001"),
            layer3_usage=_make_usage(333, 444, "claude-sonnet-4-6"),
        )
        assert result["layer1_input"] == 111
        assert result["layer1_output"] == 222
        assert result["layer3_input"] == 333
        assert result["layer3_output"] == 444

    def test_estimated_cost_is_float(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        result = aggregate_token_usage(
            layer1_usage=_make_usage(1000, 500, "claude-haiku-4-5-20251001"),
        )
        assert isinstance(result["estimated_cost_gbp"], float)

    def test_haiku_cheaper_than_sonnet(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        haiku_result = aggregate_token_usage(
            layer1_usage=_make_usage(1000, 1000, "claude-haiku-4-5-20251001"),
        )
        sonnet_result = aggregate_token_usage(
            layer1_usage=_make_usage(1000, 1000, "claude-sonnet-4-6"),
        )
        assert haiku_result["estimated_cost_gbp"] < sonnet_result["estimated_cost_gbp"]

    def test_cost_rounded_to_4dp(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        result = aggregate_token_usage(
            layer1_usage=_make_usage(100, 100, "claude-haiku-4-5-20251001"),
        )
        cost = result["estimated_cost_gbp"]
        assert cost == round(cost, 4)

    def test_unknown_model_uses_sonnet_pricing(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        result_unknown = aggregate_token_usage(
            layer1_usage=_make_usage(1000, 1000, "claude-unknown-model"),
        )
        result_sonnet = aggregate_token_usage(
            layer1_usage=_make_usage(1000, 1000, "claude-sonnet-4-6"),
        )
        assert result_unknown["estimated_cost_gbp"] == result_sonnet["estimated_cost_gbp"]

    def test_synthesis_tokens_included_in_total(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        result = aggregate_token_usage(
            synthesis_usage=_make_usage(500, 300, "claude-sonnet-4-6"),
        )
        assert result["total_input"] == 500
        assert result["total_output"] == 300
        assert result["synthesis_input"] == 500
        assert result["synthesis_output"] == 300

    def test_custom_config_pricing_used(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        custom_config = {
            "haiku_input_cost_per_1m_tokens": 1.0,
            "haiku_output_cost_per_1m_tokens": 2.0,
            "sonnet_input_cost_per_1m_tokens": 3.0,
            "sonnet_output_cost_per_1m_tokens": 6.0,
        }
        result = aggregate_token_usage(
            layer1_usage=_make_usage(1_000_000, 0, "claude-haiku-4-5-20251001"),
            config=custom_config,
        )
        # 1M input tokens at £1.0/1M = £1.0
        assert result["estimated_cost_gbp"] == 1.0

    def test_empty_usage_dict_treated_as_zero(self):
        from utilities.orchestrator.token_tracker import aggregate_token_usage
        result = aggregate_token_usage(layer1_usage={})
        assert result["layer1_input"] == 0
        assert result["layer1_output"] == 0


# ===========================================================================
# Group 2: _layer_cost_gbp (internal helper)
# ===========================================================================

class TestLayerCostGbp:

    def test_haiku_model_uses_haiku_rate(self):
        from utilities.orchestrator.token_tracker import _layer_cost_gbp
        from utilities.orchestrator.config import ORCHESTRATOR_CONFIG
        # 1M haiku input tokens
        usage = {"input": 1_000_000, "output": 0, "model": "claude-haiku-4-5-20251001"}
        cost = _layer_cost_gbp(usage, ORCHESTRATOR_CONFIG)
        expected = ORCHESTRATOR_CONFIG["haiku_input_cost_per_1m_tokens"]
        assert abs(cost - expected) < 0.0001

    def test_none_usage_returns_zero(self):
        from utilities.orchestrator.token_tracker import _layer_cost_gbp
        assert _layer_cost_gbp(None, {}) == 0.0

    def test_zero_tokens_returns_zero(self):
        from utilities.orchestrator.token_tracker import _layer_cost_gbp
        usage = {"input": 0, "output": 0, "model": "claude-sonnet-4-6"}
        assert _layer_cost_gbp(usage, {}) == 0.0
