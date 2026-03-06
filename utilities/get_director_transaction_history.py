"""
get_director_transaction_history.py

Returns all known transactions for a specific director at a specific company
from the pdmr_transactions Firestore collection.

Uses the director_name_normalisation utility to match name variations between
the query name and stored names (e.g. "J. Smith" matching "John Smith").

Execution: backend or frontend (Firestore read, no IP sensitivity).

Usage:
    from utilities.get_director_transaction_history import get_director_transaction_history
    result = get_director_transaction_history(
        ticker="FGEN",
        director_name="John Smith",
        exclude_id="rns_article_id_of_current_transaction",
    )
"""

import os
from datetime import datetime, date, timezone
from typing import Optional

from dotenv import load_dotenv

_UTILITIES_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTILITIES_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# Credentials fallback to local frontend credentials if VM path unavailable
_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds_path or not os.path.exists(_creds_path):
    _frontend_creds = os.path.join(_PROJECT_ROOT, "frontend", "gcp-credentials.json")
    if os.path.exists(_frontend_creds):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _frontend_creds

from utilities.director_name_normalisation import normalise_for_storage, names_match

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COLLECTION = "pdmr_transactions"

_OPEN_MARKET_CATEGORIES = {"open_market_purchase", "open_market_disposal"}

# data_maturity thresholds
_SUFFICIENT_THRESHOLD = 3
_SPARSE_MAX = 2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_director_transaction_history(
    ticker: str,
    director_name: str,
    exclude_id: str = None,
    db=None,
) -> dict:
    """
    Return all known transactions for a director at a company.

    Parameters
    ----------
    ticker : str
        LSE ticker (e.g. "FGEN", "LLOY").
    director_name : str
        Raw director name from the current transaction.
        Normalised internally before querying.
    exclude_id : str, optional
        rns_article_id of the current (triggering) transaction.
        Excluded from results to prevent circular self-reference.
    db : Firestore client, optional
        Injected for testing. Created automatically if None.

    Returns
    -------
    dict with keys:
        ticker                  str
        director_name_query     str     raw input name
        director_name_normalised str    normalised form used for matching
        transactions            list    all matched docs, sorted by
                                        transaction_date_actual descending
        total_count             int
        open_market_count       int     open_market_purchase + disposal only
        date_range              dict    earliest, latest (date | None)
        holding_trajectory      str     "building" | "reducing" | "mixed" |
                                        "insufficient_data"
        data_maturity           str     "sufficient" | "sparse" | "empty"
        collection_age_days     int | None  days since collection first populated
        data_note               str     (optional) present when empty + young
    """
    if db is None:
        from google.cloud import firestore
        db = firestore.Client()

    query_normalised = normalise_for_storage(director_name)

    # Fetch all pdmr_transactions for this ticker
    docs = list(
        db.collection(_COLLECTION)
        .where("ticker", "==", ticker)
        .stream()
    )

    # Filter by director name match and exclude triggering transaction
    matched = []
    for doc in docs:
        data = doc.to_dict()
        data["_doc_id"] = doc.id

        if exclude_id and data.get("rns_article_id") == exclude_id:
            continue

        stored_name = (
            data.get("director_name_normalised")
            or data.get("director_name_raw", "")
        )
        match_result = names_match(query_normalised, stored_name)
        if match_result["match"]:
            data["_name_match_method"] = match_result["method"]
            matched.append(data)

    # Sort by transaction_date_actual descending
    matched.sort(key=_tx_sort_key, reverse=True)

    # Counts
    total_count = len(matched)
    open_market_txs = [
        t for t in matched
        if t.get("transaction_category") in _OPEN_MARKET_CATEGORIES
    ]
    open_market_count = len(open_market_txs)

    # Date range
    if matched:
        dates = [_parse_tx_date(t) for t in matched]
        dates = [d for d in dates if d is not None]
        date_range = {
            "earliest": min(dates).date() if dates else None,
            "latest":   max(dates).date() if dates else None,
        }
    else:
        date_range = {"earliest": None, "latest": None}

    holding_trajectory = _classify_holding_trajectory(open_market_txs)
    data_maturity = _classify_data_maturity(total_count)
    collection_age_days = _get_collection_age_days(db)

    result = {
        "ticker":                   ticker,
        "director_name_query":      director_name,
        "director_name_normalised": query_normalised,
        "transactions":             matched,
        "total_count":              total_count,
        "open_market_count":        open_market_count,
        "date_range":               date_range,
        "holding_trajectory":       holding_trajectory,
        "data_maturity":            data_maturity,
        "collection_age_days":      collection_age_days,
    }

    if (
        data_maturity == "empty"
        and collection_age_days is not None
        and collection_age_days < 30
    ):
        result["data_note"] = (
            "Pipeline recently deployed — transaction history will accumulate "
            "over time. Empty result does not indicate this is the director's "
            "first transaction."
        )

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_tx_date(tx: dict) -> Optional[datetime]:
    """Parse transaction_date_actual to datetime (UTC-aware)."""
    raw = tx.get("transaction_date_actual")
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    return None


def _tx_sort_key(tx: dict) -> datetime:
    """Sort key for transactions — min datetime for unparseable dates."""
    dt = _parse_tx_date(tx)
    return dt if dt is not None else datetime.min.replace(tzinfo=timezone.utc)


def _classify_holding_trajectory(open_market_txs: list) -> str:
    """
    Classify holding trajectory from open market transactions.

    Requires at least 2 open market transactions for a meaningful classification.
    Returns "insufficient_data" if fewer than 2 transactions exist.
    """
    if len(open_market_txs) < 2:
        return "insufficient_data"

    purchases = [
        t for t in open_market_txs
        if t.get("transaction_category") == "open_market_purchase"
    ]
    disposals = [
        t for t in open_market_txs
        if t.get("transaction_category") == "open_market_disposal"
    ]

    if purchases and not disposals:
        return "building"
    if disposals and not purchases:
        return "reducing"
    return "mixed"


def _classify_data_maturity(total_count: int) -> str:
    if total_count == 0:
        return "empty"
    if total_count <= _SPARSE_MAX:
        return "sparse"
    return "sufficient"


def _get_collection_age_days(db) -> Optional[int]:
    """
    Calculate days since the pdmr_transactions collection was first populated.
    Uses the oldest stored_at timestamp across the entire collection.
    Returns None if collection is empty or stored_at is absent.
    """
    try:
        docs = list(
            db.collection(_COLLECTION)
            .order_by("stored_at")
            .limit(1)
            .stream()
        )
        if not docs:
            return None
        data = docs[0].to_dict()
        stored_at = data.get("stored_at")
        if stored_at is None:
            return None
        if isinstance(stored_at, datetime):
            now = datetime.now(timezone.utc)
            if stored_at.tzinfo is None:
                stored_at = stored_at.replace(tzinfo=timezone.utc)
            return max(0, (now - stored_at).days)
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("get_director_transaction_history — smoke test\n")
    print("(pdmr_transactions collection starts empty in PoC)")
    print("Querying for a ticker with no known transactions:\n")

    result = get_director_transaction_history(
        ticker="LLOY",
        director_name="John Smith",
        exclude_id=None,
    )
    print(f"  ticker:                {result['ticker']}")
    print(f"  director_normalised:   {result['director_name_normalised']}")
    print(f"  total_count:           {result['total_count']}")
    print(f"  data_maturity:         {result['data_maturity']}")
    print(f"  holding_trajectory:    {result['holding_trajectory']}")
    print(f"  collection_age_days:   {result['collection_age_days']}")
    if "data_note" in result:
        print(f"  data_note:             {result['data_note']}")
