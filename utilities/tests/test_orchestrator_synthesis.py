"""
Tests for utilities/orchestrator/synthesis.py — Stage 5.
"""

import json
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_transaction(**overrides):
    t = {
        "company_name": "Test Co plc",
        "ticker": "TST",
        "director_name": "John Smith",
        "transaction_category": "open_market_purchase",
        "total_consideration_gbp": 10000.0,
        "published_at": "2026-01-15",
    }
    t.update(overrides)
    return t


def _make_layer1_output():
    return {"platform_risk": "low", "market_cap_gbp": 50_000_000}


def _make_layer2_output():
    return {"signal_strength": 7, "signal_direction": "positive"}


def _make_layer3_output():
    return {"freshness_assessment": "likely fresh", "pre_pub_movement_pct": 1.2}


def _make_simple_output():
    return {"SIGNAL_STRENGTH": 6, "RECOMMENDED_ACTION": "Investigate further"}


def _make_investigation_result(l1=True, l2=True, l3=True):
    return {
        "layer1": {
            "output": _make_layer1_output() if l1 else None,
            "tokens": {"input": 100, "output": 200, "model": "claude-haiku-4-5-20251001"},
        },
        "layer2": {
            "output": _make_layer2_output() if l2 else None,
            "tokens": {"input": 150, "output": 250, "model": "claude-haiku-4-5-20251001"},
        },
        "layer3": {
            "output": _make_layer3_output() if l3 else None,
            "tokens": {"input": 200, "output": 300, "model": "claude-sonnet-4-6"},
        },
    }


def _make_synthesis_json(**overrides):
    d = {
        "briefing": {
            "company": "Test Co plc",
            "ticker": "TST",
            "director": "John Smith",
            "transaction": "open_market_purchase",
            "consideration_gbp": 10000.0,
            "announced": "2026-01-15",
        },
        "layer_summaries": {
            "platform": {
                "risk_rating": "low",
                "key_finding": "Solid balance sheet.",
                "data_quality": "good",
            },
            "director_signal": {
                "signal_strength": 7,
                "signal_direction": "positive",
                "key_finding": "First-time open-market purchase.",
                "data_quality": "good",
            },
            "freshness": {
                "assessment": "likely fresh",
                "key_finding": "Minimal pre-publication movement.",
                "data_quality": "good",
            },
        },
        "cross_layer_assessment": {
            "coherence": "coherent",
            "tensions": "None identified.",
            "amplifiers": "Low platform risk and strong director signal both support a positive view.",
            "detractors": "None identified.",
        },
        "fact_summary": "Director purchased 10,000 shares at £1.00.",
        "inference_summary": "INFERENCE: Signal appears genuine based on available data.",
        "data_quality_summary": {
            "overall": "good",
            "thin_data_flags": [],
            "unavailable_data": [],
        },
        "vs_simple_lens": {
            "simple_recommendation": "Monitor",
            "simple_signal_strength": 6,
            "agentic_adds": "Platform risk context and pre-publication volume analysis.",
            "agentic_changes": "Upgrades recommendation from Monitor to Investigate further.",
        },
        "recommendation": "Investigate further",
        "recommendation_justification": "Strong first-time purchase on a low-risk platform with fresh signal.",
        "limitations": "- No bid/ask spread data available.",
        "token_usage": {
            "layer1_input": 0,
            "layer1_output": 0,
            "layer2_input": 0,
            "layer2_output": 0,
            "layer3_input": 0,
            "layer3_output": 0,
            "synthesis_input": 0,
            "synthesis_output": 0,
            "total_input": 0,
            "total_output": 0,
        },
    }
    d.update(overrides)
    return d


def _make_client(synthesis_input=500, synthesis_output=1000, result=None):
    """Return a mock Anthropic client that returns a valid synthesis response."""
    if result is None:
        result = _make_synthesis_json()
    mock_resp = MagicMock()
    mock_resp.usage.input_tokens = synthesis_input
    mock_resp.usage.output_tokens = synthesis_output
    mock_resp.content = [MagicMock(type="text", text=json.dumps(result))]
    client = MagicMock()
    client.messages.create.return_value = mock_resp
    return client


def _make_mock_db():
    db = MagicMock()
    mock_doc_ref = MagicMock()
    db.collection.return_value.document.return_value = mock_doc_ref
    return db, mock_doc_ref


# ===========================================================================
# Group 1: build_synthesis_prompt
# ===========================================================================

