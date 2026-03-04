"""
test_get_company_ch_filings.py

Tests for the get_company_ch_filings tool.

Unit tests use a mocked Firestore client — no credentials needed.
Integration tests require GOOGLE_APPLICATION_CREDENTIALS to be set and
are skipped automatically when credentials are absent.

Run unit tests only:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_company_ch_filings.py -v -m "not integration"

Run all tests (requires Firestore credentials):
    scripts/venv/bin/python -m pytest utilities/tests/test_get_company_ch_filings.py -v
"""

import sys
import os
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utilities.get_company_ch_filings import (
    get_company_ch_filings,
    _classify_change,
    _extract_director_name,
    _extract_director_changes,
    _parse_published_at,
    _classify_maturity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIRESTORE_CREDENTIALS_PRESENT = bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))


def _make_db(docs: list):
    """
    Return a mock Firestore client that yields docs from .stream().

    Mocks the chain:
        db.collection("announcements")
          .where("ticker", "==", ticker)
          .where("source_name", "==", "Companies House")
          .stream()
    """
    mock_docs = []
    for d in docs:
        mock_doc = MagicMock()
        mock_doc.to_dict.return_value = d
        mock_docs.append(mock_doc)

    # .where() returns itself so chaining works; .stream() yields docs
    mock_query = MagicMock()
    mock_query.where.return_value = mock_query
    mock_query.stream.return_value = iter(mock_docs)

    mock_col = MagicMock()
    mock_col.where.return_value = mock_query

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_col
    return mock_db


def _make_db_exception():
    """Return a mock Firestore client that raises on .collection()."""
    mock_db = MagicMock()
    mock_db.collection.side_effect = Exception("Firestore unavailable")
    return mock_db


def _filing(headline: str, published_at, source_url: str = "https://find.companieshouse.gov.uk/test") -> dict:
    """Create a raw Firestore announcement document dict."""
    return {
        "headline": headline,
        "published_at": published_at,
        "source_url": source_url,
        "ticker": "TEST",
        "source_name": "Companies House",
    }


def _days_ago(n: int) -> str:
    """
    Return a datetime string n days ago in save_announcement() format:
    str(datetime_obj) → "2024-01-15 10:30:45.123456+00:00"
    """
    dt = datetime.now(timezone.utc) - timedelta(days=n)
    return str(dt)


# ---------------------------------------------------------------------------
# _classify_change — unit tests
# ---------------------------------------------------------------------------

class TestClassifyChange:

    # --- Appointment patterns ---

    def test_ch_internal_appoint_person(self):
        h = "Director Appointed: appoint-person#name=SMITH+JOHN&officer_role=director"
        assert _classify_change(h) == "appointed"

    def test_natural_appointment_of_director(self):
        h = "Director Appointed: Appointment of director"
        assert _classify_change(h) == "appointed"

    def test_natural_appointment_of_named_person(self):
        h = "Director Appointed: Appointment of John Smith as a director"
        assert _classify_change(h) == "appointed"

    def test_director_appointed_phrase(self):
        h = "Director appointed to the board"
        assert _classify_change(h) == "appointed"

    # --- Resignation patterns ---

    def test_ch_internal_terminate_person(self):
        h = "Director Resignation: terminate-person#name=SMITH+JOHN&officer_role=director"
        assert _classify_change(h) == "resigned"

    def test_termination_of_appointment(self):
        h = "Termination of appointment of Jane Doe as a director"
        assert _classify_change(h) == "resigned"

    def test_director_resigned_phrase(self):
        h = "Director resigned from the board"
        assert _classify_change(h) == "resigned"

    def test_director_resignation_phrase(self):
        h = "Director resignation announced"
        assert _classify_change(h) == "resigned"

    def test_case_insensitive_resignation(self):
        h = "DIRECTOR RESIGNED"
        assert _classify_change(h) == "resigned"

    def test_case_insensitive_appointment(self):
        h = "DIRECTOR APPOINTED"
        assert _classify_change(h) == "appointed"

    # --- Resignation takes priority (checked first) ---

    def test_termination_of_appointment_wins_over_appointment_of(self):
        h = "Termination of appointment of director"
        assert _classify_change(h) == "resigned"

    # --- No match ---

    def test_confirmation_statement_no_match(self):
        assert _classify_change("Confirmation Statement") is None

    def test_annual_report_no_match(self):
        assert _classify_change("Annual Report and Accounts") is None

    def test_empty_headline_no_match(self):
        assert _classify_change("") is None

    def test_accounts_no_match(self):
        assert _classify_change("Accounts made up to 31 December 2024") is None

    def test_mortgage_no_match(self):
        assert _classify_change("Mortgage/charge") is None


