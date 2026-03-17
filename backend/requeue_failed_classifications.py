"""
requeue_failed_classifications.py

One-off recovery script: requeue pending_jobs that were silently skipped
because the Anthropic API usage cap was hit during classification.

What it does:
  1. Finds pending_jobs with status="complete" and note="classification_failed"
  2. Deletes the announcements fingerprint for each (unblocks deduplication)
  3. Resets the job to status="pending" so job_runner picks it up again

Run from the backend directory:
    python requeue_failed_classifications.py

Use --dry-run to preview without making changes.
Use --since YYYY-MM-DD to restrict to jobs processed on or after that date.
"""

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter


def _fingerprint(text: str) -> str:
    """Matches FirestoreProvider._fingerprint and job_runner._compute_fingerprint."""
    normalised = " ".join(
        "".join(c for c in text.lower() if c.isalnum() or c.isspace()).split()
    )
    return hashlib.sha256(normalised.encode()).hexdigest()


def requeue(dry_run: bool, since: datetime | None):
    db = firestore.Client()

    query = (
        db.collection("pending_jobs")
        .where(filter=FieldFilter("status", "==", "complete"))
        .where(filter=FieldFilter("note", "==", "classification_failed"))
    )
    docs = list(query.stream())

    if since:
        def _after_since(doc_dict: dict) -> bool:
            raw = doc_dict.get("processed_at")
            if not raw:
                return True  # no timestamp — include conservatively
            try:
                if isinstance(raw, str):
                    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                else:
                    ts = raw
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return ts >= since
            except Exception:
                return True
        docs = [d for d in docs if _after_since(d.to_dict())]

    print(f"Found {len(docs)} job(s) with note='classification_failed'"
          + (f" since {since.date()}" if since else ""))

    if not docs:
        print("Nothing to do.")
        return

    requeued = 0
    skipped = 0

    for doc in docs:
        job = doc.to_dict()
        job_id = doc.id
        ticker = job.get("ticker", "?")
        headline = job.get("headline", "")[:60]
        source_url = job.get("source_url", "")
        processed_at = job.get("processed_at", "unknown")

        key_text = source_url if source_url else job.get("headline", "")
        if not key_text:
            print(f"  SKIP {job_id[:8]} [{ticker}] — no source_url or headline")
            skipped += 1
            continue

        fp = _fingerprint(key_text)
        print(f"  {'[DRY RUN] ' if dry_run else ''}{job_id[:8]} [{ticker}] "
              f"{headline}  (processed_at={processed_at})")

        if not dry_run:
            # 1. Delete the announcements fingerprint to unblock deduplication
            db.collection("announcements").document(fp).delete()

            # 2. Reset the job to pending, clearing processing metadata
            db.collection("pending_jobs").document(job_id).update({
                "status": "pending",
                "note": firestore.DELETE_FIELD,
                "claimed_at": firestore.DELETE_FIELD,
                "processed_at": firestore.DELETE_FIELD,
                "expires_at": firestore.DELETE_FIELD,
            })

        requeued += 1

    print(f"\n{'[DRY RUN] Would requeue' if dry_run else 'Requeued'} "
          f"{requeued} job(s), skipped {skipped}.")
    if dry_run:
        print("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Requeue classification-failed jobs.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing to Firestore.")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Only requeue jobs processed on or after this date (UTC).")
    args = parser.parse_args()

    since_dt = None
    if args.since:
        try:
            since_dt = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Invalid --since date: {args.since!r}. Use YYYY-MM-DD.")
            sys.exit(1)

    requeue(dry_run=args.dry_run, since=since_dt)
