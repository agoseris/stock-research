"""
remove_universe_companies.py

Bulk-remove companies from universe_companies using a CSV produced by
find_small_universe_companies.py (or any CSV with a 'ticker' column).

Dry-run by default — pass --execute to apply deletions.

After running with --execute, restart job_runner on the VM so its
in-memory universe ticker set reflects the pruned universe:
    systemctl restart job_runner

Usage:
    # Preview what will be deleted
    python scripts/remove_universe_companies.py small_caps.csv

    # Apply deletions
    python scripts/remove_universe_companies.py small_caps.csv --execute
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


def load_csv(path: str) -> list[dict]:
    """Read the CSV and return a list of row dicts. Requires a 'ticker' column."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "ticker" not in (reader.fieldnames or []):
            print(f"ERROR: CSV must have a 'ticker' column. Found: {reader.fieldnames}")
            sys.exit(1)
        for row in reader:
            ticker = (row.get("ticker") or "").strip().upper()
            if ticker:
                rows.append(row)
    return rows


def print_preview(rows: list[dict]) -> None:
    """Print a preview table of companies to be removed."""
    print(f"\n  {'TICKER':<8}  {'MARKET CAP':>10}  {'EXCHANGE':<10}  {'MUTED':<6}  COMPANY")
    print(f"  {'─'*8}  {'─'*10}  {'─'*10}  {'─'*6}  {'─'*40}")
    for row in rows:
        ticker   = (row.get("ticker") or "").upper()
        cap      = row.get("market_cap_fmt") or "—"
        exchange = row.get("listing_exchange") or "?"
        muted    = "yes" if str(row.get("muted", "")).lower() in ("true", "1", "yes") else "no"
        name     = (row.get("company_name") or "")[:50]
        print(f"  {ticker:<8}  {cap:>10}  {exchange:<10}  {muted:<6}  {name}")


def remove_companies(db, rows: list[dict], dry_run: bool) -> None:
    total   = len(rows)
    deleted = 0
    failed  = 0

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Removing {total} companies from universe_companies...\n")

    for i, row in enumerate(rows, 1):
        ticker = (row.get("ticker") or "").strip().upper()
        name   = (row.get("company_name") or "")[:40]

        if dry_run:
            print(f"  [{i:3d}/{total}] would delete  {ticker:<8}  {name}")
        else:
            try:
                db.collection("universe_companies").document(ticker).delete()
                deleted += 1
                print(f"  [{i:3d}/{total}] deleted       {ticker:<8}  {name}")
            except Exception as e:
                failed += 1
                print(f"  [{i:3d}/{total}] FAILED        {ticker:<8}  {e}")

    print(f"\n{'─' * 60}")
    if dry_run:
        print(f"  Dry run complete. {total} companies would be removed.")
        print(f"  Re-run with --execute to apply.")
    else:
        print(f"  Done. {deleted} deleted, {failed} failed.")
        if deleted:
            print(f"\n  Restart job_runner on the VM to update its in-memory universe:")
            print(f"    systemctl restart job_runner")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bulk-remove universe companies from a CSV list."
    )
    parser.add_argument(
        "csv_file",
        help="Path to CSV file with a 'ticker' column (output of find_small_universe_companies.py).",
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

    rows = load_csv(args.csv_file)
    if not rows:
        print("No rows found in CSV. Nothing to do.")
        sys.exit(0)

    print(f"CSV loaded: {len(rows)} companies from {args.csv_file}")
    print_preview(rows)

    # Count breakdowns
    muted_count = sum(
        1 for r in rows
        if str(r.get("muted", "")).lower() in ("true", "1", "yes")
    )
    exchanges: dict[str, int] = {}
    for r in rows:
        ex = r.get("listing_exchange") or "?"
        exchanges[ex] = exchanges.get(ex, 0) + 1

    print(f"\n  Total:  {len(rows)}")
    print(f"  Muted:  {muted_count}  (already excluded from pipeline)")
    for ex, n in sorted(exchanges.items()):
        print(f"  {ex}:  {n}")

    if args.execute:
        print(f"\nWARNING: This will permanently delete {len(rows)} documents from universe_companies.")
        confirm = input("Type YES to confirm: ").strip()
        if confirm != "YES":
            print("Aborted.")
            sys.exit(0)

    db = firestore.Client()
    remove_companies(db, rows, dry_run=not args.execute)