# ---------------------------------------------------------------------------
# _extract_director_name — unit tests
# ---------------------------------------------------------------------------

class TestExtractDirectorName:

    def test_ch_internal_two_tokens(self):
        # SMITH+JOHN → "SMITH, JOHN" for normaliser
        h = "Director Appointed: appoint-person#name=SMITH+JOHN"
        raw = _extract_director_name(h)
        assert raw is not None
        assert "SMITH" in raw
        assert "JOHN" in raw

    def test_ch_internal_three_tokens(self):
        # JONES+MARY+ANNE → surname JONES, forenames MARY ANNE
        h = "Director Appointed: appoint-person#name=JONES+MARY+ANNE"
        raw = _extract_director_name(h)
        assert raw is not None
        assert "JONES" in raw

    def test_ch_internal_with_extra_params(self):
        # Full CH URL format includes extra parameters after &
        h = "Director Appointed: appoint-person#name=BROWN+ALICE&officer_role=director&action=AP"
        raw = _extract_director_name(h)
        assert raw is not None
        assert "BROWN" in raw

    def test_natural_appointment_of_pattern(self):
        h = "Director Appointed: Appointment of John Smith as a director"
        raw = _extract_director_name(h)
        assert raw == "John Smith"

    def test_natural_termination_pattern(self):
        h = "Director Resignation: Termination of appointment of Jane Doe as a director"
        raw = _extract_director_name(h)
        assert raw == "Jane Doe"

    def test_natural_double_barrelled(self):
        h = "Director Appointed: Appointment of Sarah Jones-Williams as a director"
        raw = _extract_director_name(h)
        assert raw == "Sarah Jones-Williams"

    def test_no_name_extractable(self):
        h = "Confirmation Statement"
        assert _extract_director_name(h) is None

    def test_headline_without_colon_separator(self):
        # Description is the whole headline when no ": " present
        h = "appoint-person#name=GREEN+BOB"
        raw = _extract_director_name(h)
        assert raw is not None
        assert "GREEN" in raw


# ---------------------------------------------------------------------------
# _parse_published_at — unit tests
# ---------------------------------------------------------------------------

class TestParsePublishedAt:

    def test_none_returns_none(self):
        assert _parse_published_at(None) is None

    def test_space_format_from_save_announcement(self):
        # Format from str(datetime_obj): "2024-01-15 00:00:00+00:00"
        result = _parse_published_at("2024-01-15 00:00:00+00:00")
        assert isinstance(result, datetime)
        assert result.date() == date(2024, 1, 15)
        assert result.tzinfo is not None

    def test_space_format_with_microseconds(self):
        # str(datetime.now(utc)) includes microseconds
        result = _parse_published_at("2024-01-15 10:30:45.123456+00:00")
        assert result.date() == date(2024, 1, 15)

    def test_iso_t_format(self):
        result = _parse_published_at("2024-01-15T00:00:00+00:00")
        assert result.date() == date(2024, 1, 15)

    def test_date_only_string(self):
        result = _parse_published_at("2024-01-15")
        assert result.date() == date(2024, 1, 15)
        assert result.tzinfo is not None

    def test_datetime_object_with_tz(self):
        dt = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)
        result = _parse_published_at(dt)
        assert result == dt

    def test_datetime_object_naive_gets_utc(self):
        dt = datetime(2024, 6, 1, 10, 0)
        result = _parse_published_at(dt)
        assert result.tzinfo is not None
        assert result.date() == date(2024, 6, 1)

    def test_firestore_timestamp_as_datetime(self):
        # Firestore Timestamps are returned as datetime objects
        dt = datetime(2025, 3, 15, 8, 30, tzinfo=timezone.utc)
        result = _parse_published_at(dt)
        assert result == dt

    def test_empty_string_returns_none(self):
        assert _parse_published_at("") is None

    def test_invalid_string_returns_none(self):
        assert _parse_published_at("not-a-date") is None


