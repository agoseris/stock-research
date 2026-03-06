"""
Tests for utilities/orchestrator/persistence.py — Stage 1 operations.

All Firestore interaction is mocked. No live Firestore calls are made.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_db():
    """Return a mock Firestore client with collection().document() chain."""
    db = MagicMock()
    mock_doc_ref = MagicMock()
    mock_doc_ref.id = "mock_doc_id_123"
    db.collection.return_value.document.return_value = mock_doc_ref
    return db, mock_doc_ref


def _sample_transaction() -> dict:
    return {
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
        "confidence_notes": "Price stated in pence, divided by 100 to normalise to GBP",
    }


# ---------------------------------------------------------------------------
# update_announcement_classification
# ---------------------------------------------------------------------------

class TestUpdateAnnouncementClassification:

    def test_writes_to_correct_collection_and_document(self):
        from utilities.orchestrator.persistence import update_announcement_classification
        db, doc_ref = _make_mock_db()

        update_announcement_classification(
            rns_article_id="abc123",
            article_type="pdmr_transaction",
            extraction_status="complete",
            db=db,
        )

        db.collection.assert_called_once_with("announcements")
        db.collection.return_value.document.assert_called_once_with("abc123")
        doc_ref.set.assert_called_once()

    def test_uses_merge_true(self):
        from utilities.orchestrator.persistence import update_announcement_classification
        db, doc_ref = _make_mock_db()

        update_announcement_classification(
            rns_article_id="abc123",
            article_type="regulatory_catalyst",
            extraction_status="complete",
            db=db,
        )

        _, kwargs = doc_ref.set.call_args
        assert kwargs.get("merge") is True

    def test_writes_article_type_and_status(self):
        from utilities.orchestrator.persistence import update_announcement_classification
        db, doc_ref = _make_mock_db()

        update_announcement_classification(
            rns_article_id="abc123",
            article_type="substantive_news",
            extraction_status="complete",
            db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert written["article_type"] == "substantive_news"
        assert written["extraction_status"] == "complete"

    def test_writes_summary_when_provided(self):
        from utilities.orchestrator.persistence import update_announcement_classification
        db, doc_ref = _make_mock_db()

        update_announcement_classification(
            rns_article_id="abc123",
            article_type="regulatory_catalyst",
            extraction_status="complete",
            summary="Planning permission granted for solar farm.",
            db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert written["summary"] == "Planning permission granted for solar farm."

    def test_no_summary_key_when_not_provided(self):
        from utilities.orchestrator.persistence import update_announcement_classification
        db, doc_ref = _make_mock_db()

        update_announcement_classification(
            rns_article_id="abc123",
            article_type="administrative",
            extraction_status="complete",
            db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert "summary" not in written

    def test_pdmr_transaction_has_no_expires_at(self):
        from utilities.orchestrator.persistence import update_announcement_classification
        db, doc_ref = _make_mock_db()

        update_announcement_classification(
            rns_article_id="abc123",
            article_type="pdmr_transaction",
            extraction_status="complete",
            db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert "expires_at" not in written

    def test_administrative_has_expires_at(self):
        from utilities.orchestrator.persistence import update_announcement_classification
        db, doc_ref = _make_mock_db()

        update_announcement_classification(
            rns_article_id="abc123",
            article_type="administrative",
            extraction_status="complete",
            db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert "expires_at" in written
        assert isinstance(written["expires_at"], datetime)


# ---------------------------------------------------------------------------
# persist_pdmr_transaction
# ---------------------------------------------------------------------------

class TestPersistPdmrTransaction:

    def test_writes_to_pdmr_transactions_collection(self):
        from utilities.orchestrator.persistence import persist_pdmr_transaction
        db, doc_ref = _make_mock_db()

        persist_pdmr_transaction(
            rns_article_id="rns001",
            ticker="FGEN",
            company_name="Foresight Group Holdings",
            transaction=_sample_transaction(),
            db=db,
        )

        db.collection.assert_called_with("pdmr_transactions")

    def test_returns_document_id(self):
        from utilities.orchestrator.persistence import persist_pdmr_transaction
        db, doc_ref = _make_mock_db()

        result = persist_pdmr_transaction(
            rns_article_id="rns001",
            ticker="FGEN",
            company_name="Foresight Group Holdings",
            transaction=_sample_transaction(),
            db=db,
        )

        assert result == "mock_doc_id_123"

    def test_sets_linkage_fields(self):
        from utilities.orchestrator.persistence import persist_pdmr_transaction
        db, doc_ref = _make_mock_db()

        persist_pdmr_transaction(
            rns_article_id="rns001",
            ticker="FGEN",
            company_name="Foresight Group Holdings",
            transaction=_sample_transaction(),
            db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert written["rns_article_id"] == "rns001"
        assert written["ticker"] == "FGEN"
        assert written["company_name"] == "Foresight Group Holdings"

    def test_is_open_market_true_for_open_market_purchase(self):
        from utilities.orchestrator.persistence import persist_pdmr_transaction
        db, doc_ref = _make_mock_db()

        tx = _sample_transaction()
        tx["transaction_category"] = "open_market_purchase"
        persist_pdmr_transaction("rns001", "FGEN", "Company", tx, db=db)

        written = doc_ref.set.call_args[0][0]
        assert written["is_open_market"] is True

    def test_is_open_market_true_for_open_market_disposal(self):
        from utilities.orchestrator.persistence import persist_pdmr_transaction
        db, doc_ref = _make_mock_db()

        tx = _sample_transaction()
        tx["transaction_category"] = "open_market_disposal"
        persist_pdmr_transaction("rns001", "FGEN", "Company", tx, db=db)

        written = doc_ref.set.call_args[0][0]
        assert written["is_open_market"] is True

    def test_is_open_market_false_for_sip_purchase(self):
        from utilities.orchestrator.persistence import persist_pdmr_transaction
        db, doc_ref = _make_mock_db()

        tx = _sample_transaction()
        tx["transaction_category"] = "sip_purchase"
        persist_pdmr_transaction("rns001", "FGEN", "Company", tx, db=db)

        written = doc_ref.set.call_args[0][0]
        assert written["is_open_market"] is False

    def test_sets_expires_at(self):
        from utilities.orchestrator.persistence import persist_pdmr_transaction
        db, doc_ref = _make_mock_db()

        persist_pdmr_transaction(
            rns_article_id="rns001",
            ticker="FGEN",
            company_name="Company",
            transaction=_sample_transaction(),
            db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert "expires_at" in written
        assert isinstance(written["expires_at"], datetime)

    def test_sets_created_at(self):
        from utilities.orchestrator.persistence import persist_pdmr_transaction
        db, doc_ref = _make_mock_db()

        persist_pdmr_transaction("rns001", "FGEN", "Company", _sample_transaction(), db=db)

        written = doc_ref.set.call_args[0][0]
        assert "created_at" in written
        assert isinstance(written["created_at"], datetime)


# ---------------------------------------------------------------------------
# persist_news_summary
# ---------------------------------------------------------------------------

class TestPersistNewsSummary:

    def _sample_classification(self):
        return {
            "article_type": "substantive_news",
            "article_subtype": "trading_update",
            "sentiment": "positive",
            "summary": "Company reports strong Q3 trading ahead of expectations.",
            "key_topics": ["trading update", "revenue growth"],
            "transactions": [],
        }

    def test_writes_to_company_news_summaries(self):
        from utilities.orchestrator.persistence import persist_news_summary
        db, doc_ref = _make_mock_db()

        persist_news_summary(
            rns_article_id="rns001",
            ticker="FGEN",
            published_at=datetime(2026, 2, 15, tzinfo=timezone.utc),
            classification=self._sample_classification(),
            db=db,
        )

        db.collection.assert_called_with("company_news_summaries")

    def test_writes_correct_fields(self):
        from utilities.orchestrator.persistence import persist_news_summary
        db, doc_ref = _make_mock_db()

        pub = datetime(2026, 2, 15, tzinfo=timezone.utc)
        persist_news_summary("rns001", "FGEN", pub, self._sample_classification(), db=db)

        written = doc_ref.set.call_args[0][0]
        assert written["rns_article_id"] == "rns001"
        assert written["ticker"] == "FGEN"
        assert written["published_at"] == pub
        assert written["article_subtype"] == "trading_update"
        assert written["sentiment"] == "positive"
        assert "trading update" in written["key_topics"]

    def test_sets_expires_at(self):
        from utilities.orchestrator.persistence import persist_news_summary
        db, doc_ref = _make_mock_db()

        persist_news_summary(
            "rns001", "FGEN",
            datetime(2026, 2, 15, tzinfo=timezone.utc),
            self._sample_classification(),
            db=db,
        )

        written = doc_ref.set.call_args[0][0]
        assert "expires_at" in written

    def test_returns_document_id(self):
        from utilities.orchestrator.persistence import persist_news_summary
        db, doc_ref = _make_mock_db()

        result = persist_news_summary(
            "rns001", "FGEN",
            datetime(2026, 2, 15, tzinfo=timezone.utc),
            self._sample_classification(),
            db=db,
        )

        assert result == "mock_doc_id_123"

    def test_key_topics_defaults_to_empty_list_when_null(self):
        from utilities.orchestrator.persistence import persist_news_summary
        db, doc_ref = _make_mock_db()

        cls = self._sample_classification()
        cls["key_topics"] = None
        persist_news_summary("rns001", "FGEN", None, cls, db=db)

        written = doc_ref.set.call_args[0][0]
        assert written["key_topics"] == []
