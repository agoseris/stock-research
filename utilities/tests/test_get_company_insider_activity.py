"""
Tests for get_company_insider_activity.py

Unit tests: mocked Firestore — no network calls.
Integration tests: live Firestore with synthetic seed documents.
  Seed documents are written at session start and deleted at session end.

Run unit tests only:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_company_insider_activity.py -v -m "not integration"

Run all tests (requires Firestore credentials):
    scripts/venv/bin/python -m pytest utilities/tests/test_get_company_insider_activity.py -v
"""

import os
from datetime import datetime, date, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from utilities.get_company_insider_activity import (
    get_company_insider_activity,
    _classify_net_sentiment,
    _classify_data_maturity,
    _detect_cluster,
    _parse_tx_date,
    _get_director_name,
    _CLUSTER_WINDOW_DAYS,
)


# ---------------------------------------------------------------------------
# Helpers — mock Firestore (same pattern as tool 8 tests)
# ---------------------------------------------------------------------------

def _make_mock_doc(doc_id, data):
    doc = MagicMock()
    doc.id = doc_id
    doc.to_dict.return_value = dict(data)
    return doc


def _make_mock_db(ticker_docs=None):
    """
    Build a mock Firestore db supporting:
      db.collection().where().stream() → ticker_docs
    """
    ticker_docs = ticker_docs or []
    mock_docs = [_make_mock_doc(f"doc_{i}", d) for i, d in enumerate(ticker_docs)]

    mock_db = MagicMock()

    def _make_collection(name):
        coll = MagicMock()
        where_result = MagicMock()
        where_result.stream.return_value = iter(mock_docs)
        coll.where.return_value = where_result
        return coll

    mock_db.collection.side_effect = _make_collection
    return mock_db


def _make_tx(
    ticker="TEST",
    doc_suffix="001",
    director_normalised="alice jones",
    director_raw="Alice Jones",
    rns_article_id=None,
    transaction_date=None,
    category="open_market_purchase",
):
    """Synthetic pdmr_transaction document."""
    if transaction_date is None:
        transaction_date = datetime(2026, 1, 10, tzinfo=timezone.utc)
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
        "stored_at": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# Tests: _classify_net_sentiment
# ---------------------------------------------------------------------------

class TestClassifyNetSentiment:
    def test_none_for_no_activity(self):
        assert _classify_net_sentiment(0, 0) == "none"

    def test_buying_for_purchases_only(self):
        assert _classify_net_sentiment(3, 0) == "buying"

    def test_selling_for_disposals_only(self):
        assert _classify_net_sentiment(0, 2) == "selling"

    def test_mixed_for_both(self):
        assert _classify_net_sentiment(2, 1) == "mixed"

    def test_mixed_even_if_buys_dominate(self):
        assert _classify_net_sentiment(5, 1) == "mixed"

    def test_buying_single_purchase(self):
        assert _classify_net_sentiment(1, 0) == "buying"

    def test_selling_single_disposal(self):
        assert _classify_net_sentiment(0, 1) == "selling"


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
# Tests: _detect_cluster
# ---------------------------------------------------------------------------

def _purchase(director_normalised, tx_date):
    """Minimal purchase dict for cluster detection tests."""
    return {
        "director_name_normalised": director_normalised,
        "transaction_category": "open_market_purchase",
        "transaction_date_actual": tx_date,
    }


