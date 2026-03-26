"""
Tests for utilities/orchestrator/simple_lens.py — Stage 2.

All Anthropic API calls and Firestore writes are mocked.
No live network calls are made.
"""

import pytest
from unittest.mock import MagicMock, patch


def _sample_transaction(**overrides) -> dict:
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
        "extraction_confidence": "high",
        "confidence_notes": "Price stated in pence, divided by 100",
    }
    base.update(overrides)
    return base


def _sample_profile(**overrides) -> dict:
    base = {
        "market_cap_gbp": 45_000_000,
        "market_cap_stale": False,
        "index_membership": "AIM",
    }
    base.update(overrides)
    return base


def _sample_response() -> str:
    return """TRANSACTION_NATURE: Position increase
POSITION_CHANGE_PCT: 25% — (250,000 - 200,000) / 200,000 × 100 = 25%
COMMITMENT_SIZE: Material — £62,500 at a £45m market cap company represents a meaningful personal commitment of ~0.14% of market cap.
HOLDING_SIGNIFICANCE: Moderate — resulting holding of 0.85% of issued capital is a meaningful personal stake at this market cap.
SIGNAL_STRENGTH: 7/10 — Position increase of 25% with material consideration. Reporting lag is within normal range.
SIGNAL_DIRECTION: Positive
REPORTING_LAG_ASSESSMENT: Within normal range — 2 days between transaction and reporting.
LIMITATIONS:
1. Whether current share price represents good or poor value — no price history available.
2. Whether other insiders are moving in the same direction.
3. Whether the company is a suitable trading platform (liquidity, volatility, spread).
4. Whether this signal has already been priced in by the market.
5. Director's track record and credibility at this company.
RECOMMENDED_ACTION: Investigate further
SUMMARY: CEO purchased 50,000 shares representing a 25% increase in their holding at a total consideration of £62,500. At a £45m market cap company this is a material commitment suggesting genuine conviction. Further investigation is warranted to assess platform suitability and signal freshness."""


def _make_mock_db():
    db = MagicMock()
    doc_ref = MagicMock()
    doc_ref.id = "mock_signal_id"
    db.collection.return_value.document.return_value = doc_ref
    return db, doc_ref


# ===========================================================================
# Group 1: Prompt building
# ===========================================================================

class TestBuildSimpleLensPrompt:

    def test_includes_company_name(self):
        from utilities.orchestrator.simple_lens import build_simple_lens_prompt
        prompt = build_simple_lens_prompt(_sample_transaction(), _sample_profile())
        assert "Foresight Group Holdings" in prompt

    def test_includes_ticker(self):
        from utilities.orchestrator.simple_lens import build_simple_lens_prompt
        prompt = build_simple_lens_prompt(_sample_transaction(), _sample_profile())
        assert "FGEN" in prompt

    def test_includes_director_name(self):
        from utilities.orchestrator.simple_lens import build_simple_lens_prompt
        prompt = build_simple_lens_prompt(_sample_transaction(), _sample_profile())
        assert "John Smith" in prompt

    def test_includes_total_consideration(self):
        from utilities.orchestrator.simple_lens import build_simple_lens_prompt
        prompt = build_simple_lens_prompt(_sample_transaction(), _sample_profile())
        assert "62500" in prompt

    def test_includes_reporting_lag(self):
        from utilities.orchestrator.simple_lens import build_simple_lens_prompt
        prompt = build_simple_lens_prompt(_sample_transaction(), _sample_profile())
        assert "Reporting Lag" in prompt
        assert "2" in prompt

    def test_formats_market_cap_as_millions(self):
        from utilities.orchestrator.simple_lens import build_simple_lens_prompt
        prompt = build_simple_lens_prompt(_sample_transaction(), _sample_profile())
        assert "£45m" in prompt

    def test_formats_large_market_cap_as_billions(self):
        from utilities.orchestrator.simple_lens import build_simple_lens_prompt
        profile = _sample_profile(market_cap_gbp=1_500_000_000)
        prompt = build_simple_lens_prompt(_sample_transaction(), profile)
        assert "£1.5bn" in prompt

    def test_handles_unknown_market_cap(self):
        from utilities.orchestrator.simple_lens import build_simple_lens_prompt
        prompt = build_simple_lens_prompt(_sample_transaction(), {})
        assert "Unknown" in prompt

    def test_includes_index_membership(self):
        from utilities.orchestrator.simple_lens import build_simple_lens_prompt
        prompt = build_simple_lens_prompt(_sample_transaction(), _sample_profile())
        assert "AIM" in prompt

    def test_includes_limitations_instruction(self):
        from utilities.orchestrator.simple_lens import build_simple_lens_prompt
        prompt = build_simple_lens_prompt(_sample_transaction(), _sample_profile())
        assert "LIMITATIONS" in prompt

    def test_specifies_response_format(self):
        from utilities.orchestrator.simple_lens import build_simple_lens_prompt
        prompt = build_simple_lens_prompt(_sample_transaction(), _sample_profile())
        assert "SIGNAL_STRENGTH:" in prompt
        assert "RECOMMENDED_ACTION:" in prompt


