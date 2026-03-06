"""Tests for utilities/orchestrator/config.py"""

import pytest
from utilities.orchestrator.config import (
    ORCHESTRATOR_CONFIG,
    ANNOUNCEMENT_TTL_DAYS,
    PDMR_TRANSACTION_TTL_DAYS,
    COMPANY_NEWS_SUMMARY_TTL_DAYS,
    SIGNAL_TTL_DAYS,
)


# ---------------------------------------------------------------------------
# Required keys
# ---------------------------------------------------------------------------

_REQUIRED_MODEL_KEYS = [
    "classification_model",
    "simple_lens_model",
    "layer1_model",
    "layer2_model_standard",
    "layer2_model_credibility",
    "layer3_model",
    "synthesis_model",
]

_REQUIRED_NUMERIC_KEYS = [
    "layer_timeout_seconds",
    "tool_call_timeout_seconds",
    "yfinance_delay_seconds",
    "ch_api_delay_seconds",
    "credibility_holding_pct_threshold",
    "cluster_detection_window_days",
    "insider_activity_lookback_days",
    "haiku_input_cost_per_1m_tokens",
    "haiku_output_cost_per_1m_tokens",
    "sonnet_input_cost_per_1m_tokens",
    "sonnet_output_cost_per_1m_tokens",
]


def test_all_model_keys_present():
    for key in _REQUIRED_MODEL_KEYS:
        assert key in ORCHESTRATOR_CONFIG, f"Missing config key: {key}"


def test_all_numeric_keys_present():
    for key in _REQUIRED_NUMERIC_KEYS:
        assert key in ORCHESTRATOR_CONFIG, f"Missing config key: {key}"


def test_model_ids_are_non_empty_strings():
    for key in _REQUIRED_MODEL_KEYS:
        val = ORCHESTRATOR_CONFIG[key]
        assert isinstance(val, str) and val, f"{key} must be a non-empty string"


def test_numeric_values_are_positive():
    for key in _REQUIRED_NUMERIC_KEYS:
        val = ORCHESTRATOR_CONFIG[key]
        assert isinstance(val, (int, float)), f"{key} must be numeric"
        assert val > 0, f"{key} must be positive"


def test_sonnet_model_used_for_classification():
    # Classification accuracy is critical — must use Sonnet, not Haiku
    model = ORCHESTRATOR_CONFIG["classification_model"]
    assert "sonnet" in model.lower() or "opus" in model.lower(), (
        "classification_model must be Sonnet or above"
    )


def test_sonnet_model_used_for_layer3():
    # Layer 3 is reasoning-heavy — must not use Haiku
    model = ORCHESTRATOR_CONFIG["layer3_model"]
    assert "haiku" not in model.lower(), (
        "layer3_model must not be Haiku (reasoning-heavy layer)"
    )


def test_haiku_model_for_layer1():
    model = ORCHESTRATOR_CONFIG["layer1_model"]
    assert "haiku" in model.lower(), "layer1_model should be Haiku (cost efficiency)"


def test_announcement_ttl_covers_all_types():
    expected_types = {
        "pdmr_transaction",
        "regulatory_catalyst",
        "substantive_news",
        "administrative",
        "unclassified",
    }
    assert expected_types.issubset(ANNOUNCEMENT_TTL_DAYS.keys())


def test_pdmr_transaction_has_no_base_expiry():
    # pdmr_transaction base record: no expiry (None)
    assert ANNOUNCEMENT_TTL_DAYS["pdmr_transaction"] is None


def test_administrative_has_shortest_ttl():
    non_null = {
        k: v for k, v in ANNOUNCEMENT_TTL_DAYS.items() if v is not None
    }
    assert ANNOUNCEMENT_TTL_DAYS["administrative"] == min(non_null.values())


def test_child_collection_ttls_are_positive():
    assert PDMR_TRANSACTION_TTL_DAYS > 0
    assert COMPANY_NEWS_SUMMARY_TTL_DAYS > 0
    assert SIGNAL_TTL_DAYS > 0


def test_pdmr_transaction_ttl_longer_than_news_summary():
    # Signals and transaction history kept longer than news summaries
    assert PDMR_TRANSACTION_TTL_DAYS > COMPANY_NEWS_SUMMARY_TTL_DAYS
