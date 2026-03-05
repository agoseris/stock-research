"""
get_company_insider_activity.py

Returns all recent open market transactions across ALL directors at a company
to detect corroborating insider momentum.

Only open_market_purchase and open_market_disposal transactions are returned.
Plan purchases and awards are excluded.

Cluster detection: true if 2+ distinct directors (excluding the triggering
director) made purchases within any rolling 30-day window.

Execution: backend or frontend (Firestore read, no IP sensitivity).

Usage:
    from utilities.get_company_insider_activity import get_company_insider_activity
    result = get_company_insider_activity(
        ticker="FGEN",
        days_lookback=90,
        exclude_id="rns_article_id_of_current_transaction",
    )
"""

import os
from datetime import datetime, date, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv

_UTILITIES_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTILITIES_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, "backend", ".env"))

# Credentials fallback to local frontend credentials if VM path unavailable
_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds_path or not os.path.exists(_creds_path):
    _frontend_creds = os.path.join(_PROJECT_ROOT, "frontend", "gcp-credentials.json")
    if os.path.exists(_frontend_creds):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _frontend_creds

from utilities.director_name_normalisation import normalise_for_storage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COLLECTION = "pdmr_transactions"
_OPEN_MARKET_CATEGORIES = {"open_market_purchase", "open_market_disposal"}
_CLUSTER_WINDOW_DAYS = 30
_SUFFICIENT_THRESHOLD = 3
_SPARSE_MAX = 2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_company_insider_activity(
    ticker: str,
    days_lookback: int = 90,
    exclude_id: str = None,
    db=None,
) -> dict:
    """
    Return all recent open market transactions across all directors at a company.

    Parameters
    ----------
    ticker : str
        LSE ticker (e.g. "FGEN", "LLOY").
    days_lookback : int
        Lookback window in days. Default 90.
        Only transactions with transaction_date_actual >= (now - days_lookback)
        are returned.
    exclude_id : str, optional
        rns_article_id of the current (triggering) transaction.
        Excluded from results; triggering director excluded from cluster detection.
    db : Firestore client, optional
        Injected for testing. Created automatically if None.

    Returns
    -------
    dict with keys:
        ticker              str
        days_lookback       int
        transactions        list    open_market only, within lookback,
                                    sorted by transaction_date_actual descending
        buy_count           int
        sell_count          int
        buying_directors    list    distinct normalised names of buying directors
        selling_directors   list    distinct normalised names of selling directors
        net_sentiment       str     "buying" | "selling" | "mixed" | "none"
        cluster_detected    bool
        cluster_details     list    [{directors, window_start, window_end,
                                      transaction_count}] — empty if no cluster
        data_maturity       str     "sufficient" | "sparse" | "empty"
    """
    if db is None:
        from google.cloud import firestore
        db = firestore.Client()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_lookback)

    # Fetch all pdmr_transactions for this ticker
    all_docs = list(
        db.collection(_COLLECTION)
        .where("ticker", "==", ticker)
        .stream()
    )
    all_data = []
    for doc in all_docs:
        data = doc.to_dict()
        data["_doc_id"] = doc.id
        all_data.append(data)

    # Identify triggering director (for cluster exclusion)
    triggering_director_normalised = None
    if exclude_id:
        for data in all_data:
            if data.get("rns_article_id") == exclude_id:
                triggering_director_normalised = _get_director_name(data)
                break

    # Filter: open_market only, within lookback, exclude triggering transaction
    filtered = []
    for data in all_data:
        if exclude_id and data.get("rns_article_id") == exclude_id:
            continue
        if data.get("transaction_category") not in _OPEN_MARKET_CATEGORIES:
            continue
        tx_date = _parse_tx_date(data)
        if tx_date is not None and tx_date < cutoff:
            continue
        filtered.append(data)

    # Sort by transaction_date_actual descending
    filtered.sort(key=_tx_sort_key, reverse=True)

    # Counts and director lists
    purchases = [t for t in filtered if t.get("transaction_category") == "open_market_purchase"]
    disposals = [t for t in filtered if t.get("transaction_category") == "open_market_disposal"]

    buy_count = len(purchases)
    sell_count = len(disposals)

    buying_directors = sorted({_get_director_name(t) for t in purchases if _get_director_name(t)})
    selling_directors = sorted({_get_director_name(t) for t in disposals if _get_director_name(t)})

    net_sentiment = _classify_net_sentiment(buy_count, sell_count)
    data_maturity = _classify_data_maturity(len(filtered))

    # Cluster detection — purchases by non-triggering directors only
    cluster_purchases = [
        t for t in purchases
        if _get_director_name(t) != triggering_director_normalised
    ]
    cluster_detected, cluster_details = _detect_cluster(cluster_purchases)

    return {
        "ticker":             ticker,
        "days_lookback":      days_lookback,
        "transactions":       filtered,
        "buy_count":          buy_count,
        "sell_count":         sell_count,
        "buying_directors":   buying_directors,
        "selling_directors":  selling_directors,
        "net_sentiment":      net_sentiment,
        "cluster_detected":   cluster_detected,
        "cluster_details":    cluster_details,
        "data_maturity":      data_maturity,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_director_name(tx: dict) -> Optional[str]:
    """Return normalised director name from a transaction document."""
    return tx.get("director_name_normalised") or tx.get("director_name_raw") or None


def _parse_tx_date(tx: dict) -> Optional[datetime]:
    """Parse transaction_date_actual to UTC-aware datetime."""
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
    """Sort key — min datetime for unparseable dates."""
    dt = _parse_tx_date(tx)
    return dt if dt is not None else datetime.min.replace(tzinfo=timezone.utc)


def _classify_net_sentiment(buy_count: int, sell_count: int) -> str:
    if buy_count == 0 and sell_count == 0:
        return "none"
    if buy_count > 0 and sell_count == 0:
        return "buying"
    if sell_count > 0 and buy_count == 0:
        return "selling"
    return "mixed"


def _classify_data_maturity(total_open_market_count: int) -> str:
    if total_open_market_count == 0:
        return "empty"
    if total_open_market_count <= _SPARSE_MAX:
        return "sparse"
    return "sufficient"


def _detect_cluster(purchases: list) -> tuple:
    """
    Detect rolling 30-day windows where 2+ distinct directors made purchases.

    Algorithm: sort purchases by date ascending. For each purchase i, collect
    all purchases j where j.date is within 30 days of i.date. If any window
    contains 2+ distinct directors, a cluster is detected.

    Returns (cluster_detected: bool, cluster_details: list).
    cluster_details contains one entry per distinct cluster found (deduplicated
    by director set). Each entry has: directors, window_start, window_end,
    transaction_count.
    """
    if len(purchases) < 2:
        return False, []

    sorted_p = sorted(purchases, key=_tx_sort_key)
    seen_director_sets = set()
    cluster_details = []

    for i, tx_i in enumerate(sorted_p):
        dt_i = _parse_tx_date(tx_i)
        if dt_i is None:
            continue

        window_end_dt = dt_i + timedelta(days=_CLUSTER_WINDOW_DAYS)

        # Collect all purchases in this 30-day window starting at tx_i
        window_txs = []
        for tx_j in sorted_p[i:]:
            dt_j = _parse_tx_date(tx_j)
            if dt_j is None:
                continue
            if dt_j > window_end_dt:
                break
            window_txs.append(tx_j)

        # Check for 2+ distinct directors in this window
        directors_in_window = {
            _get_director_name(t) for t in window_txs
            if _get_director_name(t)
        }

        if len(directors_in_window) >= 2:
            # Deduplicate: same set of directors → same cluster
            frozen_key = frozenset(directors_in_window)
            if frozen_key not in seen_director_sets:
                seen_director_sets.add(frozen_key)
                window_dates = [_parse_tx_date(t) for t in window_txs if _parse_tx_date(t)]
                cluster_details.append({
                    "directors":         sorted(directors_in_window),
                    "window_start":      dt_i.date(),
                    "window_end":        max(window_dates).date(),
                    "transaction_count": len(window_txs),
                })

    return bool(cluster_details), cluster_details


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("get_company_insider_activity — smoke test\n")
    print("(pdmr_transactions collection starts empty in PoC)")

    result = get_company_insider_activity(
        ticker="LLOY",
        days_lookback=90,
    )
    print(f"  ticker:           {result['ticker']}")
    print(f"  buy_count:        {result['buy_count']}")
    print(f"  sell_count:       {result['sell_count']}")
    print(f"  net_sentiment:    {result['net_sentiment']}")
    print(f"  cluster_detected: {result['cluster_detected']}")
    print(f"  data_maturity:    {result['data_maturity']}")
