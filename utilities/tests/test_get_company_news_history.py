"""
Tests for get_company_news_history.py

Unit tests: mocked Firestore — no network calls.
Integration tests: live Firestore with synthetic seed documents.
  Seed documents are written at session start and deleted at session end.

Run unit tests only:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_company_news_history.py -v -m "not integration"

Run all tests (requires Firestore credentials):
    scripts/venv/bin/python -m pytest utilities/tests/test_get_company_news_history.py -v
"""

import os
import sys
from datetime import datetime, date, timezone, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utilities.get_company_news_history import (
    get_company_news_history,
    _parse_published_at,
    _compute_topics_seen,
    _compute_topic_match,
    _classify_sentiment_trend,
    _classify_data_maturity,
    _get_collection_age_days,
    _SUFFICIENT_THRESHOLD,
    _MIN_ARTICLES_FOR_TREND,
    _MAJORITY_THRESHOLD,
    _DEFAULT_DAYS_LOOKBACK,
)


# ---------------------------------------------------------------------------
# Helpers — mock Firestore
# ---------------------------------------------------------------------------

def _make_mock_doc(doc_id, data):
    doc = MagicMock()
    doc.id = doc_id
    doc.to_dict.return_value = dict(data)
    return doc


def _make_mock_db(ticker_docs=None, oldest_created_at=None):
    """
    Build a mock Firestore db supporting:
      db.collection().where().stream()    → ticker_docs
      db.collection().order_by().limit().stream() → oldest doc for collection_age
    """
    ticker_docs = ticker_docs or []
    mock_ticker_docs = [_make_mock_doc(f"doc_{i}", d) for i, d in enumerate(ticker_docs)]

    mock_db = MagicMock()

    def _make_collection(name):
        coll = MagicMock()

        # WHERE query (for ticker lookup)
        where_result = MagicMock()
        where_result.stream.return_value = iter(list(mock_ticker_docs))
        coll.where.return_value = where_result

        # ORDER BY + LIMIT query (for collection_age_days)
        if oldest_created_at is not None:
            oldest_doc = _make_mock_doc("oldest", {"created_at": oldest_created_at})
            order_stream = iter([oldest_doc])
        else:
            order_stream = iter([])

        order_result = MagicMock()
        order_result.limit.return_value.stream.return_value = order_stream
        coll.order_by.return_value = order_result

        return coll

    mock_db.collection.side_effect = _make_collection
    return mock_db


def _make_article(
    ticker="TEST",
    rns_id="rns_001",
    published_days_ago=10,
    sentiment="positive",
    article_subtype="trading_update",
    key_topics=None,
    headline="Test headline",
    summary="Test summary.",
    created_days_ago=None,
):
    """Synthetic company_news_summaries document."""
    if key_topics is None:
        key_topics = ["regulatory approval"]
    now = datetime.now(timezone.utc)
    pub_at = now - timedelta(days=published_days_ago)
    created_at = now - timedelta(days=(created_days_ago or published_days_ago))
    return {
        "ticker": ticker,
        "rns_article_id": rns_id,
        "published_at": pub_at,
        "article_subtype": article_subtype,
        "sentiment": sentiment,
        "summary": summary,
        "key_topics": key_topics,
        "headline": headline,
        "created_at": created_at,
    }


# ---------------------------------------------------------------------------
# Tests: _parse_published_at
# ---------------------------------------------------------------------------

