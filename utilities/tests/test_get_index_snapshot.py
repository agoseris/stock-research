"""
test_get_index_snapshot.py

Tests for the get_index_snapshot tool.

Unit tests use a mocked Firestore client — no credentials needed.
Integration tests require GOOGLE_APPLICATION_CREDENTIALS to be set and
are skipped automatically when credentials are absent.

Run unit tests only:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_index_snapshot.py -v -m "not integration"

Run all tests (requires Firestore credentials and at least one scraper run):
    scripts/venv/bin/python -m pytest utilities/tests/test_get_index_snapshot.py -v
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utilities.get_index_snapshot import (
    get_index_snapshot,
    _select_index,
    _build_result,
    _empty_result,
    _parse_scrape_date,
    _get_snapshot_by_date,
    _get_most_recent_snapshot,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIRESTORE_CREDENTIALS_PRESENT = bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))

_TODAY = datetime.now(timezone.utc).date()


def _make_snapshot_data(
    index_name="AIM_ALL_SHARE",
    index_value=850.25,
    change_1d_pct=-0.42,
    week_52_high=950.00,
    week_52_low=750.00,
    scrape_date=None,
):
    """Return a minimal index_snapshots Firestore document dict."""
    return {
        "index_name": index_name,
        "index_value": index_value,
        "change_1d_pct": change_1d_pct,
        "week_52_high": week_52_high,
        "week_52_low": week_52_low,
        "scrape_date": scrape_date or _TODAY.isoformat(),
    }


def _make_db_with_doc(doc_data: dict = None, exists: bool = True):
    """Return a mock Firestore client that returns the given document on direct get."""
    mock_doc = MagicMock()
    mock_doc.exists = exists
    mock_doc.to_dict.return_value = doc_data or {}

    mock_col = MagicMock()
    mock_col.document.return_value.get.return_value = mock_doc

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_col
    return mock_db


def _make_db_no_today_has_recent(recent_data: dict):
    """
    Return a mock Firestore client where:
    - Direct document get returns not found (today's snapshot absent)
    - Query for most recent returns one document
    """
    # Direct get returns not found
    mock_doc = MagicMock()
    mock_doc.exists = False

    # Query stream returns one result
    mock_query_doc = MagicMock()
    mock_query_doc.to_dict.return_value = recent_data

    mock_col = MagicMock()
    mock_col.document.return_value.get.return_value = mock_doc

    # Chain for query: .where().order_by().limit().stream()
    mock_stream = MagicMock()
    mock_stream.__iter__ = MagicMock(return_value=iter([mock_query_doc]))
    mock_col.where.return_value.order_by.return_value.limit.return_value.stream.return_value = mock_stream

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_col
    return mock_db


def _make_db_no_data():
    """Return a mock Firestore client where no snapshot exists anywhere."""
    mock_doc = MagicMock()
    mock_doc.exists = False

    mock_col = MagicMock()
    mock_col.document.return_value.get.return_value = mock_doc

    mock_stream = MagicMock()
    mock_stream.__iter__ = MagicMock(return_value=iter([]))
    mock_col.where.return_value.order_by.return_value.limit.return_value.stream.return_value = mock_stream

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_col
    return mock_db


# ---------------------------------------------------------------------------
# _select_index tests
# ---------------------------------------------------------------------------

class TestSelectIndex:

    def test_aim_returns_aim_all_share(self):
        assert _select_index("AIM", 50_000_000) == "AIM_ALL_SHARE"

    def test_aim_returns_aim_regardless_of_cap(self):
        assert _select_index("AIM", 1_000_000_000) == "AIM_ALL_SHARE"

    def test_aim_cap_none_returns_aim_all_share(self):
        assert _select_index("AIM", None) == "AIM_ALL_SHARE"

    def test_main_market_below_500m_returns_ftse_small_cap(self):
        assert _select_index("MAIN_MARKET", 200_000_000) == "FTSE_SMALL_CAP"

    def test_main_market_above_500m_returns_ftse_250(self):
        assert _select_index("MAIN_MARKET", 600_000_000) == "FTSE_250"

    def test_main_market_exactly_500m_returns_ftse_250(self):
        # Boundary: exactly £500m → FTSE 250 (≥ threshold)
        assert _select_index("MAIN_MARKET", 500_000_000) == "FTSE_250"

    def test_main_market_499m_returns_ftse_small_cap(self):
        # Just below £500m → FTSE Small Cap
        assert _select_index("MAIN_MARKET", 499_999_999) == "FTSE_SMALL_CAP"

    def test_main_market_cap_none_returns_ftse_small_cap(self):
        # Safe default: unknown cap → FTSE Small Cap
        assert _select_index("MAIN_MARKET", None) == "FTSE_SMALL_CAP"

    def test_main_market_cap_zero_returns_ftse_small_cap(self):
        assert _select_index("MAIN_MARKET", 0) == "FTSE_SMALL_CAP"

    def test_main_market_very_large_cap_returns_ftse_250(self):
        assert _select_index("MAIN_MARKET", 10_000_000_000) == "FTSE_250"


# ---------------------------------------------------------------------------
# _parse_scrape_date tests
# ---------------------------------------------------------------------------

class TestParseScrapedDate:

    def test_none_returns_none(self):
        assert _parse_scrape_date(None) is None

    def test_iso_string(self):
        assert _parse_scrape_date("2026-03-05") == date(2026, 3, 5)

    def test_iso_string_with_time(self):
        # Firestore may return datetime strings
        assert _parse_scrape_date("2026-03-05T17:00:00+00:00") == date(2026, 3, 5)

    def test_date_object(self):
        d = date(2026, 3, 5)
        assert _parse_scrape_date(d) == d

    def test_datetime_object(self):
        dt = datetime(2026, 3, 5, 17, 0, tzinfo=timezone.utc)
        assert _parse_scrape_date(dt) == date(2026, 3, 5)

    def test_empty_string_returns_none(self):
        assert _parse_scrape_date("") is None

    def test_invalid_string_returns_none(self):
        assert _parse_scrape_date("not-a-date") is None


# ---------------------------------------------------------------------------
# _build_result tests
# ---------------------------------------------------------------------------

class TestBuildResult:

    def test_basic_structure(self):
        data = _make_snapshot_data(scrape_date="2026-03-05")
        result = _build_result("AIM_ALL_SHARE", data, date(2026, 3, 5))
        assert result["data_found"] is True
        assert result["index_name"] == "AIM_ALL_SHARE"
        assert result["index_value"] == 850.25
        assert result["change_1d_pct"] == -0.42
        assert result["week_52_high"] == 950.00
        assert result["week_52_low"] == 750.00
        assert result["scrape_date"] == date(2026, 3, 5)

    def test_snapshot_age_days_zero_for_today(self):
        data = _make_snapshot_data(scrape_date=_TODAY.isoformat())
        result = _build_result("AIM_ALL_SHARE", data, _TODAY)
        assert result["snapshot_age_days"] == 0

    def test_snapshot_age_days_one(self):
        yesterday = _TODAY - timedelta(days=1)
        data = _make_snapshot_data(scrape_date=yesterday.isoformat())
        result = _build_result("AIM_ALL_SHARE", data, _TODAY)
        assert result["snapshot_age_days"] == 1

    def test_snapshot_age_days_weekend_gap(self):
        # Weekend gap: Friday snapshot read on Monday → age = 3
        friday = date(2026, 3, 6)   # a Friday
        monday = date(2026, 3, 9)   # the following Monday
        data = _make_snapshot_data(scrape_date=friday.isoformat())
        result = _build_result("AIM_ALL_SHARE", data, monday)
        assert result["snapshot_age_days"] == 3

    def test_snapshot_age_none_when_scrape_date_none(self):
        data = _make_snapshot_data()
        data["scrape_date"] = None
        result = _build_result("AIM_ALL_SHARE", data, _TODAY)
        assert result["snapshot_age_days"] is None

    def test_all_required_keys_present(self):
        data = _make_snapshot_data()
        result = _build_result("AIM_ALL_SHARE", data, _TODAY)
        required = {
            "data_found", "index_name", "index_value", "week_52_high",
            "week_52_low", "change_1d_pct", "scrape_date", "snapshot_age_days",
        }
        assert required.issubset(set(result.keys()))


# ---------------------------------------------------------------------------
# _empty_result tests
# ---------------------------------------------------------------------------

class TestEmptyResult:

    def test_data_found_false(self):
        result = _empty_result("AIM_ALL_SHARE")
        assert result["data_found"] is False
        assert result["index_name"] == "AIM_ALL_SHARE"

    def test_all_numeric_fields_none(self):
        result = _empty_result("FTSE_250")
        assert result["index_value"] is None
        assert result["week_52_high"] is None
        assert result["week_52_low"] is None
        assert result["change_1d_pct"] is None
        assert result["scrape_date"] is None
        assert result["snapshot_age_days"] is None

    def test_all_required_keys_present(self):
        result = _empty_result("FTSE_SMALL_CAP")
        required = {
            "data_found", "index_name", "index_value", "week_52_high",
            "week_52_low", "change_1d_pct", "scrape_date", "snapshot_age_days",
        }
        assert required.issubset(set(result.keys()))


# ---------------------------------------------------------------------------
# get_index_snapshot — mock Firestore
# ---------------------------------------------------------------------------

class TestGetIndexSnapshot:

    def test_aim_today_found(self):
        data = _make_snapshot_data(index_name="AIM_ALL_SHARE")
        db = _make_db_with_doc(doc_data=data, exists=True)
        result = get_index_snapshot("AIM", market_cap_gbp=50_000_000, db=db)
        assert result["data_found"] is True
        assert result["index_name"] == "AIM_ALL_SHARE"

    def test_main_market_below_500m_uses_small_cap(self):
        data = _make_snapshot_data(index_name="FTSE_SMALL_CAP")
        db = _make_db_with_doc(doc_data=data, exists=True)
        result = get_index_snapshot("MAIN_MARKET", market_cap_gbp=200_000_000, db=db)
        assert result["data_found"] is True
        assert result["index_name"] == "FTSE_SMALL_CAP"

    def test_main_market_above_500m_uses_ftse_250(self):
        data = _make_snapshot_data(index_name="FTSE_250")
        db = _make_db_with_doc(doc_data=data, exists=True)
        result = get_index_snapshot("MAIN_MARKET", market_cap_gbp=700_000_000, db=db)
        assert result["data_found"] is True
        assert result["index_name"] == "FTSE_250"

    def test_today_missing_falls_back_to_most_recent(self):
        recent_data = _make_snapshot_data(
            index_name="AIM_ALL_SHARE",
            scrape_date=(_TODAY - timedelta(days=1)).isoformat(),
        )
        db = _make_db_no_today_has_recent(recent_data)
        result = get_index_snapshot("AIM", market_cap_gbp=80_000_000, db=db)
        assert result["data_found"] is True
        assert result["snapshot_age_days"] == 1

    def test_no_data_returns_data_found_false(self):
        db = _make_db_no_data()
        result = get_index_snapshot("AIM", market_cap_gbp=50_000_000, db=db)
        assert result["data_found"] is False
        assert result["index_name"] == "AIM_ALL_SHARE"

    def test_no_data_does_not_raise(self):
        db = _make_db_no_data()
        result = get_index_snapshot("MAIN_MARKET", market_cap_gbp=None, db=db)
        assert isinstance(result, dict)

    def test_firestore_exception_returns_gracefully(self):
        mock_db = MagicMock()
        mock_db.collection.side_effect = Exception("Firestore unavailable")
        result = get_index_snapshot("AIM", market_cap_gbp=50_000_000, db=mock_db)
        assert result["data_found"] is False
        assert isinstance(result, dict)

    def test_all_required_keys_present_when_found(self):
        data = _make_snapshot_data()
        db = _make_db_with_doc(doc_data=data, exists=True)
        result = get_index_snapshot("AIM", db=db)
        required = {
            "data_found", "index_name", "index_value", "week_52_high",
            "week_52_low", "change_1d_pct", "scrape_date", "snapshot_age_days",
        }
        assert required.issubset(set(result.keys()))

    def test_all_required_keys_present_when_not_found(self):
        db = _make_db_no_data()
        result = get_index_snapshot("AIM", db=db)
        required = {
            "data_found", "index_name", "index_value", "week_52_high",
            "week_52_low", "change_1d_pct", "scrape_date", "snapshot_age_days",
        }
        assert required.issubset(set(result.keys()))

    def test_main_market_cap_none_uses_ftse_small_cap(self):
        db = _make_db_no_data()
        result = get_index_snapshot("MAIN_MARKET", market_cap_gbp=None, db=db)
        assert result["index_name"] == "FTSE_SMALL_CAP"

    def test_main_market_exactly_500m_uses_ftse_250(self):
        db = _make_db_no_data()
        result = get_index_snapshot("MAIN_MARKET", market_cap_gbp=500_000_000, db=db)
        assert result["index_name"] == "FTSE_250"

    def test_snapshot_age_days_correct(self):
        scrape_date = _TODAY - timedelta(days=2)
        data = _make_snapshot_data(scrape_date=scrape_date.isoformat())
        db = _make_db_with_doc(doc_data=data, exists=True)
        result = get_index_snapshot("AIM", db=db)
        assert result["snapshot_age_days"] == 2

    def test_today_snapshot_age_zero(self):
        data = _make_snapshot_data(scrape_date=_TODAY.isoformat())
        db = _make_db_with_doc(doc_data=data, exists=True)
        result = get_index_snapshot("AIM", db=db)
        assert result["snapshot_age_days"] == 0

    def test_doc_id_format_uses_correct_index_name(self):
        """Verify the document ID constructed for today's direct get uses correct format."""
        data = _make_snapshot_data()
        db = _make_db_with_doc(doc_data=data, exists=True)
        get_index_snapshot("AIM", market_cap_gbp=50_000_000, db=db)
        # Check that collection was called with index_snapshots
        db.collection.assert_called_with("index_snapshots")
        # Check that document was called with the expected ID format
        expected_doc_id = f"AIM_ALL_SHARE_{_TODAY.isoformat()}"
        db.collection.return_value.document.assert_called_with(expected_doc_id)

    def test_positive_change_1d_pct(self):
        data = _make_snapshot_data(change_1d_pct=1.23)
        db = _make_db_with_doc(doc_data=data, exists=True)
        result = get_index_snapshot("AIM", db=db)
        assert result["change_1d_pct"] == 1.23

    def test_negative_change_1d_pct(self):
        data = _make_snapshot_data(change_1d_pct=-0.78)
        db = _make_db_with_doc(doc_data=data, exists=True)
        result = get_index_snapshot("AIM", db=db)
        assert result["change_1d_pct"] == -0.78


# ---------------------------------------------------------------------------
# Integration tests — require live Firestore + at least one scraper run
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    not FIRESTORE_CREDENTIALS_PRESENT,
    reason="GOOGLE_APPLICATION_CREDENTIALS not set — skipping Firestore integration tests",
)
class TestGetIndexSnapshotIntegration:
    """
    Acceptance criteria checks against live Firestore.
    Requires scripts/scrape_index_snapshots.py to have run at least once
    to populate the index_snapshots collection.

    Run with:
        scripts/venv/bin/python -m pytest utilities/tests/test_get_index_snapshot.py -v -m integration
    """

    @pytest.fixture(scope="class")
    def db(self):
        from google.cloud import firestore
        return firestore.Client()

    def test_aim_snapshot_returned(self, db):
        """AC: Returns a snapshot for AIM company."""
        result = get_index_snapshot("AIM", market_cap_gbp=50_000_000, db=db)
        print(f"\n[AIM snapshot] {result}")
        assert result["index_name"] == "AIM_ALL_SHARE"
        if result["data_found"]:
            assert result["index_value"] is not None
            assert result["week_52_high"] is not None
            assert result["week_52_low"] is not None

    def test_ftse_small_cap_snapshot_returned(self, db):
        """AC: Returns a snapshot for FTSE Small Cap company."""
        result = get_index_snapshot("MAIN_MARKET", market_cap_gbp=100_000_000, db=db)
        print(f"\n[FTSE Small Cap snapshot] {result}")
        assert result["index_name"] == "FTSE_SMALL_CAP"

    def test_ftse_250_snapshot_returned(self, db):
        """AC: Returns a snapshot for FTSE 250 company."""
        result = get_index_snapshot("MAIN_MARKET", market_cap_gbp=700_000_000, db=db)
        print(f"\n[FTSE 250 snapshot] {result}")
        assert result["index_name"] == "FTSE_250"

    def test_snapshot_age_days_calculated(self, db):
        """AC: snapshot_age_days correctly calculated."""
        result = get_index_snapshot("AIM", market_cap_gbp=50_000_000, db=db)
        if result["data_found"]:
            assert result["snapshot_age_days"] is not None
            assert result["snapshot_age_days"] >= 0
            print(
                f"\n[snapshot_age_days] {result['scrape_date']} → "
                f"age={result['snapshot_age_days']} days"
            )

    def test_graceful_when_no_data(self, db):
        """AC: Returns gracefully when no snapshot available (via empty-result path)."""
        # Use a non-existent index name directly via _empty_result
        result = _empty_result("NONEXISTENT_INDEX")
        assert result["data_found"] is False
        assert isinstance(result, dict)

    def test_all_required_keys_present(self, db):
        """AC: All required output keys present in result."""
        result = get_index_snapshot("AIM", market_cap_gbp=50_000_000, db=db)
        required = {
            "data_found", "index_name", "index_value", "week_52_high",
            "week_52_low", "change_1d_pct", "scrape_date", "snapshot_age_days",
        }
        assert required.issubset(set(result.keys()))
