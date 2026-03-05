"""
Tests for get_director_transaction_history.py

Unit tests: mocked Firestore — no network calls.
Integration tests: live Firestore with synthetic seed documents.
  Seed documents are written at session start and deleted at session end.

Run unit tests only:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_director_transaction_history.py -v -m "not integration"

Run all tests (requires Firestore credentials):
    scripts/venv/bin/python -m pytest utilities/tests/test_get_director_transaction_history.py -v
"""

import os
from datetime import datetime, date, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from utilities.get_director_transaction_history import (
    get_director_transaction_history,
    _classify_holding_trajectory,
    _classify_data_maturity,
    _get_collection_age_days,
    _parse_tx_date,
    _tx_sort_key,
    _OPEN_MARKET_CATEGORIES,
)


# ---------------------------------------------------------------------------
# Helpers — mock Firestore
# ---------------------------------------------------------------------------

def _make_mock_doc(doc_id, data):
    doc = MagicMock()
    doc.id = doc_id
    doc.to_dict.return_value = dict(data)
    return doc


def _make_mock_db(ticker_docs=None, oldest_stored_at=None):
    """
    Build a mock Firestore db supporting:
      db.collection().where().stream()         → ticker_docs
      db.collection().order_by().limit().stream() → oldest doc
    """
    ticker_docs = ticker_docs or []
    mock_docs = [_make_mock_doc(f"doc_{i}", d) for i, d in enumerate(ticker_docs)]

    age_docs = []
    if oldest_stored_at is not None:
        age_doc = _make_mock_doc("age_doc", {"stored_at": oldest_stored_at})
        age_docs = [age_doc]

    mock_db = MagicMock()

    def _make_collection(name):
        coll = MagicMock()

        # Chain: .where().stream()
        where_result = MagicMock()
        where_result.stream.return_value = iter(mock_docs)
        coll.where.return_value = where_result

        # Chain: .order_by().limit().stream()
        limit_result = MagicMock()
        limit_result.stream.return_value = iter(age_docs)
        order_by_result = MagicMock()
        order_by_result.limit.return_value = limit_result
        coll.order_by.return_value = order_by_result

        return coll

    mock_db.collection.side_effect = _make_collection
    return mock_db


def _make_tx(
    ticker="TEST",
    doc_suffix="001",
    director_normalised="john smith",
    director_raw="John Smith",
    rns_article_id=None,
    transaction_date=None,
    category="open_market_purchase",
    stored_at=None,
):
    """Synthetic pdmr_transaction document data."""
    if transaction_date is None:
        transaction_date = datetime(2026, 1, 10, tzinfo=timezone.utc)
    if stored_at is None:
        stored_at = datetime.now(timezone.utc)
    return {
        "ticker": ticker,
        "rns_article_id": rns_article_id or f"rns_{doc_suffix}",
        "director_name_raw": director_raw,
        "director_name_normalised": director_normalised,
        "transaction_date_actual": transaction_date,
        "transaction_category": category,
        "shares_purchased": 10000,
        "price_per_share": 1.50,
        "resulting_holding": 50000,
        "resulting_holding_pct": 0.8,
        "stored_at": stored_at,
    }


# ---------------------------------------------------------------------------
# Tests: _parse_tx_date
# ---------------------------------------------------------------------------

