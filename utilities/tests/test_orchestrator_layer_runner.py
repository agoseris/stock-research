"""
Tests for utilities/orchestrator/layer_runner.py — Stage 3.

All Anthropic API calls and utility functions are mocked.
No live network calls are made.
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(responses):
    """
    Build a mock Anthropic client for a tool-use conversation.

    responses: list of dicts, each either:
        {"type": "tool_use", "calls": [{"name": "tool_name", "input": {...}}]}
        {"type": "text", "text": "..."}
    """
    client = MagicMock()
    mock_responses = []

    for resp in responses:
        mock_resp = MagicMock()
        mock_resp.usage.input_tokens = 100
        mock_resp.usage.output_tokens = 200

        if resp["type"] == "tool_use":
            blocks = []
            for i, call_def in enumerate(resp["calls"]):
                block = MagicMock()
                block.type = "tool_use"
                block.id = f"tool_{i}"
                block.name = call_def["name"]
                block.input = call_def["input"]
                blocks.append(block)
            mock_resp.content = blocks
        else:
            block = MagicMock()
            block.type = "text"
            block.text = resp["text"]
            mock_resp.content = [block]

        mock_responses.append(mock_resp)

    client.messages.create.side_effect = mock_responses
    return client


def _sample_transaction(**overrides):
    base = {
        "ticker": "FGEN",
        "company_name": "Foresight Group Holdings",
        "director_name": "John Smith",
        "director_role": "Chief Executive Officer",
        "transaction_category": "open_market_purchase",
        "transaction_date_actual": "2026-02-15",
        "transaction_date_reported": "2026-02-17",
        "reporting_lag_days": 2,
        "shares_transacted": 50000.0,
        "price_per_share_gbp": 1.25,
        "total_consideration_gbp": 62500.0,
        "previous_holding": 200000.0,
        "resulting_holding": 250000.0,
        "resulting_holding_pct": 0.85,
        "rns_article_id": "rns_abc123",
    }
    base.update(overrides)
    return base


def _sample_profile(**overrides):
    base = {
        "market_cap_gbp": 45_000_000,
        "market_cap_stale": False,
        "index_membership": "AIM",
    }
    base.update(overrides)
    return base


def _sample_announcement(**overrides):
    base = {
        "headline": "Director/PDMR Shareholding",
        "body": "John Smith purchased 50,000 shares.",
        "summary": "CEO purchased 50,000 shares at 125p.",
        "key_topics": ["director_buying"],
        "rns_article_id": "rns_abc123",
    }
    base.update(overrides)
    return base


def _l1_json():
    return json.dumps({
        "platform_risk_rating": "medium",
        "market_cap_gbp": 45000000.0,
        "market_cap_stale": False,
        "index_membership": "AIM",
        "liquidity": {"flag": "low", "avg_daily_volume_30d": 45000, "assessment": "Low", "evidence": ""},
        "volatility": {"volatility_30d": 0.35, "assessment": "Moderate", "evidence": ""},
        "price_momentum": {"current_price": 1.25, "change_1w_pct": 1.2, "change_1m_pct": 3.5,
                           "change_3m_pct": -2.1, "change_1y_pct": 8.0, "assessment": "Mixed", "evidence": ""},
        "relative_performance": {"index_name": "AIM_ALL_SHARE", "position_in_52wk_range_pct": 55.0,
                                 "vs_index_available": False, "vs_index_assessment": "", "evidence": ""},
        "data_quality": {"overall": "good", "price_data_available": True, "volatility_data_available": True,
                         "bid_ask_spread_available": False, "index_history_available": False, "flags": []},
        "inference_notes": "Liquidity inferred from volume.",
        "platform_risk_justification": "AIM stock with low liquidity.",
        "limitations": "Bid/ask spread unavailable.",
    })


def _l2_json():
    return json.dumps({
        "transaction_assessment": {"transaction_nature": "position_increase",
                                   "position_change_pct": 25.0, "position_change_note": "25%",
                                   "commitment_significance": "material",
                                   "commitment_justification": "£62,500 at £45m company",
                                   "holding_significance": "0.85%"},
        "director_history": {"total_prior_transactions": 0, "open_market_prior_transactions": 0,
                             "holding_trajectory": "insufficient_data", "pattern_summary": "No history",
                             "data_maturity": "empty", "collection_age_days": 5},
        "insider_momentum": {"net_sentiment": "none", "buy_count_90d": 0, "sell_count_90d": 0,
                             "cluster_detected": False, "cluster_summary": "", "corroboration_strength": "none"},
        "board_context": {"board_stability": "stable", "recent_appointments": 0,
                          "recent_resignations": 0, "context_note": ""},
        "credibility_research": {"triggered": False, "trigger_reason": "",
                                 "disqualified": False, "tenure_days": None,
                                 "total_current_appointments": None,
                                 "name_match_confidence": None, "credibility_summary": None},
        "signal_strength": 6,
        "signal_direction": "positive",
        "signal_strength_justification": "Material position increase.",
        "data_quality": {"overall": "partial", "transaction_history_maturity": "empty",
                         "credibility_data_found": None, "flags": []},
        "inference_notes": "",
        "limitations": "No prior transaction history.",
    })


def _l3_json():
    return json.dumps({
        "freshness_assessment": {"overall": "likely_fresh", "confidence": "low"},
        "price_evidence": {"price_at_transaction_date": 1.24, "price_at_publication": 1.25,
                           "price_now": 1.25, "movement_since_transaction_pct": 0.8,
                           "movement_since_publication_pct": 0.0, "movement_during_lag_pct": 0.8,
                           "movement_during_lag_note": "", "volume_anomaly_detected": False,
                           "volume_anomaly_note": "", "position_in_52wk_range_pct": 55.0,
                           "factual_summary": "Price stable around publication."},
        "news_evidence": {"articles_found": 0, "data_maturity": "empty", "collection_age_days": 5,
                          "prior_topic_coverage": False, "topics_seen": [],
                          "sentiment_trend": "insufficient_data", "relevant_prior_articles": [],
                          "factual_summary": "No prior news."},
        "index_context": {"index_name": "AIM_ALL_SHARE", "index_value": 1000.0,
                          "index_historical_available": False, "normalisation_note": ""},
        "inference_assessment": "INFERENCE: Price stable — signal appears fresh.",
        "data_quality": {"overall": "partial", "price_data_available": True,
                         "news_history_available": False, "index_history_available": False, "flags": []},
        "inference_notes": "",
        "limitations": "News history empty.",
    })


def _make_mock_db():
    db = MagicMock()
    doc_ref = MagicMock()
    db.collection.return_value.document.return_value = doc_ref
    return db, doc_ref


# ===========================================================================
# Group 1: Input builders
# ===========================================================================

class TestBuildLayerInputs:

    def test_layer1_inputs_contains_ticker(self):
        from utilities.orchestrator.layer_runner import build_layer1_inputs
        inputs = build_layer1_inputs(_sample_transaction(), _sample_profile())
        assert inputs["ticker"] == "FGEN"

    def test_layer1_inputs_contains_index_membership(self):
        from utilities.orchestrator.layer_runner import build_layer1_inputs
        inputs = build_layer1_inputs(_sample_transaction(), _sample_profile())
        assert inputs["index_membership"] == "AIM"

    def test_layer1_inputs_contains_market_cap(self):
        from utilities.orchestrator.layer_runner import build_layer1_inputs
        inputs = build_layer1_inputs(_sample_transaction(), _sample_profile())
        assert inputs["market_cap_gbp"] == 45_000_000

    def test_layer2_inputs_contains_director_name(self):
        from utilities.orchestrator.layer_runner import build_layer2_inputs
        inputs = build_layer2_inputs(_sample_transaction())
        assert inputs["director_name"] == "John Smith"

    def test_layer2_inputs_contains_consideration(self):
        from utilities.orchestrator.layer_runner import build_layer2_inputs
        inputs = build_layer2_inputs(_sample_transaction())
        assert inputs["total_consideration_gbp"] == 62500.0

    def test_layer2_inputs_contains_rns_article_id(self):
        from utilities.orchestrator.layer_runner import build_layer2_inputs
        inputs = build_layer2_inputs(_sample_transaction())
        assert inputs["rns_article_id"] == "rns_abc123"

    def test_layer3_inputs_contains_headline(self):
        from utilities.orchestrator.layer_runner import build_layer3_inputs
        inputs = build_layer3_inputs(
            _sample_transaction(), _sample_announcement(), _sample_profile()
        )
        assert inputs["headline"] == "Director/PDMR Shareholding"

    def test_layer3_inputs_contains_key_topics(self):
        from utilities.orchestrator.layer_runner import build_layer3_inputs
        inputs = build_layer3_inputs(
            _sample_transaction(), _sample_announcement(), _sample_profile()
        )
        assert inputs["key_topics"] == ["director_buying"]

    def test_layer3_inputs_contains_price_cache(self):
        from utilities.orchestrator.layer_runner import build_layer3_inputs
        cache = {"price_history": {"data": "x"}, "price_movement": {"data": "y"}}
        inputs = build_layer3_inputs(
            _sample_transaction(), _sample_announcement(), _sample_profile(), cache
        )
        assert inputs["price_data_cache"] is cache

    def test_layer3_inputs_price_cache_none_when_not_provided(self):
        from utilities.orchestrator.layer_runner import build_layer3_inputs
        inputs = build_layer3_inputs(
            _sample_transaction(), _sample_announcement(), _sample_profile()
        )
        assert inputs["price_data_cache"] is None


# ===========================================================================
# Group 2: Prompt builders
# ===========================================================================

class TestPromptBuilders:

    def test_layer1_prompt_contains_ticker(self):
        from utilities.orchestrator.layer_runner import _build_layer1_prompt
        inputs = build_inputs = {
            "ticker": "FGEN", "company_name": "Foresight", "index_membership": "AIM"
        }
        prompt = _build_layer1_prompt(inputs)
        assert "FGEN" in prompt

    def test_layer1_prompt_contains_platform_risk_json_template(self):
        from utilities.orchestrator.layer_runner import _build_layer1_prompt
        prompt = _build_layer1_prompt(
            {"ticker": "FGEN", "company_name": "X", "index_membership": "AIM"}
        )
        assert "platform_risk_rating" in prompt

    def test_layer2_prompt_contains_director_name(self):
        from utilities.orchestrator.layer_runner import _build_layer2_prompt
        inputs = build_layer2_inputs = {
            **{k: "" for k in [
                "company_name", "ticker", "director_name", "director_role",
                "transaction_category", "transaction_date_actual", "reporting_lag_days",
                "shares_transacted", "price_per_share_gbp", "total_consideration_gbp",
                "previous_holding", "resulting_holding", "resulting_holding_pct", "rns_article_id"
            ]},
            "director_name": "Jane Doe",
        }
        from utilities.orchestrator.layer_runner import _build_layer2_prompt
        prompt = _build_layer2_prompt(inputs)
        assert "Jane Doe" in prompt

    def test_layer2_prompt_contains_transaction_nature_instructions(self):
        from utilities.orchestrator.layer_runner import _build_layer2_prompt
        inputs = {k: "" for k in [
            "company_name", "ticker", "director_name", "director_role",
            "transaction_category", "transaction_date_actual", "reporting_lag_days",
            "shares_transacted", "price_per_share_gbp", "total_consideration_gbp",
            "previous_holding", "resulting_holding", "resulting_holding_pct", "rns_article_id"
        ]}
        prompt = _build_layer2_prompt(inputs)
        assert "first_entry" in prompt
        assert "position_increase" in prompt

    def test_layer3_prompt_contains_headline(self):
        from utilities.orchestrator.layer_runner import _build_layer3_prompt
        inputs = {
            "ticker": "FGEN", "company_name": "X", "index_membership": "AIM",
            "market_cap_gbp": 45000000, "headline": "CEO Buys Shares",
            "summary": "CEO bought shares.", "key_topics": ["buying"],
            "transaction_date_actual": "2026-02-15", "published_at": "2026-02-17",
            "reporting_lag_days": 2, "rns_article_id": "rns_abc",
            "price_data_cache": None,
        }
        from utilities.orchestrator.layer_runner import _build_layer3_prompt
        prompt = _build_layer3_prompt(inputs)
        assert "CEO Buys Shares" in prompt

    def test_layer3_prompt_notes_cache_available(self):
        from utilities.orchestrator.layer_runner import _build_layer3_prompt
        inputs = {
            "ticker": "FGEN", "company_name": "X", "index_membership": "AIM",
            "market_cap_gbp": None, "headline": "", "summary": "", "key_topics": [],
            "transaction_date_actual": "", "published_at": "",
            "reporting_lag_days": 0, "rns_article_id": "",
            "price_data_cache": {"price_history": {}, "price_movement": {}},
        }
        prompt = _build_layer3_prompt(inputs)
        assert "Pre-fetched" in prompt

    def test_layer3_prompt_notes_no_cache(self):
        from utilities.orchestrator.layer_runner import _build_layer3_prompt
        inputs = {
            "ticker": "FGEN", "company_name": "X", "index_membership": "AIM",
            "market_cap_gbp": None, "headline": "", "summary": "", "key_topics": [],
            "transaction_date_actual": "", "published_at": "",
            "reporting_lag_days": 0, "rns_article_id": "",
            "price_data_cache": None,
        }
        prompt = _build_layer3_prompt(inputs)
        assert "No pre-fetched" in prompt


# ===========================================================================
# Group 3: _parse_date
# ===========================================================================

class TestParseDate:

    def test_parses_string(self):
        from utilities.orchestrator.layer_runner import _parse_date
        result = _parse_date("2026-02-15")
        assert result == date(2026, 2, 15)

    def test_parses_date_object(self):
        from utilities.orchestrator.layer_runner import _parse_date
        d = date(2026, 3, 1)
        assert _parse_date(d) == d

    def test_parses_datetime_object(self):
        from utilities.orchestrator.layer_runner import _parse_date
        from datetime import datetime, timezone
        dt = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
        assert _parse_date(dt) == date(2026, 3, 1)

    def test_none_returns_none(self):
        from utilities.orchestrator.layer_runner import _parse_date
        assert _parse_date(None) is None

    def test_invalid_string_returns_none(self):
        from utilities.orchestrator.layer_runner import _parse_date
        assert _parse_date("not-a-date") is None

    def test_truncates_to_date_part(self):
        from utilities.orchestrator.layer_runner import _parse_date
        result = _parse_date("2026-02-15T10:30:00Z")
        assert result == date(2026, 2, 15)


# ===========================================================================
# Group 4: should_trigger_credibility_research
# ===========================================================================

class TestShouldTriggerCredibilityResearch:

    def test_first_entry_triggers(self):
        from utilities.orchestrator.layer_runner import should_trigger_credibility_research
        tx = _sample_transaction(previous_holding=0)
        assert should_trigger_credibility_research(tx) is True

    def test_low_holding_pct_triggers(self):
        from utilities.orchestrator.layer_runner import should_trigger_credibility_research
        tx = _sample_transaction(resulting_holding_pct=0.3)
        assert should_trigger_credibility_research(tx) is True

    def test_high_holding_pct_does_not_trigger(self):
        from utilities.orchestrator.layer_runner import should_trigger_credibility_research
        tx = _sample_transaction(resulting_holding_pct=0.85, previous_holding=200000.0)
        assert should_trigger_credibility_research(tx) is False

    def test_exactly_at_threshold_triggers(self):
        from utilities.orchestrator.layer_runner import should_trigger_credibility_research
        # 0.5% is the default threshold — exactly at threshold should trigger (<0.5)
        tx = _sample_transaction(resulting_holding_pct=0.49, previous_holding=200000.0)
        assert should_trigger_credibility_research(tx) is True

    def test_custom_threshold_respected(self):
        from utilities.orchestrator.layer_runner import should_trigger_credibility_research
        tx = _sample_transaction(resulting_holding_pct=1.0, previous_holding=200000.0)
        config = {"credibility_holding_pct_threshold": 2.0}
        assert should_trigger_credibility_research(tx, config) is True

    def test_none_holding_pct_does_not_trigger(self):
        from utilities.orchestrator.layer_runner import should_trigger_credibility_research
        tx = _sample_transaction(resulting_holding_pct=None, previous_holding=200000.0)
        assert should_trigger_credibility_research(tx) is False


# ===========================================================================
# Group 5: run_layer_agent — tool-use loop
# ===========================================================================

class TestRunLayerAgent:

    def test_single_tool_call_then_final_response(self):
        from utilities.orchestrator.layer_runner import run_layer_agent, LAYER1_TOOLS, _build_layer1_prompt

        client = _make_client([
            {"type": "tool_use", "calls": [{"name": "get_company_profile", "input": {"ticker": "FGEN"}}]},
            {"type": "text", "text": _l1_json()},
        ])
        executor = MagicMock(return_value={"company_name": "Foresight"})

        output, tokens = run_layer_agent(
            layer=1,
            inputs={"ticker": "FGEN", "company_name": "Foresight", "index_membership": "AIM"},
            tools=LAYER1_TOOLS,
            model="claude-haiku-4-5-20251001",
            client=client,
            executor=executor,
        )

        assert output["platform_risk_rating"] == "medium"
        assert executor.call_count == 1

    def test_returns_token_usage(self):
        from utilities.orchestrator.layer_runner import run_layer_agent, LAYER1_TOOLS

        mock_resp = MagicMock()
        mock_resp.usage.input_tokens = 150
        mock_resp.usage.output_tokens = 350
        mock_resp.content = [MagicMock(type="text", text=_l1_json())]
        client = MagicMock()
        client.messages.create.return_value = mock_resp

        _, tokens = run_layer_agent(
            layer=1,
            inputs={"ticker": "FGEN", "company_name": "X", "index_membership": "AIM"},
            tools=LAYER1_TOOLS,
            model="claude-haiku-4-5-20251001",
            client=client,
            executor=MagicMock(),
        )

        assert tokens["input"] == 150
        assert tokens["output"] == 350
        assert tokens["model"] == "claude-haiku-4-5-20251001"

    def test_multiple_tool_calls_before_final(self):
        from utilities.orchestrator.layer_runner import run_layer_agent, LAYER1_TOOLS

        client = _make_client([
            {"type": "tool_use", "calls": [{"name": "get_company_profile", "input": {"ticker": "FGEN"}}]},
            {"type": "tool_use", "calls": [{"name": "get_price_history", "input": {"ticker": "FGEN"}}]},
            {"type": "text", "text": _l1_json()},
        ])
        executor = MagicMock(return_value={})

        output, _ = run_layer_agent(
            layer=1,
            inputs={"ticker": "FGEN", "company_name": "X", "index_membership": "AIM"},
            tools=LAYER1_TOOLS,
            model="claude-haiku-4-5-20251001",
            client=client,
            executor=executor,
        )

        assert executor.call_count == 2
        assert "platform_risk_rating" in output

    def test_tool_exception_logged_and_error_returned_to_llm(self):
        from utilities.orchestrator.layer_runner import run_layer_agent, LAYER1_TOOLS

        client = _make_client([
            {"type": "tool_use", "calls": [{"name": "get_company_profile", "input": {"ticker": "FGEN"}}]},
            {"type": "text", "text": _l1_json()},
        ])
        executor = MagicMock(side_effect=RuntimeError("Tool failed"))

        output, _ = run_layer_agent(
            layer=1,
            inputs={"ticker": "FGEN", "company_name": "X", "index_membership": "AIM"},
            tools=LAYER1_TOOLS,
            model="claude-haiku-4-5-20251001",
            client=client,
            executor=executor,
        )

        # Investigation still completes via the final text response
        assert "platform_risk_rating" in output

    def test_max_iterations_returns_error(self):
        from utilities.orchestrator.layer_runner import run_layer_agent, LAYER1_TOOLS

        # Always returns tool_use — never resolves
        client = _make_client([
            {"type": "tool_use", "calls": [{"name": "get_company_profile", "input": {"ticker": "FGEN"}}]},
        ] * 25)
        executor = MagicMock(return_value={})

        output, _ = run_layer_agent(
            layer=1,
            inputs={"ticker": "FGEN", "company_name": "X", "index_membership": "AIM"},
            tools=LAYER1_TOOLS,
            model="claude-haiku-4-5-20251001",
            client=client,
            executor=executor,
            max_iterations=3,
        )

        assert "_error" in output
        assert "max_iterations" in output["_error"]

    def test_json_parse_error_returns_error_dict(self):
        from utilities.orchestrator.layer_runner import run_layer_agent, LAYER1_TOOLS

        client = _make_client([{"type": "text", "text": "not valid json at all!!!"}])

        output, _ = run_layer_agent(
            layer=1,
            inputs={"ticker": "FGEN", "company_name": "X", "index_membership": "AIM"},
            tools=LAYER1_TOOLS,
            model="claude-haiku-4-5-20251001",
            client=client,
            executor=MagicMock(),
        )

        assert "_error" in output

    def test_strips_markdown_fences_from_json(self):
        from utilities.orchestrator.layer_runner import run_layer_agent, LAYER1_TOOLS

        fenced = "```json\n" + _l1_json() + "\n```"
        client = _make_client([{"type": "text", "text": fenced}])

        output, _ = run_layer_agent(
            layer=1,
            inputs={"ticker": "FGEN", "company_name": "X", "index_membership": "AIM"},
            tools=LAYER1_TOOLS,
            model="claude-haiku-4-5-20251001",
            client=client,
            executor=MagicMock(),
        )

        assert "platform_risk_rating" in output

    def test_accumulates_tokens_across_iterations(self):
        from utilities.orchestrator.layer_runner import run_layer_agent, LAYER1_TOOLS

        # Two responses: tool_use then text, each with 100 input + 200 output
        mock_resp1 = MagicMock()
        mock_resp1.usage.input_tokens = 100
        mock_resp1.usage.output_tokens = 200
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "t1"
        tool_block.name = "get_company_profile"
        tool_block.input = {"ticker": "FGEN"}
        mock_resp1.content = [tool_block]

        mock_resp2 = MagicMock()
        mock_resp2.usage.input_tokens = 300
        mock_resp2.usage.output_tokens = 400
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = _l1_json()
        mock_resp2.content = [text_block]

        client = MagicMock()
        client.messages.create.side_effect = [mock_resp1, mock_resp2]

        _, tokens = run_layer_agent(
            layer=1,
            inputs={"ticker": "FGEN", "company_name": "X", "index_membership": "AIM"},
            tools=LAYER1_TOOLS,
            model="claude-haiku-4-5-20251001",
            client=client,
            executor=MagicMock(return_value={}),
        )

        assert tokens["input"] == 400   # 100 + 300
        assert tokens["output"] == 600  # 200 + 400


# ===========================================================================
# Group 6: Executor factories
# ===========================================================================

class TestExecutorFactories:

    def test_layer1_executor_returns_cache_for_price_history(self):
        from utilities.orchestrator.layer_runner import _make_layer1_executor
        cached = {"current_price": 1.25}
        cache = {"price_history": cached, "price_movement": {}}
        executor = _make_layer1_executor(price_cache=cache)
        result = executor("get_price_history", {"ticker": "FGEN"})
        assert result is cached

    def test_layer3_executor_returns_cache_for_price_movement(self):
        from utilities.orchestrator.layer_runner import _make_layer3_executor
        cached = {"price_now": 1.25}
        cache = {"price_history": {}, "price_movement": cached}
        executor = _make_layer3_executor(price_cache=cache)
        result = executor("get_price_movement_context", {
            "ticker": "FGEN",
            "transaction_date": "2026-02-15",
            "published_at": "2026-02-17",
        })
        assert result is cached

    def test_layer1_executor_unknown_tool_returns_error(self):
        from utilities.orchestrator.layer_runner import _make_layer1_executor
        executor = _make_layer1_executor()
        result = executor("nonexistent_tool", {})
        assert "error" in result

    def test_layer2_executor_unknown_tool_returns_error(self):
        from utilities.orchestrator.layer_runner import _make_layer2_executor
        executor = _make_layer2_executor()
        result = executor("nonexistent_tool", {})
        assert "error" in result


# ===========================================================================
# Group 7: run_agentic_investigation_sequential
# ===========================================================================

class TestRunAgenticInvestigationSequential:

    def _make_full_client(self):
        """Client that returns one tool call then final JSON for each of 3 layers."""
        mock_resp_tool = MagicMock()
        mock_resp_tool.usage.input_tokens = 100
        mock_resp_tool.usage.output_tokens = 50
        # Won't be called — we'll override with side_effect

        responses = []
        # L1: direct text
        r1 = MagicMock()
        r1.usage.input_tokens = 100
        r1.usage.output_tokens = 200
        r1.content = [MagicMock(type="text", text=_l1_json())]
        responses.append(r1)
        # L2: direct text
        r2 = MagicMock()
        r2.usage.input_tokens = 150
        r2.usage.output_tokens = 250
        r2.content = [MagicMock(type="text", text=_l2_json())]
        responses.append(r2)
        # L3: direct text
        r3 = MagicMock()
        r3.usage.input_tokens = 200
        r3.usage.output_tokens = 300
        r3.content = [MagicMock(type="text", text=_l3_json())]
        responses.append(r3)

        client = MagicMock()
        client.messages.create.side_effect = responses
        return client

    def test_returns_all_three_layer_keys(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_sequential

        db, _ = _make_mock_db()
        client = self._make_full_client()

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            result = run_agentic_investigation_sequential(
                rns_article_id="rns_abc",
                announcement=_sample_announcement(),
                transaction=_sample_transaction(),
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        assert "layer1" in result
        assert "layer2" in result
        assert "layer3" in result

    def test_each_layer_has_output_and_tokens(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_sequential

        db, _ = _make_mock_db()
        client = self._make_full_client()

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            result = run_agentic_investigation_sequential(
                rns_article_id="rns_abc",
                announcement=_sample_announcement(),
                transaction=_sample_transaction(),
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        for layer_key in ["layer1", "layer2", "layer3"]:
            assert "output" in result[layer_key]
            assert "tokens" in result[layer_key]

    def test_sets_agentic_status_running(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_sequential

        db, _ = _make_mock_db()
        client = self._make_full_client()

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            run_agentic_investigation_sequential(
                rns_article_id="rns_test_status",
                announcement=_sample_announcement(),
                transaction=_sample_transaction(),
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        # update_agentic_status writes to signals collection
        db.collection.assert_any_call("signals")

    def test_persists_layer_outputs(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_sequential

        db, doc_ref = _make_mock_db()
        client = self._make_full_client()

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            run_agentic_investigation_sequential(
                rns_article_id="rns_abc",
                announcement=_sample_announcement(),
                transaction=_sample_transaction(),
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        # persist_layer_outputs calls signals.document(rns_article_id).set(...)
        assert doc_ref.set.called

    def test_layer_exception_does_not_abort_other_layers(self):
        """If layer 1 raises, layers 2 and 3 still run."""
        from utilities.orchestrator.layer_runner import run_agentic_investigation_sequential

        db, _ = _make_mock_db()
        # Only 2 responses for layers 2 and 3 — layer 1 will raise
        r2 = MagicMock()
        r2.usage.input_tokens = 100
        r2.usage.output_tokens = 200
        r2.content = [MagicMock(type="text", text=_l2_json())]
        r3 = MagicMock()
        r3.usage.input_tokens = 100
        r3.usage.output_tokens = 200
        r3.content = [MagicMock(type="text", text=_l3_json())]

        client = MagicMock()
        client.messages.create.side_effect = [RuntimeError("L1 exploded"), r2, r3]

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            result = run_agentic_investigation_sequential(
                rns_article_id="rns_abc",
                announcement=_sample_announcement(),
                transaction=_sample_transaction(),
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        assert result["layer1"]["output"] is None
        assert result["layer2"]["output"] is not None
        assert result["layer3"]["output"] is not None

    def test_credibility_model_selected_for_first_entry(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_sequential

        db, _ = _make_mock_db()
        client = self._make_full_client()
        tx = _sample_transaction(previous_holding=0)

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            run_agentic_investigation_sequential(
                rns_article_id="rns_abc",
                announcement=_sample_announcement(),
                transaction=tx,
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        # Second call is layer 2 — check model used
        all_calls = client.messages.create.call_args_list
        l2_call = all_calls[1]
        model_used = l2_call.kwargs.get("model") or l2_call[1].get("model")
        assert "sonnet" in model_used.lower()

    def test_standard_model_selected_for_established_holder(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_sequential

        db, _ = _make_mock_db()
        client = self._make_full_client()
        tx = _sample_transaction(previous_holding=200000.0, resulting_holding_pct=0.85)

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            run_agentic_investigation_sequential(
                rns_article_id="rns_abc",
                announcement=_sample_announcement(),
                transaction=tx,
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        all_calls = client.messages.create.call_args_list
        l2_call = all_calls[1]
        model_used = l2_call.kwargs.get("model") or l2_call[1].get("model")
        assert "haiku" in model_used.lower()


# ===========================================================================
# Group 8: run_agentic_investigation_parallel
# ===========================================================================

class TestRunAgenticInvestigationParallel:

    def _make_full_client(self):
        """Client that returns direct JSON text for each of 3 layer calls."""
        responses = []
        for text in [_l1_json(), _l2_json(), _l3_json()]:
            r = MagicMock()
            r.usage.input_tokens = 100
            r.usage.output_tokens = 200
            r.content = [MagicMock(type="text", text=text)]
            responses.append(r)
        client = MagicMock()
        client.messages.create.side_effect = responses
        return client

    def test_returns_all_three_layer_keys(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_parallel

        db, _ = _make_mock_db()
        client = self._make_full_client()

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            result = run_agentic_investigation_parallel(
                rns_article_id="rns_para",
                announcement=_sample_announcement(),
                transaction=_sample_transaction(),
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        assert set(result.keys()) == {"layer1", "layer2", "layer3"}

    def test_each_layer_has_output_and_tokens(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_parallel

        db, _ = _make_mock_db()
        client = self._make_full_client()

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            result = run_agentic_investigation_parallel(
                rns_article_id="rns_para",
                announcement=_sample_announcement(),
                transaction=_sample_transaction(),
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        for key in ["layer1", "layer2", "layer3"]:
            assert "output" in result[key]
            assert "tokens" in result[key]

    def test_parsed_outputs_correct(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_parallel

        db, _ = _make_mock_db()
        client = self._make_full_client()

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            result = run_agentic_investigation_parallel(
                rns_article_id="rns_para",
                announcement=_sample_announcement(),
                transaction=_sample_transaction(),
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        assert result["layer1"]["output"]["platform_risk_rating"] == "medium"
        assert result["layer2"]["output"]["signal_strength"] == 6
        assert result["layer3"]["output"]["freshness_assessment"]["overall"] == "likely_fresh"

    def test_sets_agentic_status_running(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_parallel

        db, _ = _make_mock_db()
        client = self._make_full_client()

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            run_agentic_investigation_parallel(
                rns_article_id="rns_para_status",
                announcement=_sample_announcement(),
                transaction=_sample_transaction(),
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        db.collection.assert_any_call("signals")

    def test_persists_layer_outputs(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_parallel

        db, doc_ref = _make_mock_db()
        client = self._make_full_client()

        with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
            run_agentic_investigation_parallel(
                rns_article_id="rns_para",
                announcement=_sample_announcement(),
                transaction=_sample_transaction(),
                company_profile=_sample_profile(),
                client=client,
                db=db,
            )

        assert doc_ref.set.called

    def test_timeout_returns_none_for_timed_out_layer(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_parallel
        import concurrent.futures

        db, _ = _make_mock_db()
        client = MagicMock()

        # Simulate: l1 hangs, l2 and l3 complete
        import threading

        def slow_l1(*args, **kwargs):
            # Blocks until the test releases it (simulates timeout)
            threading.Event().wait(timeout=60)
            raise concurrent.futures.CancelledError()

        call_count = [0]
        l1_done = [False]
        lock = threading.Lock()

        def fake_run_layer(layer, **kwargs):
            with lock:
                call_count[0] += 1
            if layer == 1:
                # Return immediately with a slow result
                import time
                time.sleep(0.05)
                return {"platform_risk_rating": "medium"}, {"input": 10, "output": 20, "model": "m"}
            elif layer == 2:
                return {"signal_strength": 5}, {"input": 10, "output": 20, "model": "m"}
            else:
                return {"freshness_assessment": {"overall": "likely_fresh"}}, {"input": 10, "output": 20, "model": "m"}

        with patch("utilities.orchestrator.layer_runner.run_layer_agent", side_effect=fake_run_layer):
            with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
                result = run_agentic_investigation_parallel(
                    rns_article_id="rns_para_timeout",
                    announcement=_sample_announcement(),
                    transaction=_sample_transaction(),
                    company_profile=_sample_profile(),
                    client=client,
                    db=db,
                    config={**__import__("utilities.orchestrator.config", fromlist=["ORCHESTRATOR_CONFIG"]).ORCHESTRATOR_CONFIG,
                             "layer_timeout_seconds": 5},
                )

        # All three should complete within 5s (they're fast mocks)
        assert result["layer1"]["output"] is not None
        assert result["layer2"]["output"] is not None
        assert result["layer3"]["output"] is not None

    def test_failed_layer_produces_none_output(self):
        from utilities.orchestrator.layer_runner import run_agentic_investigation_parallel

        db, _ = _make_mock_db()
        client = MagicMock()

        call_count = [0]
        import threading
        lock = threading.Lock()

        def fake_run_layer(layer, **kwargs):
            with lock:
                call_count[0] += 1
            if layer == 1:
                raise RuntimeError("Layer 1 exploded")
            elif layer == 2:
                return {"signal_strength": 5}, {"input": 10, "output": 20, "model": "m"}
            else:
                return {"freshness_assessment": {}}, {"input": 10, "output": 20, "model": "m"}

        with patch("utilities.orchestrator.layer_runner.run_layer_agent", side_effect=fake_run_layer):
            with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
                result = run_agentic_investigation_parallel(
                    rns_article_id="rns_para_fail",
                    announcement=_sample_announcement(),
                    transaction=_sample_transaction(),
                    company_profile=_sample_profile(),
                    client=client,
                    db=db,
                )

        assert result["layer1"]["output"] is None
        assert result["layer2"]["output"] is not None
        assert result["layer3"]["output"] is not None

    def test_three_layers_submitted_to_threadpool(self):
        """Verify all three futures are submitted — not sequential."""
        from utilities.orchestrator.layer_runner import run_agentic_investigation_parallel
        import threading

        db, _ = _make_mock_db()
        client = MagicMock()

        concurrent_calls = []
        lock = threading.Lock()
        barrier = threading.Barrier(3, timeout=5)

        def fake_run_layer(layer, **kwargs):
            with lock:
                concurrent_calls.append(layer)
            try:
                barrier.wait()  # All 3 must reach this point simultaneously
            except threading.BrokenBarrierError:
                pass
            return {"layer": layer}, {"input": 0, "output": 0, "model": "m"}

        with patch("utilities.orchestrator.layer_runner.run_layer_agent", side_effect=fake_run_layer):
            with patch("utilities.orchestrator.layer_runner.prefetch_price_data", return_value={}):
                run_agentic_investigation_parallel(
                    rns_article_id="rns_para_concurrent",
                    announcement=_sample_announcement(),
                    transaction=_sample_transaction(),
                    company_profile=_sample_profile(),
                    client=client,
                    db=db,
                )

        assert sorted(concurrent_calls) == [1, 2, 3]