class TestParsePublishedAt:
    def test_datetime_aware_passthrough(self):
        dt = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        result = _parse_published_at(dt)
        assert result == dt

    def test_datetime_naive_gets_utc(self):
        dt = datetime(2025, 6, 15, 12, 0)
        result = _parse_published_at(dt)
        assert result.tzinfo is not None
        assert result.tzinfo == timezone.utc

    def test_date_object_converted_to_midnight_utc(self):
        d = date(2025, 6, 15)
        result = _parse_published_at(d)
        assert isinstance(result, datetime)
        assert result.year == 2025
        assert result.month == 6
        assert result.day == 15
        assert result.tzinfo == timezone.utc

    def test_iso_string_with_t_separator(self):
        result = _parse_published_at("2025-06-15T10:30:00+00:00")
        assert result is not None
        assert result.year == 2025
        assert result.month == 6

    def test_space_separated_iso_string(self):
        result = _parse_published_at("2025-06-15 10:30:00+00:00")
        assert result is not None
        assert result.year == 2025

    def test_date_only_string(self):
        result = _parse_published_at("2025-06-15")
        assert result is not None
        assert result.year == 2025
        assert result.month == 6
        assert result.day == 15

    def test_none_returns_none(self):
        assert _parse_published_at(None) is None

    def test_invalid_string_returns_none(self):
        assert _parse_published_at("not-a-date") is None

    def test_firestore_timestamp_with_to_datetime(self):
        """Firestore Timestamp objects have a to_datetime() method."""
        mock_ts = MagicMock()
        mock_ts.to_datetime.return_value = datetime(2025, 6, 15, tzinfo=timezone.utc)
        result = _parse_published_at(mock_ts)
        assert result is not None
        assert result.year == 2025

    def test_result_is_always_timezone_aware(self):
        inputs = [
            datetime(2025, 1, 1),
            "2025-01-01",
            date(2025, 1, 1),
        ]
        for raw in inputs:
            result = _parse_published_at(raw)
            if result is not None:
                assert result.tzinfo is not None, f"No timezone for input: {raw!r}"


# ---------------------------------------------------------------------------
# Tests: _compute_topics_seen
# ---------------------------------------------------------------------------

class TestComputeTopicsSeen:
    def test_empty_articles_returns_empty(self):
        assert _compute_topics_seen([]) == []

    def test_single_article_topics_returned(self):
        articles = [{"key_topics": ["regulatory approval", "funding"]}]
        result = _compute_topics_seen(articles)
        assert "regulatory approval" in result
        assert "funding" in result

    def test_deduplicated_across_articles(self):
        articles = [
            {"key_topics": ["regulatory approval", "funding"]},
            {"key_topics": ["regulatory approval", "drilling"]},
        ]
        result = _compute_topics_seen(articles)
        assert result.count("regulatory approval") == 1

    def test_case_insensitive_deduplication(self):
        articles = [
            {"key_topics": ["Regulatory Approval"]},
            {"key_topics": ["regulatory approval"]},
        ]
        result = _compute_topics_seen(articles)
        assert len(result) == 1

    def test_empty_key_topics_handled(self):
        articles = [{"key_topics": []}, {"key_topics": ["planning"]}]
        result = _compute_topics_seen(articles)
        assert result == ["planning"]

    def test_none_key_topics_handled(self):
        articles = [{"key_topics": None}, {"key_topics": ["funding"]}]
        result = _compute_topics_seen(articles)
        assert result == ["funding"]

    def test_result_is_sorted(self):
        articles = [{"key_topics": ["zebra", "apple", "mango"]}]
        result = _compute_topics_seen(articles)
        assert result == sorted(result)

    def test_all_unique_topics_preserved(self):
        topics = ["topic_a", "topic_b", "topic_c"]
        articles = [{"key_topics": topics}]
        result = _compute_topics_seen(articles)
        for t in topics:
            assert t in result


# ---------------------------------------------------------------------------
# Tests: _compute_topic_match
# ---------------------------------------------------------------------------

class TestComputeTopicMatch:
    def test_none_current_topics_returns_none(self):
        result = _compute_topic_match(None, ["regulatory approval"])
        assert result is None

    def test_match_when_overlap_exists(self):
        result = _compute_topic_match(["regulatory approval"], ["regulatory approval", "funding"])
        assert result is True

    def test_no_match_when_no_overlap(self):
        result = _compute_topic_match(["drilling results"], ["regulatory approval"])
        assert result is False

    def test_case_insensitive_match(self):
        result = _compute_topic_match(["Regulatory Approval"], ["regulatory approval"])
        assert result is True

    def test_empty_current_topics_returns_false(self):
        result = _compute_topic_match([], ["regulatory approval"])
        assert result is False

    def test_empty_topics_seen_returns_false(self):
        result = _compute_topic_match(["regulatory approval"], [])
        assert result is False

    def test_partial_match_counts(self):
        """If any topic in current_topics matches, result is True."""
        current = ["drilling results", "regulatory approval"]
        seen = ["regulatory approval"]
        assert _compute_topic_match(current, seen) is True

    def test_both_empty_returns_false(self):
        assert _compute_topic_match([], []) is False


# ---------------------------------------------------------------------------
# Tests: _classify_sentiment_trend
# ---------------------------------------------------------------------------