# ===========================================================================
# Group 2: Response parsing
# ===========================================================================

class TestParseSimpleLensResponse:

    def test_extracts_transaction_nature(self):
        from utilities.orchestrator.simple_lens import parse_simple_lens_response
        result = parse_simple_lens_response(_sample_response())
        assert result["TRANSACTION_NATURE"] == "Position increase"

    def test_extracts_signal_strength_as_integer(self):
        from utilities.orchestrator.simple_lens import parse_simple_lens_response
        result = parse_simple_lens_response(_sample_response())
        assert result["SIGNAL_STRENGTH"] == 7
        assert isinstance(result["SIGNAL_STRENGTH"], int)

    def test_extracts_signal_direction(self):
        from utilities.orchestrator.simple_lens import parse_simple_lens_response
        result = parse_simple_lens_response(_sample_response())
        assert result["SIGNAL_DIRECTION"] == "Positive"

    def test_extracts_recommended_action(self):
        from utilities.orchestrator.simple_lens import parse_simple_lens_response
        result = parse_simple_lens_response(_sample_response())
        assert result["RECOMMENDED_ACTION"] == "Investigate further"

    def test_extracts_position_change_pct_as_float(self):
        from utilities.orchestrator.simple_lens import parse_simple_lens_response
        result = parse_simple_lens_response(_sample_response())
        assert result["POSITION_CHANGE_PCT"] == 25.0
        assert isinstance(result["POSITION_CHANGE_PCT"], float)

    def test_position_change_pct_is_none_for_new_position(self):
        from utilities.orchestrator.simple_lens import parse_simple_lens_response
        text = _sample_response().replace(
            "POSITION_CHANGE_PCT: 25% — (250,000 - 200,000) / 200,000 × 100 = 25%",
            "POSITION_CHANGE_PCT: New position — previous holding was zero",
        )
        result = parse_simple_lens_response(text)
        assert result["POSITION_CHANGE_PCT"] is None

    def test_captures_multiline_limitations(self):
        from utilities.orchestrator.simple_lens import parse_simple_lens_response
        result = parse_simple_lens_response(_sample_response())
        limitations = result["LIMITATIONS"]
        assert "price" in limitations.lower() or "current share" in limitations.lower()
        # Should capture all five limitation points
        assert "1." in limitations
        assert "5." in limitations

    def test_captures_multiline_summary(self):
        from utilities.orchestrator.simple_lens import parse_simple_lens_response
        result = parse_simple_lens_response(_sample_response())
        assert len(result["SUMMARY"]) > 50  # multi-sentence, not just first line

    def test_signal_strength_clamped_to_1_10(self):
        from utilities.orchestrator.simple_lens import _parse_signal_strength
        assert _parse_signal_strength("11/10 — very high") == 10
        assert _parse_signal_strength("0/10 — very low") == 1

    def test_missing_fields_default_to_empty_string(self):
        from utilities.orchestrator.simple_lens import parse_simple_lens_response
        result = parse_simple_lens_response("SIGNAL_DIRECTION: Positive")
        assert result["TRANSACTION_NATURE"] == ""
        assert result["SUMMARY"] == ""

    def test_signal_strength_returns_none_for_unparseable(self):
        from utilities.orchestrator.simple_lens import _parse_signal_strength
        assert _parse_signal_strength("not a score") is None
        assert _parse_signal_strength("") is None

    def test_negative_position_change_extracted(self):
        from utilities.orchestrator.simple_lens import _parse_position_change
        assert _parse_position_change("-33% — sold one third") == -33.0

    def test_raw_dict_preserved(self):
        from utilities.orchestrator.simple_lens import parse_simple_lens_response
        result = parse_simple_lens_response(_sample_response())
        assert "_raw" in result
        assert isinstance(result["_raw"], dict)


