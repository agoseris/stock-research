"""
backfill_announcements_ttl.py

One-shot script to add `expires_at` to all existing announcements documents
that do not already have the field.

TTL is set by article_type, anchored on published_at (not "now"), so that
old administrative notices expire immediately rather than getting a fresh
14-day window. Documents are not modified if expires_at is already present.

TTL rules (from ANNOUNCEMENT_TTL_DAYS in utilities/orchestrator/config.py):
  pdmr_transaction    → no expiry (expires_at not set)
  regulatory_catalyst → 90 days from published_at
  substantive_news    → 90 days from published_at
  administrative      → 14 days from published_at
  unclassified / None → no expiry by default; use --expire-legacy to apply
                        a fallback TTL (default 90 days from published_at)

Run once from your local machine (requires GCP credentials):

    python scripts/backfill_announcements_ttl.py

Use --dry-run to print what would be updated without writing anything.
Use --limit N to process only N documents (useful for testing).
Use --expire-legacy to also expire unclassified/legacy documents.
Use --legacy-ttl-days N to override the fallback TTL (default 90).
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent
load_dotenv(_REPO_ROOT / ".env")

# Credentials fallback: .env path → frontend/gcp-credentials.json (local dev)
_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds or not os.path.exists(_creds):
    _fallback = _REPO_ROOT / "frontend" / "gcp-credentials.json"
    if _fallback.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_fallback)

from google.cloud import firestore  # noqa: E402

# TTL in days by article_type. None = no expiry, do not set expires_at.
TTL_BY_TYPE = {
    "pdmr_transaction":    None,
    "regulatory_catalyst": 90,
    "substantive_news":    90,
    "administrative":      14,
}
DEFAULT_LEGACY_TTL = 90  # used when --expire-legacy is set


def _parse_published_at(value) -> datetime | None:
    """
    Coerce published_at to a UTC-aware datetime.
    Handles Firestore Timestamp, datetime, and ISO string.
    Returns None if the value cannot be parsed.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    # Firestore DatetimeWithNanoseconds
    try:
        return value.replace(tzinfo=timezone.utc) if not value.tzinfo else value
    except Exception:
        return None


def backfill(dry_run: bool, limit: int | None,
             expire_legacy: bool, legacy_ttl_days: int) -> None:
    db = firestore.Client()

    print("Scanning announcements collection...")
    docs = list(db.collection("announcements").stream())
    if limit:
        docs = docs[:limit]

    total = len(docs)
    counts = {
        "skipped_has_expires": 0,
        "skipped_no_expiry":   0,
        "updated":             0,
        "would_update":        0,
    }
    type_counts: dict[str, int] = {}

    now = datetime.now(timezone.utc)

    for doc in docs:
        data = doc.to_dict()

        # Skip if already has expires_at
        if data.get("expires_at") is not None:
            counts["skipped_has_expires"] += 1
            continue

        article_type = data.get("article_type") or "unclassified"
        ttl_days = TTL_BY_TYPE.get(article_type)

        if ttl_days is None:
            if article_type == "pdmr_transaction":
                # Always retain pdmr_transaction base records
                counts["skipped_no_expiry"] += 1
                continue
            # unclassified / unknown type
            if not expire_legacy:
                counts["skipped_no_expiry"] += 1
                continue
            ttl_days = legacy_ttl_days

        # Anchor on published_at; fall back to now if missing/unparseable
        published_at = _parse_published_at(data.get("published_at"))
        if published_at is None:
            anchor = now
            anchor_note = "now (published_at missing)"
        else:
            anchor = published_at
            anchor_note = published_at.strftime("%Y-%m-%d")

        expires_at = anchor + timedelta(days=ttl_days)
        type_counts[article_type] = type_counts.get(article_type, 0) + 1

        if dry_run:
            print(
                f"  [dry-run] {doc.id[:16]}  type={article_type:22s}"
                f"  anchor={anchor_note}  expires={expires_at.strftime('%Y-%m-%d')}"
            )
            counts["would_update"] += 1
        else:
            db.collection("announcements").document(doc.id).update(
                {"expires_at": expires_at}
            )
            counts["updated"] += 1

    print(f"\n{'DRY RUN ' if dry_run else ''}Results ({total} documents scanned):")
    print(f"  Already had expires_at:  {counts['skipped_has_expires']}")
    print(f"  No expiry (retained):    {counts['skipped_no_expiry']}")
    action = "Would update" if dry_run else "Updated"
    print(f"  {action:24s} {counts['would_update'] if dry_run else counts['updated']}")

    if type_counts:
        print("\n  Breakdown by article_type:")
        for atype, n in sorted(type_counts.items()):
            ttl = TTL_BY_TYPE.get(atype) or (legacy_ttl_days if expire_legacy else None)
            print(f"    {atype:25s}  TTL={ttl} days  count={n}")

    if dry_run:
        print("\nRe-run without --dry-run to apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill expires_at TTL field on announcements by article_type."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be updated without writing to Firestore.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N documents (for testing).",
    )
    parser.add_argument(
        "--expire-legacy",
        action="store_true",
        help="Also set expires_at on unclassified/legacy documents.",
    )
    parser.add_argument(
        "--legacy-ttl-days",
        type=int,
        default=DEFAULT_LEGACY_TTL,
        help=f"TTL in days for legacy documents when --expire-legacy is set "
             f"(default: {DEFAULT_LEGACY_TTL}).",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"Backfilling announcements TTL [{mode}]")
    print(f"  TTL rules: administrative=14d, regulatory_catalyst/substantive_news=90d, "
          f"pdmr_transaction=no expiry")
    if args.expire_legacy:
        print(f"  Legacy/unclassified: {args.legacy_ttl_days}d (--expire-legacy set)")
    else:
        print(f"  Legacy/unclassified: no expiry (pass --expire-legacy to include)")
    if args.limit:
        print(f"  Limit: {args.limit} documents")
    print()

    backfill(
        dry_run=args.dry_run,
        limit=args.limit,
        expire_legacy=args.expire_legacy,
        legacy_ttl_days=args.legacy_ttl_days,
    )
