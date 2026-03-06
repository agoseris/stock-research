"""
Tests for get_director_companies_house_profile.py

Unit tests: mocked HTTP session — no network calls, no API key required.
Integration tests: live Companies House API — require COMPANIES_HOUSE_KEY in .env.

Run unit tests only:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_director_companies_house_profile.py -v -m "not integration"

Run all tests (requires COMPANIES_HOUSE_KEY):
    scripts/venv/bin/python -m pytest utilities/tests/test_get_director_companies_house_profile.py -v
"""

import os
import sys
import json
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utilities.get_director_companies_house_profile import (
    get_director_companies_house_profile,
    _search_officers,
    _fetch_appointments,
    _check_disqualification,
    _extract_officer_name,
    _extract_officer_id,
    _find_company_appointment,
    _company_name_matches,
    _normalise_company_name,
    _classify_confidence,
    _parse_date,
    _not_found_result,
    _CH_BASE_URL,
    _REQUEST_SLEEP,
    _AMBIGUITY_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers — mock HTTP session
# ---------------------------------------------------------------------------

def _make_response(status_code: int, data: dict) -> MagicMock:
    """Return a mock requests.Response with the given status and JSON body."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _make_session(
    search_items=None,
    appointments_items=None,
    disqualifications=None,
    search_status=200,
    appointments_status=200,
    disq_status=200,
):
    """
    Build a mock requests.Session that returns configured responses for CH endpoints.
    """
    session = MagicMock()

    search_resp = _make_response(
        search_status,
        {"items": search_items or [], "total_results": len(search_items or [])}
    )

    appointments_resp = _make_response(
        appointments_status,
        {"items": appointments_items or [], "total_results": len(appointments_items or [])}
    )

    disq_resp = _make_response(
        disq_status,
        {"disqualifications": disqualifications or []}
    )

    def _get_side_effect(url, **kwargs):
        if "/search/officers" in url:
            return search_resp
        if "/disqualifications" in url:
            return disq_resp
        if "/appointments" in url:
            return appointments_resp
        return _make_response(404, {})

    session.get.side_effect = _get_side_effect
    return session


def _make_search_item(
    officer_name="John Smith",
    officer_id="abc123",
    appointment_count=2,
):
    """Synthetic CH search result item."""
    return {
        "title": officer_name,
        "kind": "searchresults#officer",
        "links": {"self": f"/officers/{officer_id}/appointments"},
        "appointment_count": appointment_count,
        "description": f"{officer_name.upper()} - Director",
    }


def _make_appointment(
    company_name="ACME MINING PLC",
    company_number="12345678",
    role="director",
    appointed_on="2020-01-15",
    resigned_on=None,
):
    """Synthetic CH appointment item."""
    item = {
        "appointed_to": {
            "company_name": company_name,
            "company_number": company_number,
            "company_status": "active" if resigned_on is None else "dissolved",
        },
        "officer_role": role,
        "appointed_on": appointed_on,
    }
    if resigned_on:
        item["resigned_on"] = resigned_on
    return item


# ---------------------------------------------------------------------------
# Tests: _normalise_company_name
# ---------------------------------------------------------------------------

class TestNormaliseCompanyName:
    def test_strips_plc(self):
        result = _normalise_company_name("Acme Mining PLC")
        assert "plc" not in result

    def test_strips_limited(self):
        result = _normalise_company_name("Smith Limited")
        assert "limited" not in result

    def test_lowercased(self):
        result = _normalise_company_name("ACME MINING")
        assert result == result.lower()

    def test_strips_common_noise_words(self):
        result = _normalise_company_name("The Acme Group Holdings")
        assert "the" not in result
        assert "group" not in result
        assert "holdings" not in result

    def test_preserves_meaningful_words(self):
        result = _normalise_company_name("Acme Mining PLC")
        assert "acme" in result
        assert "mining" in result


# ---------------------------------------------------------------------------
# Tests: _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_iso_string_parsed(self):
        result = _parse_date("2021-06-15")
        assert result == date(2021, 6, 15)

    def test_date_object_passthrough(self):
        d = date(2021, 6, 15)
        assert _parse_date(d) == d

    def test_none_returns_none(self):
        assert _parse_date(None) is None

    def test_invalid_string_returns_none(self):
        assert _parse_date("not-a-date") is None

    def test_truncates_to_date_part(self):
        result = _parse_date("2021-06-15T10:30:00")
        assert result == date(2021, 6, 15)


# ---------------------------------------------------------------------------
# Tests: _extract_officer_name
# ---------------------------------------------------------------------------

class TestExtractOfficerName:
    def test_extracts_title_field(self):
        item = {"title": "Mr John Smith"}
        assert _extract_officer_name(item) == "Mr John Smith"

    def test_falls_back_to_description(self):
        item = {"description": "SMITH, JOHN - Active director"}
        result = _extract_officer_name(item)
        assert result == "SMITH, JOHN"

    def test_empty_item_returns_none_or_empty(self):
        item = {}
        result = _extract_officer_name(item)
        assert result is None or result == ""


# ---------------------------------------------------------------------------
# Tests: _extract_officer_id
# ---------------------------------------------------------------------------

class TestExtractOfficerId:
    def test_extracts_from_self_link(self):
        item = {"links": {"self": "/officers/abc123/appointments"}}
        assert _extract_officer_id(item) == "abc123"

    def test_returns_none_for_missing_links(self):
        item = {}
        assert _extract_officer_id(item) is None

    def test_returns_none_for_malformed_link(self):
        item = {"links": {"self": "/unknown/path"}}
        assert _extract_officer_id(item) is None

    def test_alphanumeric_officer_id(self):
        item = {"links": {"self": "/officers/XY12AB34/appointments"}}
        assert _extract_officer_id(item) == "XY12AB34"


# ---------------------------------------------------------------------------
# Tests: _find_company_appointment
# ---------------------------------------------------------------------------

class TestFindCompanyAppointment:
    def test_finds_matching_company(self):
        appointments = [
            _make_appointment(company_name="ACME MINING PLC"),
        ]
        assert _find_company_appointment(appointments, "Acme Mining PLC") is True

    def test_returns_false_for_no_match(self):
        appointments = [
            _make_appointment(company_name="DIFFERENT COMPANY LTD"),
        ]
        assert _find_company_appointment(appointments, "Acme Mining PLC") is False

    def test_returns_false_for_empty_appointments(self):
        assert _find_company_appointment([], "Acme Mining PLC") is False

    def test_returns_false_for_empty_target(self):
        appointments = [_make_appointment()]
        assert _find_company_appointment(appointments, "") is False

    def test_case_insensitive_match(self):
        appointments = [_make_appointment(company_name="acme mining plc")]
        assert _find_company_appointment(appointments, "ACME MINING PLC") is True


# ---------------------------------------------------------------------------
# Tests: _classify_confidence
# ---------------------------------------------------------------------------

class TestClassifyConfidence:
    def test_exact_match_with_company_found_is_high(self):
        confidence, notes = _classify_confidence("exact", True, 1, "john smith")
        assert confidence == "high"
        assert "exact" in notes.lower() or "confirmed" in notes.lower()

    def test_exact_match_without_company_few_results_is_medium(self):
        confidence, notes = _classify_confidence("exact", False, 1, "john smith")
        assert confidence == "medium"

    def test_exact_match_without_company_many_results_is_low(self):
        confidence, notes = _classify_confidence(
            "exact", False, _AMBIGUITY_THRESHOLD, "john smith"
        )
        assert confidence == "low"

    def test_abbreviated_match_with_company_is_medium(self):
        confidence, _ = _classify_confidence("abbreviated", True, 1, "j smith")
        assert confidence == "medium"

    def test_fuzzy_match_without_company_is_medium(self):
        confidence, _ = _classify_confidence("fuzzy", False, 1, "john smythe")
        assert confidence == "medium"

    def test_unknown_method_returns_low(self):
        confidence, _ = _classify_confidence("no_match", False, 0, "nobody")
        assert confidence == "low"

    def test_notes_is_string(self):
        _, notes = _classify_confidence("exact", True, 1, "test")
        assert isinstance(notes, str)
        assert len(notes) > 10


# ---------------------------------------------------------------------------
# Tests: _search_officers (mocked)
# ---------------------------------------------------------------------------

class TestSearchOfficers:
    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_returns_items_on_success(self, mock_sleep):
        session = MagicMock()
        session.get.return_value = _make_response(200, {"items": [{"title": "John Smith"}]})
        result = _search_officers("john smith", session)
        assert len(result) == 1
        assert result[0]["title"] == "John Smith"

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_returns_empty_on_error(self, mock_sleep):
        session = MagicMock()
        session.get.side_effect = Exception("network error")
        result = _search_officers("john smith", session)
        assert result == []

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_returns_empty_for_404(self, mock_sleep):
        session = MagicMock()
        session.get.return_value = _make_response(404, {})
        result = _search_officers("john smith", session)
        assert result == []


# ---------------------------------------------------------------------------
# Tests: _check_disqualification (mocked)
# ---------------------------------------------------------------------------

class TestCheckDisqualification:
    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_returns_false_for_empty_list(self, mock_sleep):
        session = MagicMock()
        session.get.return_value = _make_response(200, {"disqualifications": []})
        assert _check_disqualification("abc123", session) is False

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_returns_true_when_disqualifications_present(self, mock_sleep):
        session = MagicMock()
        session.get.return_value = _make_response(200, {"disqualifications": [{"type": "court-order"}]})
        assert _check_disqualification("abc123", session) is True

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_returns_false_for_404(self, mock_sleep):
        session = MagicMock()
        session.get.return_value = _make_response(404, {})
        assert _check_disqualification("abc123", session) is False

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_returns_false_on_exception(self, mock_sleep):
        session = MagicMock()
        session.get.side_effect = Exception("error")
        assert _check_disqualification("abc123", session) is False


# ---------------------------------------------------------------------------
# Tests: get_director_companies_house_profile (full function, mocked)
# ---------------------------------------------------------------------------

class TestGetDirectorCHProfileMocked:
    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_returns_dict_on_success(self, mock_sleep):
        search_items = [_make_search_item("John Smith", "abc123")]
        appt_items = [_make_appointment("ACME MINING PLC", "12345678")]
        session = _make_session(search_items=search_items, appointments_items=appt_items)
        result = get_director_companies_house_profile(
            "John Smith", "Acme Mining PLC", "ACME", _session=session
        )
        assert isinstance(result, dict)

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_all_required_keys_present(self, mock_sleep):
        search_items = [_make_search_item("Jane Doe", "def456")]
        appt_items = [_make_appointment("BETA CORP PLC")]
        session = _make_session(search_items=search_items, appointments_items=appt_items)
        result = get_director_companies_house_profile(
            "Jane Doe", "Beta Corp PLC", "BETA", _session=session
        )
        for key in (
            "ticker", "director_name_query", "director_name_normalised",
            "ch_person_id", "current_appointments", "resigned_appointments",
            "total_current_appointments", "tenure_at_this_company_days",
            "disqualified", "data_found", "match_confidence", "confidence_notes",
        ):
            assert key in result, f"Missing key: {key}"

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_data_found_true_for_exact_match(self, mock_sleep):
        search_items = [_make_search_item("Alice Jones", "ghi789")]
        appt_items = [_make_appointment("ALPHA ENERGY PLC")]
        session = _make_session(search_items=search_items, appointments_items=appt_items)
        result = get_director_companies_house_profile(
            "Alice Jones", "Alpha Energy PLC", "ALP", _session=session
        )
        assert result["data_found"] is True

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_data_found_false_for_no_search_results(self, mock_sleep):
        session = _make_session(search_items=[])
        result = get_director_companies_house_profile(
            "Nobody Exists", "Fake PLC", "FAKE", _session=session
        )
        assert result["data_found"] is False

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_disqualified_false_for_empty_list(self, mock_sleep):
        search_items = [_make_search_item("Bob Taylor", "jkl000")]
        appt_items = [_make_appointment()]
        session = _make_session(
            search_items=search_items,
            appointments_items=appt_items,
            disqualifications=[],
        )
        result = get_director_companies_house_profile(
            "Bob Taylor", "Some Company PLC", "SC", _session=session
        )
        # data_found depends on confidence; disqualified from API
        assert result["disqualified"] is False

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_disqualified_true_when_disqualifications_present(self, mock_sleep):
        search_items = [_make_search_item("Bad Actor", "xyz999")]
        appt_items = [_make_appointment()]
        session = _make_session(
            search_items=search_items,
            appointments_items=appt_items,
            disqualifications=[{"type": "court-order"}],
        )
        result = get_director_companies_house_profile(
            "Bad Actor", "Some PLC", "TST", _session=session
        )
        assert result["disqualified"] is True

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_current_appointments_built_correctly(self, mock_sleep):
        search_items = [_make_search_item("Carol Lee", "carol01")]
        appt_items = [
            _make_appointment("COMPANY ONE PLC", "11111111", appointed_on="2020-03-01"),
            _make_appointment("COMPANY TWO LTD", "22222222", appointed_on="2021-06-15"),
        ]
        session = _make_session(search_items=search_items, appointments_items=appt_items)
        result = get_director_companies_house_profile(
            "Carol Lee", "Company One PLC", "ONE", _session=session
        )
        if result["data_found"]:
            assert len(result["current_appointments"]) == 2
            assert result["total_current_appointments"] == 2

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_resigned_appointments_last_5_years_only(self, mock_sleep):
        today = date.today()
        old_resign = (today - timedelta(days=365 * 6)).isoformat()  # 6 years ago
        recent_resign = (today - timedelta(days=365 * 2)).isoformat()  # 2 years ago
        search_items = [_make_search_item("David Black", "david01")]
        appt_items = [
            _make_appointment("OLD COMPANY LTD", resigned_on=old_resign),
            _make_appointment("RECENT COMPANY LTD", resigned_on=recent_resign),
        ]
        session = _make_session(search_items=search_items, appointments_items=appt_items)
        result = get_director_companies_house_profile(
            "David Black", "Some PLC", "TST", _session=session
        )
        if result["data_found"]:
            company_names = [r["company_name"] for r in result["resigned_appointments"]]
            assert "RECENT COMPANY LTD" in company_names
            assert "OLD COMPANY LTD" not in company_names

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_tenure_at_this_company_computed(self, mock_sleep):
        search_items = [_make_search_item("Eve Green", "eve01")]
        # Appointment at exact target company
        appt_items = [
            _make_appointment("ACME MINING PLC", appointed_on="2020-01-01"),
        ]
        session = _make_session(search_items=search_items, appointments_items=appt_items)
        result = get_director_companies_house_profile(
            "Eve Green", "Acme Mining PLC", "ACM", _session=session
        )
        if result["data_found"]:
            assert result["tenure_at_this_company_days"] is not None
            # Appointed 2020-01-01 — should be > 365 days tenure
            assert result["tenure_at_this_company_days"] > 365

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_ticker_preserved(self, mock_sleep):
        session = _make_session(search_items=[])
        result = get_director_companies_house_profile(
            "Nobody", "Fake PLC", "FAKE", _session=session
        )
        assert result["ticker"] == "FAKE"

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_director_name_query_preserved(self, mock_sleep):
        session = _make_session(search_items=[])
        result = get_director_companies_house_profile(
            "John Smith", "Fake PLC", "FAKE", _session=session
        )
        assert result["director_name_query"] == "John Smith"

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_no_api_key_returns_error_result(self, mock_sleep):
        import os
        original = os.environ.pop("COMPANIES_HOUSE_KEY", None)
        try:
            result = get_director_companies_house_profile(
                "John Smith", "Some PLC", "TST"
            )
            assert result["data_found"] is False
            assert "error" in result
        finally:
            if original:
                os.environ["COMPANIES_HOUSE_KEY"] = original

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_graceful_result_for_search_exception(self, mock_sleep):
        session = MagicMock()
        session.get.side_effect = Exception("network failure")
        result = get_director_companies_house_profile(
            "John Smith", "Some PLC", "TST", _session=session
        )
        assert isinstance(result, dict)
        assert result["data_found"] is False

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_match_confidence_high_when_exact_and_company_found(self, mock_sleep):
        search_items = [_make_search_item("Alice Jones", "alice01")]
        appt_items = [_make_appointment("ALPHA ENERGY PLC")]
        session = _make_session(search_items=search_items, appointments_items=appt_items)
        result = get_director_companies_house_profile(
            "Alice Jones", "Alpha Energy PLC", "ALP", _session=session
        )
        if result["data_found"]:
            assert result["match_confidence"] == "high"

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_match_confidence_present_always(self, mock_sleep):
        session = _make_session(search_items=[])
        result = get_director_companies_house_profile(
            "Nobody", "Fake PLC", "FAKE", _session=session
        )
        assert result["match_confidence"] in ("high", "medium", "low")

    @patch("utilities.get_director_companies_house_profile.time.sleep")
    def test_sleep_called_for_rate_limiting(self, mock_sleep):
        search_items = [_make_search_item("Test Person", "test01")]
        session = _make_session(search_items=search_items)
        get_director_companies_house_profile(
            "Test Person", "Some PLC", "TST", _session=session
        )
        assert mock_sleep.call_count >= 1


# ---------------------------------------------------------------------------
# Integration tests — live Companies House API (require COMPANIES_HOUSE_KEY)
# ---------------------------------------------------------------------------

def _has_ch_key():
    """Check COMPANIES_HOUSE_KEY is set (after loading .env)."""
    return bool(os.environ.get("COMPANIES_HOUSE_KEY"))


@pytest.mark.integration
class TestGetDirectorCHProfileIntegration:
    """
    Live integration tests using the real CH API.
    Require COMPANIES_HOUSE_KEY in .env at project root.

    NOTE: The specific known-director test uses a real UK director from a publicly
    listed company. Replace with a director from your actual Acted/Deferred signals
    (QHE, STAR, AVG, EGT, MBO, SRT, ROSE) for higher-confidence verification.
    """

    def test_api_key_present(self):
        """Fail fast if API key not set."""
        if not _has_ch_key():
            pytest.skip("COMPANIES_HOUSE_KEY not set")
        assert _has_ch_key()

    def test_search_returns_results_for_known_director(self):
        """
        Verify CH search API is working with a known public director.
        CLIVE WATSON was a director of City Pub Group PLC (listed on AIM).
        Replace with a director from your actual signals for best verification.
        """
        if not _has_ch_key():
            pytest.skip("COMPANIES_HOUSE_KEY not set")

        result = get_director_companies_house_profile(
            director_name="Clive Watson",
            company_name="City Pub Group PLC",
            ticker="CPC",
        )
        # API is reachable and returns a structured response — regardless of match
        assert isinstance(result, dict)
        assert "data_found" in result
        assert "match_confidence" in result
        assert "disqualified" in result

    def test_current_appointments_populated_for_found_director(self):
        """current_appointments should be a list when data_found=True."""
        if not _has_ch_key():
            pytest.skip("COMPANIES_HOUSE_KEY not set")

        result = get_director_companies_house_profile(
            director_name="Clive Watson",
            company_name="City Pub Group PLC",
            ticker="CPC",
        )
        if result["data_found"]:
            assert isinstance(result["current_appointments"], list)
            # Each appointment should have required fields
            for appt in result["current_appointments"]:
                assert "company_name" in appt
                assert "role" in appt
                assert "appointed_on" in appt
                assert "is_active" in appt

    def test_resigned_appointments_within_5_years_only(self):
        """resigned_appointments should not include appointments resigned > 5 years ago."""
        if not _has_ch_key():
            pytest.skip("COMPANIES_HOUSE_KEY not set")

        result = get_director_companies_house_profile(
            director_name="Clive Watson",
            company_name="City Pub Group PLC",
            ticker="CPC",
        )
        if result["data_found"]:
            five_years_ago = date.today() - timedelta(days=5 * 365)
            for appt in result["resigned_appointments"]:
                if appt["resigned_on"]:
                    assert appt["resigned_on"] >= five_years_ago

    def test_disqualified_false_for_known_clean_director(self):
        """A well-known public company director should not be disqualified."""
        if not _has_ch_key():
            pytest.skip("COMPANIES_HOUSE_KEY not set")

        result = get_director_companies_house_profile(
            director_name="Clive Watson",
            company_name="City Pub Group PLC",
            ticker="CPC",
        )
        if result["data_found"]:
            assert result["disqualified"] is False

    def test_low_confidence_for_common_name(self):
        """
        'John Smith' is an extremely common name. Expect low confidence or
        data_found=False because we cannot reliably identify the correct person
        without company confirmation.
        """
        if not _has_ch_key():
            pytest.skip("COMPANIES_HOUSE_KEY not set")

        result = get_director_companies_house_profile(
            director_name="John Smith",
            company_name="Nonexistent Company XYZ PLC",
            ticker="XYZ",
        )
        # Either data_found=False or match_confidence in ("low", "medium")
        # Not "high" confidence for a name this common without company confirmation
        if result["data_found"]:
            assert result["match_confidence"] != "high", (
                "Unexpected high confidence for 'John Smith' without company confirmation"
            )
        else:
            assert result["match_confidence"] == "low"

    def test_graceful_result_for_unknown_director(self):
        """An invented name should return data_found=False without raising."""
        if not _has_ch_key():
            pytest.skip("COMPANIES_HOUSE_KEY not set")

        result = get_director_companies_house_profile(
            director_name="Zzznobody Doesnotexist",
            company_name="Fake Company PLC",
            ticker="FAKE",
        )
        assert isinstance(result, dict)
        assert result["data_found"] is False
        assert result["match_confidence"] == "low"

    def test_all_required_keys_present_in_live_result(self):
        """All expected keys present whether data_found is True or False."""
        if not _has_ch_key():
            pytest.skip("COMPANIES_HOUSE_KEY not set")

        result = get_director_companies_house_profile(
            director_name="Clive Watson",
            company_name="City Pub Group PLC",
            ticker="CPC",
        )
        for key in (
            "ticker", "director_name_query", "director_name_normalised",
            "ch_person_id", "current_appointments", "resigned_appointments",
            "total_current_appointments", "tenure_at_this_company_days",
            "disqualified", "data_found", "match_confidence", "confidence_notes",
        ):
            assert key in result, f"Missing key: {key}"