# ===========================================================================
# Group 3: LLM call
# ===========================================================================

class TestCallSimpleLens:

    def _mock_call_gemini(self, response_text, input_tokens=80, output_tokens=400):
        token_usage = {"input": input_tokens, "output": output_tokens, "model": "gemini-2.0-flash"}
        return patch(
            "utilities.orchestrator.simple_lens.call_gemini",
            return_value=(response_text, token_usage),
        )

    def test_uses_simple_lens_model_from_config(self):
        from utilities.orchestrator.simple_lens import call_simple_lens
        config = {"simple_lens_model": "gemini-test-flash"}
        with patch("utilities.orchestrator.simple_lens.call_gemini") as mock_cg:
            mock_cg.return_value = (_sample_response(), {"input": 0, "output": 0, "model": "gemini-test-flash"})
            call_simple_lens(_sample_transaction(), _sample_profile(), config=config)
            assert mock_cg.call_args[0][1] == "gemini-test-flash"

    def test_returns_token_usage(self):
        from utilities.orchestrator.simple_lens import call_simple_lens
        with self._mock_call_gemini(_sample_response(), input_tokens=120, output_tokens=380):
            _, token_usage = call_simple_lens(_sample_transaction(), _sample_profile())
        assert token_usage["input"] == 120
        assert token_usage["output"] == 380
        assert "model" in token_usage

    def test_api_error_returns_empty_result_with_zero_tokens(self):
        from utilities.orchestrator.simple_lens import call_simple_lens
        with patch(
            "utilities.orchestrator.simple_lens.call_gemini",
            side_effect=Exception("API unavailable"),
        ):
            result, token_usage = call_simple_lens(_sample_transaction(), _sample_profile())
        assert result["SIGNAL_STRENGTH"] is None
        assert result["RECOMMENDED_ACTION"] == ""
        assert token_usage["input"] == 0
        assert "_parse_error" in result

    def test_result_contains_all_expected_keys(self):
        from utilities.orchestrator.simple_lens import call_simple_lens
        with self._mock_call_gemini(_sample_response()):
            result, _ = call_simple_lens(_sample_transaction(), _sample_profile())
        for key in [
            "TRANSACTION_NATURE", "POSITION_CHANGE_PCT", "COMMITMENT_SIZE",
            "HOLDING_SIGNIFICANCE", "SIGNAL_STRENGTH", "SIGNAL_DIRECTION",
            "REPORTING_LAG_ASSESSMENT", "LIMITATIONS", "RECOMMENDED_ACTION", "SUMMARY",
        ]:
            assert key in result, f"Missing key: {key}"


# ===========================================================================
# Group 4: Persistence (persist_simple_lens_result)
# ===========================================================================