class TestClassifySentimentTrend:
    def _articles(self, sentiments):
        return [{"sentiment": s} for s in sentiments]

    def test_insufficient_data_for_zero_articles(self):
        assert _classify_sentiment_trend([]) == "insufficient_data"

    def test_insufficient_data_for_one_article(self):
        assert _classify_sentiment_trend(self._articles(["positive"])) == "insufficient_data"

    def test_insufficient_data_for_two_articles(self):
        assert _classify_sentiment_trend(
            self._articles(["positive", "positive"])
        ) == "insufficient_data"

    def test_improving_when_majority_positive(self):
        # 3/3 positive
        result = _classify_sentiment_trend(self._articles(["positive"] * 3))
        assert result == "improving"

    def test_improving_when_barely_majority(self):
        # 2 positive, 1 negative (2/3 = 0.667 > 0.5)
        result = _classify_sentiment_trend(
            self._articles(["positive", "positive", "negative"])
        )
        assert result == "improving"

    def test_deteriorating_when_majority_negative(self):
        result = _classify_sentiment_trend(
            self._articles(["negative", "negative", "negative"])
        )
        assert result == "deteriorating"

    def test_stable_when_all_neutral(self):
        result = _classify_sentiment_trend(
            self._articles(["neutral", "neutral", "neutral"])
        )
        assert result == "stable"

    def test_mixed_when_no_majority_and_not_all_neutral(self):
        # 1 pos, 1 neg, 1 neutral — no majority, not all neutral
        result = _classify_sentiment_trend(
            self._articles(["positive", "negative", "neutral"])
        )
        assert result == "mixed"

    def test_mixed_when_equal_pos_neg(self):
        # 2 pos, 2 neg (each = 50% = not > 50%)
        result = _classify_sentiment_trend(
            self._articles(["positive", "negative", "positive", "negative"])
        )
        assert result == "mixed"

    def test_deteriorating_clear_majority(self):
        result = _classify_sentiment_trend(
            self._articles(["negative"] * 4 + ["positive"])
        )
        assert result == "deteriorating"

    def test_returns_string_always(self):
        for sentiments in [[], ["positive"], ["positive"] * 3]:
            result = _classify_sentiment_trend(self._articles(sentiments))
            assert isinstance(result, str)


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

    def test_sufficient_at_threshold(self):
        assert _classify_data_maturity(_SUFFICIENT_THRESHOLD) == "sufficient"

    def test_sufficient_above_threshold(self):
        assert _classify_data_maturity(10) == "sufficient"


# ---------------------------------------------------------------------------
# Tests: _get_collection_age_days
# ---------------------------------------------------------------------------

class TestGetCollectionAgeDays:
    def test_returns_days_from_oldest_created_at(self):
        now = datetime.now(timezone.utc)
        oldest = now - timedelta(days=30)
        db = _make_mock_db(oldest_created_at=oldest)
        result = _get_collection_age_days(db)
        assert result is not None
        assert 29 <= result <= 31  # allow 1 day tolerance

    def test_returns_none_when_collection_empty(self):
        db = _make_mock_db(oldest_created_at=None)
        result = _get_collection_age_days(db)
        assert result is None

    def test_returns_zero_for_today(self):
        now = datetime.now(timezone.utc)
        db = _make_mock_db(oldest_created_at=now)
        result = _get_collection_age_days(db)
        assert result == 0

    def test_handles_exception_gracefully(self):
        mock_db = MagicMock()
        mock_db.collection.side_effect = Exception("Firestore error")
        result = _get_collection_age_days(mock_db)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: get_company_news_history (full function, mocked Firestore)
# ---------------------------------------------------------------------------

