"""
repair_signal_performance.py

Wipe the signal_performance collection entirely so it can be repopulated
cleanly by update_signal_performance.py.

Usage:
    python scripts/repair_signal_performance.py --dry-run   # preview
    python scripts/repair_signal_performance.py             # apply

After running, repopulate with:
    python scripts/update_signal_performance.py
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

COLLECTION = "signal_performance"


def run(dry_run: bool) -> None:
    db = firestore.Client()
    docs = list(db.collection(COLLECTION).stream())
    print(f"{len(docs)} documents in {COLLECTION}")

    if not docs:
        print("Nothing to delete.")
        return

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"{prefix}Deleting {len(docs)} documents...")

    if not dry_run:
        for doc in docs:
            doc.reference.delete()
        print(f"Done. Run update_signal_performance.py to repopulate.")
    else:
        print(f"Re-run without --dry-run to apply.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run)