# ---------------------------------------------------------------------------
# _classify_maturity — unit tests
# ---------------------------------------------------------------------------

class TestClassifyMaturity:

    def test_zero_is_empty(self):
        assert _classify_maturity(0) == "empty"

    def test_one_is_sparse(self):
        assert _classify_maturity(1) == "sparse"

    def test_two_is_sparse(self):
        assert _classify_maturity(2) == "sparse"

    def test_three_is_sufficient(self):
        assert _classify_maturity(3) == "sufficient"

    def test_large_count_is_sufficient(self):
        assert _classify_maturity(50) == "sufficient"


# ---------------------------------------------------------------------------
# _extract_director_changes — unit tests
# ---------------------------------------------------------------------------

class TestExtractDirectorChanges:

    def test_extracts_appointment(self):
        filings = [{
            "headline": "Director Appointed: Appointment of John Smith as a director",
            "published_at": datetime(2025, 1, 10, tzinfo=timezone.utc),
            "source_url": "https://example.com",
        }]
        changes = _extract_director_changes(filings)
        assert len(changes) == 1
        assert changes[0]["change_type"] == "appointed"
        assert changes[0]["date"] == date(2025, 1, 10)

    def test_extracts_resignation(self):
        filings = [{
            "headline": "Director Resignation: Termination of appointment of Jane Doe as a director",
            "published_at": datetime(2025, 2, 20, tzinfo=timezone.utc),
            "source_url": "https://example.com",
        }]
        changes = _extract_director_changes(filings)
        assert len(changes) == 1
        assert changes[0]["change_type"] == "resigned"

    def test_skips_non_director_filings(self):
        filings = [
            {
                "headline": "Confirmation Statement",
                "published_at": datetime(2025, 1, 10, tzinfo=timezone.utc),
                "source_url": "https://example.com",
            },
            {
                "headline": "Director Appointed: Appointment of Bob Green as a director",
                "published_at": datetime(2025, 1, 15, tzinfo=timezone.utc),
                "source_url": "https://example.com",
            },
        ]
        changes = _extract_director_changes(filings)
        assert len(changes) == 1
        assert changes[0]["change_type"] == "appointed"

    def test_normalises_name(self):
        filings = [{
            "headline": "Director Appointed: Appointment of JOHN SMITH as a director",
            "published_at": datetime(2025, 1, 10, tzinfo=timezone.utc),
            "source_url": "https://example.com",
        }]
        changes = _extract_director_changes(filings)
        assert changes[0]["director_name"] == "john smith"

    def test_date_none_when_published_at_none(self):
        filings = [{
            "headline": "Director Appointed: Appointment of Bob Green as a director",
            "published_at": None,
            "source_url": "https://example.com",
        }]
        changes = _extract_director_changes(filings)
        assert changes[0]["date"] is None

    def test_director_name_none_when_not_extractable(self):
        # Headline classifies as appointment but contains no extractable name
        filings = [{
            "headline": "Director appointed",
            "published_at": datetime(2025, 1, 10, tzinfo=timezone.utc),
            "source_url": "https://example.com",
        }]
        changes = _extract_director_changes(filings)
        assert len(changes) == 1
        assert changes[0]["director_name"] is None

    def test_multiple_changes_extracted(self):
        filings = [
            {
                "headline": "Director Appointed: Appointment of Alice Blue as a director",
                "published_at": datetime(2025, 3, 1, tzinfo=timezone.utc),
                "source_url": "https://example.com",
            },
            {
                "headline": "Director Resigned: terminate-person#name=GREEN+BOB&officer_role=director",
                "published_at": datetime(2025, 2, 1, tzinfo=timezone.utc),
                "source_url": "https://example.com",
            },
        ]
        changes = _extract_director_changes(filings)
        assert len(changes) == 2
        types = {c["change_type"] for c in changes}
        assert types == {"appointed", "resigned"}


# ---------------------------------------------------------------------------
# board_stability — unit tests (via get_company_ch_filings)
# ---------------------------------------------------------------------------

