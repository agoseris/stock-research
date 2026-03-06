"""
get_company_ch_filings.py

Returns recent Companies House filings for a company from the Firestore
announcements collection. Provides director tenure context without
additional API calls.

Source:    Firestore announcements collection (source_name = "Companies House")
Execution: backend or frontend (Firestore read, no IP sensitivity)
Status:    EXISTS (partial) — data already ingested by CH pipeline.
           Query wrapper and director_changes extraction are new.

Usage:
    from utilities.get_company_ch_filings import get_company_ch_filings
    result = get_company_ch_filings("QHE", days_lookback=365)
"""

import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import unquote_plus

from dotenv import load_dotenv

# Load credentials from .env at the project root.
_UTILITIES_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTILITIES_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

sys_path_parent = _PROJECT_ROOT
import sys
if sys_path_parent not in sys.path:
    sys.path.insert(0, sys_path_parent)

from utilities.director_name_normalisation import normalise_for_storage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BOARD_STABILITY_WINDOW_DAYS = 90

# Patterns that indicate a director appointment in the headline
_APPOINTMENT_PATTERNS = [
    "director appointed",
    "appointment of director",
    "appointment of",          # catches natural language descriptions
    "appoint-person",          # CH API internal format
]

# Patterns that indicate a director resignation/removal in the headline
_RESIGNATION_PATTERNS = [
    "director resigned",
    "director resignation",
    "termination of appointment",
    "terminate-person",        # CH API internal format
]

# ---------------------------------------------------------------------------
# Firestore client (lazy singleton)
# ---------------------------------------------------------------------------

_db = None


