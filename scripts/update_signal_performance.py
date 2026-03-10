"""
update_signal_performance.py

Daily cron script that maintains signal performance snapshots in Firestore.

For every eligible signal it records closing prices at standard offsets
relative to the signal date, plus the concurrent benchmark index level,
allowing lens accuracy to be measured retrospectively.

Eligible signals
----------------
  signals collection (director_buying, tr1_crossing):
    simple_lens_result.recommendation in ("investigate", "monitor")
    investor_action != "dismissed"

  signal_results collection (regulatory_catalyst):
    signal_strength in ("strong", "moderate")
    dismissed != True

Snapshot windows
----------------
  Pre-signal:  pre_90d (-90d baseline), pre_30d, pre_1w
  At signal:   baseline (0d)
  Post-signal: post_1d … post_7d (daily — captures the first-week reaction),
               post_2w, post_1m, post_3m, post_6m, post_12m

A snapshot is "due" when today >= signal_date + offset and "pending" when it
has not yet been recorded.  Already-filled snapshots are never overwritten.

Price source: yfinance ({ticker}.L), closing prices in pence (GBp).
Index source: ^FTAI (AIM), ^FTSC (FTSE Small Cap), ^FTMC (FTSE 250).

Execution: local machine only (yfinance IP-sensitive rate limiting).
Cron:  0 18 * * 1-5   # weekday evenings after UK market close

Usage:
    python scripts/update_signal_performance.py            # live run
    python scripts/update_signal_performance.py --dry-run  # preview only
    python scripts/update_signal_performance.py --ticker NCC  # one ticker
"""

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent
load_dotenv(_REPO_ROOT / ".env")

_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds or not os.path.exists(_creds):
    _fallback = _REPO_ROOT / "frontend" / "gcp-credentials.json"
    if _fallback.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_fallback)

from google.cloud import firestore  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLLECTION_SIGNALS        = "signals"
COLLECTION_SIGNAL_RESULTS = "signal_results"
COLLECTION_PERFORMANCE    = "signal_performance"
COLLECTION_UNIVERSE       = "universe_companies"

# Days from signal date for each snapshot (negative = before signal)
SNAPSHOT_OFFSETS: dict[str, int] = {
    "pre_90d":  -90,
    "pre_30d":  -30,
    "pre_1w":    -7,
    "baseline":   0,
    "post_1d":    1,
    "post_2d":    2,
    "post_3d":    3,
    "post_4d":    4,
    "post_5d":    5,
    "post_6d":    6,
    "post_7d":    7,
    "post_2w":   14,
    "post_1m":   30,
    "post_3m":   90,
    "post_6m":  180,
    "post_12m": 365,
}

# How far back to fetch yfinance history to cover all pre-signal windows
_HISTORY_LOOKBACK_DAYS = 400  # covers pre_90d + some buffer

# yfinance rate limit
_RATE_LIMIT_SECONDS = 2

# Index ticker selection
_INDEX_BY_EXCHANGE = {
    "AIM": "^FTAI",
}
_INDEX_MAIN_LARGE  = "^FTMC"   # FTSE 250  (market_cap_gbp >= 500M)
_INDEX_MAIN_SMALL  = "^FTSC"   # FTSE Small Cap
_INDEX_DEFAULT     = "^FTSC"


# ---------------------------------------------------------------------------
# Index selection
# ---------------------------------------------------------------------------