class TestBoardStability:

    def test_stable_when_no_director_changes(self):
        docs = [
            _filing("Confirmation Statement", _days_ago(10)),
            _filing("Annual Report", _days_ago(50)),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["board_stability"] == "stable"

    def test_stable_when_change_older_than_90_days(self):
        docs = [
            _filing(
                "Director Appointed: Appointment of John Smith as a director",
                _days_ago(120),
            ),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["board_stability"] == "stable"

    def test_active_change_appointment_within_90_days(self):
        docs = [
            _filing(
                "Director Appointed: Appointment of John Smith as a director",
                _days_ago(45),
            ),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["board_stability"] == "active_change"

    def test_active_change_resignation_within_90_days(self):
        docs = [
            _filing(
                "Director Resignation: Termination of appointment of Jane Doe as a director",
                _days_ago(30),
            ),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["board_stability"] == "active_change"

    def test_stable_when_no_filings(self):
        result = get_company_ch_filings("NOPE", db=_make_db([]))
        assert result["board_stability"] == "stable"

    def test_active_change_89_days_ago(self):
        # 89 days ago is within the 90-day window
        docs = [
            _filing(
                "Director Appointed: Appointment of Alice Brown as a director",
                _days_ago(89),
            ),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["board_stability"] == "active_change"

    def test_stable_91_days_ago(self):
        # 91 days ago is outside the 90-day window
        docs = [
            _filing(
                "Director Appointed: Appointment of Alice Brown as a director",
                _days_ago(91),
            ),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["board_stability"] == "stable"

    def test_active_change_ch_internal_format(self):
        # board_stability works for CH internal format too
        docs = [
            _filing(
                "Director Appointed: appoint-person#name=SMITH+JOHN&officer_role=director",
                _days_ago(10),
            ),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["board_stability"] == "active_change"


# ---------------------------------------------------------------------------
# days_lookback filtering — unit tests
# ---------------------------------------------------------------------------

class TestDaysLookback:

    def test_filing_within_window_returned(self):
        docs = [_filing("Confirmation Statement", _days_ago(30))]
        result = get_company_ch_filings("TEST", days_lookback=90, db=_make_db(docs))
        assert result["filing_count"] == 1

    def test_filing_outside_window_excluded(self):
        docs = [_filing("Confirmation Statement", _days_ago(200))]
        result = get_company_ch_filings("TEST", days_lookback=90, db=_make_db(docs))
        assert result["filing_count"] == 0

    def test_mixed_window_filters_correctly(self):
        docs = [
            _filing("Filing inside 1", _days_ago(30)),
            _filing("Filing outside", _days_ago(200)),
            _filing("Filing inside 2", _days_ago(60)),
        ]
        result = get_company_ch_filings("TEST", days_lookback=90, db=_make_db(docs))
        assert result["filing_count"] == 2

    def test_default_365_includes_year_old_filing(self):
        docs = [_filing("Old Filing", _days_ago(360))]
        result = get_company_ch_filings("TEST", db=_make_db(docs))  # default 365
        assert result["filing_count"] == 1

    def test_lookback_30_excludes_45_day_filing(self):
        docs = [_filing("Filing", _days_ago(45))]
        result = get_company_ch_filings("TEST", days_lookback=30, db=_make_db(docs))
        assert result["filing_count"] == 0

    def test_none_published_at_is_included(self):
        # Filings with unparseable published_at pass through (not excluded)
        docs = [{"headline": "Filing", "published_at": None, "source_url": ""}]
        result = get_company_ch_filings("TEST", days_lookback=90, db=_make_db(docs))
        assert result["filing_count"] == 1


# ---------------------------------------------------------------------------
# get_company_ch_filings — mock Firestore integration
# ---------------------------------------------------------------------------

class TestGetCompanyChFilings:

    def test_data_found_true_when_filings_present(self):
        docs = [_filing("Confirmation Statement", _days_ago(10))]
        result = get_company_ch_filings("QHE", db=_make_db(docs))
        assert result["data_found"] is True
        assert result["ticker"] == "QHE"

    def test_data_found_false_when_no_filings(self):
        result = get_company_ch_filings("INVALID_TICKER_XYZ", db=_make_db([]))
        assert result["data_found"] is False
        assert result["ticker"] == "INVALID_TICKER_XYZ"

    def test_filing_count_matches_doc_count(self):
        docs = [
            _filing("Filing A", _days_ago(10)),
            _filing("Filing B", _days_ago(20)),
            _filing("Filing C", _days_ago(30)),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["filing_count"] == 3

    def test_recent_filings_sorted_most_recent_first(self):
        docs = [
            _filing("Old Filing", _days_ago(50)),
            _filing("Recent Filing", _days_ago(5)),
            _filing("Mid Filing", _days_ago(25)),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        headlines = [f["headline"] for f in result["recent_filings"]]
        assert headlines[0] == "Recent Filing"
        assert headlines[-1] == "Old Filing"

    def test_data_maturity_empty(self):
        result = get_company_ch_filings("NONE", db=_make_db([]))
        assert result["data_maturity"] == "empty"

    def test_data_maturity_sparse_one_filing(self):
        docs = [_filing("Filing A", _days_ago(10))]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["data_maturity"] == "sparse"

    def test_data_maturity_sparse_two_filings(self):
        docs = [_filing(f"Filing {i}", _days_ago(i + 1)) for i in range(2)]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["data_maturity"] == "sparse"

    def test_data_maturity_sufficient_three_plus(self):
        docs = [_filing(f"Filing {i}", _days_ago(i + 1)) for i in range(5)]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["data_maturity"] == "sufficient"

    def test_director_changes_extracted_from_natural_language(self):
        docs = [
            _filing(
                "Director Appointed: Appointment of John Smith as a director",
                _days_ago(10),
            ),
            _filing(
                "Director Resignation: Termination of appointment of Jane Doe as a director",
                _days_ago(20),
            ),
            _filing("Confirmation Statement", _days_ago(30)),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert len(result["director_changes"]) == 2
        types = {c["change_type"] for c in result["director_changes"]}
        assert types == {"appointed", "resigned"}

    def test_director_changes_extracted_from_ch_internal_format(self):
        docs = [
            _filing(
                "Director Appointed: appoint-person#name=JONES+SARAH&officer_role=director",
                _days_ago(10),
            ),
            _filing(
                "Director Resigned: terminate-person#name=BROWN+THOMAS&officer_role=director",
                _days_ago(20),
            ),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert len(result["director_changes"]) == 2
        types = {c["change_type"] for c in result["director_changes"]}
        assert types == {"appointed", "resigned"}

    def test_director_name_normalised_in_output(self):
        docs = [
            _filing(
                "Director Appointed: Appointment of JOHN SMITH as a director",
                _days_ago(10),
            ),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        assert result["director_changes"][0]["director_name"] == "john smith"

    def test_firestore_exception_returns_gracefully(self):
        result = get_company_ch_filings("QHE", db=_make_db_exception())
        assert result["data_found"] is False
        assert "error" in result
        assert result["filing_count"] == 0

    def test_all_required_keys_present(self):
        docs = [_filing("Confirmation Statement", _days_ago(10))]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        required = {
            "data_found", "ticker", "recent_filings", "director_changes",
            "filing_count", "data_maturity", "board_stability",
        }
        assert required.issubset(set(result.keys()))

    def test_recent_filings_structure(self):
        docs = [_filing("Confirmation Statement", _days_ago(10), "https://ch.example.com/1")]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        f = result["recent_filings"][0]
        assert "headline" in f
        assert "published_at" in f
        assert "source_url" in f

    def test_director_change_structure(self):
        docs = [
            _filing(
                "Director Appointed: Appointment of Bob Brown as a director",
                _days_ago(10),
            ),
        ]
        result = get_company_ch_filings("TEST", db=_make_db(docs))
        c = result["director_changes"][0]
        assert "director_name" in c
        assert "change_type" in c
        assert "date" in c

    def test_no_exception_for_unknown_ticker(self):
        result = get_company_ch_filings("ZZZNOPE", db=_make_db([]))
        assert isinstance(result, dict)

    def test_empty_result_keys_complete(self):
        result = get_company_ch_filings("NONE", db=_make_db([]))
        assert result["data_found"] is False
        assert result["recent_filings"] == []
        assert result["director_changes"] == []
        assert result["filing_count"] == 0
        assert result["data_maturity"] == "empty"
        assert result["board_stability"] == "stable"


# ---------------------------------------------------------------------------
# Integration tests — require live Firestore credentials
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    not FIRESTORE_CREDENTIALS_PRESENT,
    reason="GOOGLE_APPLICATION_CREDENTIALS not set — skipping Firestore integration tests",
)
class TestGetCompanyChFilingsIntegration:
    """
    Acceptance criteria checks against live Firestore.
    Tickers: REE, VCT, IDOX — confirmed to have CH filings in Firestore.
    (QHE/STAR/AVG have no CH data in the last 365 days.)
    """

    # Tickers confirmed to have CH announcements in Firestore (verified March 2026)
    _TEST_TICKERS = ["REE", "VCT", "IDOX"]

    @pytest.fixture(scope="class")
    def db(self):
        from google.cloud import firestore
        return firestore.Client()

    def test_two_known_tickers_return_data(self, db):
        """AC: Returns filings for at least 2 known tickers with CH data in Firestore."""
        tickers_with_data = []
        for ticker in self._TEST_TICKERS:
            result = get_company_ch_filings(ticker, days_lookback=365, db=db)
            print(
                f"\n[{ticker}] filing_count={result['filing_count']}, "
                f"maturity={result['data_maturity']}, "
                f"stability={result['board_stability']}"
            )
            if result["data_found"]:
                tickers_with_data.append(ticker)
        assert len(tickers_with_data) >= 2, (
            f"Expected data for at least 2 tickers; got data for: {tickers_with_data}"
        )

    def test_director_changes_extraction(self, db):
        """AC: director_changes correctly extracted with valid change_type values."""
        for ticker in self._TEST_TICKERS:
            result = get_company_ch_filings(ticker, days_lookback=365, db=db)
            if result["director_changes"]:
                print(f"\n[{ticker}] director_changes ({len(result['director_changes'])}):")
                for c in result["director_changes"]:
                    print(
                        f"  {c['change_type']:10s}  "
                        f"{c['director_name'] or '(no name)':30s}  "
                        f"{c['date']}"
                    )
                    assert c["change_type"] in ("appointed", "resigned"), (
                        f"{ticker}: unexpected change_type={c['change_type']!r}"
                    )

    def test_board_stability_values(self, db):
        """AC: board_stability is 'stable' or 'active_change'."""
        for ticker in self._TEST_TICKERS:
            result = get_company_ch_filings(ticker, days_lookback=365, db=db)
            if result["data_found"]:
                assert result["board_stability"] in ("stable", "active_change"), (
                    f"{ticker}: unexpected board_stability={result['board_stability']!r}"
                )
                print(f"\n[{ticker}] board_stability={result['board_stability']}")

    def test_unknown_ticker_graceful(self, db):
        """AC: Returns data_found=False gracefully for ticker with no CH data."""
        result = get_company_ch_filings("INVALID_TICKER_XYZ_9999", days_lookback=365, db=db)
        assert isinstance(result, dict)
        assert result["data_found"] is False
        assert result["ticker"] == "INVALID_TICKER_XYZ_9999"
        assert result["filing_count"] == 0
        assert result["data_maturity"] == "empty"

    def test_days_lookback_reduces_count(self, db):
        """AC: days_lookback filter working — results outside window not returned."""
        found = False
        for ticker in self._TEST_TICKERS:
            result_365 = get_company_ch_filings(ticker, days_lookback=365, db=db)
            if result_365["filing_count"] > 0:
                result_30 = get_company_ch_filings(ticker, days_lookback=30, db=db)
                assert result_30["filing_count"] <= result_365["filing_count"], (
                    f"{ticker}: 30-day count ({result_30['filing_count']}) > "
                    f"365-day count ({result_365['filing_count']})"
                )
                print(
                    f"\n[{ticker}] 365d={result_365['filing_count']}, "
                    f"30d={result_30['filing_count']}"
                )
                found = True
                break
        assert found, "Could not find any ticker with filings to test lookback filter"

    def test_recent_filings_structure(self, db):
        """AC: recent_filings items have required keys."""
        for ticker in self._TEST_TICKERS:
            result = get_company_ch_filings(ticker, days_lookback=365, db=db)
            if result["recent_filings"]:
                f = result["recent_filings"][0]
                assert "headline" in f
                assert "published_at" in f
                assert "source_url" in f
                print(f"\n[{ticker}] most recent: {f['headline'][:80]}")
                break
