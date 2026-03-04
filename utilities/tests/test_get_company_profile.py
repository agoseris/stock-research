"""
test_get_company_profile.py

Tests for the get_company_profile tool.

Unit tests use a mocked Firestore client — no credentials needed.
Integration tests require GOOGLE_APPLICATION_CREDENTIALS to be set and
are skipped automatically when credentials are absent.

Run unit tests only:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_company_profile.py -v -m "not integration"

Run all tests (requires Firestore credentials):
    scripts/venv/bin/python -m pytest utilities/tests/test_get_company_profile.py -v
"""

import sys
import os
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utilities.get_company_profile import get_company_profile, _build_profile, _parse_to_date

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIRESTORE_CREDENTIALS_PRESENT = bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))


def _make_db(doc_data: dict = None, exists: bool = True):
    """Return a mock Firestore client that returns the given document data."""
    mock_doc = MagicMock()
    mock_doc.exists = exists
    mock_doc.to_dict.return_value = doc_data or {}

    mock_col = MagicMock()
    mock_col.document.return_value.get.return_value = mock_doc

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_col
    return mock_db


def _make_universe_doc(
    ticker="QHE",
    company_name="Quilter plc",
    market_cap_gbp=250_000_000.0,
    listing_exchange="LSE_MAIN",
    last_refreshed="2026-03-01T10:00:00+00:00",
    universe_added_date="2026-01-15",
    sector=None,
):
    """Return a minimal universe_companies Firestore document dict."""
    return {
        "ticker_lse": ticker,
        "company_name": company_name,
        "market_cap_gbp": market_cap_gbp,
        "listing_exchange": listing_exchange,
        "last_refreshed": last_refreshed,
        "universe_added_date": universe_added_date,
        "sector": sector,
        "tier": 1,
        "entity_type": "OPERATING",
        "liquidity_flag": "MEDIUM",
        "not_of_interest": False,
    }


# ---------------------------------------------------------------------------
# _parse_to_date helper tests
# ---------------------------------------------------------------------------

class TestParseToDate:

    def test_none_returns_none(self):
        assert _parse_to_date(None) is None

    def test_iso_string(self):
        assert _parse_to_date("2026-03-01T10:00:00+00:00") == date(2026, 3, 1)

    def test_date_string_only(self):
        assert _parse_to_date("2026-01-15") == date(2026, 1, 15)

    def test_date_object(self):
        d = date(2025, 6, 1)
        assert _parse_to_date(d) == d

    def test_datetime_object(self):
        dt = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
        assert _parse_to_date(dt) == date(2025, 6, 1)

    def test_firestore_timestamp(self):
        # Firestore Timestamps are returned as datetime objects
        dt = datetime(2026, 2, 14, 8, 30, tzinfo=timezone.utc)
        assert _parse_to_date(dt) == date(2026, 2, 14)

    def test_invalid_string(self):
        assert _parse_to_date("not-a-date") is None


# ---------------------------------------------------------------------------
# _build_profile tests (no Firestore connection needed)
# ---------------------------------------------------------------------------