def _index_ticker(listing_exchange: str | None, market_cap_gbp: float | None) -> str:
    ex = (listing_exchange or "").upper()
    if ex == "AIM":
        return "^FTAI"
    if ex in ("LSE_MAIN", "MAIN"):
        cap = float(market_cap_gbp) if market_cap_gbp else 0
        return _INDEX_MAIN_LARGE if cap >= 500_000_000 else _INDEX_MAIN_SMALL
    return _INDEX_DEFAULT


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _to_date(val) -> date | None:
    """Convert a Firestore Timestamp, datetime, date, or ISO string to date."""
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        s = str(val).replace("Z", "+00:00")
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def _nearest_price(hist: pd.DataFrame, target: date, direction: str = "nearest") -> tuple[date | None, float | None]:
    """
    Find the closing price nearest to target in a yfinance history DataFrame.

    direction:
        "back"    — use the most recent available date <= target (pre-signal)
        "forward" — use the earliest available date >= target (post-signal / baseline)
        "nearest" — closest in either direction
    """
    if hist is None or hist.empty:
        return None, None

    # Normalise index to date objects
    idx = hist.index
    if hasattr(idx[0], "date"):
        dates = [d.date() for d in idx]
    else:
        dates = list(idx)

    date_series = pd.Series(dates, index=range(len(dates)))

    if direction == "back":
        candidates = [(d, i) for i, d in enumerate(dates) if d <= target]
        if not candidates:
            return None, None
        actual_date, row_i = max(candidates, key=lambda x: x[0])
    elif direction == "forward":
        candidates = [(d, i) for i, d in enumerate(dates) if d >= target]
        if not candidates:
            return None, None
        actual_date, row_i = min(candidates, key=lambda x: x[0])
    else:  # nearest
        if not dates:
            return None, None
        deltas = [(abs((d - target).days), i, d) for i, d in enumerate(dates)]
        _, row_i, actual_date = min(deltas)

    price = float(hist.iloc[row_i]["Close"])
    return actual_date, price


def _direction_for_offset(offset_days: int) -> str:
    if offset_days < 0:
        return "back"
    return "forward"


# ---------------------------------------------------------------------------
# yfinance fetching (with caching for index series)
# ---------------------------------------------------------------------------

_index_cache: dict[str, pd.DataFrame] = {}


