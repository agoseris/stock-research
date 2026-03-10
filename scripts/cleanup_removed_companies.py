"""
cleanup_removed_companies.py

Delete Firestore data for companies that have been removed from the universe.

TTL policies handle: announcements, company_news_summaries, pdmr_transactions,
pending_jobs, signals. This script handles the remaining collections that have
no TTL:

  - signal_results       (queried by ticker field)
  - universe_companies/{ticker}/signal_history   (orphaned subcollection)
  - universe_companies/{ticker}/position_history (orphaned subcollection)

Usage:
    # Preview what will be deleted
    python scripts/cleanup_removed_companies.py small_caps.csv

    # Apply deletions
    python scripts/cleanup_removed_companies.py small_caps.csv --execute
"""

import argparse
import csv
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent
load_dotenv(_REPO_ROOT / ".env")

_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds or not os.path.exists(_creds):
    _fallback = _REPO_ROOT / "frontend" / "gcp-credentials.json"
    if _fallback.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_fallback)

from google.cloud import firestore  # noqa: E402


def load_tickers(path: str) -> list[str]:
    tickers = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "ticker" not in (reader.fieldnames or []):
            print(f"ERROR: CSV must have a 'ticker' column. Found: {reader.fieldnames}")
            sys.exit(1)
        for row in reader:
            ticker = (row.get("ticker") or "").strip().upper()
            if ticker:
                tickers.append(ticker)
    return tickers


def delete_subcollection(db, ticker: str, subcollection: str, dry_run: bool) -> int:
    """Delete all docs in universe_companies/{ticker}/{subcollection}. Returns count."""
    docs = list(
        db.collection("universe_companies")
        .document(ticker)
        .collection(subcollection)
        .stream()
    )
    if not docs:
        return 0
    if not dry_run:
        for doc in docs:
            doc.reference.delete()
    return len(docs)


def delete_signal_results(db, ticker: str, dry_run: bool) -> int:
    """Delete all signal_results docs for a ticker. Returns count."""
    docs = list(
        db.collection("signal_results")
        .where("ticker", "==", ticker)
        .stream()
    )
    if not docs:
        return 0
    if not dry_run:
        for doc in docs:
            doc.reference.delete()
    return len(docs)


def run_cleanup(db, tickers: list[str], dry_run: bool) -> None:
    total = len(tickers)
    prefix = "[DRY RUN] " if dry_run else ""

    totals = {
        "signal_results": 0,
        "signal_history": 0,
        "position_history": 0,
    }

    print(f"\n{prefix}Cleaning up {total} tickers...\n")
    print(f"  {'TICKER':<10}  {'signal_results':>14}  {'signal_history':>14}  {'position_history':>16}")
    print(f"  {'─'*10}  {'─'*14}  {'─'*14}  {'─'*16}")

    for ticker in tickers:
        sr  = delete_signal_results(db, ticker, dry_run)
        sh  = delete_subcollection(db, ticker, "signal_history", dry_run)
        ph  = delete_subcollection(db, ticker, "position_history", dry_run)

        totals["signal_results"]  += sr
        totals["signal_history"]  += sh
        totals["position_history"] += ph

        if sr or sh or ph:
            print(f"  {ticker:<10}  {sr:>14}  {sh:>14}  {ph:>16}")

    print(f"\n{'─' * 60}")
    if dry_run:
        print(f"  Dry run complete.")
        print(f"  Would delete: {totals['signal_results']} signal_results, "
              f"{totals['signal_history']} signal_history, "
              f"{totals['position_history']} position_history docs.")
        print(f"  Re-run with --execute to apply.")
    else:
        print(f"  Done.")
        print(f"  Deleted: {totals['signal_results']} signal_results, "
              f"{totals['signal_history']} signal_history, "
              f"{totals['position_history']} position_history docs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Clean up Firestore data for removed universe companies."
    )
    parser.add_argument(
        "csv_file",
        help="CSV file with a 'ticker' column (same format as find_small_universe_companies.py output).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply deletions. Without this flag, runs in dry-run mode.",
    )
    args = parser.parse_args()

    if not Path(args.csv_file).exists():
        print(f"ERROR: file not found: {args.csv_file}")
        sys.exit(1)

    tickers = load_tickers(args.csv_file)
    if not tickers:
        print("No tickers found in CSV. Nothing to do.")
        sys.exit(0)

    print(f"CSV loaded: {len(tickers)} tickers from {args.csv_file}")

    if args.execute:
        print(f"\nWARNING: This will permanently delete Firestore documents for {len(tickers)} companies.")
        confirm = input("Type YES to confirm: ").strip()
        if confirm != "YES":
            print("Aborted.")
            sys.exit(0)

    db = firestore.Client()
    run_cleanup(db, tickers, dry_run=not args.execute)