class TestBuildProfile:

    def test_standard_main_market(self):
        data = _make_universe_doc()
        profile = _build_profile("QHE", data)

        assert profile["data_found"] is True
        assert profile["ticker"] == "QHE"
        assert profile["company_name"] == "Quilter plc"
        assert profile["market_cap_gbp"] == 250_000_000.0
        assert profile["market_cap_date"] == date(2026, 3, 1)
        assert profile["index_membership"] == "MAIN_MARKET"
        assert profile["sector"] is None
        assert profile["universe_added_at"] == date(2026, 1, 15)

    def test_aim_company(self):
        data = _make_universe_doc(listing_exchange="AIM")
        profile = _build_profile("ACG", data)
        assert profile["index_membership"] == "AIM"

    def test_market_cap_fresh(self):
        # last_refreshed = today — should NOT be stale
        today_iso = datetime.now(timezone.utc).date().isoformat() + "T00:00:00+00:00"
        data = _make_universe_doc(last_refreshed=today_iso)
        profile = _build_profile("QHE", data)
        assert profile["market_cap_stale"] is False

    def test_market_cap_stale_91_days(self):
        # last_refreshed = 91 days ago — should be stale
        stale_date = datetime.now(timezone.utc).date() - timedelta(days=91)
        stale_iso = stale_date.isoformat() + "T00:00:00+00:00"
        data = _make_universe_doc(last_refreshed=stale_iso)
        profile = _build_profile("QHE", data)
        assert profile["market_cap_stale"] is True

    def test_market_cap_stale_exactly_90_days(self):
        # Exactly 90 days old — NOT stale (threshold is strictly >)
        boundary_date = datetime.now(timezone.utc).date() - timedelta(days=90)
        boundary_iso = boundary_date.isoformat() + "T00:00:00+00:00"
        data = _make_universe_doc(last_refreshed=boundary_iso)
        profile = _build_profile("QHE", data)
        assert profile["market_cap_stale"] is False

    def test_market_cap_stale_91_days_boundary(self):
        # 91 days old — stale
        stale_date = datetime.now(timezone.utc).date() - timedelta(days=91)
        stale_iso = stale_date.isoformat() + "T00:00:00+00:00"
        data = _make_universe_doc(last_refreshed=stale_iso)
        profile = _build_profile("QHE", data)
        assert profile["market_cap_stale"] is True

    def test_market_cap_stale_none_when_no_date(self):
        # No last_refreshed, no market_cap_gbp → stale is None
        data = _make_universe_doc(last_refreshed=None, market_cap_gbp=None)
        profile = _build_profile("QHE", data)
        assert profile["market_cap_stale"] is None

    def test_market_cap_stale_true_when_cap_but_no_date(self):
        # Has market cap value but no date → conservatively stale
        data = _make_universe_doc(last_refreshed=None, market_cap_gbp=100_000_000.0)
        profile = _build_profile("QHE", data)
        assert profile["market_cap_stale"] is True

    def test_sector_present(self):
        data = _make_universe_doc(sector="Financial Services")
        profile = _build_profile("QHE", data)
        assert profile["sector"] == "Financial Services"

    def test_sector_none(self):
        data = _make_universe_doc(sector=None)
        profile = _build_profile("QHE", data)
        assert profile["sector"] is None

    def test_unknown_listing_exchange_defaults_to_main_market(self):
        # Defensive: unknown exchange value → MAIN_MARKET
        data = _make_universe_doc(listing_exchange="UNKNOWN")
        profile = _build_profile("XYZ", data)
        assert profile["index_membership"] == "MAIN_MARKET"

    def test_last_refreshed_as_datetime_object(self):
        # Firestore may return datetime objects (Timestamp type)
        data = _make_universe_doc()
        data["last_refreshed"] = datetime(2025, 10, 1, tzinfo=timezone.utc)
        profile = _build_profile("QHE", data)
        assert profile["market_cap_date"] == date(2025, 10, 1)
        assert profile["market_cap_stale"] is True  # > 90 days before Mar 2026


# ---------------------------------------------------------------------------
# get_company_profile — mock Firestore integration
# ---------------------------------------------------------------------------

class TestGetCompanyProfile:

    def test_known_ticker_returns_data(self):
        db = _make_db(doc_data=_make_universe_doc(), exists=True)
        profile = get_company_profile("QHE", db=db)
        assert profile["data_found"] is True
        assert profile["company_name"] == "Quilter plc"

    def test_unknown_ticker_returns_not_found(self):
        db = _make_db(exists=False)
        profile = get_company_profile("INVALID_TICKER_XYZ", db=db)
        assert profile["data_found"] is False
        assert profile["ticker"] == "INVALID_TICKER_XYZ"
        assert profile["company_name"] is None
        assert profile["market_cap_gbp"] is None
        assert profile["market_cap_stale"] is None
        assert profile["index_membership"] is None

    def test_no_exception_on_unknown_ticker(self):
        db = _make_db(exists=False)
        # Must return a dict, never raise
        result = get_company_profile("ZZZNOPE", db=db)
        assert isinstance(result, dict)

    def test_firestore_exception_returns_gracefully(self):
        # Simulate Firestore being unreachable
        mock_db = MagicMock()
        mock_db.collection.side_effect = Exception("Firestore unavailable")
        result = get_company_profile("QHE", db=mock_db)
        assert result["data_found"] is False
        assert "error" in result

    def test_aim_index_membership(self):
        db = _make_db(doc_data=_make_universe_doc(listing_exchange="AIM"), exists=True)
        profile = get_company_profile("ACG", db=db)
        assert profile["index_membership"] == "AIM"

    def test_main_market_index_membership(self):
        db = _make_db(doc_data=_make_universe_doc(listing_exchange="LSE_MAIN"), exists=True)
        profile = get_company_profile("QHE", db=db)
        assert profile["index_membership"] == "MAIN_MARKET"

    def test_stale_market_cap(self):
        stale_date = (datetime.now(timezone.utc).date() - timedelta(days=120)).isoformat()
        data = _make_universe_doc(last_refreshed=stale_date + "T00:00:00+00:00")
        db = _make_db(doc_data=data, exists=True)
        profile = get_company_profile("QHE", db=db)
        assert profile["market_cap_stale"] is True

    def test_fresh_market_cap(self):
        fresh_date = datetime.now(timezone.utc).date().isoformat()
        data = _make_universe_doc(last_refreshed=fresh_date + "T00:00:00+00:00")
        db = _make_db(doc_data=data, exists=True)
        profile = get_company_profile("QHE", db=db)
        assert profile["market_cap_stale"] is False

    def test_all_required_keys_present(self):
        db = _make_db(doc_data=_make_universe_doc(), exists=True)
        profile = get_company_profile("QHE", db=db)
        required = {
            "data_found", "ticker", "company_name", "market_cap_gbp",
            "market_cap_date", "market_cap_stale", "index_membership",
            "sector", "universe_added_at",
        }
        assert required.issubset(set(profile.keys()))


