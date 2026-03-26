"""
Tests for utilities/orchestrator/classification.py — Stage 1.

All Anthropic API calls and Firestore writes are mocked.
No live network calls are made.
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call


def _make_announcement(**overrides):
    base = {
        "company_name": "Foresight Group Holdings",
        "ticker": "FGEN",
        "source_name": "LSEG RNS",
        "headline": "Director/PDMR Shareholding",
        "body": "John Smith, CEO, purchased 50,000 shares at 125p on 15 Feb 2026.",
        "published_at": "2026-02-17T08:00:00Z",
    }
    base.update(overrides)
    return base


def _pdmr_json(transactions=None) -> str:
    if transactions is None:
        transactions = [{
            "director_name": "John Smith",
            "director_role": "Chief Executive Officer",
            "transaction_type": "purchase",
            "transaction_category": "open_market_purchase",
            "transaction_date_actual": "2026-02-15",
            "transaction_date_reported": "2026-02-17",
            "reporting_lag_days": 2,
            "shares_transacted": 50000.0,
            "price_per_share_raw": "125p",
            "price_per_share_gbp": 1.25,
            "total_consideration_gbp": 62500.0,
            "currency_original": "GBP",
            "previous_holding": 200000.0,
            "resulting_holding": 250000.0,
            "resulting_holding_pct": 0.85,
            "isin": "GB00B0N8QD54",
            "lei": "",
            "exchange": "LONDON STOCK EXCHANGE (XLON)",
            "plan_name": None,
            "extraction_confidence": "high",
            "confidence_notes": "Price stated in pence, divided by 100",
        }]
    return json.dumps({
        "article_type": "pdmr_transaction",
        "summary": "John Smith purchased 50,000 shares at 125p.",
        "sentiment": None,
        "key_topics": None,
        "article_subtype": None,
        "transactions": transactions,
    })


def _make_mock_db():
    db = MagicMock()
    mock_doc_ref = MagicMock()
    mock_doc_ref.id = "mock_doc_id"
    db.collection.return_value.document.return_value = mock_doc_ref
    return db


# ===========================================================================
# Group 1: Prompt building
# ===========================================================================

class TestBuildClassificationPrompt:

    def test_includes_company_name(self):
        from utilities.orchestrator.classification import build_classification_prompt
        prompt = build_classification_prompt(_make_announcement())
        assert "Foresight Group Holdings" in prompt

    def test_includes_ticker(self):
        from utilities.orchestrator.classification import build_classification_prompt
        prompt = build_classification_prompt(_make_announcement())
        assert "FGEN" in prompt

    def test_includes_headline(self):
        from utilities.orchestrator.classification import build_classification_prompt
        prompt = build_classification_prompt(_make_announcement())
        assert "Director/PDMR Shareholding" in prompt

    def test_includes_body(self):
        from utilities.orchestrator.classification import build_classification_prompt
        prompt = build_classification_prompt(_make_announcement())
        assert "John Smith, CEO, purchased 50,000 shares" in prompt

    def test_includes_priority_rules(self):
        from utilities.orchestrator.classification import build_classification_prompt
        prompt = build_classification_prompt(_make_announcement())
        assert "CLASSIFICATION PRIORITY RULES" in prompt

    def test_includes_all_four_article_types(self):
        from utilities.orchestrator.classification import build_classification_prompt
        prompt = build_classification_prompt(_make_announcement())
        for t in ["pdmr_transaction", "regulatory_catalyst",
                  "substantive_news", "administrative"]:
            assert t in prompt

    def test_instructs_full_body_classification(self):
        from utilities.orchestrator.classification import build_classification_prompt
        prompt = build_classification_prompt(_make_announcement())
        assert "FULL body text" in prompt

    def test_requests_json_response_format(self):
        from utilities.orchestrator.classification import build_classification_prompt
        prompt = build_classification_prompt(_make_announcement())
        assert "RESPOND IN EXACTLY THIS JSON FORMAT" in prompt


# ===========================================================================
# Group 2: Response parsing
# ===========================================================================

class TestParseClassificationResponse:

    def test_parses_pdmr_transaction(self):
        from utilities.orchestrator.classification import parse_classification_response
        result = parse_classification_response(_pdmr_json())
        assert result["article_type"] == "pdmr_transaction"
        assert result["extraction_status"] == "complete"
        assert len(result["transactions"]) == 1

    def test_parses_regulatory_catalyst(self):
        from utilities.orchestrator.classification import parse_classification_response
        text = json.dumps({
            "article_type": "regulatory_catalyst",
            "summary": "Planning permission granted.",
            "sentiment": None, "key_topics": None, "article_subtype": None,
            "transactions": [],
        })
        result = parse_classification_response(text)
        assert result["article_type"] == "regulatory_catalyst"
        assert result["transactions"] == []

    def test_parses_substantive_news_with_sentiment(self):
        from utilities.orchestrator.classification import parse_classification_response
        text = json.dumps({
            "article_type": "substantive_news",
            "summary": "Strong Q3 trading update.",
            "sentiment": "positive",
            "key_topics": ["trading update", "revenue"],
            "article_subtype": "trading_update",
            "transactions": [],
        })
        result = parse_classification_response(text)
        assert result["article_type"] == "substantive_news"
        assert result["sentiment"] == "positive"
        assert "trading update" in result["key_topics"]

    def test_parses_administrative(self):
        from utilities.orchestrator.classification import parse_classification_response
        text = json.dumps({
            "article_type": "administrative",
            "summary": "AGM notice.",
            "sentiment": None, "key_topics": None, "article_subtype": None,
            "transactions": [],
        })
        result = parse_classification_response(text)
        assert result["article_type"] == "administrative"

    def test_invalid_json_returns_failed_status(self):
        from utilities.orchestrator.classification import parse_classification_response
        result = parse_classification_response("not json at all")
        assert result["extraction_status"] == "failed"
        assert result["article_type"] == "administrative"

    def test_unrecognised_article_type_treated_as_administrative(self):
        from utilities.orchestrator.classification import parse_classification_response
        text = json.dumps({
            "article_type": "mystery_type",
            "summary": "Unknown.", "sentiment": None, "key_topics": None,
            "article_subtype": None, "transactions": [],
        })
        result = parse_classification_response(text)
        assert result["article_type"] == "administrative"

    def test_strips_markdown_fences(self):
        from utilities.orchestrator.classification import parse_classification_response
        fenced = "```json\n" + _pdmr_json() + "\n```"
        result = parse_classification_response(fenced)
        assert result["article_type"] == "pdmr_transaction"
        assert result["extraction_status"] == "complete"

    def test_non_pdmr_has_empty_transactions(self):
        from utilities.orchestrator.classification import parse_classification_response
        text = json.dumps({
            "article_type": "regulatory_catalyst",
            "summary": "Approval received.", "sentiment": None, "key_topics": None,
            "article_subtype": None, "transactions": [],
        })
        result = parse_classification_response(text)
        assert result["transactions"] == []

    def test_multi_transaction_filing_preserved(self):
        from utilities.orchestrator.classification import parse_classification_response
        tx = json.loads(_pdmr_json())
        tx["transactions"].append({**tx["transactions"][0], "director_name": "Jane Doe"})
        result = parse_classification_response(json.dumps(tx))
        assert len(result["transactions"]) == 2

    def test_missing_transactions_key_defaults_to_list(self):
        from utilities.orchestrator.classification import parse_classification_response
        text = json.dumps({
            "article_type": "regulatory_catalyst",
            "summary": "Licence granted.",
        })
        result = parse_classification_response(text)
        assert isinstance(result["transactions"], list)


# ===========================================================================
# Group 3: LLM call
# ===========================================================================

class TestCallClassification:

    def _mock_call_gemini(self, response_text, input_tokens=100, output_tokens=200):
        """Return a patch target and return value for call_gemini."""
        token_usage = {"input": input_tokens, "output": output_tokens, "model": "gemini-2.0-flash"}
        return patch(
            "utilities.orchestrator.classification.call_gemini",
            return_value=(response_text, token_usage),
        )

    def test_uses_classification_model_from_config(self):
        from utilities.orchestrator.classification import call_classification
        config = {"classification_model": "gemini-test-model"}
        with patch("utilities.orchestrator.classification.call_gemini") as mock_cg:
            mock_cg.return_value = (_pdmr_json(), {"input": 0, "output": 0, "model": "gemini-test-model"})
            call_classification(_make_announcement(), config=config)
            _, call_kwargs = mock_cg.call_args
            assert mock_cg.call_args[0][1] == "gemini-test-model"

    def test_returns_token_usage(self):
        from utilities.orchestrator.classification import call_classification
        with self._mock_call_gemini(_pdmr_json(), input_tokens=150, output_tokens=300):
            _, token_usage = call_classification(_make_announcement())
        assert token_usage["input"] == 150
        assert token_usage["output"] == 300

    def test_token_usage_includes_model(self):
        from utilities.orchestrator.classification import call_classification
        with self._mock_call_gemini(_pdmr_json()):
            _, token_usage = call_classification(_make_announcement())
        assert "model" in token_usage

    def test_api_error_returns_failed_result_with_zero_tokens(self):
        from utilities.orchestrator.classification import call_classification
        with patch(
            "utilities.orchestrator.classification.call_gemini",
            side_effect=Exception("API unavailable"),
        ):
            result, token_usage = call_classification(_make_announcement())
        assert result["extraction_status"] == "failed"
        assert token_usage["input"] == 0
        assert token_usage["output"] == 0

    def test_json_parse_error_returns_failed_result(self):
        from utilities.orchestrator.classification import call_classification
        with self._mock_call_gemini("this is not valid json"):
            result, _ = call_classification(_make_announcement())
        assert result["extraction_status"] == "failed"


# ===========================================================================
# Group 4: Routing
# ===========================================================================

class TestRouteClassification:

    def _run_route(self, article_type, transactions=None, **cls_extra):
        from utilities.orchestrator.classification import route_classification
        db = _make_mock_db()

        classification = {
            "article_type": article_type,
            "extraction_status": "complete",
            "summary": "Test summary.",
            "sentiment": None,
            "key_topics": None,
            "article_subtype": None,
            "transactions": transactions or [],
            **cls_extra,
        }
        announcement = _make_announcement()
        article_type_returned = route_classification(
            rns_article_id="rns_abc",
            announcement=announcement,
            classification=classification,
            db=db,
        )
        return article_type_returned, classification, db

    def test_always_updates_announcement_document(self):
        _, _, db = self._run_route("administrative")
        db.collection.assert_any_call("announcements")

    def test_pdmr_creates_transaction_documents(self):
        tx = json.loads(_pdmr_json())["transactions"]
        returned, cls, db = self._run_route("pdmr_transaction", transactions=tx)
        db.collection.assert_any_call("pdmr_transactions")
        assert "persisted_transaction_ids" in cls
        assert len(cls["persisted_transaction_ids"]) == 1

    def test_pdmr_multi_transaction_creates_multiple_docs(self):
        tx = json.loads(_pdmr_json())["transactions"]
        tx.append({**tx[0], "director_name": "Jane Doe"})
        _, cls, db = self._run_route("pdmr_transaction", transactions=tx)
        assert len(cls["persisted_transaction_ids"]) == 2

    def test_substantive_news_creates_summary_document(self):
        _, _, db = self._run_route(
            "substantive_news",
            sentiment="positive",
            key_topics=["revenue"],
            article_subtype="trading_update",
        )
        db.collection.assert_any_call("company_news_summaries")

    def test_regulatory_catalyst_no_child_documents(self):
        _, _, db = self._run_route("regulatory_catalyst")
        called_collections = [c[0][0] for c in db.collection.call_args_list]
        assert "pdmr_transactions" not in called_collections
        assert "company_news_summaries" not in called_collections

    def test_administrative_no_child_documents(self):
        _, _, db = self._run_route("administrative")
        called_collections = [c[0][0] for c in db.collection.call_args_list]
        assert "pdmr_transactions" not in called_collections
        assert "company_news_summaries" not in called_collections

    def test_returns_article_type(self):
        returned, _, _ = self._run_route("regulatory_catalyst")
        assert returned == "regulatory_catalyst"


# ===========================================================================
# Group 5: get_open_market_transactions
# ===========================================================================

class TestGetOpenMarketTransactions:

    def test_returns_only_open_market_purchase(self):
        from utilities.orchestrator.classification import get_open_market_transactions
        cls = {
            "transactions": [
                {"transaction_category": "open_market_purchase"},
                {"transaction_category": "sip_purchase"},
                {"transaction_category": "drip_purchase"},
            ]
        }
        result = get_open_market_transactions(cls)
        assert len(result) == 1
        assert result[0]["transaction_category"] == "open_market_purchase"

    def test_returns_open_market_disposal(self):
        from utilities.orchestrator.classification import get_open_market_transactions
        cls = {
            "transactions": [
                {"transaction_category": "open_market_disposal"},
                {"transaction_category": "option_exercise"},
            ]
        }
        result = get_open_market_transactions(cls)
        assert len(result) == 1
        assert result[0]["transaction_category"] == "open_market_disposal"

    def test_returns_empty_for_non_open_market(self):
        from utilities.orchestrator.classification import get_open_market_transactions
        cls = {
            "transactions": [
                {"transaction_category": "sip_purchase"},
                {"transaction_category": "share_award_vesting"},
            ]
        }
        result = get_open_market_transactions(cls)
        assert result == []

    def test_returns_multiple_open_market(self):
        from utilities.orchestrator.classification import get_open_market_transactions
        cls = {
            "transactions": [
                {"transaction_category": "open_market_purchase", "director_name": "A"},
                {"transaction_category": "open_market_purchase", "director_name": "B"},
                {"transaction_category": "sip_award", "director_name": "C"},
            ]
        }
        result = get_open_market_transactions(cls)
        assert len(result) == 2

    def test_empty_transactions_list(self):
        from utilities.orchestrator.classification import get_open_market_transactions
        result = get_open_market_transactions({"transactions": []})
        assert result == []

    def test_missing_transactions_key(self):
        from utilities.orchestrator.classification import get_open_market_transactions
        result = get_open_market_transactions({})
        assert result == []
