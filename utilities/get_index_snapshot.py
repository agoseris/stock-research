"""
get_index_snapshot.py

Returns the most recent daily index snapshot from Firestore for relative
performance benchmarking. Used by Layer 1 platform assessment tools.

The Firestore index_snapshots collection is populated daily by
scripts/scrape_index_snapshots.py which runs via cron after UK market close.

Index selection logic:
  AIM company               → AIM_ALL_SHARE
  Main market, cap < £500m  → FTSE_SMALL_CAP
  Main market, cap ≥ £500m  → FTSE_250
  Main market, cap unknown  → FTSE_SMALL_CAP (safe default)

Usage:
    from utilities.get_index_snapshot import get_index_snapshot
    result = get_index_snapshot("AIM", market_cap_gbp=50_000_000)
"""

import os
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

# Load credentials from .env at the project root.
_UTILITIES_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTILITIES_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# The .env carries the VM credentials path. On the local machine, fall back to
# frontend/gcp-credentials.json if the env-var is unset or the path doesn't exist.
_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds_path or not os.path.exists(_creds_path):
    _local_creds = os.path.join(_PROJECT_ROOT, "frontend", "gcp-credentials.json")
    if os.path.exists(_local_creds):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _local_creds

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COLLECTION = "index_snapshots"

# Main market threshold: ≥ £500m → FTSE 250; < £500m → FTSE Small Cap
_LARGE_CAP_THRESHOLD = 500_000_000

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

def get_index_snapshot(
    index_membership: str,
    market_cap_gbp=None,
    db=None,
) -> dict:
    """
    Return the most recent index snapshot from Firestore for the given company.

    Parameters
    ----------
    index_membership : str
        "AIM" or "MAIN_MARKET"
    market_cap_gbp : float, optional
        Market cap in GBP (raw value, not millions). Used to select
        FTSE Small Cap vs FTSE 250 for main market companies.
        If None, defaults to FTSE_SMALL_CAP.
    db : optional
        Firestore client. Created from GOOGLE_APPLICATION_CREDENTIALS if absent.

    Returns
    -------
    dict with keys:
        data_found          bool
        index_name          str | None    "AIM_ALL_SHARE" | "FTSE_SMALL_CAP" | "FTSE_250"
        index_value         float | None
        week_52_high        float | None
        week_52_low         float | None
        change_1d_pct       float | None
        scrape_date         date | None
        snapshot_age_days   int | None    days since scrape_date; None when no data
    """
    if db is None:
        db = _get_db()

    index_name = _select_index(index_membership, market_cap_gbp)
    today = datetime.now(timezone.utc).date()

    # 1. Try today's document directly (fast path — direct get, no query index needed)
    data = _get_snapshot_by_date(db, index_name, today)
    if data is not None:
        return _build_result(index_name, data, today)

    # 2. Fallback: most recent available snapshot for this index
    data = _get_most_recent_snapshot(db, index_name)
    if data is not None:
        return _build_result(index_name, data, today)

    # 3. No data at all — return gracefully, do not block the pipeline
    return _empty_result(index_name)


# ---------------------------------------------------------------------------
# Index selection logic
# ---------------------------------------------------------------------------

def _select_index(index_membership: str, market_cap_gbp) -> str:
    """
    Select the Firestore index key for the given company classification.

    AIM company               → AIM_ALL_SHARE
    Main market, cap ≥ £500m  → FTSE_250
    Main market, cap < £500m  → FTSE_SMALL_CAP
    Main market, cap unknown  → FTSE_SMALL_CAP (safe default)
    """
    if index_membership == "AIM":
        return "AIM_ALL_SHARE"
    if market_cap_gbp is not None and market_cap_gbp >= _LARGE_CAP_THRESHOLD:
        return "FTSE_250"
    return "FTSE_SMALL_CAP"


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------

def _get_snapshot_by_date(db, index_name: str, snapshot_date: date):
    """
    Fetch a snapshot document by exact date via direct document get.
    Returns the document dict, or None if not found or on error.
    """
    doc_id = f"{index_name}_{snapshot_date.isoformat()}"
    try:
        doc = db.collection(_COLLECTION).document(doc_id).get()
        if doc.exists:
            return doc.to_dict()
    except Exception:
        pass
    return None


def _get_most_recent_snapshot(db, index_name: str):
    """
    Query for the most recent snapshot for the given index.
    Returns the document dict, or None if not found or on error.
    """
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter
        docs = (
            db.collection(_COLLECTION)
            .where(filter=FieldFilter("index_name", "==", index_name))
            .order_by("scrape_date", direction="DESCENDING")
            .limit(1)
            .stream()
        )
        for doc in docs:
            return doc.to_dict()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------

def _build_result(index_name: str, data: dict, today: date) -> dict:
    scrape_date = _parse_scrape_date(data.get("scrape_date"))
    age_days = (today - scrape_date).days if scrape_date is not None else None
    return {
        "data_found": True,
        "index_name": index_name,
        "index_value": data.get("index_value"),
        "week_52_high": data.get("week_52_high"),
        "week_52_low": data.get("week_52_low"),
        "change_1d_pct": data.get("change_1d_pct"),
        "scrape_date": scrape_date,
        "snapshot_age_days": age_days,
    }


def _empty_result(index_name: str) -> dict:
    return {
        "data_found": False,
        "index_name": index_name,
        "index_value": None,
        "week_52_high": None,
        "week_52_low": None,
        "change_1d_pct": None,
        "scrape_date": None,
        "snapshot_age_days": None,
    }


def _parse_scrape_date(value) -> date:
    """
    Parse the scrape_date field stored by the scraper.

    The scraper stores scrape_date as a 'YYYY-MM-DD' string.
    Handles: string, datetime, date objects.
    Returns None on any parse failure.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_cases = [
        {"index_membership": "AIM",         "market_cap_gbp": 50_000_000},
        {"index_membership": "MAIN_MARKET", "market_cap_gbp": 200_000_000},
        {"index_membership": "MAIN_MARKET", "market_cap_gbp": 700_000_000},
        {"index_membership": "MAIN_MARKET", "market_cap_gbp": None},
    ]

    print("get_index_snapshot — smoke test\n")
    for tc in test_cases:
        result = get_index_snapshot(**tc)
        label = f"  {tc['index_membership']}, cap={tc['market_cap_gbp']}"
        print(f"--- {label.strip()} ---")
        print(f"  index_name:        {result['index_name']}")
        print(f"  data_found:        {result['data_found']}")
        print(f"  index_value:       {result['index_value']}")
        print(f"  week_52_high:      {result['week_52_high']}")
        print(f"  week_52_low:       {result['week_52_low']}")
        print(f"  change_1d_pct:     {result['change_1d_pct']}")
        print(f"  scrape_date:       {result['scrape_date']}")
        print(f"  snapshot_age_days: {result['snapshot_age_days']}")
        print()