class TestBuildSynthesisPrompt:

    def test_ticker_in_prompt(self):
        from utilities.orchestrator.synthesis import build_synthesis_prompt
        prompt = build_synthesis_prompt(
            _make_transaction(), _make_layer1_output(), None, None, None
        )
        assert "TST" in prompt

    def test_company_name_in_prompt(self):
        from utilities.orchestrator.synthesis import build_synthesis_prompt
        prompt = build_synthesis_prompt(
            _make_transaction(), None, None, None, None
        )
        assert "Test Co plc" in prompt

    def test_director_name_in_prompt(self):
        from utilities.orchestrator.synthesis import build_synthesis_prompt
        prompt = build_synthesis_prompt(
            _make_transaction(), None, None, None, None
        )
        assert "John Smith" in prompt

    def test_consideration_formatted(self):
        from utilities.orchestrator.synthesis import build_synthesis_prompt
        prompt = build_synthesis_prompt(
            _make_transaction(total_consideration_gbp=12345.0),
            None, None, None, None
        )
        assert "£12,345" in prompt

    def test_layer1_json_in_prompt(self):
        from utilities.orchestrator.synthesis import build_synthesis_prompt
        prompt = build_synthesis_prompt(
            _make_transaction(), _make_layer1_output(), None, None, None
        )
        assert "platform_risk" in prompt

    def test_none_layer_shows_unavailable(self):
        from utilities.orchestrator.synthesis import build_synthesis_prompt
        prompt = build_synthesis_prompt(
            _make_transaction(), None, None, None, None
        )
        assert "UNAVAILABLE" in prompt

    def test_none_simple_lens_shows_unavailable(self):
        from utilities.orchestrator.synthesis import build_synthesis_prompt
        prompt = build_synthesis_prompt(
            _make_transaction(), _make_layer1_output(),
            _make_layer2_output(), _make_layer3_output(), None
        )
        assert "UNAVAILABLE" in prompt

    def test_json_template_present(self):
        from utilities.orchestrator.synthesis import build_synthesis_prompt
        prompt = build_synthesis_prompt(
            _make_transaction(), None, None, None, None
        )
        assert "recommendation_justification" in prompt


# ===========================================================================
# Group 2: parse_synthesis_response
# ===========================================================================

class TestParseSynthesisResponse:

    def test_valid_json_returned_as_dict(self):
        from utilities.orchestrator.synthesis import parse_synthesis_response
        data = {"recommendation": "Investigate further", "limitations": "None."}
        result = parse_synthesis_response(json.dumps(data))
        assert result["recommendation"] == "Investigate further"

    def test_json_fence_stripped(self):
        from utilities.orchestrator.synthesis import parse_synthesis_response
        data = {"recommendation": "Monitor"}
        text = "```json\n" + json.dumps(data) + "\n```"
        result = parse_synthesis_response(text)
        assert result["recommendation"] == "Monitor"

    def test_plain_fence_stripped(self):
        from utilities.orchestrator.synthesis import parse_synthesis_response
        data = {"recommendation": "Ignore"}
        text = "```\n" + json.dumps(data) + "\n```"
        result = parse_synthesis_response(text)
        assert result["recommendation"] == "Ignore"

    def test_invalid_json_returns_error(self):
        from utilities.orchestrator.synthesis import parse_synthesis_response
        result = parse_synthesis_response("not valid json {{{")
        assert "_error" in result

    def test_raw_text_preserved_on_error(self):
        from utilities.orchestrator.synthesis import parse_synthesis_response
        bad = "not valid json {{{"
        result = parse_synthesis_response(bad)
        assert result.get("_raw_text") == bad


# ===========================================================================
# Group 3: run_synthesis
# ===========================================================================

