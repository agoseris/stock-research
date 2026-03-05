"""
get_director_companies_house_profile.py

Returns the Companies House profile for a director — their current and recent
appointments, and a disqualification check. Called conditionally from the Layer 2
agent when the credibility research branch is triggered.

Uses the director_name_normalisation utility for matching search results to the
queried director name. Company name disambiguation is used to boost confidence
when a unique match at the target company is found.

API endpoints used:
  GET /search/officers?q={name}&items_per_page=10   ← search by name
  GET /officers/{officer_id}/appointments            ← fetch appointment list
  GET /officers/{officer_id}/disqualifications       ← disqualification check

Rate limit: 600 requests / 5 minutes. Well within typical usage.
Authentication: HTTP Basic Auth with COMPANIES_HOUSE_KEY as username, empty password.

Execution: GCP backend or local machine (Companies House API, no IP sensitivity).

Usage:
    from utilities.get_director_companies_house_profile import (
        get_director_companies_house_profile
    )
    result = get_director_companies_house_profile(
        director_name="John Smith",
        company_name="Acme Mining PLC",
        ticker="ACME",
    )
"""

import os
import time
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv

from utilities.director_name_normalisation import normalise_for_storage, names_match

_UTILITIES_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTILITIES_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, "backend", ".env"))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CH_BASE_URL = "https://api.company-information.service.gov.uk"
_SEARCH_ITEMS_PER_PAGE = 10
_REQUEST_SLEEP = 0.6  # seconds — conservative rate limiting

# resigned_appointments: only include resignations within this period
_RESIGNED_LOOKBACK_YEARS = 5