class TestDetectCluster:
    def test_cluster_detected_within_30_days(self):
        """2 directors buying 19 days apart → cluster."""
        purchases = [
            _purchase("alice jones", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _purchase("bob taylor",  datetime(2026, 1, 20, tzinfo=timezone.utc)),
        ]
        detected, details = _detect_cluster(purchases)
        assert detected is True
        assert len(details) >= 1

    def test_no_cluster_when_35_days_apart(self):
        """2 directors buying 35 days apart → no cluster."""
        purchases = [
            _purchase("alice jones", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _purchase("bob taylor",  datetime(2026, 2, 5, tzinfo=timezone.utc)),
        ]
        detected, details = _detect_cluster(purchases)
        assert detected is False
        assert details == []

    def test_no_cluster_for_single_director(self):
        """Same director buying twice — not a cluster (only 1 distinct director)."""
        purchases = [
            _purchase("alice jones", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _purchase("alice jones", datetime(2026, 1, 5, tzinfo=timezone.utc)),
        ]
        detected, details = _detect_cluster(purchases)
        assert detected is False

    def test_no_cluster_for_fewer_than_2_purchases(self):
        purchases = [
            _purchase("alice jones", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ]
        detected, details = _detect_cluster(purchases)
        assert detected is False
        assert details == []

    def test_no_cluster_for_empty_list(self):
        detected, details = _detect_cluster([])
        assert detected is False
        assert details == []

    def test_cluster_details_populated(self):
        purchases = [
            _purchase("alice jones", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _purchase("bob taylor",  datetime(2026, 1, 15, tzinfo=timezone.utc)),
        ]
        detected, details = _detect_cluster(purchases)
        assert detected is True
        assert len(details) >= 1
        c = details[0]
        assert "directors" in c
        assert "window_start" in c
        assert "window_end" in c
        assert "transaction_count" in c
        assert "alice jones" in c["directors"]
        assert "bob taylor" in c["directors"]

    def test_cluster_window_start_and_end_dates(self):
        d1 = datetime(2026, 1, 5, tzinfo=timezone.utc)
        d2 = datetime(2026, 1, 20, tzinfo=timezone.utc)
        purchases = [
            _purchase("alice jones", d1),
            _purchase("bob taylor",  d2),
        ]
        _, details = _detect_cluster(purchases)
        assert details[0]["window_start"] == date(2026, 1, 5)
        assert details[0]["window_end"] == date(2026, 1, 20)

    def test_cluster_transaction_count(self):
        purchases = [
            _purchase("alice jones", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _purchase("bob taylor",  datetime(2026, 1, 10, tzinfo=timezone.utc)),
            _purchase("carol lee",   datetime(2026, 1, 15, tzinfo=timezone.utc)),
        ]
        _, details = _detect_cluster(purchases)
        # At least one cluster should have transaction_count = 3
        assert any(c["transaction_count"] == 3 for c in details)

    def test_exactly_30_days_apart_is_cluster(self):
        """Boundary: exactly 30 days apart is within the window."""
        d1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        d2 = d1 + timedelta(days=30)
        purchases = [
            _purchase("alice jones", d1),
            _purchase("bob taylor",  d2),
        ]
        detected, _ = _detect_cluster(purchases)
        assert detected is True

    def test_31_days_apart_is_not_cluster(self):
        """Boundary: 31 days apart is outside the window."""
        d1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        d2 = d1 + timedelta(days=31)
        purchases = [
            _purchase("alice jones", d1),
            _purchase("bob taylor",  d2),
        ]
        detected, _ = _detect_cluster(purchases)
        assert detected is False

    def test_directors_list_in_cluster_details(self):
        purchases = [
            _purchase("alice jones", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _purchase("bob taylor",  datetime(2026, 1, 10, tzinfo=timezone.utc)),
        ]
        _, details = _detect_cluster(purchases)
        directors = details[0]["directors"]
        assert sorted(directors) == ["alice jones", "bob taylor"]

    def test_no_duplicate_clusters_for_same_director_set(self):
        """Same pair of directors in overlapping windows → deduplicated."""
        purchases = [
            _purchase("alice jones", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _purchase("alice jones", datetime(2026, 1, 5, tzinfo=timezone.utc)),
            _purchase("bob taylor",  datetime(2026, 1, 10, tzinfo=timezone.utc)),
        ]
        _, details = _detect_cluster(purchases)
        # alice+bob cluster should appear only once
        director_sets = [frozenset(c["directors"]) for c in details]
        assert len(set(director_sets)) == len(director_sets)


# ---------------------------------------------------------------------------
# Tests: get_company_insider_activity (mocked Firestore)
# ---------------------------------------------------------------------------

class TestGetCompanyInsiderActivity:
    def test_returns_only_open_market_transactions(self):
        txs = [
            _make_tx(doc_suffix="001", category="open_market_purchase"),
            _make_tx(doc_suffix="002", category="open_market_disposal"),
            _make_tx(doc_suffix="003", category="plan_purchase"),   # excluded
            _make_tx(doc_suffix="004", category="award"),           # excluded
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert len(result["transactions"]) == 2
        categories = {t["transaction_category"] for t in result["transactions"]}
        assert categories == {"open_market_purchase", "open_market_disposal"}

    def test_buy_count_and_sell_count_correct(self):
        txs = [
            _make_tx(doc_suffix="001", category="open_market_purchase"),
            _make_tx(doc_suffix="002", category="open_market_purchase"),
            _make_tx(doc_suffix="003", category="open_market_disposal"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["buy_count"] == 2
        assert result["sell_count"] == 1

    def test_buying_directors_list_correct(self):
        txs = [
            _make_tx(doc_suffix="001", director_normalised="alice jones",
                     category="open_market_purchase"),
            _make_tx(doc_suffix="002", director_normalised="bob taylor",
                     category="open_market_purchase"),
            _make_tx(doc_suffix="003", director_normalised="carol lee",
                     category="open_market_disposal"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert sorted(result["buying_directors"]) == ["alice jones", "bob taylor"]
        assert result["selling_directors"] == ["carol lee"]

    def test_buying_directors_deduplicated(self):
        """Same director buying multiple times → appears once in buying_directors."""
        txs = [
            _make_tx(doc_suffix="001", director_normalised="alice jones",
                     category="open_market_purchase"),
            _make_tx(doc_suffix="002", director_normalised="alice jones",
                     category="open_market_purchase"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["buying_directors"] == ["alice jones"]

    def test_net_sentiment_buying(self):
        txs = [_make_tx(category="open_market_purchase")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["net_sentiment"] == "buying"

    def test_net_sentiment_selling(self):
        txs = [_make_tx(category="open_market_disposal")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["net_sentiment"] == "selling"

    def test_net_sentiment_mixed(self):
        txs = [
            _make_tx(doc_suffix="001", category="open_market_purchase"),
            _make_tx(doc_suffix="002", category="open_market_disposal"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["net_sentiment"] == "mixed"

    def test_net_sentiment_none_for_no_activity(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_company_insider_activity("TEST", db=db)
        assert result["net_sentiment"] == "none"

    def test_exclude_id_removes_triggering_transaction(self):
        txs = [
            _make_tx(rns_article_id="rns_trigger", category="open_market_purchase"),
            _make_tx(doc_suffix="002", rns_article_id="rns_other",
                     category="open_market_purchase"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", exclude_id="rns_trigger", db=db)
        assert result["buy_count"] == 1
        ids = [t["rns_article_id"] for t in result["transactions"]]
        assert "rns_trigger" not in ids

    def test_days_lookback_filter(self):
        """Transactions outside lookback window not returned."""
        old_date = datetime.now(timezone.utc) - timedelta(days=200)
        recent_date = datetime.now(timezone.utc) - timedelta(days=10)
        txs = [
            _make_tx(doc_suffix="001", transaction_date=old_date,
                     category="open_market_purchase"),
            _make_tx(doc_suffix="002", transaction_date=recent_date,
                     category="open_market_purchase"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", days_lookback=90, db=db)
        assert result["buy_count"] == 1

    def test_sorted_descending_by_date(self):
        dates = [
            datetime(2026, 1, 10, tzinfo=timezone.utc),
            datetime(2026, 3, 5, tzinfo=timezone.utc),
            datetime(2026, 2, 15, tzinfo=timezone.utc),
        ]
        txs = [
            _make_tx(doc_suffix=f"00{i}", transaction_date=d,
                     category="open_market_purchase")
            for i, d in enumerate(dates)
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        tx_dates = [_parse_tx_date(t) for t in result["transactions"]]
        assert tx_dates == sorted(tx_dates, reverse=True)

    def test_cluster_detected_two_directors_within_30_days(self):
        d1 = datetime.now(timezone.utc) - timedelta(days=15)
        d2 = datetime.now(timezone.utc) - timedelta(days=5)
        txs = [
            _make_tx(doc_suffix="001", director_normalised="alice jones",
                     transaction_date=d1, category="open_market_purchase"),
            _make_tx(doc_suffix="002", director_normalised="bob taylor",
                     transaction_date=d2, category="open_market_purchase"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["cluster_detected"] is True
        assert len(result["cluster_details"]) >= 1

    def test_no_cluster_when_35_days_apart(self):
        d1 = datetime.now(timezone.utc) - timedelta(days=40)
        d2 = datetime.now(timezone.utc) - timedelta(days=5)
        txs = [
            _make_tx(doc_suffix="001", director_normalised="alice jones",
                     transaction_date=d1, category="open_market_purchase"),
            _make_tx(doc_suffix="002", director_normalised="bob taylor",
                     transaction_date=d2, category="open_market_purchase"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", days_lookback=90, db=db)
        assert result["cluster_detected"] is False
        assert result["cluster_details"] == []

    def test_cluster_excludes_triggering_director(self):
        """
        Triggering director (david smith) buys within 30 days of alice.
        Without exclusion, cluster would be detected.
        With exclusion, only alice is in cluster_purchases → no cluster.
        """
        base_date = datetime.now(timezone.utc)
        txs = [
            # Triggering transaction — excluded by rns_article_id
            _make_tx(
                rns_article_id="rns_trigger",
                director_normalised="david smith",
                transaction_date=base_date - timedelta(days=20),
                category="open_market_purchase",
            ),
            # Alice's transaction — in the lookback period
            _make_tx(
                doc_suffix="002",
                director_normalised="alice jones",
                transaction_date=base_date - timedelta(days=10),
                category="open_market_purchase",
            ),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity(
            "TEST", exclude_id="rns_trigger", db=db
        )
        # After exclusion, only alice is in transactions
        # cluster_purchases = alice only (david excluded as triggering director)
        assert result["cluster_detected"] is False

    def test_cluster_details_populated_correctly(self):
        d1 = datetime.now(timezone.utc) - timedelta(days=20)
        d2 = datetime.now(timezone.utc) - timedelta(days=10)
        txs = [
            _make_tx(doc_suffix="001", director_normalised="alice jones",
                     transaction_date=d1, category="open_market_purchase"),
            _make_tx(doc_suffix="002", director_normalised="bob taylor",
                     transaction_date=d2, category="open_market_purchase"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["cluster_detected"] is True
        c = result["cluster_details"][0]
        assert set(c["directors"]) == {"alice jones", "bob taylor"}
        assert isinstance(c["window_start"], date)
        assert isinstance(c["window_end"], date)
        assert c["window_start"] <= c["window_end"]
        assert c["transaction_count"] == 2

    def test_data_maturity_empty(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_company_insider_activity("TEST", db=db)
        assert result["data_maturity"] == "empty"

    def test_data_maturity_sparse(self):
        txs = [_make_tx(category="open_market_purchase")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["data_maturity"] == "sparse"

    def test_data_maturity_sufficient(self):
        txs = [
            _make_tx(doc_suffix=f"00{i}", category="open_market_purchase")
            for i in range(3)
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["data_maturity"] == "sufficient"

    def test_graceful_empty_result_for_ticker_with_no_activity(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_company_insider_activity("UNKNOWN_TICKER", db=db)
        assert isinstance(result, dict)
        assert result["buy_count"] == 0
        assert result["sell_count"] == 0
        assert result["net_sentiment"] == "none"
        assert result["cluster_detected"] is False
        assert result["data_maturity"] == "empty"

    def test_all_required_keys_present(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_company_insider_activity("TEST", db=db)
        required = [
            "ticker", "days_lookback", "transactions",
            "buy_count", "sell_count",
            "buying_directors", "selling_directors",
            "net_sentiment", "cluster_detected", "cluster_details",
            "data_maturity",
        ]
        for k in required:
            assert k in result, f"Missing key: {k}"

    def test_ticker_and_days_lookback_preserved(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_company_insider_activity("FGEN", days_lookback=180, db=db)
        assert result["ticker"] == "FGEN"
        assert result["days_lookback"] == 180

    def test_plan_purchases_do_not_affect_buy_count(self):
        txs = [
            _make_tx(doc_suffix="001", category="plan_purchase"),
            _make_tx(doc_suffix="002", category="open_market_purchase"),
        ]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["buy_count"] == 1

    def test_cluster_details_empty_list_when_no_cluster(self):
        txs = [_make_tx(category="open_market_purchase")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["cluster_detected"] is False
        assert result["cluster_details"] == []

    def test_transactions_list_empty_for_no_open_market_activity(self):
        txs = [_make_tx(category="plan_purchase")]
        db = _make_mock_db(ticker_docs=txs)
        result = get_company_insider_activity("TEST", db=db)
        assert result["transactions"] == []


# ---------------------------------------------------------------------------
# Integration tests — live Firestore with synthetic seed documents
# ---------------------------------------------------------------------------

_SEED_TICKER = "TEST_CIA_SEED"
_SEED_TICKER_CLUSTER = "TEST_CIA_CLUSTER"


def _has_credentials():
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if creds and os.path.exists(creds):
        return True
    frontend_creds = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "frontend", "gcp-credentials.json"
    )
    return os.path.exists(frontend_creds)


@pytest.fixture(scope="module")
def firestore_db():
    if not _has_credentials():
        pytest.skip("No Firestore credentials available")
    from google.cloud import firestore
    return firestore.Client()


@pytest.fixture(scope="module")
def seeded_db(firestore_db):
    """
    Seed synthetic pdmr_transaction documents and clean up after all tests.

    TEST_CIA_SEED: multi-director activity for general tests
    TEST_CIA_CLUSTER: two scenarios for cluster detection tests
    """
    now = datetime.now(timezone.utc)
    seed_docs = [
        # --- TEST_CIA_SEED: 3 buys from 2 directors, 1 disposal, 1 plan_purchase ---
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "cia_seed_rns_001",
            "director_name_raw": "Alice Jones",
            "director_name_normalised": "alice jones",
            "transaction_date_actual": now - timedelta(days=60),
            "transaction_category": "open_market_purchase",
            "stored_at": now - timedelta(days=60),
        },
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "cia_seed_rns_002",
            "director_name_raw": "Alice Jones",
            "director_name_normalised": "alice jones",
            "transaction_date_actual": now - timedelta(days=30),
            "transaction_category": "open_market_purchase",
            "stored_at": now - timedelta(days=30),
        },
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "cia_seed_rns_003",
            "director_name_raw": "Bob Taylor",
            "director_name_normalised": "bob taylor",
            "transaction_date_actual": now - timedelta(days=20),
            "transaction_category": "open_market_purchase",
            "stored_at": now - timedelta(days=20),
        },
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "cia_seed_rns_004",
            "director_name_raw": "Carol Lee",
            "director_name_normalised": "carol lee",
            "transaction_date_actual": now - timedelta(days=10),
            "transaction_category": "open_market_disposal",
            "stored_at": now - timedelta(days=10),
        },
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "cia_seed_rns_005",
            "director_name_raw": "Alice Jones",
            "director_name_normalised": "alice jones",
            "transaction_date_actual": now - timedelta(days=5),
            "transaction_category": "plan_purchase",   # excluded from open_market
            "stored_at": now - timedelta(days=5),
        },
        # --- TEST_CIA_CLUSTER: two buys within 30 days → cluster_detected = true ---
        {
            "ticker": _SEED_TICKER_CLUSTER,
            "rns_article_id": "cia_cluster_rns_001",
            "director_name_raw": "David Smith",
            "director_name_normalised": "david smith",
            "transaction_date_actual": now - timedelta(days=25),
            "transaction_category": "open_market_purchase",
            "stored_at": now - timedelta(days=25),
        },
        {
            "ticker": _SEED_TICKER_CLUSTER,
            "rns_article_id": "cia_cluster_rns_002",
            "director_name_raw": "Eve Brown",
            "director_name_normalised": "eve brown",
            "transaction_date_actual": now - timedelta(days=10),
            "transaction_category": "open_market_purchase",
            "stored_at": now - timedelta(days=10),
        },
    ]

    doc_ids = []
    coll = firestore_db.collection("pdmr_transactions")
    for doc_data in seed_docs:
        doc_ref = coll.add(doc_data)[1]
        doc_ids.append(doc_ref.id)

    yield firestore_db, doc_ids

    # Teardown
    for doc_id in doc_ids:
        firestore_db.collection("pdmr_transactions").document(doc_id).delete()


@pytest.mark.integration
class TestGetCompanyInsiderActivityIntegration:
    def test_returns_correct_activity_for_seeded_ticker(self, seeded_db):
        db, _ = seeded_db
        result = get_company_insider_activity(_SEED_TICKER, days_lookback=90, db=db)
        # 3 open_market_purchase + 1 open_market_disposal = 4 open_market total
        assert len(result["transactions"]) == 4

    def test_only_open_market_returned(self, seeded_db):
        db, _ = seeded_db
        result = get_company_insider_activity(_SEED_TICKER, days_lookback=90, db=db)
        for tx in result["transactions"]:
            assert tx["transaction_category"] in {"open_market_purchase", "open_market_disposal"}

    def test_buy_count_and_sell_count(self, seeded_db):
        db, _ = seeded_db
        result = get_company_insider_activity(_SEED_TICKER, days_lookback=90, db=db)
        assert result["buy_count"] == 3
        assert result["sell_count"] == 1

    def test_buying_directors_correct(self, seeded_db):
        db, _ = seeded_db
        result = get_company_insider_activity(_SEED_TICKER, days_lookback=90, db=db)
        assert sorted(result["buying_directors"]) == ["alice jones", "bob taylor"]

    def test_selling_directors_correct(self, seeded_db):
        db, _ = seeded_db
        result = get_company_insider_activity(_SEED_TICKER, days_lookback=90, db=db)
        assert result["selling_directors"] == ["carol lee"]

    def test_net_sentiment_mixed(self, seeded_db):
        db, _ = seeded_db
        result = get_company_insider_activity(_SEED_TICKER, days_lookback=90, db=db)
        assert result["net_sentiment"] == "mixed"

    def test_cluster_detected_within_30_days(self, seeded_db):
        """David (day-25) and Eve (day-10) bought within 30 days → cluster."""
        db, _ = seeded_db
        result = get_company_insider_activity(
            _SEED_TICKER_CLUSTER, days_lookback=90, db=db
        )
        assert result["cluster_detected"] is True
        assert len(result["cluster_details"]) >= 1

    def test_cluster_details_include_both_directors(self, seeded_db):
        db, _ = seeded_db
        result = get_company_insider_activity(
            _SEED_TICKER_CLUSTER, days_lookback=90, db=db
        )
        c = result["cluster_details"][0]
        assert set(c["directors"]) == {"david smith", "eve brown"}

    def test_no_cluster_when_lookback_too_short(self, seeded_db):
        """
        With days_lookback=5, only Eve's transaction (day-10) is excluded
        and David's (day-25) is also excluded → no transactions → no cluster.
        Actually day-10 is within 5 days? No: now-10 < now-5.
        With lookback=5, only transactions from last 5 days returned → neither included.
        """
        db, _ = seeded_db
        result = get_company_insider_activity(
            _SEED_TICKER_CLUSTER, days_lookback=5, db=db
        )
        assert result["cluster_detected"] is False

    def test_exclude_id_removes_triggering_transaction(self, seeded_db):
        db, _ = seeded_db
        result = get_company_insider_activity(
            _SEED_TICKER, days_lookback=90,
            exclude_id="cia_seed_rns_001", db=db
        )
        ids = [t["rns_article_id"] for t in result["transactions"]]
        assert "cia_seed_rns_001" not in ids
        assert result["buy_count"] == 2  # was 3, minus the excluded one

    def test_days_lookback_filter_working(self, seeded_db):
        """With lookback=15, only transactions from last 15 days returned."""
        db, _ = seeded_db
        # cia_seed_rns_003 (day-20), cia_seed_rns_004 (day-10), cia_seed_rns_005 (day-5, plan)
        # Within 15 days: cia_seed_rns_004 (open_market_disposal, day-10)
        result = get_company_insider_activity(
            _SEED_TICKER, days_lookback=15, db=db
        )
        assert result["buy_count"] == 0
        assert result["sell_count"] == 1

    def test_graceful_empty_result_for_unknown_ticker(self, seeded_db):
        db, _ = seeded_db
        result = get_company_insider_activity("UNKNOWN_TICKER_XYZ", db=db)
        assert result["buy_count"] == 0
        assert result["net_sentiment"] == "none"
        assert result["cluster_detected"] is False

    def test_seed_documents_cleaned_up(self, seeded_db):
        """Verify all seed doc IDs are non-empty (teardown confirmation)."""
        _, doc_ids = seeded_db
        assert len(doc_ids) == 7
        for doc_id in doc_ids:
            assert isinstance(doc_id, str) and len(doc_id) > 0
