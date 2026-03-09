"""
backfill_pending_jobs_ttl.py

One-shot script to add `expires_at` (now + 7 days) to all existing
pending_jobs documents with status "complete" or "failed".

These documents pre-date the TTL field added to _complete_job/_fail_job
in job_runner.py.  Without this backfill, the Firestore TTL policy would
never touch them and they would accumulate indefinitely.

Run once from your local machine (requires GCP credentials):

    python scripts/backfill_pending_jobs_ttl.py

Use --dry-run to print what would be updated without writing anything.
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

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

TTL_DAYS = 7
TERMINAL_STATUSES = ("complete", "failed")


def backfill(dry_run: bool) -> None:
    db = firestore.Client()
    expires_at = datetime.now(timezone.utc) + timedelta(days=TTL_DAYS)

    total_found = 0
    total_updated = 0

    for status in TERMINAL_STATUSES:
        docs = list(
            db.collection("pending_jobs")
            .where(filter=FieldFilter("status", "==", status))
            .stream()
        )
        print(f"  {status}: {len(docs)} document(s) found")
        total_found += len(docs)

        for doc in docs:
            data = doc.to_dict()
            if data.get("expires_at") is not None:
                # Already has the field — skip to avoid overwriting
                continue
            if dry_run:
                print(f"    [dry-run] would set expires_at on {doc.id} (status={status})")
            else:
                db.collection("pending_jobs").document(doc.id).update(
                    {"expires_at": expires_at}
                )
            total_updated += 1

    action = "would update" if dry_run else "updated"
    print(f"\nDone. {total_found} documents found, {total_updated} {action}.")
    if dry_run:
        print("Re-run without --dry-run to apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill expires_at TTL field on completed/failed pending_jobs."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be updated without writing to Firestore.",
    )
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"Backfilling pending_jobs TTL [{mode}]")
    print(f"  expires_at = now + {TTL_DAYS} days")
    print(f"  statuses   = {TERMINAL_STATUSES}\n")

    backfill(dry_run=args.dry_run)