# Confidence thresholds for name matching
# "high":   exact match AND company found in appointments
# "medium": exact match (no company confirmation) OR abbreviated/fuzzy match
# "low":    ambiguous / cannot confirm match → data_found=False
_AMBIGUITY_THRESHOLD = 3  # 3+ near-name-matches without company confirmation → "low"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_director_companies_house_profile(
    director_name: str,
    company_name: str,
    ticker: str,
    _session: requests.Session = None,
) -> dict:
    """
    Return the Companies House profile for a director.

    Parameters
    ----------
    director_name : str
        Raw or normalised director name from the PDMR filing.
        Normalised internally before querying.
    company_name : str
        Company name from the universe. Used to disambiguate common names
        by checking whether the matched officer has an appointment at this company.
    ticker : str
        LSE ticker — passed through to the result for reference.
    _session : requests.Session, optional
        Injected HTTP session for testing. Created automatically if None.

    Returns
    -------
    dict with keys:
        ticker                      str
        director_name_query         str     raw input name
        director_name_normalised    str     normalised form used for search
        ch_person_id                str | None
        current_appointments        list    each: {company_name, company_number,
                                                   role, appointed_on,
                                                   is_active, tenure_days}
        resigned_appointments       list    last 5 years; each: {company_name,
                                                                   company_number,
                                                                   role,
                                                                   appointed_on,
                                                                   resigned_on}
        total_current_appointments  int
        tenure_at_this_company_days int | None  days since appointment at
                                                 company_name (if found)
        disqualified                bool
        data_found                  bool
        match_confidence            str     "high" | "medium" | "low"
        confidence_notes            str
    """
    api_key = os.getenv("COMPANIES_HOUSE_KEY")
    if not api_key:
        return _not_found_result(
            director_name,
            ticker,
            error="COMPANIES_HOUSE_KEY not set in environment",
        )

    if _session is None:
        _session = requests.Session()
        _session.auth = (api_key, "")

    normalised = normalise_for_storage(director_name)

    # --- Step 1: Search for the officer by name ---
    search_results = _search_officers(normalised, _session)
    time.sleep(_REQUEST_SLEEP)

    if not search_results:
        return _not_found_result(director_name, ticker, normalised=normalised)

    # --- Step 2: Score name matches ---
    scored = []
    for item in search_results:
        candidate_name = _extract_officer_name(item)
        if not candidate_name:
            continue
        match_result = names_match(normalised, candidate_name)
        if match_result["match"]:
            scored.append((item, match_result))

    if not scored:
        return _not_found_result(director_name, ticker, normalised=normalised)

    # Take the best-matching candidate (exact > abbreviated > fuzzy)
    _METHOD_RANK = {"exact": 0, "abbreviated": 1, "fuzzy": 2}
    scored.sort(key=lambda x: _METHOD_RANK.get(x[1]["method"], 9))
    best_item, best_match = scored[0]

    officer_id = _extract_officer_id(best_item)
    if not officer_id:
        return _not_found_result(director_name, ticker, normalised=normalised)

    # --- Step 3: Fetch full appointment list ---
    appointments_data = _fetch_appointments(officer_id, _session)
    time.sleep(_REQUEST_SLEEP)

    all_appointments = appointments_data.get("items", []) if appointments_data else []

    # --- Step 4: Check for appointment at target company ---
    company_match_found = _find_company_appointment(all_appointments, company_name)

    # --- Step 5: Classify match confidence ---
    n_near_matches = len(scored)
    match_confidence, confidence_notes = _classify_confidence(
        best_match["method"], company_match_found, n_near_matches, normalised
    )

    if match_confidence == "low":
        return _not_found_result(
            director_name, ticker,
            normalised=normalised,
            ch_person_id=officer_id,
            match_confidence="low",
            confidence_notes=confidence_notes,
        )

    # --- Step 6: Build appointment lists ---
    today = date.today()
    five_years_ago = today - timedelta(days=_RESIGNED_LOOKBACK_YEARS * 365)

    current_appointments = []
    resigned_appointments = []
    tenure_at_this_company_days = None

    for appt in all_appointments:
        appointed_to = appt.get("appointed_to", {})
        appt_company_name = appointed_to.get("company_name", "")
        appt_company_number = appointed_to.get("company_number", "")
        role = appt.get("officer_role", "")
        appointed_on = _parse_date(appt.get("appointed_on"))
        resigned_on = _parse_date(appt.get("resigned_on"))
        is_active = resigned_on is None

        if is_active:
            tenure_days = (today - appointed_on).days if appointed_on else None
            current_appointments.append({
                "company_name":    appt_company_name,
                "company_number":  appt_company_number,
                "role":            role,
                "appointed_on":    appointed_on,
                "is_active":       True,
                "tenure_days":     tenure_days,
            })
            # Check if this is the target company
            if company_match_found and _company_name_matches(appt_company_name, company_name):
                tenure_at_this_company_days = tenure_days
        else:
            # Include resigned appointments from last 5 years
            if resigned_on and resigned_on >= five_years_ago:
                resigned_appointments.append({
                    "company_name":   appt_company_name,
                    "company_number": appt_company_number,
                    "role":           role,
                    "appointed_on":   appointed_on,
                    "resigned_on":    resigned_on,
                })

    # --- Step 7: Disqualification check ---
    disqualified = _check_disqualification(officer_id, _session)
    time.sleep(_REQUEST_SLEEP)

    return {
        "ticker":                       ticker,
        "director_name_query":          director_name,
        "director_name_normalised":     normalised,
        "ch_person_id":                 officer_id,
        "current_appointments":         current_appointments,
        "resigned_appointments":        resigned_appointments,
        "total_current_appointments":   len(current_appointments),
        "tenure_at_this_company_days":  tenure_at_this_company_days,
        "disqualified":                 disqualified,
        "data_found":                   True,
        "match_confidence":             match_confidence,
        "confidence_notes":             confidence_notes,
    }


# ---------------------------------------------------------------------------
# Internal helpers — Companies House API calls
# ---------------------------------------------------------------------------

