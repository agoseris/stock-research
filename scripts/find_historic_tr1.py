"""
find_historic_tr1.py

Find TR-1 / "Holding(s) in Company" announcements in the Firestore
announcements collection that were ingested before the tr1_crossing
article type existed (i.e. classified as administrative or unclassified).

To re-process them through the TR-1 lens:
  1. Run this script to identify candidates.
  2. Run with --delete to clear their deduplication records.
  3. Re-submit the source URLs via the Streamlit Ingest tab — job_runner
     will re-classify them as tr1_crossing and run the lens.

TR-1 headlines match one or both of:
  - Starts with "TR-1:" (e.g. "TR-1: Notification of major holdings")
  - Contains "Holding(s) in Company"

Usage:
    # List candidates (no changes made)
    python scripts/find_historic_tr1.py

    # Also write a CSV with source URLs for easy reference
    python scripts/find_historic_tr1.py --csv tr1_backlog.csv

    # Delete dedup records so articles can be re-ingested
    python scripts/find_historic_tr1.py --delete

    # Limit to a specific ticker
    python scripts/find_historic_tr1.py --ticker AMS
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# Headline matching
# ---------------------------------------------------------------------------

def _is_tr1_headline(headline: str) -> bool:
    """Return True if the headline matches the TR-1 / significant shareholder pattern."""
    if not headline:
        return False
    h = headline.lower()
    return h.startswith("tr-1:") or "holding(s) in company" in h


def _coerce_published_at(value) -> str:
    """Convert Firestore Timestamp / datetime / str to a display string."""
    if value is None:
        return "—"
    if isinstance(value, str):
        return value[:19].replace("T", " ")
    try:
        return value.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def find_tr1_announcements(
    db,
    ticker_filter: str | None = None,
    include_already_classified: bool = False,
) -> list[dict]:
    """
    Scan the announcements collection for TR-1 candidates.

    Parameters
    ----------
    db : Firestore client
    ticker_filter : str | None
        If set, only return records for this ticker (case-insensitive).
    include_already_classified : bool
        If True, also return records already classified as tr1_crossing.
        Default False — those are already processed.

    Returns
    -------
    list[dict]
        Each dict has: doc_id, ticker, company_name, headline, source_url,
        published_at (str), article_type, has_source_url.
    """
    print("Scanning announcements collection...")
    docs = list(db.collection("announcements").stream())
    print(f"  {len(docs)} documents fetched.")

    candidates = []
    for doc in docs:
        data = doc.to_dict()
        headline = data.get("headline") or ""

        if not _is_tr1_headline(headline):
            continue

        article_type = data.get("article_type") or "unclassified"
        if not include_already_classified and article_type == "tr1_crossing":
            continue

        ticker = (data.get("ticker") or "").upper()
        if ticker_filter and ticker != ticker_filter.upper():
            continue

        source_url = data.get("source_url") or ""
        candidates.append({
            "doc_id":       doc.id,
            "ticker":       ticker,
            "company_name": data.get("company_name") or "",
            "headline":     headline,
            "source_url":   source_url,
            "published_at": _coerce_published_at(data.get("published_at")),
            "stored_at":    _coerce_published_at(data.get("stored_at")),
            "article_type": article_type,
            "has_source_url": bool(source_url),
        })

    # Sort by published_at descending
    candidates.sort(key=lambda r: r["published_at"], reverse=True)
    return candidates


def print_results(candidates: list[dict]) -> None:
    """Print a formatted table of results."""
    if not candidates:
        print("\nNo TR-1 candidates found.")
        return

    # Count by article_type
    type_counts: dict[str, int] = {}
    no_url_count = 0
    for r in candidates:
        at = r["article_type"]
        type_counts[at] = type_counts.get(at, 0) + 1
        if not r["has_source_url"]:
            no_url_count += 1

    print(f"\nFound {len(candidates)} TR-1 candidate(s):\n")

    for r in candidates:
        url_flag = "  [no URL]" if not r["has_source_url"] else ""
        print(
            f"  {r['published_at']}  [{r['ticker'] or '?':6s}]  "
            f"type={r['article_type']:16s}  {r['headline'][:70]}{url_flag}"
        )

    print(f"\nBreakdown by article_type:")
    for at, n in sorted(type_counts.items()):
        print(f"  {at:20s}  {n}")

    if no_url_count:
        print(f"\nNote: {no_url_count} record(s) have no source_url — "
              "these cannot be re-ingested via the LSEG Ingest tab.")


def write_csv(candidates: list[dict], path: str) -> None:
    """Write candidates to a CSV file."""
    fieldnames = [
        "ticker", "company_name", "published_at", "headline",
        "source_url", "article_type", "doc_id",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(candidates)
    print(f"\nCSV written: {path}  ({len(candidates)} rows)")


def delete_dedup_records(db, candidates: list[dict], dry_run: bool) -> None:
    """
    Delete the announcements dedup records for the candidates.

    After deletion, job_runner will no longer consider these as duplicates,
    so re-submitting their source URLs via the Ingest tab will trigger
    full re-processing through the tr1_crossing path.

    Only candidates with a source_url are safe to delete — records without
    a URL are keyed on the headline fingerprint and re-ingestion would
    require knowing the exact headline to re-submit.
    """
    deletable = [r for r in candidates if r["has_source_url"]]
    skipped   = [r for r in candidates if not r["has_source_url"]]

    if skipped:
        print(f"\nSkipping {len(skipped)} record(s) with no source_url "
              "(cannot be safely re-ingested):")
        for r in skipped:
            print(f"  [{r['ticker']:6s}] {r['headline'][:70]}")

    if not deletable:
        print("\nNo deletable records (all lack source_url).")
        return

    print(f"\n{'[DRY RUN] Would delete' if dry_run else 'Deleting'} "
          f"{len(deletable)} dedup record(s):")

    deleted = 0
    for r in deletable:
        print(f"  [{r['ticker']:6s}] {r['headline'][:70]}")
        if not dry_run:
            try:
                db.collection("announcements").document(r["doc_id"]).delete()
                deleted += 1
            except Exception as e:
                print(f"    ERROR deleting {r['doc_id'][:12]}: {e}")

    if dry_run:
        print("\nRe-run with --delete (without --dry-run) to apply.")
    else:
        print(f"\nDeleted {deleted}/{len(deletable)} dedup records.")
        print("These articles can now be re-submitted via the Ingest tab.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Find historic TR-1 announcements for re-processing."
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete Firestore dedup records so articles can be re-ingested. "
             "Use --dry-run first to preview.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --delete: print what would be deleted without writing.",
    )
    parser.add_argument(
        "--csv",
        metavar="FILE",
        default=None,
        help="Write results to a CSV file (e.g. tr1_backlog.csv).",
    )
    parser.add_argument(
        "--ticker",
        metavar="TICKER",
        default=None,
        help="Filter to a specific ticker (e.g. AMS).",
    )
    parser.add_argument(
        "--include-classified",
        action="store_true",
        help="Also show records already classified as tr1_crossing.",
    )
    args = parser.parse_args()

    if args.delete and not args.dry_run:
        print("WARNING: --delete will permanently remove Firestore dedup records.")
        confirm = input("Type YES to confirm: ").strip()
        if confirm != "YES":
            print("Aborted.")
            sys.exit(0)

    db = firestore.Client()

    candidates = find_tr1_announcements(
        db,
        ticker_filter=args.ticker,
        include_already_classified=args.include_classified,
    )

    print_results(candidates)

    if args.csv and candidates:
        write_csv(candidates, args.csv)

    if args.delete or args.dry_run:
        delete_dedup_records(db, candidates, dry_run=args.dry_run)
