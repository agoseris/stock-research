"""
backfill_signal_results_ttl.py

Set expires_at = now + 90 days on every signal_results document that does
not already have an expires_at field.

After running this script, configure the TTL policy in the GCP Console:
  Firestore → Data → signal_results → (⋮) → Manage TTL policy
  Field path: expires_at

Dry-run by default — pass --execute to apply.

Usage:
    python scripts/backfill_signal_results_ttl.py
    python scripts/backfill_signal_results_ttl.py --execute
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
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

TTL_DAYS = 90
COLLECTION = "signal_results"


def run(dry_run: bool) -> None:
    db = firestore.Client()
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=TTL_DAYS)

    print(f"Scanning {COLLECTION} for docs without expires_at...")
    docs = list(db.collection(COLLECTION).stream())
    print(f"  {len(docs)} total documents.")

    to_update = [d for d in docs if "expires_at" not in (d.to_dict() or {})]
    print(f"  {len(to_update)} documents need expires_at.")

    if not to_update:
        print("Nothing to do.")
        return

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{prefix}Setting expires_at = {expires_at.date()} + 90 days on {len(to_update)} docs...\n")

    updated = 0
    failed  = 0
    for i, doc in enumerate(to_update, 1):
        if dry_run:
            if i <= 5 or i == len(to_update):
                print(f"  [{i:4d}/{len(to_update)}] would update  {doc.id}")
            elif i == 6:
                print(f"  ... ({len(to_update) - 5} more)")
        else:
            try:
                doc.reference.update({"expires_at": expires_at})
                updated += 1
                if i % 100 == 0 or i == len(to_update):
                    print(f"  [{i:4d}/{len(to_update)}] updated")
            except Exception as e:
                failed += 1
                print(f"  [{i:4d}/{len(to_update)}] FAILED  {doc.id}  {e}")

    print(f"\n{'─' * 60}")
    if dry_run:
        print(f"  Dry run complete. {len(to_update)} docs would be updated.")
        print(f"  Re-run with --execute to apply.")
    else:
        print(f"  Done. {updated} updated, {failed} failed.")
        print(f"\n  Next step: configure TTL policy in GCP Console if not already done:")
        print(f"    Firestore → Data → {COLLECTION} → Manage TTL policy → field: expires_at")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=f"Backfill expires_at on {COLLECTION} documents."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply updates. Without this flag, runs in dry-run mode.",
    )
    args = parser.parse_args()
    run(dry_run=not args.execute)