class TestPersistSimpleLensResult:

    def _parsed_result(self):
        from utilities.orchestrator.simple_lens import parse_simple_lens_response
        return parse_simple_lens_response(_sample_response())

    def test_writes_to_signals_collection(self):
        from utilities.orchestrator.persistence import persist_simple_lens_result
        db, doc_ref = _make_mock_db()

        persist_simple_lens_result(
            rns_article_id="rns_abc",
            ticker="FGEN",
            company_name="Foresight Group Holdings",
            published_at="2026-02-17",
            signal_type="director_buying",
            simple_result=self._parsed_result(),
            db=db,
        )

        db.collection.assert_called_with("signals")

    def test_uses_rns_article_id_as_document_id(self):
        from utilities.orchestrator.persistence import persist_simple_lens_result
        db, doc_ref = _make_mock_db()

        persist_simple_lens_result(
            rns_article_id="unique_rns_id_001",
            ticker="FGEN", company_name="X", published_at=None,
            signal_type="director_buying", simple_result=self._parsed_result(), db=db,
        )

        db.collection.return_value.document.assert_called_with("unique_rns_id_001")

    def test_writes_simple_signal_strength(self):
        from utilities.orchestrator.persistence import persist_simple_lens_result
        db, doc_ref = _make_mock_db()

        persist_simple_lens_result(
            "rns_abc", "FGEN", "X", None, "director_buying",
            self._parsed_result(), db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert written["simple_signal_strength"] == 7

    def test_sets_agentic_status_pending(self):
        from utilities.orchestrator.persistence import persist_simple_lens_result
        db, doc_ref = _make_mock_db()

        persist_simple_lens_result(
            "rns_abc", "FGEN", "X", None, "director_buying",
            self._parsed_result(), db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert written["agentic_status"] == "pending"

    def test_sets_investor_action_pending(self):
        from utilities.orchestrator.persistence import persist_simple_lens_result
        db, doc_ref = _make_mock_db()

        persist_simple_lens_result(
            "rns_abc", "FGEN", "X", None, "director_buying",
            self._parsed_result(), db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert written["investor_action"] == "pending"

    def test_uses_merge_true(self):
        from utilities.orchestrator.persistence import persist_simple_lens_result
        db, doc_ref = _make_mock_db()

        persist_simple_lens_result(
            "rns_abc", "FGEN", "X", None, "director_buying",
            self._parsed_result(), db=db,
        )

        assert doc_ref.set.call_args.kwargs.get("merge") is True

    def test_sets_expires_at(self):
        from utilities.orchestrator.persistence import persist_simple_lens_result
        from datetime import datetime
        db, doc_ref = _make_mock_db()

        persist_simple_lens_result(
            "rns_abc", "FGEN", "X", None, "director_buying",
            self._parsed_result(), db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert "expires_at" in written
        assert isinstance(written["expires_at"], datetime)


# ===========================================================================
# Group 5: run_simple_lens integration
# ===========================================================================

class TestRunSimpleLens:

    def test_signal_type_is_director_buying_for_purchase(self):
        from utilities.orchestrator.simple_lens import run_simple_lens
        db, doc_ref = _make_mock_db()
        client = _make_client(_sample_response())

        run_simple_lens("rns_abc", _sample_transaction(), _sample_profile(), client, db=db)

        written = doc_ref.set.call_args[0][0]
        assert written["signal_type"] == "director_buying"

    def test_signal_type_is_director_disposal_for_disposal(self):
        from utilities.orchestrator.simple_lens import run_simple_lens
        db, doc_ref = _make_mock_db()
        client = _make_client(_sample_response())
        tx = _sample_transaction(transaction_category="open_market_disposal")

        run_simple_lens("rns_abc", tx, _sample_profile(), client, db=db)

        written = doc_ref.set.call_args[0][0]
        assert written["signal_type"] == "director_disposal"

    def test_returns_result_and_token_usage(self):
        from utilities.orchestrator.simple_lens import run_simple_lens
        db, _ = _make_mock_db()
        client = _make_client(_sample_response(), input_tokens=90, output_tokens=350)

        result, token_usage = run_simple_lens(
            "rns_abc", _sample_transaction(), _sample_profile(), client, db=db
        )

        assert result["SIGNAL_STRENGTH"] == 7
        assert token_usage["input"] == 90
        assert token_usage["output"] == 350

    def test_persists_to_firestore(self):
        from utilities.orchestrator.simple_lens import run_simple_lens
        db, doc_ref = _make_mock_db()
        client = _make_client(_sample_response())

        run_simple_lens("rns_abc", _sample_transaction(), _sample_profile(), client, db=db)

        doc_ref.set.assert_called_once()