def _fetch_history(yf_ticker: str, days_back: int = _HISTORY_LOOKBACK_DAYS) -> pd.DataFrame | None:
    """Fetch daily close history from yfinance. Sleeps for rate limiting."""
    try:
        start = (datetime.now(tz=timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        ticker_obj = yf.Ticker(yf_ticker)
        hist = ticker_obj.history(start=start, interval="1d")
        time.sleep(_RATE_LIMIT_SECONDS)
        if hist.empty:
            return None
        return hist
    except Exception as e:
        print(f"    yfinance error for {yf_ticker}: {e}")
        time.sleep(_RATE_LIMIT_SECONDS)
        return None


def _get_index_history(index_yf: str) -> pd.DataFrame | None:
    """Fetch (and cache) index history for the session."""
    if index_yf not in _index_cache:
        print(f"  Fetching index history: {index_yf}")
        hist = _fetch_history(index_yf, days_back=_HISTORY_LOOKBACK_DAYS)
        _index_cache[index_yf] = hist  # None stored if fetch failed
    return _index_cache[index_yf]


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def _eligible_signal(data: dict) -> bool:
    """Is a doc from the signals collection eligible for performance tracking?"""
    if (data.get("investor_action") or "") == "dismissed":
        return False
    rec = (
        (data.get("simple_lens_result") or {}).get("recommendation")  # TR-1
        or data.get("simple_recommended_action")                        # director buying
        or data.get("recommendation")
        or ""
    ).lower()
    return rec in ("investigate", "monitor")


def _parse_recommended_action(llm_analysis: str) -> str:
    """
    Extract RECOMMENDED_ACTION from raw LLM analysis text.
    Returns 'yes', 'monitor', or 'no' (default).
    """
    for line in (llm_analysis or "").splitlines():
        if "RECOMMENDED_ACTION" in line.upper():
            val = line.split(":", 1)[-1].strip().lower()
            if val.startswith("yes"):
                return "yes"
            if val.startswith("monitor"):
                return "monitor"
            return "no"
    return "no"


def _eligible_signal_result(data: dict) -> bool:
    """
    Is a doc from signal_results eligible for performance tracking?

    signal_results docs do not store signal_strength — that is only written
    to the signal_history subcollection by pipeline.py. Eligibility is
    determined by parsing RECOMMENDED_ACTION from the raw llm_analysis text.
    """
    if data.get("dismissed", False):
        return False
    action = _parse_recommended_action(data.get("llm_analysis") or "")
    return action in ("yes", "monitor")


# ---------------------------------------------------------------------------
# Signal date extraction
# ---------------------------------------------------------------------------

def _signal_date(data: dict, collection: str) -> date | None:
    """Extract the signal date from a document."""
    # Prefer the announcement publication date (closest to the market event)
    for field in ("announcement_published_at", "published_at", "stored_at", "created_at"):
        d = _to_date(data.get(field))
        if d:
            return d
    return None


# ---------------------------------------------------------------------------
# Snapshot computation
# ---------------------------------------------------------------------------

def _compute_snapshots(
    signal_date: date,
    ticker_hist: pd.DataFrame,
    index_hist: pd.DataFrame | None,
    existing_snapshots: dict,
    dry_run: bool,
) -> dict:
    """
    For each snapshot window that is (a) now due and (b) not yet recorded,
    compute the price and index values.

    Returns a dict of new/updated snapshot entries only.
    """
    today = date.today()
    new_snapshots = {}

    # Resolve baseline price first (needed for % change calculations)
    baseline_existing = existing_snapshots.get("baseline", {})
    baseline_price = baseline_existing.get("price_p") if baseline_existing else None

    # We may need to compute baseline as part of this pass
    baseline_target = signal_date + timedelta(days=0)
    if baseline_price is None and baseline_target <= today:
        actual_date, price = _nearest_price(ticker_hist, baseline_target, "forward")
        if price:
            baseline_price = price

    for key, offset in SNAPSHOT_OFFSETS.items():
        target_date = signal_date + timedelta(days=offset)

        # Not due yet
        if target_date > today:
            continue

        # Already recorded
        if existing_snapshots.get(key, {}).get("price_p") is not None:
            continue

        direction = _direction_for_offset(offset)
        actual_date, price = _nearest_price(ticker_hist, target_date, direction)

        if price is None:
            continue

        snapshot: dict = {
            "target_date":  target_date.isoformat(),
            "actual_date":  actual_date.isoformat() if actual_date else None,
            "price_p":      round(price, 2),
        }

        # % change vs baseline
        if baseline_price and key != "baseline":
            snapshot["pct_vs_baseline"] = round((price - baseline_price) / baseline_price * 100, 2)
        else:
            snapshot["pct_vs_baseline"] = None

        # Index level and relative performance
        if index_hist is not None:
            idx_date, idx_level = _nearest_price(index_hist, target_date, direction)
            idx_base_date, idx_base = _nearest_price(index_hist, signal_date, "forward")
            snapshot["index_level"] = round(idx_level, 2) if idx_level else None

            if idx_base and idx_level and key != "baseline":
                idx_pct = (idx_level - idx_base) / idx_base * 100
                if snapshot.get("pct_vs_baseline") is not None:
                    snapshot["pct_vs_index"] = round(snapshot["pct_vs_baseline"] - idx_pct, 2)
                else:
                    snapshot["pct_vs_index"] = None
            else:
                snapshot["pct_vs_index"] = None
        else:
            snapshot["index_level"] = None
            snapshot["pct_vs_index"] = None

        new_snapshots[key] = snapshot

    return new_snapshots


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------

def _load_existing_performance(db) -> dict[str, dict]:
    """Load all existing signal_performance docs keyed by doc_id."""
    docs = db.collection(COLLECTION_PERFORMANCE).stream()
    return {doc.id: doc.to_dict() for doc in docs}


def _load_universe_map(db) -> dict[str, dict]:
    """Load universe_companies into a ticker → data dict."""
    docs = db.collection(COLLECTION_UNIVERSE).stream()
    result = {}
    for doc in docs:
        data = doc.to_dict() or {}
        ticker = (data.get("ticker_lse") or doc.id or "").upper()
        if ticker:
            result[ticker] = data
    return result


def _upsert_performance(db, doc_id: str, update: dict, dry_run: bool) -> None:
    if dry_run:
        return
    db.collection(COLLECTION_PERFORMANCE).document(doc_id).set(update, merge=True)


def _delete_performance(db, doc_id: str, dry_run: bool) -> None:
    if dry_run:
        return
    db.collection(COLLECTION_PERFORMANCE).document(doc_id).delete()


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def _process_signal(
    doc_id: str,
    data: dict,
    collection: str,
    existing_perf: dict,
    universe_map: dict,
    db,
    dry_run: bool,
    stats: dict,
) -> None:
    ticker = (data.get("ticker") or "").upper()
    if not ticker:
        return

    signal_date = _signal_date(data, collection)
    if not signal_date:
        print(f"  [{ticker}] {doc_id[:16]}… no signal date — skipping")
        return

    # Company info for index selection
    company = universe_map.get(ticker, {})
    exchange   = company.get("listing_exchange") or data.get("listing_exchange") or ""
    mkt_cap    = company.get("market_cap_gbp")
    index_yf   = _index_ticker(exchange, mkt_cap)

    # Lens type and signal strength
    if collection == COLLECTION_SIGNALS:
        lens_type  = data.get("signal_type") or "unknown"
        simple     = data.get("simple_lens_result") or {}
        strength   = simple.get("signal_strength") or data.get("signal_strength") or ""
        rec        = (simple.get("recommendation")              # TR-1
                      or data.get("simple_recommended_action")  # director buying
                      or "")
    else:
        lens_type  = "regulatory_catalyst"
        strength   = data.get("signal_strength") or ""
        rec        = _parse_recommended_action(data.get("llm_analysis") or "")

    # Existing performance doc
    existing = existing_perf.get(doc_id, {})
    existing_snapshots = existing.get("snapshots", {})

    # Determine which snapshots are due and missing
    today = date.today()
    due_missing = [
        key for key, offset in SNAPSHOT_OFFSETS.items()
        if (signal_date + timedelta(days=offset)) <= today
        and existing_snapshots.get(key, {}).get("price_p") is None
    ]

    if not due_missing:
        stats["already_complete"] += 1
        return

    # Fetch ticker price history
    yf_ticker = f"{ticker}.L"
    ticker_hist = _fetch_history(yf_ticker)
    if ticker_hist is None or ticker_hist.empty:
        print(f"  [{ticker}] no yfinance data — skipping")
        stats["skipped_no_price"] += 1
        return

    # Fetch index history (cached)
    index_hist = _get_index_history(index_yf)

    # Compute new snapshots
    new_snapshots = _compute_snapshots(
        signal_date, ticker_hist, index_hist, existing_snapshots, dry_run
    )

    if not new_snapshots:
        stats["already_complete"] += 1
        return

    # Merge new snapshots into existing and write as a proper nested map.
    # NOTE: set(..., merge=True) with dot-notation keys stores them as literal
    # field names, not nested paths — only update() supports dot-notation.
    # Writing the full snapshots dict avoids that pitfall entirely.
    merged_snapshots = dict(existing_snapshots)
    merged_snapshots.update(new_snapshots)

    update: dict = {
        "ticker":             ticker,
        "company_name":       data.get("company_name") or company.get("company_name") or "",
        "lens_type":          lens_type,
        "signal_strength":    strength,
        "recommendation":     rec,
        "signal_date":        signal_date.isoformat(),
        "listing_exchange":   exchange,
        "index_ticker":       index_yf,
        "source_collection":  collection,
        "last_updated":       datetime.now(tz=timezone.utc),
        "snapshots":          merged_snapshots,
    }

    snapshot_keys = sorted(new_snapshots.keys())
    flag = "[DRY RUN] " if dry_run else ""
    print(
        f"  {flag}[{ticker}] {lens_type} | {signal_date} | "
        f"filling {len(new_snapshots)} snapshots: {', '.join(snapshot_keys)}"
    )

    _upsert_performance(db, doc_id, update, dry_run)
    stats["updated"] += 1


def _cleanup_dismissed(db, existing_perf: dict, active_ids: set, dry_run: bool, stats: dict) -> None:
    """Delete signal_performance docs whose parent signal no longer exists or is dismissed."""
    stale = set(existing_perf.keys()) - active_ids
    for doc_id in stale:
        flag = "[DRY RUN] " if dry_run else ""
        print(f"  {flag}Removing stale performance doc: {doc_id[:24]}…")
        _delete_performance(db, doc_id, dry_run)
        stats["cleaned_up"] += 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(dry_run: bool, ticker_filter: str | None, debug: bool = False) -> None:
    db = firestore.Client()
    today = date.today()

    print(f"{'[DRY RUN] ' if dry_run else ''}update_signal_performance — {today}\n")

    print("Loading data from Firestore...")
    existing_perf  = _load_existing_performance(db)
    universe_map   = _load_universe_map(db)
    print(f"  {len(existing_perf)} existing performance docs")
    print(f"  {len(universe_map)} universe companies")

    stats = {
        "eligible":        0,
        "updated":         0,
        "already_complete": 0,
        "skipped_no_price": 0,
        "cleaned_up":      0,
    }

    active_signal_ids: set[str] = set()

    # --- signals collection (director_buying, tr1_crossing) ---
    print(f"\nScanning {COLLECTION_SIGNALS}...")
    sig_docs = list(db.collection(COLLECTION_SIGNALS).stream())
    print(f"  {len(sig_docs)} total documents")

    if debug:
        from collections import Counter
        rec_counts: Counter = Counter()
        dismissed_count = 0
        for doc in sig_docs:
            d = doc.to_dict() or {}
            if (d.get("investor_action") or "") == "dismissed":
                dismissed_count += 1
            rec = ((d.get("simple_lens_result") or {}).get("recommendation")
                   or d.get("recommendation") or "—").lower()
            sig_type = d.get("signal_type") or "unknown"
            rec_counts[f"{sig_type} / {rec}"] += 1
        print(f"  dismissed: {dismissed_count}")
        print(f"  recommendation breakdown:")
        for k, v in sorted(rec_counts.items()):
            print(f"    {k}: {v}")

    for doc in sig_docs:
        data = doc.to_dict() or {}
        ticker = (data.get("ticker") or "").upper()
        if ticker_filter and ticker != ticker_filter.upper():
            continue

        if ticker_filter:
            # Verbose trace for targeted debugging
            investor_action = data.get("investor_action") or ""
            simple_rec  = (data.get("simple_lens_result") or {}).get("recommendation") or ""
            simple_act  = data.get("simple_recommended_action") or ""
            sig_type    = data.get("signal_type") or ""
            already_perf = doc.id in existing_perf
            eligible    = _eligible_signal(data)
            print(f"  doc={doc.id[:20]}  signal_type={sig_type}")
            print(f"    investor_action={investor_action!r}")
            print(f"    simple_lens_result.recommendation={simple_rec!r}")
            print(f"    simple_recommended_action={simple_act!r}")
            print(f"    already_in_performance={already_perf}")
            print(f"    eligible={eligible}")

        if not _eligible_signal(data):
            continue
        stats["eligible"] += 1
        active_signal_ids.add(doc.id)
        _process_signal(
            doc.id, data, COLLECTION_SIGNALS,
            existing_perf, universe_map, db, dry_run, stats,
        )

    # --- signal_results collection (regulatory_catalyst) ---
    print(f"\nScanning {COLLECTION_SIGNAL_RESULTS}...")
    sr_docs = list(db.collection(COLLECTION_SIGNAL_RESULTS).stream())
    print(f"  {len(sr_docs)} total documents")

    if debug:
        from collections import Counter
        sr_action_counts: Counter = Counter()
        sr_dismissed = 0
        for doc in sr_docs:
            d = doc.to_dict() or {}
            if d.get("dismissed", False):
                sr_dismissed += 1
            action = _parse_recommended_action(d.get("llm_analysis") or "")
            sr_action_counts[action] += 1
        print(f"  dismissed: {sr_dismissed}")
        print(f"  RECOMMENDED_ACTION breakdown:")
        for k, v in sorted(sr_action_counts.items()):
            print(f"    {k}: {v}")

    for doc in sr_docs:
        data = doc.to_dict() or {}
        ticker = (data.get("ticker") or "").upper()
        if ticker_filter and ticker != ticker_filter.upper():
            continue
        if not _eligible_signal_result(data):
            continue
        stats["eligible"] += 1
        active_signal_ids.add(doc.id)
        _process_signal(
            doc.id, data, COLLECTION_SIGNAL_RESULTS,
            existing_perf, universe_map, db, dry_run, stats,
        )

    # --- Clean up dismissed / deleted signals ---
    if not ticker_filter:
        print(f"\nChecking for stale performance docs...")
        _cleanup_dismissed(db, existing_perf, active_signal_ids, dry_run, stats)

    # --- Summary ---
    print(f"\n{'─' * 60}")
    print(f"  Eligible signals:    {stats['eligible']}")
    print(f"  Updated:             {stats['updated']}")
    print(f"  Already complete:    {stats['already_complete']}")
    print(f"  Skipped (no price):  {stats['skipped_no_price']}")
    print(f"  Stale docs removed:  {stats['cleaned_up']}")
    if dry_run:
        print(f"\n  Re-run without --dry-run to apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Maintain signal performance snapshots in Firestore."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be written without touching Firestore.",
    )
    parser.add_argument(
        "--ticker",
        metavar="TICKER",
        default=None,
        help="Process only signals for this ticker (useful for testing).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print signal_strength / recommendation breakdown to diagnose eligibility.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run, ticker_filter=args.ticker, debug=args.debug)
