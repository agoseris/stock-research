"""
get_company_profile.py

Returns basic company identity and classification from the Firestore
universe_companies collection.

Source:    Firestore — universe_companies collection
Execution: backend or frontend (Firestore read, no IP sensitivity)
Status:    EXISTS — market_cap_stale flag is new

Usage:
    from utilities.get_company_profile import get_company_profile
    profile = get_company_profile("QHE")
"""

import os
from datetime import date, datetime, timezone
from typing import Optional

from dotenv import load_dotenv

# Load credentials from .env at the project root.
# Computed from this file's location so it works regardless of CWD.
_UTILITIES_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTILITIES_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

_STALE_THRESHOLD_DAYS = 90

# Mapping from Firestore listing_exchange values to the spec's index_membership
_EXCHANGE_TO_INDEX = {
    "AIM": "AIM",
    "LSE_MAIN": "MAIN_MARKET",
}


# ---------------------------------------------------------------------------
# Firestore client (lazy singleton)
# ---------------------------------------------------------------------------

_db = None


def _get_db():
    """Return a Firestore client, creating it on first call."""
    global _db
    if _db is None:
        from google.cloud import firestore
        _db = firestore.Client()
    return _db


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_company_profile(ticker: str, db=None) -> dict:
    """
    Return basic company identity and classification for a universe ticker.

    Parameters
    ----------
    ticker : str
        LSE ticker as stored in universe_companies (e.g. "QHE", "SRT").
    db : optional
        Firestore client to use. Created from GOOGLE_APPLICATION_CREDENTIALS
        if not provided.

    Returns
    -------
    dict with keys:
        data_found        bool    — False for unknown / out-of-universe tickers
        ticker            str
        company_name      str | None
        market_cap_gbp    float | None  — raw GBP (e.g. 45_300_000), not £m
        market_cap_date   date | None   — date market cap was last written
                                          (derived from last_refreshed)
        market_cap_stale  bool | None   — True if market_cap_date > 90 days ago;
                                          None if no date available
        index_membership  str | None    — "AIM" | "MAIN_MARKET"
        sector            str | None    — sparse; None for most companies
        universe_added_at date | None
    """
    if db is None:
        db = _get_db()

    try:
        doc = db.collection("universe_companies").document(ticker).get()
    except Exception as e:
        return _not_found(ticker, error=str(e))

    if not doc.exists:
        return _not_found(ticker)

    data = doc.to_dict()
    return _build_profile(ticker, data)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_profile(ticker: str, data: dict) -> dict:
    """Construct the profile dict from a raw Firestore document dict."""

    # --- market_cap_date: derived from last_refreshed -----------------------
    # last_refreshed is stored as an ISO string by _company_to_dict, but may
    # be a Firestore Timestamp (datetime) if written by other code.
    market_cap_date = _parse_to_date(data.get("last_refreshed"))

    # --- market_cap_stale ---------------------------------------------------
    market_cap_stale: Optional[bool] = None
    if market_cap_date is not None:
        today = datetime.now(timezone.utc).date()
        age_days = (today - market_cap_date).days
        market_cap_stale = age_days > _STALE_THRESHOLD_DAYS
    elif data.get("market_cap_gbp") is not None:
        # Market cap value present but no date — treat conservatively as stale
        market_cap_stale = True

    # --- index_membership ---------------------------------------------------
    listing_exchange = data.get("listing_exchange", "")
    index_membership = _EXCHANGE_TO_INDEX.get(listing_exchange, "MAIN_MARKET")

    # --- universe_added_at --------------------------------------------------
    universe_added_at = _parse_to_date(data.get("universe_added_date"))

    return {
        "data_found": True,
        "ticker": data.get("ticker_lse", ticker),
        "company_name": data.get("company_name"),
        "market_cap_gbp": data.get("market_cap_gbp"),
        "market_cap_date": market_cap_date,
        "market_cap_stale": market_cap_stale,
        "index_membership": index_membership,
        "sector": data.get("sector"),
        "universe_added_at": universe_added_at,
    }


def _not_found(ticker: str, error: str = None) -> dict:
    """Return a graceful not-found result."""
    result = {
        "data_found": False,
        "ticker": ticker,
        "company_name": None,
        "market_cap_gbp": None,
        "market_cap_date": None,
        "market_cap_stale": None,
        "index_membership": None,
        "sector": None,
        "universe_added_at": None,
    }
    if error:
        result["error"] = error
    return result


def _parse_to_date(value) -> Optional[date]:
    """
    Parse a Firestore field value to a date object.

    Handles:
      - None                        → None
      - datetime (Firestore Timestamp or Python datetime)  → .date()
      - date                        → as-is
      - ISO string "2026-03-01..." → parse first 10 chars
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # ISO string — take first 10 chars (date part only)
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    test_tickers = ["QHE", "STAR", "AVG", "INVALID_TICKER_XYZ"]
    print("get_company_profile — smoke test\n")

    for t in test_tickers:
        profile = get_company_profile(t)
        print(f"--- {t} ---")
        for k, v in profile.items():
            print(f"  {k}: {v}")
        print()