class TestRunSynthesis:

    def test_returns_dict(self):
        from utilities.orchestrator.synthesis import run_synthesis
        db, _ = _make_mock_db()
        result = run_synthesis(
            "rns123", _make_transaction(),
            _make_investigation_result(), _make_simple_output(),
            _make_client(), db=db,
        )
        assert isinstance(result, dict)

    def test_calls_client_with_synthesis_model(self):
        from utilities.orchestrator.synthesis import run_synthesis
        from utilities.orchestrator.config import ORCHESTRATOR_CONFIG
        db, _ = _make_mock_db()
        client = _make_client()
        run_synthesis(
            "rns123", _make_transaction(),
            _make_investigation_result(), _make_simple_output(),
            client, db=db,
        )
        call_kwargs = client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == ORCHESTRATOR_CONFIG["synthesis_model"]

    def test_synthesis_tokens_in_token_usage(self):
        from utilities.orchestrator.synthesis import run_synthesis
        db, _ = _make_mock_db()
        client = _make_client(synthesis_input=600, synthesis_output=1200)
        result = run_synthesis(
            "rns123", _make_transaction(),
            _make_investigation_result(), _make_simple_output(),
            client, db=db,
        )
        assert result["token_usage"]["synthesis_input"] == 600
        assert result["token_usage"]["synthesis_output"] == 1200

    def test_layer_tokens_aggregated_in_total(self):
        from utilities.orchestrator.synthesis import run_synthesis
        db, _ = _make_mock_db()
        inv = _make_investigation_result()
        # layer totals: 100+150+200 input, 200+250+300 output
        client = _make_client(synthesis_input=0, synthesis_output=0)
        result = run_synthesis(
            "rns123", _make_transaction(), inv, _make_simple_output(),
            client, db=db,
        )
        assert result["token_usage"]["total_input"] == 100 + 150 + 200
        assert result["token_usage"]["total_output"] == 200 + 250 + 300

    def test_token_usage_overwrites_placeholder_zeros(self):
        from utilities.orchestrator.synthesis import run_synthesis
        db, _ = _make_mock_db()
        # LLM returns zeros in token_usage — we must overwrite them
        client = _make_client(synthesis_input=500, synthesis_output=1000)
        result = run_synthesis(
            "rns123", _make_transaction(),
            _make_investigation_result(), _make_simple_output(),
            client, db=db,
        )
        # synthesis_input should be 500, not 0
        assert result["token_usage"]["synthesis_input"] == 500

    def test_persists_to_firestore(self):
        from utilities.orchestrator.synthesis import run_synthesis
        db, doc_ref = _make_mock_db()
        run_synthesis(
            "rns123", _make_transaction(),
            _make_investigation_result(), _make_simple_output(),
            _make_client(), db=db,
        )
        db.collection.assert_called_with("signals")
        db.collection.return_value.document.assert_called_with("rns123")
        assert doc_ref.set.called

    def test_result_has_recommendation(self):
        from utilities.orchestrator.synthesis import run_synthesis
        db, _ = _make_mock_db()
        result = run_synthesis(
            "rns123", _make_transaction(),
            _make_investigation_result(), _make_simple_output(),
            _make_client(), db=db,
        )
        assert "recommendation" in result

    def test_none_layer_outputs_in_investigation_result(self):
        from utilities.orchestrator.synthesis import run_synthesis
        db, _ = _make_mock_db()
        inv = _make_investigation_result(l1=False, l2=False, l3=False)
        result = run_synthesis(
            "rns123", _make_transaction(), inv, None,
            _make_client(), db=db,
        )
        assert isinstance(result, dict)

    def test_parse_error_returns_error_dict(self):
        from utilities.orchestrator.synthesis import run_synthesis
        db, _ = _make_mock_db()
        # Make client return invalid JSON
        mock_resp = MagicMock()
        mock_resp.usage.input_tokens = 100
        mock_resp.usage.output_tokens = 50
        mock_resp.content = [MagicMock(type="text", text="not json {{{{")]
        client = MagicMock()
        client.messages.create.return_value = mock_resp
        result = run_synthesis(
            "rns123", _make_transaction(),
            _make_investigation_result(), None,
            client, db=db,
        )
        assert "_error" in result

    def test_client_exception_updates_status_and_reraises(self):
        from utilities.orchestrator.synthesis import run_synthesis
        db, doc_ref = _make_mock_db()
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("API down")
        with pytest.raises(RuntimeError, match="API down"):
            run_synthesis(
                "rns123", _make_transaction(),
                _make_investigation_result(), None,
                client, db=db,
            )
        # Best-effort agentic_status="failed" write should have been attempted
        assert doc_ref.set.called


# ===========================================================================
# Group 4: persist_synthesis_result
# ===========================================================================

class TestPersistSynthesisResult:

    def _call(self, synthesis_result, token_usage=None):
        from utilities.orchestrator.persistence import persist_synthesis_result
        db, doc_ref = _make_mock_db()
        persist_synthesis_result(
            "rns_abc",
            synthesis_result,
            token_usage or {"total_input": 100, "total_output": 200, "estimated_cost_gbp": 0.01},
            db=db,
        )
        return doc_ref

    def test_sets_complete_status(self):
        doc_ref = self._call(_make_synthesis_json())
        written = doc_ref.set.call_args[0][0]
        assert written["agentic_status"] == "complete"

    def test_sets_recommendation(self):
        doc_ref = self._call(_make_synthesis_json())
        written = doc_ref.set.call_args[0][0]
        assert written["agentic_recommendation"] == "Investigate further"

    def test_sets_justification(self):
        doc_ref = self._call(_make_synthesis_json())
        written = doc_ref.set.call_args[0][0]
        assert "Strong first-time purchase" in written["agentic_justification"]

    def test_sets_limitations(self):
        doc_ref = self._call(_make_synthesis_json())
        written = doc_ref.set.call_args[0][0]
        assert "bid/ask spread" in written["agentic_limitations"]

    def test_error_result_sets_failed_status(self):
        error_result = {"_error": "JSON parse failed: ...", "_raw_text": "bad json"}
        doc_ref = self._call(error_result)
        written = doc_ref.set.call_args[0][0]
        assert written["agentic_status"] == "failed"