def _search_officers(normalised_name: str, session: requests.Session) -> list:
    """
    Call GET /search/officers?q={name}&items_per_page=N.
    Returns list of search result items, or [] on error.
    """
    try:
        resp = session.get(
            f"{_CH_BASE_URL}/search/officers",
            params={"q": normalised_name, "items_per_page": _SEARCH_ITEMS_PER_PAGE},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("items", [])
    except Exception:
        return []


def _fetch_appointments(officer_id: str, session: requests.Session) -> dict:
    """
    Call GET /officers/{officer_id}/appointments.
    Returns response dict, or {} on error.
    """
    try:
        resp = session.get(
            f"{_CH_BASE_URL}/officers/{officer_id}/appointments",
            params={"items_per_page": 50},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def _check_disqualification(officer_id: str, session: requests.Session) -> bool:
    """
    Call GET /officers/{officer_id}/disqualifications.
    Returns True if any disqualifications found, False otherwise.
    Returns False gracefully on error.
    """
    try:
        resp = session.get(
            f"{_CH_BASE_URL}/officers/{officer_id}/disqualifications",
            timeout=10,
        )
        if resp.status_code == 404:
            # 404 from disqualifications endpoint means not found in disqualified register
            return False
        resp.raise_for_status()
        data = resp.json()
        return len(data.get("disqualifications", [])) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Internal helpers — data extraction and classification
# ---------------------------------------------------------------------------

def _extract_officer_name(search_item: dict) -> Optional[str]:
    """
    Extract normalised officer name from a /search/officers result item.
    CH returns names in various formats:
      - "title" field: "Mr John Smith" or "SMITH, JOHN"
      - "description" field: "SMITH, JOHN - Active director of ..."
    """
    # Try "title" first (cleaner)
    title = search_item.get("title", "").strip()
    if title:
        return title
    # Fallback: description up to first " - "
    description = search_item.get("description", "")
    if " - " in description:
        return description.split(" - ")[0].strip()
    return description.strip() or None


def _extract_officer_id(search_item: dict) -> Optional[str]:
    """
    Extract officer_id from the links.self field.
    links.self format: "/officers/{officer_id}/appointments"
    """
    links = search_item.get("links", {})
    self_link = links.get("self", "")
    # "/officers/abc123/appointments" → ["", "officers", "abc123", "appointments"]
    parts = [p for p in self_link.split("/") if p]
    if len(parts) >= 2 and parts[0] == "officers":
        return parts[1]
    return None


def _find_company_appointment(appointments: list, target_company: str) -> bool:
    """
    Return True if any appointment in the list matches the target company name.
    Uses case-insensitive substring matching with key words.
    """
    if not appointments or not target_company:
        return False
    target_words = set(_normalise_company_name(target_company).split())
    for appt in appointments:
        appt_name = appt.get("appointed_to", {}).get("company_name", "")
        if not appt_name:
            continue
        appt_words = set(_normalise_company_name(appt_name).split())
        # Consider a match if 3+ significant words overlap (or all target words overlap)
        overlap = target_words & appt_words
        if len(overlap) >= min(3, len(target_words)):
            return True
    return False


def _company_name_matches(appt_company: str, target_company: str) -> bool:
    """Reuse _find_company_appointment logic for a single company pair."""
    return _find_company_appointment(
        [{"appointed_to": {"company_name": appt_company}}], target_company
    )


def _normalise_company_name(name: str) -> str:
    """
    Strip common suffixes and normalise for comparison.
    "Acme Mining PLC" → "acme mining"
    """
    noise = {"plc", "ltd", "limited", "group", "holdings", "the", "and", "&"}
    words = [w for w in name.lower().split() if w not in noise]
    return " ".join(words)


def _classify_confidence(
    match_method: str,
    company_found: bool,
    n_near_matches: int,
    normalised_name: str,
) -> tuple:
    """
    Return (match_confidence, confidence_notes) based on:
    - match_method: "exact" | "abbreviated" | "fuzzy"
    - company_found: whether the officer has an appointment at the target company
    - n_near_matches: number of CH results with a name match
    - normalised_name: for building the confidence note
    """
    if match_method == "exact" and company_found:
        return "high", (
            f"Exact name match for '{normalised_name}' confirmed by appointment "
            "at target company in Companies House record."
        )

    if match_method == "exact" and not company_found:
        if n_near_matches >= _AMBIGUITY_THRESHOLD:
            return "low", (
                f"Multiple CH records match '{normalised_name}' exactly and none "
                "confirmed at target company — ambiguous, cannot identify correct person."
            )
        return "medium", (
            f"Exact name match for '{normalised_name}' but target company not "
            "found in current appointments — may be under a different company name "
            "or recently appointed."
        )

    if match_method in ("abbreviated", "fuzzy"):
        if company_found:
            return "medium", (
                f"Abbreviated/fuzzy name match for '{normalised_name}' confirmed "
                "by appointment at target company."
            )
        return "medium", (
            f"Abbreviated/fuzzy name match for '{normalised_name}'. "
            "Target company not confirmed in appointments."
        )

    return "low", f"Weak or ambiguous match for '{normalised_name}'."


def _parse_date(raw) -> Optional[date]:
    """Parse a date from CH API response (string "YYYY-MM-DD" or already a date)."""
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            pass
    return None


def _not_found_result(
    director_name: str,
    ticker: str,
    normalised: str = None,
    error: str = None,
    ch_person_id: str = None,
    match_confidence: str = "low",
    confidence_notes: str = None,
) -> dict:
    """Return a well-formed not-found / low-confidence result."""
    result = {
        "ticker":                       ticker,
        "director_name_query":          director_name,
        "director_name_normalised":     normalised or normalise_for_storage(director_name),
        "ch_person_id":                 ch_person_id,
        "current_appointments":         [],
        "resigned_appointments":        [],
        "total_current_appointments":   0,
        "tenure_at_this_company_days":  None,
        "disqualified":                 False,
        "data_found":                   False,
        "match_confidence":             match_confidence,
        "confidence_notes":             confidence_notes or "No match found in Companies House officer search.",
    }
    if error:
        result["error"] = error
    return result


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("get_director_companies_house_profile — smoke test\n")

    test_cases = [
        {
            "director_name": "Clive Watson",
            "company_name": "City Pub Group PLC",
            "ticker": "CPC",
            "desc": "Known UK small-cap director",
        },
        {
            "director_name": "John Smith",
            "company_name": "Some Company PLC",
            "ticker": "TEST",
            "desc": "Common name — expect low/ambiguous confidence",
        },
        {
            "director_name": "Zzznobody Doesnotexist",
            "company_name": "Fake PLC",
            "ticker": "FAKE",
            "desc": "Unknown director — expect graceful not-found",
        },
    ]

    for tc in test_cases:
        print(f"--- {tc['director_name']} ({tc['desc']}) ---")
        result = get_director_companies_house_profile(
            director_name=tc["director_name"],
            company_name=tc["company_name"],
            ticker=tc["ticker"],
        )
        print(f"  data_found:                   {result['data_found']}")
        print(f"  match_confidence:             {result['match_confidence']}")
        print(f"  confidence_notes:             {result['confidence_notes']}")
        print(f"  ch_person_id:                 {result['ch_person_id']}")
        print(f"  disqualified:                 {result['disqualified']}")
        print(f"  total_current_appointments:   {result['total_current_appointments']}")
        print(f"  tenure_at_this_company_days:  {result['tenure_at_this_company_days']}")
        for appt in result.get("current_appointments", [])[:3]:
            print(
                f"    → {appt['company_name']} | {appt['role']} | "
                f"from {appt['appointed_on']} ({appt['tenure_days']}d)"
            )
        if "error" in result:
            print(f"  error: {result['error']}")
        print()