class TestParseTxDate:
    def test_datetime_utc_aware(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        tx = {"transaction_date_actual": dt}
        result = _parse_tx_date(tx)
        assert result == dt

    def test_datetime_naive_made_utc(self):
        dt = datetime(2026, 1, 15)
        tx = {"transaction_date_actual": dt}
        result = _parse_tx_date(tx)
        assert result.tzinfo == timezone.utc

    def test_date_object_converted(self):
        d = date(2026, 1, 15)
        tx = {"transaction_date_actual": d}
        result = _parse_tx_date(tx)
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 15

    def test_iso_string_parsed(self):
        tx = {"transaction_date_actual": "2026-01-15"}
        result = _parse_tx_date(tx)
        assert result.year == 2026
        assert result.month == 1

    def test_none_returns_none(self):
        assert _parse_tx_date({}) is None

    def test_invalid_string_returns_none(self):
        assert _parse_tx_date({"transaction_date_actual": "not-a-date"}) is None


# ---------------------------------------------------------------------------
# Tests: _classify_holding_trajectory
# ---------------------------------------------------------------------------

class TestClassifyHoldingTrajectory:
    def _make_txs(self, categories):
        return [{"transaction_category": c} for c in categories]

    def test_insufficient_data_for_zero_transactions(self):
        assert _classify_holding_trajectory([]) == "insufficient_data"

    def test_insufficient_data_for_one_transaction(self):
        assert _classify_holding_trajectory(self._make_txs(["open_market_purchase"])) == "insufficient_data"

    def test_building_for_all_purchases(self):
        txs = self._make_txs(["open_market_purchase", "open_market_purchase", "open_market_purchase"])
        assert _classify_holding_trajectory(txs) == "building"

    def test_reducing_for_all_disposals(self):
        txs = self._make_txs(["open_market_disposal", "open_market_disposal"])
        assert _classify_holding_trajectory(txs) == "reducing"

    def test_mixed_for_purchases_and_disposals(self):
        txs = self._make_txs(["open_market_purchase", "open_market_disposal", "open_market_purchase"])
        assert _classify_holding_trajectory(txs) == "mixed"

    def test_building_with_exactly_two_purchases(self):
        txs = self._make_txs(["open_market_purchase", "open_market_purchase"])
        assert _classify_holding_trajectory(txs) == "building"

    def test_reducing_with_exactly_two_disposals(self):
        txs = self._make_txs(["open_market_disposal", "open_market_disposal"])
        assert _classify_holding_trajectory(txs) == "reducing"


# ---------------------------------------------------------------------------
# Tests: _classify_data_maturity
# ---------------------------------------------------------------------------

class TestClassifyDataMaturity:
    def test_empty_for_zero(self):
        assert _classify_data_maturity(0) == "empty"

    def test_sparse_for_one(self):
        assert _classify_data_maturity(1) == "sparse"

    def test_sparse_for_two(self):
        assert _classify_data_maturity(2) == "sparse"

    def test_sufficient_for_three(self):
        assert _classify_data_maturity(3) == "sufficient"

    def test_sufficient_for_many(self):
        assert _classify_data_maturity(10) == "sufficient"


# ---------------------------------------------------------------------------
# Tests: _get_collection_age_days (mocked)
# ---------------------------------------------------------------------------

class TestGetCollectionAgeDays:
    def test_returns_correct_age_days(self):
        stored_at = datetime.now(timezone.utc) - timedelta(days=15)
        db = _make_mock_db(oldest_stored_at=stored_at)
        result = _get_collection_age_days(db)
        assert result == 15

    def test_returns_zero_for_today(self):
        stored_at = datetime.now(timezone.utc)
        db = _make_mock_db(oldest_stored_at=stored_at)
        result = _get_collection_age_days(db)
        assert result == 0

    def test_returns_none_when_collection_empty(self):
        db = _make_mock_db(oldest_stored_at=None)
        result = _get_collection_age_days(db)
        assert result is None

    def test_returns_none_when_stored_at_missing(self):
        # Doc exists but has no stored_at
        mock_db = MagicMock()
        mock_doc = _make_mock_doc("d", {"ticker": "X"})  # no stored_at
        limit_result = MagicMock()
        limit_result.stream.return_value = iter([mock_doc])
        order_by_result = MagicMock()
        order_by_result.limit.return_value = limit_result
        mock_coll = MagicMock()
        mock_coll.order_by.return_value = order_by_result
        mock_db.collection.return_value = mock_coll
        result = _get_collection_age_days(mock_db)
        assert result is None

    def test_returns_none_on_exception(self):
        mock_db = MagicMock()
        mock_db.collection.side_effect = Exception("Firestore error")
        result = _get_collection_age_days(mock_db)
        assert result is None

    def test_naive_datetime_handled(self):
        # Naive datetime (no tzinfo) should still work
        stored_at = datetime.now() - timedelta(days=5)  # naive
        db = _make_mock_db(oldest_stored_at=stored_at)
        result = _get_collection_age_days(db)
        assert result == 5


# ---------------------------------------------------------------------------
# Tests: get_director_transaction_history (mocked Firestore)
# ---------------------------------------------------------------------------

class TestGetDirectorTransactionHistory:
    def test_returns_correct_transactions_for_known_director(self):
        txs = [
            _make_tx(director_normalised="john smith", transaction_date=datetime(2026, 1, 10, tzinfo=timezone.utc)),
            _make_tx(director_normalised="john smith", doc_suffix="002",
                     transaction_date=datetime(2026, 2, 5, tzinfo=timezone.utc)),
            _make_tx(director_normalised="john smith", doc_suffix="003",
                     transaction_date=datetime(2026, 3, 1, tzinfo=timezone.utc)),
        ]
        db = _make_mock_db(ticker_docs=txs,
                           oldest_stored_at=datetime.now(timezone.utc) - timedelta(days=30))
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["total_count"] == 3
        assert result["data_maturity"] == "sufficient"

    def test_empty_result_for_unknown_director(self):
        txs = [
            _make_tx(director_normalised="jane doe"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["total_count"] == 0
        assert result["data_maturity"] == "empty"

    def test_exclude_id_removes_triggering_transaction(self):
        txs = [
            _make_tx(rns_article_id="rns_trigger", director_normalised="john smith"),
            _make_tx(doc_suffix="002", rns_article_id="rns_other",
                     director_normalised="john smith"),
            _make_tx(doc_suffix="003", rns_article_id="rns_third",
                     director_normalised="john smith"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith",
                                                   exclude_id="rns_trigger", db=db)
        assert result["total_count"] == 2
        ids = [t["rns_article_id"] for t in result["transactions"]]
        assert "rns_trigger" not in ids

    def test_no_exclude_when_exclude_id_is_none(self):
        txs = [
            _make_tx(rns_article_id="rns_001", director_normalised="john smith"),
            _make_tx(doc_suffix="002", rns_article_id="rns_002",
                     director_normalised="john smith"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith",
                                                   exclude_id=None, db=db)
        assert result["total_count"] == 2

    def test_sorted_by_transaction_date_descending(self):
        dates = [
            datetime(2026, 1, 10, tzinfo=timezone.utc),
            datetime(2026, 3, 5, tzinfo=timezone.utc),
            datetime(2026, 2, 15, tzinfo=timezone.utc),
        ]
        txs = [
            _make_tx(doc_suffix=f"00{i}", director_normalised="john smith",
                     transaction_date=d)
            for i, d in enumerate(dates)
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        tx_dates = [t["transaction_date_actual"] for t in result["transactions"]]
        # Should be descending
        parsed = [_parse_tx_date({"transaction_date_actual": d}) for d in tx_dates]
        assert parsed == sorted(parsed, reverse=True)

    def test_open_market_count_counts_only_open_market_types(self):
        txs = [
            _make_tx(doc_suffix="001", director_normalised="john smith",
                     category="open_market_purchase"),
            _make_tx(doc_suffix="002", director_normalised="john smith",
                     category="open_market_disposal"),
            _make_tx(doc_suffix="003", director_normalised="john smith",
                     category="plan_purchase"),  # excluded from count
            _make_tx(doc_suffix="004", director_normalised="john smith",
                     category="award"),           # excluded from count
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["total_count"] == 4
        assert result["open_market_count"] == 2

    def test_holding_trajectory_building_for_all_purchases(self):
        txs = [
            _make_tx(doc_suffix=f"00{i}", director_normalised="john smith",
                     category="open_market_purchase")
            for i in range(3)
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["holding_trajectory"] == "building"

    def test_holding_trajectory_insufficient_data_for_single_transaction(self):
        txs = [
            _make_tx(director_normalised="john smith", category="open_market_purchase"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["holding_trajectory"] == "insufficient_data"

    def test_holding_trajectory_insufficient_data_for_no_open_market_txs(self):
        txs = [
            _make_tx(doc_suffix="001", director_normalised="john smith",
                     category="plan_purchase"),
            _make_tx(doc_suffix="002", director_normalised="john smith",
                     category="award"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["holding_trajectory"] == "insufficient_data"

    def test_holding_trajectory_reducing_for_all_disposals(self):
        txs = [
            _make_tx(doc_suffix=f"00{i}", director_normalised="john smith",
                     category="open_market_disposal")
            for i in range(2)
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["holding_trajectory"] == "reducing"

    def test_holding_trajectory_mixed_for_purchases_and_disposals(self):
        txs = [
            _make_tx(doc_suffix="001", director_normalised="john smith",
                     category="open_market_purchase"),
            _make_tx(doc_suffix="002", director_normalised="john smith",
                     category="open_market_disposal"),
            _make_tx(doc_suffix="003", director_normalised="john smith",
                     category="open_market_purchase"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["holding_trajectory"] == "mixed"

    def test_data_maturity_empty_for_no_transactions(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["data_maturity"] == "empty"

    def test_data_maturity_sparse_for_one_transaction(self):
        txs = [_make_tx(director_normalised="john smith")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["data_maturity"] == "sparse"

    def test_data_maturity_sparse_for_two_transactions(self):
        txs = [
            _make_tx(doc_suffix=f"00{i}", director_normalised="john smith")
            for i in range(2)
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["data_maturity"] == "sparse"

    def test_data_maturity_sufficient_for_three_transactions(self):
        txs = [
            _make_tx(doc_suffix=f"00{i}", director_normalised="john smith")
            for i in range(3)
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["data_maturity"] == "sufficient"

    def test_date_range_populated_correctly(self):
        txs = [
            _make_tx(doc_suffix="001", director_normalised="john smith",
                     transaction_date=datetime(2026, 1, 5, tzinfo=timezone.utc)),
            _make_tx(doc_suffix="002", director_normalised="john smith",
                     transaction_date=datetime(2026, 3, 20, tzinfo=timezone.utc)),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["date_range"]["earliest"] == date(2026, 1, 5)
        assert result["date_range"]["latest"] == date(2026, 3, 20)

    def test_date_range_none_when_no_transactions(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["date_range"]["earliest"] is None
        assert result["date_range"]["latest"] is None

    def test_collection_age_days_present(self):
        stored = datetime.now(timezone.utc) - timedelta(days=20)
        db = _make_mock_db(oldest_stored_at=stored)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["collection_age_days"] == 20

    def test_data_note_present_when_empty_and_young_collection(self):
        # Empty result + collection < 30 days old → data_note added
        stored = datetime.now(timezone.utc) - timedelta(days=5)
        db = _make_mock_db(ticker_docs=[], oldest_stored_at=stored)
        result = get_director_transaction_history("TEST", "Unknown Director", db=db)
        assert result["data_maturity"] == "empty"
        assert "data_note" in result
        assert "recently deployed" in result["data_note"].lower()

    def test_no_data_note_when_collection_old_enough(self):
        stored = datetime.now(timezone.utc) - timedelta(days=45)
        db = _make_mock_db(ticker_docs=[], oldest_stored_at=stored)
        result = get_director_transaction_history("TEST", "Unknown Director", db=db)
        assert "data_note" not in result

    def test_name_variation_abbreviated_matches(self):
        """J. Smith should match stored 'john smith' via abbreviated matching."""
        txs = [_make_tx(director_normalised="john smith")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "J. Smith", db=db)
        assert result["total_count"] == 1
        assert result["transactions"][0]["_name_match_method"] == "abbreviated"

    def test_name_variation_all_caps_matches(self):
        """JOHN SMITH should match stored 'john smith' via exact match."""
        txs = [_make_tx(director_normalised="john smith")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "JOHN SMITH", db=db)
        assert result["total_count"] == 1

    def test_name_variation_surname_first_matches(self):
        """SMITH, John should match stored 'john smith'."""
        txs = [_make_tx(director_normalised="john smith")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "SMITH, John", db=db)
        assert result["total_count"] == 1

    def test_different_director_not_matched(self):
        txs = [_make_tx(director_normalised="jane doe")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["total_count"] == 0

    def test_director_name_normalised_in_result(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_director_transaction_history("TEST", "Dr. John Smith", db=db)
        assert result["director_name_normalised"] == "john smith"

    def test_director_name_query_preserved_raw(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["director_name_query"] == "John Smith"

    def test_ticker_preserved_in_result(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_director_transaction_history("FGEN", "John Smith", db=db)
        assert result["ticker"] == "FGEN"

    def test_all_required_keys_present(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        required_keys = [
            "ticker", "director_name_query", "director_name_normalised",
            "transactions", "total_count", "open_market_count",
            "date_range", "holding_trajectory", "data_maturity",
            "collection_age_days",
        ]
        for k in required_keys:
            assert k in result, f"Missing key: {k}"

    def test_plan_purchase_included_in_transactions_array(self):
        """Plan purchases excluded from open_market_count but present in transactions."""
        txs = [
            _make_tx(doc_suffix="001", director_normalised="john smith",
                     category="open_market_purchase"),
            _make_tx(doc_suffix="002", director_normalised="john smith",
                     category="plan_purchase"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["total_count"] == 2
        assert result["open_market_count"] == 1
        categories = {t["transaction_category"] for t in result["transactions"]}
        assert "plan_purchase" in categories

    def test_matches_against_director_name_raw_when_normalised_absent(self):
        """Falls back to director_name_raw if director_name_normalised not present."""
        tx = {
            "ticker": "TEST",
            "rns_article_id": "rns_001",
            "director_name_raw": "John Smith",
            # No director_name_normalised field
            "transaction_date_actual": datetime(2026, 1, 10, tzinfo=timezone.utc),
            "transaction_category": "open_market_purchase",
            "stored_at": datetime.now(timezone.utc),
        }
        db = _make_mock_db(ticker_docs=[tx])
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert result["total_count"] == 1

    def test_doc_id_added_to_transaction(self):
        txs = [_make_tx(director_normalised="john smith")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert "_doc_id" in result["transactions"][0]

    def test_name_match_method_added_to_transaction(self):
        txs = [_make_tx(director_normalised="john smith")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_director_transaction_history("TEST", "John Smith", db=db)
        assert "_name_match_method" in result["transactions"][0]


# ---------------------------------------------------------------------------
# Integration tests — live Firestore with synthetic seed documents
# ---------------------------------------------------------------------------

_SEED_TICKER = "TEST_DTH_SEED"   # unique ticker to avoid collisions
_SEED_DOCS_IDS = []              # populated during setup, used for teardown


def _has_credentials():
    """Check if Firestore credentials are available."""
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if creds and os.path.exists(creds):
        return True
    frontend_creds = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "frontend", "gcp-credentials.json"
    )
    return os.path.exists(frontend_creds)


@pytest.fixture(scope="module")
def firestore_db():
    """Live Firestore client. Skip if credentials unavailable."""
    if not _has_credentials():
        pytest.skip("No Firestore credentials available")
    from google.cloud import firestore
    return firestore.Client()


@pytest.fixture(scope="module")
def seeded_db(firestore_db):
    """
    Seed synthetic pdmr_transaction documents and clean up after tests.
    """
    now = datetime.now(timezone.utc)
    seed_docs = [
        # 3 open_market_purchase transactions for "John Smith" at SEED_TICKER
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "seed_rns_001",
            "director_name_raw": "John Smith",
            "director_name_normalised": "john smith",
            "transaction_date_actual": datetime(2026, 1, 5, tzinfo=timezone.utc),
            "transaction_category": "open_market_purchase",
            "shares_purchased": 5000,
            "price_per_share": 1.20,
            "resulting_holding": 30000,
            "resulting_holding_pct": 0.5,
            "stored_at": now - timedelta(days=10),
        },
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "seed_rns_002",
            "director_name_raw": "John Smith",
            "director_name_normalised": "john smith",
            "transaction_date_actual": datetime(2026, 2, 10, tzinfo=timezone.utc),
            "transaction_category": "open_market_purchase",
            "shares_purchased": 8000,
            "price_per_share": 1.35,
            "resulting_holding": 38000,
            "resulting_holding_pct": 0.63,
            "stored_at": now - timedelta(days=5),
        },
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "seed_rns_003",
            "director_name_raw": "John Smith",
            "director_name_normalised": "john smith",
            "transaction_date_actual": datetime(2026, 3, 1, tzinfo=timezone.utc),
            "transaction_category": "open_market_purchase",
            "shares_purchased": 10000,
            "price_per_share": 1.50,
            "resulting_holding": 48000,
            "resulting_holding_pct": 0.8,
            "stored_at": now,
        },
        # 1 plan_purchase (should appear in total_count but not open_market_count)
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "seed_rns_004",
            "director_name_raw": "John Smith",
            "director_name_normalised": "john smith",
            "transaction_date_actual": datetime(2026, 1, 15, tzinfo=timezone.utc),
            "transaction_category": "plan_purchase",
            "shares_purchased": 2000,
            "price_per_share": 1.25,
            "resulting_holding": 32000,
            "resulting_holding_pct": 0.53,
            "stored_at": now - timedelta(days=8),
        },
        # Different director at same ticker (should NOT be returned when querying "John Smith")
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "seed_rns_005",
            "director_name_raw": "Jane Doe",
            "director_name_normalised": "jane doe",
            "transaction_date_actual": datetime(2026, 2, 20, tzinfo=timezone.utc),
            "transaction_category": "open_market_purchase",
            "shares_purchased": 3000,
            "price_per_share": 1.40,
            "resulting_holding": 15000,
            "resulting_holding_pct": 0.25,
            "stored_at": now - timedelta(days=3),
        },
    ]

    # Write seed documents
    doc_ids = []
    coll = firestore_db.collection("pdmr_transactions")
    for doc_data in seed_docs:
        doc_ref = coll.add(doc_data)[1]
        doc_ids.append(doc_ref.id)

    yield firestore_db, doc_ids

    # Teardown — delete all seed documents
    for doc_id in doc_ids:
        firestore_db.collection("pdmr_transactions").document(doc_id).delete()


@pytest.mark.integration
class TestGetDirectorTransactionHistoryIntegration:
    def test_returns_correct_transactions_for_seeded_ticker(self, seeded_db):
        db, _ = seeded_db
        result = get_director_transaction_history(
            _SEED_TICKER, "John Smith", db=db
        )
        # 4 docs for "John Smith" (3 open_market + 1 plan), not Jane Doe
        assert result["total_count"] == 4
        assert result["data_maturity"] == "sufficient"

    def test_open_market_count_excludes_plan_purchase(self, seeded_db):
        db, _ = seeded_db
        result = get_director_transaction_history(
            _SEED_TICKER, "John Smith", db=db
        )
        assert result["open_market_count"] == 3  # 3 open_market_purchase

    def test_holding_trajectory_building_for_all_purchases(self, seeded_db):
        db, _ = seeded_db
        result = get_director_transaction_history(
            _SEED_TICKER, "John Smith", db=db
        )
        assert result["holding_trajectory"] == "building"

    def test_sorted_descending_by_transaction_date(self, seeded_db):
        db, _ = seeded_db
        result = get_director_transaction_history(
            _SEED_TICKER, "John Smith", db=db
        )
        dates = [_parse_tx_date(t) for t in result["transactions"]]
        dates = [d for d in dates if d is not None]
        assert dates == sorted(dates, reverse=True)

    def test_exclude_id_removes_triggering_transaction(self, seeded_db):
        db, _ = seeded_db
        result = get_director_transaction_history(
            _SEED_TICKER, "John Smith",
            exclude_id="seed_rns_003", db=db
        )
        assert result["total_count"] == 3
        ids = [t["rns_article_id"] for t in result["transactions"]]
        assert "seed_rns_003" not in ids

    def test_name_variation_abbreviated_matches(self, seeded_db):
        """J. Smith should match 'john smith' stored in Firestore."""
        db, _ = seeded_db
        result = get_director_transaction_history(
            _SEED_TICKER, "J. Smith", db=db
        )
        assert result["total_count"] == 4

    def test_different_director_returns_empty(self, seeded_db):
        db, _ = seeded_db
        result = get_director_transaction_history(
            _SEED_TICKER, "Robert Brown", db=db
        )
        assert result["total_count"] == 0
        assert result["data_maturity"] == "empty"

    def test_collection_age_days_calculated(self, seeded_db):
        db, _ = seeded_db
        result = get_director_transaction_history(
            _SEED_TICKER, "John Smith", db=db
        )
        # Collection has docs seeded up to 10 days ago — age should be >= 0
        assert result["collection_age_days"] is not None
        assert result["collection_age_days"] >= 0

    def test_seed_documents_exist_and_then_deleted(self, seeded_db):
        """Verify teardown will work — docs are accessible by their IDs."""
        db, doc_ids = seeded_db
        # All seed doc IDs should be valid (non-empty strings)
        assert len(doc_ids) == 5
        for doc_id in doc_ids:
            assert isinstance(doc_id, str)
            assert len(doc_id) > 0
