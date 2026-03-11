"""
backfill_signal_results_dismissed.py

Set dismissed=False on any signal_results documents that lack the field.
The frontend query uses .where("dismissed", "==", False) which excludes
documents where the field is absent — this caused signals to be invisible
in the UI despite being processed and notified via Telegram.

Usage:
    python scripts/backfill_signal_results_dismissed.py --dry-run
    python scripts/backfill_signal_results_dismissed.py
"""

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

COLLECTION = "signal_results"


def run(dry_run: bool) -> None:
    db = firestore.Client()
    docs = list(db.collection(COLLECTION).stream())
    print(f"{len(docs)} total documents in {COLLECTION}")

    missing = [d for d in docs if "dismissed" not in (d.to_dict() or {})]
    print(f"{len(missing)} documents missing 'dismissed' field")

    if not missing:
        print("Nothing to update.")
        return

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"{prefix}Setting dismissed=False on {len(missing)} documents...")

    if not dry_run:
        for doc in missing:
            doc.reference.update({"dismissed": False})
        print("Done.")
    else:
        for doc in missing:
            data = doc.to_dict() or {}
            print(f"  {doc.id[:12]}… ticker={data.get('ticker','?')} stored_at={data.get('stored_at','?')}")
        print("Re-run without --dry-run to apply.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run)