def _get_db():
    global _db
    if _db is None:
        from google.cloud import firestore
        _db = firestore.Client()
    return _db


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_company_ch_filings(
    ticker: str,
    days_lookback: int = 365,
    db=None,
) -> dict:
    """
    Return recent Companies House filings for a company from Firestore.

    Parameters
    ----------
    ticker : str
        LSE ticker as stored in universe_companies.
    days_lookback : int
        How far back to look, in days. Default 365.
    db : optional
        Firestore client. Created from GOOGLE_APPLICATION_CREDENTIALS if absent.

    Returns
    -------
    dict with keys:
        data_found        bool
        ticker            str
        recent_filings    list of {headline, published_at, source_url}
        director_changes  list of {director_name, change_type, date}
        filing_count      int
        data_maturity     str  — "sufficient" | "sparse" | "empty"
        board_stability   str  — "stable" | "active_change"
                                 (active_change = appointment or resignation
                                 within last 90 days)
    """
    if db is None:
        db = _get_db()

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days_lookback)

    try:
        docs = (
            db.collection("announcements")
            .where("ticker", "==", ticker)
            .where("source_name", "==", "Companies House")
            .stream()
        )
    except Exception as e:
        return _empty_result(ticker, error=str(e))

    # Parse, date-filter, collect
    filings = []
    for doc in docs:
        data = doc.to_dict()
        published_at = _parse_published_at(data.get("published_at"))
        if published_at is not None and published_at < cutoff_dt:
            continue
        filings.append({
            "headline": data.get("headline", ""),
            "published_at": published_at,
            "source_url": data.get("source_url", ""),
        })

    # Sort most-recent first
    filings.sort(
        key=lambda f: f["published_at"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # Extract director changes
    director_changes = _extract_director_changes(filings)

    # Board stability — any change within 90 days
    ninety_days_ago = (datetime.now(timezone.utc) - timedelta(days=_BOARD_STABILITY_WINDOW_DAYS)).date()
    recent_changes = [
        c for c in director_changes
        if c["date"] is not None and c["date"] >= ninety_days_ago
    ]
    board_stability = "active_change" if recent_changes else "stable"

    filing_count = len(filings)
    data_found = filing_count > 0
    data_maturity = _classify_maturity(filing_count)

    return {
        "data_found": data_found,
        "ticker": ticker,
        "recent_filings": filings,
        "director_changes": director_changes,
        "filing_count": filing_count,
        "data_maturity": data_maturity,
        "board_stability": board_stability,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_change(headline: str) -> Optional[str]:
    """
    Return "appointed", "resigned", or None based on headline text.
    Matches against both CH filing labels and description text patterns.
    """
    h = headline.lower()
    # Check resignation patterns first (more specific than appointment)
    for pat in _RESIGNATION_PATTERNS:
        if pat in h:
            return "resigned"
    for pat in _APPOINTMENT_PATTERNS:
        if pat in h:
            return "appointed"
    return None


def _extract_director_name(headline: str) -> Optional[str]:
    """
    Attempt to extract a director name from a CH filing headline.

    Handles two formats:
      1. CH internal:  "Director Appointed: appoint-person#name=SMITH+JOHN&..."
      2. Natural:      "Director Appointed: Appointment of John Smith as a director"

    Returns the raw name string before normalisation, or None if no pattern matches.
    """
    # Split label from description on first ": "
    if ": " in headline:
        description = headline.split(": ", 1)[1].strip()
    else:
        description = headline.strip()

    # Pattern 1: CH internal key format "#name=SURNAME+FORENAMES&..."
    # CH convention: name tokens in UPPERCASE, first token = surname
    m = re.search(r"#name=([A-Za-z%+\-]+)", description)
    if m:
        raw = unquote_plus(m.group(1).replace("+", " ")).strip()
        parts = raw.split()
        if len(parts) >= 2:
            # Reformat as "SURNAME, FORENAMES" so normaliser re-orders correctly
            return parts[0] + ", " + " ".join(parts[1:])
        return raw  # single-token name — just return as-is

    # Pattern 2: "Appointment of [name] as a director"
    m = re.search(r"appointment of (.+?) as a director", description, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Pattern 3: "Termination of appointment of [name] as a director"
    m = re.search(
        r"termination of appointment of (.+?) as a director", description, re.IGNORECASE
    )
    if m:
        return m.group(1).strip()

    return None


def _extract_director_changes(filings: list) -> list:
    """
    Scan filings list and return a list of director change dicts.
    Each entry: {director_name (normalised or None), change_type, date}
    """
    changes = []
    for f in filings:
        change_type = _classify_change(f["headline"])
        if change_type is None:
            continue
        raw_name = _extract_director_name(f["headline"])
        if raw_name:
            normalised = normalise_for_storage(raw_name)
        else:
            normalised = None
        filing_date = f["published_at"].date() if f["published_at"] else None
        changes.append({
            "director_name": normalised,
            "change_type": change_type,
            "date": filing_date,
        })
    return changes


def _classify_maturity(filing_count: int) -> str:
    if filing_count == 0:
        return "empty"
    if filing_count <= 2:
        return "sparse"
    return "sufficient"


def _parse_published_at(value) -> Optional[datetime]:
    """
    Parse a published_at value stored by save_announcement().

    save_announcement() stores: str(announcement.published_at)
    which gives e.g. "2024-01-15 00:00:00+00:00".

    Also handles Firestore Timestamp (datetime), ISO strings with "T",
    and date-only strings.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    s = str(value).strip()
    if not s:
        return None
    # Normalise the space-format "2024-01-15 00:00:00+00:00" to ISO
    s = s.replace(" ", "T", 1) if "T" not in s and " " in s else s
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass
    # Date-only fallback "2024-01-15"
    try:
        d = date.fromisoformat(s[:10])
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _empty_result(ticker: str, error: str = None) -> dict:
    result = {
        "data_found": False,
        "ticker": ticker,
        "recent_filings": [],
        "director_changes": [],
        "filing_count": 0,
        "data_maturity": "empty",
        "board_stability": "stable",
    }
    if error:
        result["error"] = error
    return result


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    test_tickers = ["QHE", "STAR", "AVG", "INVALID_TICKER_XYZ"]
    print("get_company_ch_filings — smoke test\n")

    for t in test_tickers:
        result = get_company_ch_filings(t, days_lookback=365)
        print(f"--- {t} ---")
        print(f"  data_found:      {result['data_found']}")
        print(f"  filing_count:    {result['filing_count']}")
        print(f"  data_maturity:   {result['data_maturity']}")
        print(f"  board_stability: {result['board_stability']}")
        if result["director_changes"]:
            print(f"  director_changes ({len(result['director_changes'])}):")
            for c in result["director_changes"]:
                print(f"    {c['change_type']:10s}  {c['director_name'] or '(name not extracted)':30s}  {c['date']}")
        if result["recent_filings"]:
            print(f"  most recent filing: {result['recent_filings'][0]['headline'][:80]}")
        if "error" in result:
            print(f"  error: {result['error']}")
        print()