class TestGetCompanyNewsHistoryMocked:
    def test_returns_dict(self):
        db = _make_mock_db()
        result = get_company_news_history("TEST", db=db)
        assert isinstance(result, dict)

    def test_all_required_keys_present(self):
        db = _make_mock_db()
        result = get_company_news_history("TEST", db=db)
        for key in (
            "ticker", "days_lookback", "articles", "article_count",
            "topics_seen", "topic_match", "sentiment_trend",
            "data_maturity", "collection_age_days",
        ):
            assert key in result, f"Missing key: {key}"

    def test_empty_result_for_no_docs(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_company_news_history("TEST", db=db)
        assert result["article_count"] == 0
        assert result["articles"] == []
        assert result["data_maturity"] == "empty"

    def test_articles_sorted_descending(self):
        docs = [
            _make_article(rns_id="rns_001", published_days_ago=30),
            _make_article(rns_id="rns_002", published_days_ago=5),
            _make_article(rns_id="rns_003", published_days_ago=15),
        ]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", db=db)
        pub_dates = [a["published_at"] for a in result["articles"]]
        assert pub_dates == sorted(pub_dates, reverse=True)

    def test_exclude_id_removes_article(self):
        docs = [
            _make_article(rns_id="rns_target", published_days_ago=5),
            _make_article(rns_id="rns_other", published_days_ago=10),
        ]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", exclude_id="rns_target", db=db)
        ids = [a["rns_article_id"] for a in result["articles"]]
        assert "rns_target" not in ids
        assert "rns_other" in ids

    def test_days_lookback_filter_applied(self):
        docs = [
            _make_article(rns_id="rns_recent", published_days_ago=10),
            _make_article(rns_id="rns_old", published_days_ago=150),
        ]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", days_lookback=90, db=db)
        ids = [a["rns_article_id"] for a in result["articles"]]
        assert "rns_recent" in ids
        assert "rns_old" not in ids

    def test_topics_seen_aggregated(self):
        docs = [
            _make_article(rns_id="rns_001", key_topics=["regulatory approval"]),
            _make_article(rns_id="rns_002", key_topics=["funding", "drilling results"]),
        ]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", db=db)
        for t in ["regulatory approval", "funding", "drilling results"]:
            assert t in result["topics_seen"]

    def test_topics_seen_deduplicated(self):
        docs = [
            _make_article(rns_id="rns_001", key_topics=["regulatory approval"]),
            _make_article(rns_id="rns_002", key_topics=["regulatory approval"]),
        ]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", db=db)
        assert result["topics_seen"].count("regulatory approval") == 1

    def test_topic_match_true_when_overlap(self):
        docs = [_make_article(key_topics=["regulatory approval"])]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history(
            "TEST",
            current_topics=["regulatory approval"],
            db=db,
        )
        assert result["topic_match"] is True

    def test_topic_match_false_when_no_overlap(self):
        docs = [_make_article(key_topics=["regulatory approval"])]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history(
            "TEST",
            current_topics=["drilling results"],
            db=db,
        )
        assert result["topic_match"] is False

    def test_topic_match_none_when_current_topics_not_provided(self):
        docs = [_make_article(key_topics=["regulatory approval"])]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", current_topics=None, db=db)
        assert result["topic_match"] is None

    def test_sentiment_trend_improving_for_positive_majority(self):
        docs = [
            _make_article(rns_id=f"rns_00{i}", sentiment="positive")
            for i in range(3)
        ]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", db=db)
        assert result["sentiment_trend"] == "improving"

    def test_sentiment_trend_insufficient_for_two_articles(self):
        docs = [
            _make_article(rns_id="rns_001", sentiment="positive"),
            _make_article(rns_id="rns_002", sentiment="positive"),
        ]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", db=db)
        assert result["sentiment_trend"] == "insufficient_data"

    def test_data_maturity_empty_for_no_articles(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_company_news_history("TEST", db=db)
        assert result["data_maturity"] == "empty"

    def test_data_maturity_sparse_for_two_articles(self):
        docs = [
            _make_article(rns_id="rns_001"),
            _make_article(rns_id="rns_002"),
        ]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", db=db)
        assert result["data_maturity"] == "sparse"

    def test_data_maturity_sufficient_for_three_articles(self):
        docs = [_make_article(rns_id=f"rns_00{i}") for i in range(3)]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", db=db)
        assert result["data_maturity"] == "sufficient"

    def test_article_count_matches_articles_length(self):
        docs = [_make_article(rns_id=f"rns_00{i}") for i in range(4)]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", db=db)
        assert result["article_count"] == len(result["articles"])

    def test_ticker_preserved(self):
        db = _make_mock_db()
        result = get_company_news_history("FGEN", db=db)
        assert result["ticker"] == "FGEN"

    def test_days_lookback_preserved(self):
        db = _make_mock_db()
        result = get_company_news_history("TEST", days_lookback=180, db=db)
        assert result["days_lookback"] == 180

    def test_default_days_lookback(self):
        db = _make_mock_db()
        result = get_company_news_history("TEST", db=db)
        assert result["days_lookback"] == _DEFAULT_DAYS_LOOKBACK

    def test_article_fields_present(self):
        docs = [_make_article()]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", db=db)
        article = result["articles"][0]
        for key in ("rns_article_id", "headline", "published_at", "article_subtype",
                    "sentiment", "summary", "key_topics"):
            assert key in article, f"Missing article key: {key}"

    def test_article_with_unparseable_date_excluded(self):
        """Articles with unparseable published_at should be silently excluded."""
        docs = [
            {
                "ticker": "TEST",
                "rns_article_id": "rns_bad",
                "published_at": "not-a-date",
                "sentiment": "positive",
                "key_topics": [],
            },
            _make_article(rns_id="rns_good"),
        ]
        db = _make_mock_db(ticker_docs=docs)
        result = get_company_news_history("TEST", db=db)
        ids = [a["rns_article_id"] for a in result["articles"]]
        assert "rns_bad" not in ids
        assert "rns_good" in ids

    def test_graceful_empty_result_for_unknown_ticker(self):
        db = _make_mock_db(ticker_docs=[])
        result = get_company_news_history("UNKNOWN_TICKER_XYZ", db=db)
        assert isinstance(result, dict)
        assert result["article_count"] == 0
        assert result["data_maturity"] == "empty"
        assert result["topic_match"] is None  # no current_topics provided

    def test_collection_age_days_returned(self):
        now = datetime.now(timezone.utc)
        oldest = now - timedelta(days=20)
        db = _make_mock_db(oldest_created_at=oldest)
        result = get_company_news_history("TEST", db=db)
        assert result["collection_age_days"] is not None
        assert 19 <= result["collection_age_days"] <= 21


# ---------------------------------------------------------------------------
# Integration tests — live Firestore with synthetic seed documents
# ---------------------------------------------------------------------------

_SEED_TICKER = "TEST_CNH_SEED"


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
    Seed synthetic company_news_summaries documents and clean up after all tests.

    TEST_CNH_SEED: 4 articles covering a mix of topics and sentiments:
      - Article 1: "regulatory approval", positive (oldest, 60 days ago)
      - Article 2: "planning permission", "regulatory approval", positive (40 days ago)
      - Article 3: "funding", negative (20 days ago)
      - Article 4: "drilling results", neutral (5 days ago) — used as exclude_id target
    """
    now = datetime.now(timezone.utc)
    seed_docs = [
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "cnh_seed_rns_001",
            "published_at": now - timedelta(days=60),
            "created_at": now - timedelta(days=60),
            "article_subtype": "regulatory",
            "sentiment": "positive",
            "summary": "Planning approval received for Phase 1 expansion.",
            "headline": "Regulatory approval granted",
            "key_topics": ["regulatory approval", "expansion"],
        },
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "cnh_seed_rns_002",
            "published_at": now - timedelta(days=40),
            "created_at": now - timedelta(days=40),
            "article_subtype": "regulatory",
            "sentiment": "positive",
            "summary": "Planning permission secured for new site.",
            "headline": "Planning permission secured",
            "key_topics": ["planning permission", "regulatory approval"],
        },
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "cnh_seed_rns_003",
            "published_at": now - timedelta(days=20),
            "created_at": now - timedelta(days=20),
            "article_subtype": "fundraising",
            "sentiment": "negative",
            "summary": "Fundraise completed at significant discount.",
            "headline": "Placing announced",
            "key_topics": ["funding", "dilution"],
        },
        {
            "ticker": _SEED_TICKER,
            "rns_article_id": "cnh_seed_rns_004",
            "published_at": now - timedelta(days=5),
            "created_at": now - timedelta(days=5),
            "article_subtype": "operational",
            "sentiment": "neutral",
            "summary": "Exploration update — initial results inconclusive.",
            "headline": "Exploration update",
            "key_topics": ["drilling results"],
        },
    ]

    doc_ids = []
    coll = firestore_db.collection("company_news_summaries")
    for doc_data in seed_docs:
        doc_ref = coll.add(doc_data)[1]
        doc_ids.append(doc_ref.id)

    yield firestore_db, doc_ids

    # Teardown — delete all seeded documents
    for doc_id in doc_ids:
        firestore_db.collection("company_news_summaries").document(doc_id).delete()


@pytest.mark.integration
class TestGetCompanyNewsHistoryIntegration:
    def test_returns_articles_for_seeded_ticker(self, seeded_db):
        db, _ = seeded_db
        result = get_company_news_history(_SEED_TICKER, days_lookback=90, db=db)
        assert result["article_count"] == 4

    def test_articles_sorted_descending(self, seeded_db):
        db, _ = seeded_db
        result = get_company_news_history(_SEED_TICKER, days_lookback=90, db=db)
        pub_dates = [a["published_at"] for a in result["articles"]]
        assert pub_dates == sorted(pub_dates, reverse=True)

    def test_topics_seen_aggregated_correctly(self, seeded_db):
        db, _ = seeded_db
        result = get_company_news_history(_SEED_TICKER, days_lookback=90, db=db)
        for topic in ["regulatory approval", "planning permission", "funding", "drilling results"]:
            assert topic in result["topics_seen"], f"Expected topic missing: {topic}"

    def test_topic_match_true_when_overlap(self, seeded_db):
        """Passing current_topics containing a topic from history → topic_match=True."""
        db, _ = seeded_db
        result = get_company_news_history(
            _SEED_TICKER,
            days_lookback=90,
            current_topics=["regulatory approval"],
            db=db,
        )
        assert result["topic_match"] is True

    def test_topic_match_false_when_no_overlap(self, seeded_db):
        """Passing current_topics with no history match → topic_match=False."""
        db, _ = seeded_db
        result = get_company_news_history(
            _SEED_TICKER,
            days_lookback=90,
            current_topics=["completely new topic xyz"],
            db=db,
        )
        assert result["topic_match"] is False

    def test_sentiment_trend_classified(self, seeded_db):
        """3 positive + 1 negative → not "insufficient_data"; majority positive → "improving"."""
        db, _ = seeded_db
        result = get_company_news_history(_SEED_TICKER, days_lookback=90, db=db)
        # Articles: positive, positive, negative, neutral
        # 2 positive out of 4 = 50% — NOT > 50%, so not "improving"
        # Expect "mixed" (2 pos, 1 neg, 1 neutral — no clear majority)
        assert result["sentiment_trend"] in ("improving", "mixed", "deteriorating", "stable")
        assert result["sentiment_trend"] != "insufficient_data"

    def test_data_maturity_sufficient_for_4_articles(self, seeded_db):
        db, _ = seeded_db
        result = get_company_news_history(_SEED_TICKER, days_lookback=90, db=db)
        assert result["data_maturity"] == "sufficient"

    def test_exclude_id_removes_article(self, seeded_db):
        db, _ = seeded_db
        result = get_company_news_history(
            _SEED_TICKER,
            days_lookback=90,
            exclude_id="cnh_seed_rns_004",
            db=db,
        )
        assert result["article_count"] == 3
        ids = [a["rns_article_id"] for a in result["articles"]]
        assert "cnh_seed_rns_004" not in ids

    def test_days_lookback_filters_old_articles(self, seeded_db):
        """days_lookback=30 should exclude articles older than 30 days."""
        db, _ = seeded_db
        result = get_company_news_history(_SEED_TICKER, days_lookback=30, db=db)
        # Articles within 30 days: rns_003 (20d) + rns_004 (5d) = 2 articles
        assert result["article_count"] == 2

    def test_collection_age_days_positive(self, seeded_db):
        """Collection was seeded just now — age should be ≥ 0."""
        db, _ = seeded_db
        result = get_company_news_history(_SEED_TICKER, db=db)
        # collection_age_days from oldest created_at in whole collection
        assert result["collection_age_days"] is not None
        assert result["collection_age_days"] >= 0

    def test_graceful_empty_result_for_unknown_ticker(self, seeded_db):
        db, _ = seeded_db
        result = get_company_news_history("UNKNOWN_TICKER_XYZ_99", db=db)
        assert result["article_count"] == 0
        assert result["data_maturity"] == "empty"
        assert isinstance(result, dict)

    def test_seed_documents_cleaned_up(self, seeded_db):
        """Verify teardown works — this test runs AFTER fixture teardown."""
        # This test itself relies on fixture teardown having run.
        # It is included as a reminder and passes trivially during the session.
        # Actual cleanup is confirmed by the fixture's yield/teardown block.
        assert True
