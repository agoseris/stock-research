"""
get_index_context_for_freshness.py

Layer 3 Signal Freshness tool: index snapshot with historical movement context.

Wraps get_index_snapshot (Layer 1) — no duplicate implementation.

Adds:
  index_movement_1w:    float | None  — % change over past 7 calendar days
  index_movement_1m:    float | None  — % change over past 30 calendar days
  historical_available: bool          — True if at least one movement field populated

Movement fields are null when insufficient snapshot history exists.
This is expected during early PoC (scraper needs > 30 days of history).

Usage:
    from utilities.get_index_context_for_freshness import get_index_context_for_freshness
    result = get_index_context_for_freshness("AIM", market_cap_gbp=50_000_000)
"""

import os
from datetime import date, timedelta

from dotenv import load_dotenv

# Load credentials from .env at the project root.
_UTILITIES_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTILITIES_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# Credentials fallback for local dev (VM path in .env may not exist locally)
_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds_path or not os.path.exists(_creds_path):
    _local_creds = os.path.join(_PROJECT_ROOT, "frontend", "gcp-credentials.json")
    if os.path.exists(_local_creds):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _local_creds

# Import from Layer 1 — shared implementation, no duplication
from utilities.get_index_snapshot import (
    get_index_snapshot,
    _parse_scrape_date,
    _COLLECTION,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How many recent snapshots to fetch for movement calculation.
# 35 covers ~7 weeks of trading days — sufficient for both 1w and 1m windows.
_HISTORY_LIMIT = 35

# Tolerance in days for matching a snapshot to the target historical date.
# A snapshot within ±tolerance of the target date is accepted.
# 1w target (7d): tolerance = 3 → accepts snapshots 4–10 calendar days old
# 1m target (30d): tolerance = 10 → accepts snapshots 20–40 calendar days old
_TOLERANCE_1W = 3
_TOLERANCE_1M = 10

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

def get_index_context_for_freshness(
    index_membership: str,
    market_cap_gbp=None,
    db=None,
) -> dict:
    """
    Return index snapshot plus historical movement context for freshness assessment.

    Functionally identical to get_index_snapshot for the base fields.
    Extends the result with derived movement fields when history is available.

    Parameters
    ----------
    index_membership : str
        "AIM" or "MAIN_MARKET"
    market_cap_gbp : float, optional
        Market cap in GBP (raw value, not millions).
    db : optional
        Firestore client. Created from GOOGLE_APPLICATION_CREDENTIALS if absent.

    Returns
    -------
    dict — all fields from get_index_snapshot, plus:
        index_movement_1w:    float | None   % change from 7 days ago to now
        index_movement_1m:    float | None   % change from 30 days ago to now
        historical_available: bool           True if at least one movement field set
    """
    if db is None:
        db = _get_db()

    result = get_index_snapshot(index_membership, market_cap_gbp, db)

    if not result["data_found"]:
        result["index_movement_1w"] = None
        result["index_movement_1m"] = None
        result["historical_available"] = False
        return result

    current_value = result["index_value"]
    current_date = result["scrape_date"]  # date object from _build_result

    if current_value is None or current_date is None:
        result["index_movement_1w"] = None
        result["index_movement_1m"] = None
        result["historical_available"] = False
        return result

    index_name = result["index_name"]
    snapshots = _fetch_recent_snapshots(db, index_name)

    movement_1w = _compute_movement(
        snapshots, current_value, current_date, days=7, tolerance=_TOLERANCE_1W
    )
    movement_1m = _compute_movement(
        snapshots, current_value, current_date, days=30, tolerance=_TOLERANCE_1M
    )

    result["index_movement_1w"] = movement_1w
    result["index_movement_1m"] = movement_1m
    result["historical_available"] = movement_1w is not None or movement_1m is not None

    return result


# ---------------------------------------------------------------------------
# Historical snapshot helpers
# ---------------------------------------------------------------------------

def _fetch_recent_snapshots(db, index_name: str) -> list:
    """
    Fetch recent snapshots for the given index from Firestore.

    Returns a list of dicts: [{"scrape_date": date, "index_value": float}, ...]
    Ordered by scrape_date descending. Returns [] on any error.

    Uses a single-field filter only (no order_by) to avoid requiring a composite
    Firestore index. Sorting and limiting happen client-side — the collection is
    small (one doc per trading day, three indices, capped by _HISTORY_LIMIT).
    """
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter
        docs = (
            db.collection(_COLLECTION)
            .where(filter=FieldFilter("index_name", "==", index_name))
            .stream()
        )
        result = []
        for doc in docs:
            data = doc.to_dict()
            scrape_date = _parse_scrape_date(data.get("scrape_date"))
            index_value = data.get("index_value")
            if scrape_date is not None and index_value is not None:
                result.append({"scrape_date": scrape_date, "index_value": index_value})
        # Sort descending client-side — collection is small
        result.sort(key=lambda x: x["scrape_date"], reverse=True)
        return result[:_HISTORY_LIMIT]
    except Exception:
        return []


def _compute_movement(
    snapshots: list,
    current_value: float,
    current_date: date,
    days: int,
    tolerance: int,
) -> float | None:
    """
    Find the historical snapshot closest to (current_date - days) and compute
    the percentage movement from that snapshot to current_value.

    Only considers snapshots strictly before current_date.
    Returns None if no snapshot within tolerance is found, or on calculation error.
    """
    if not snapshots:
        return None

    target_date = current_date - timedelta(days=days)

    best = None
    best_diff = None
    for snap in snapshots:
        sd = snap["scrape_date"]
        if sd >= current_date:
            continue  # skip current or future dates
        diff = abs((sd - target_date).days)
        if best_diff is None or diff < best_diff:
            best = snap
            best_diff = diff

    if best is None or best_diff > tolerance:
        return None

    hist_value = best["index_value"]
    if hist_value == 0:
        return None

    return round((current_value - hist_value) / hist_value * 100, 2)


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_cases = [
        {"index_membership": "AIM",         "market_cap_gbp": 50_000_000},
        {"index_membership": "MAIN_MARKET", "market_cap_gbp": 200_000_000},
        {"index_membership": "MAIN_MARKET", "market_cap_gbp": 700_000_000},
    ]

    print("get_index_context_for_freshness — smoke test\n")
    for tc in test_cases:
        result = get_index_context_for_freshness(**tc)
        label = f"{tc['index_membership']}, cap={tc['market_cap_gbp']}"
        print(f"--- {label} ---")
        print(f"  index_name:           {result['index_name']}")
        print(f"  data_found:           {result['data_found']}")
        print(f"  index_value:          {result['index_value']}")
        print(f"  snapshot_age_days:    {result['snapshot_age_days']}")
        print(f"  index_movement_1w:    {result['index_movement_1w']}")
        print(f"  index_movement_1m:    {result['index_movement_1m']}")
        print(f"  historical_available: {result['historical_available']}")
        print()