# ---------------------------------------------------------------------------
# Integration tests — require live Firestore credentials
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    not FIRESTORE_CREDENTIALS_PRESENT,
    reason="GOOGLE_APPLICATION_CREDENTIALS not set — skipping Firestore integration tests",
)
class TestGetCompanyProfileIntegration:
    """
    Acceptance criteria checks against live Firestore.
    Tickers used: QHE, STAR, AVG (all known universe members).
    Results are printed for human verification.
    """

    @pytest.fixture(scope="class")
    def db(self):
        from google.cloud import firestore
        return firestore.Client()

    def test_three_known_tickers_return_data(self, db):
        """AC: Returns correct data for at least 3 known universe tickers."""
        tickers = ["QHE", "STAR", "AVG"]
        for ticker in tickers:
            profile = get_company_profile(ticker, db=db)
            print(f"\n[{ticker}] {profile}")
            assert profile["data_found"] is True, (
                f"{ticker} not found in universe — check ticker is admitted"
            )
            assert profile["company_name"] is not None
            assert profile["ticker"] == ticker

    def test_index_membership_values(self, db):
        """AC: index_membership returned as 'AIM' or 'MAIN_MARKET'."""
        for ticker in ["QHE", "STAR", "AVG"]:
            profile = get_company_profile(ticker, db=db)
            if profile["data_found"]:
                assert profile["index_membership"] in ("AIM", "MAIN_MARKET"), (
                    f"{ticker}: unexpected index_membership={profile['index_membership']!r}"
                )

    def test_market_cap_stale_false_for_recent_data(self, db):
        """
        AC: market_cap_stale correctly set to false for recently updated ticker.
        All tickers should be fresh — backfill ran March 2026.
        """
        for ticker in ["QHE", "STAR", "AVG"]:
            profile = get_company_profile(ticker, db=db)
            if profile["data_found"] and profile["market_cap_date"] is not None:
                print(
                    f"\n[{ticker}] market_cap_date={profile['market_cap_date']}, "
                    f"stale={profile['market_cap_stale']}"
                )
                # Backfill was March 2026 — should be fresh
                assert profile["market_cap_stale"] is False, (
                    f"{ticker}: expected fresh but market_cap_date={profile['market_cap_date']}"
                )

    def test_unknown_ticker_graceful(self, db):
        """AC: Returns graceful result (not exception) for unknown ticker."""
        profile = get_company_profile("INVALID_TICKER_XYZ_9999", db=db)
        assert isinstance(profile, dict)
        assert profile["data_found"] is False
        assert profile["company_name"] is None

    def test_market_cap_stale_logic_with_synthetic_stale_date(self):
        """
        AC: market_cap_stale correctly set to true for stale data.
        Uses _build_profile directly with a synthetic stale date — no Firestore needed.
        Included here so it runs alongside the integration suite.
        """
        stale_date = datetime.now(timezone.utc).date() - timedelta(days=180)
        data = _make_universe_doc(
            last_refreshed=stale_date.isoformat() + "T00:00:00+00:00",
        )
        profile = _build_profile("QHE", data)
        assert profile["market_cap_stale"] is True
        print(f"\n[stale check] market_cap_date={profile['market_cap_date']}, stale=True ✓")
