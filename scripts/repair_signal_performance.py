"""
repair_signal_performance.py  (one-off)

signal_performance docs written by the initial script version contain
malformed top-level fields with literal dots in their names
(e.g. "snapshots.pre_90d") alongside the correct nested "snapshots" map.

This script rewrites each document as a clean full set() — no merge —
keeping only well-formed keys (no dots in key name).

Safe to re-run: idempotent once the malformed keys are gone.
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

    repaired = 0
    clean    = 0

    for doc in docs:
        data = doc.to_dict() or {}
        malformed = [k for k in data if "." in k]

        if not malformed:
            clean += 1
            continue

        # Strip all malformed keys — keep only well-formed ones
        clean_data = {k: v for k, v in data.items() if "." not in k}

        prefix = "[DRY RUN] " if dry_run else ""
        print(f"  {prefix}{doc.id[:24]}…  removing {len(malformed)} malformed keys")

        if not dry_run:
            doc.reference.set(clean_data)   # full replace — no merge

        repaired += 1

    print(f"\n  Already clean: {clean}")
    print(f"  Repaired:      {repaired}")
    if dry_run:
        print(f"\n  Re-run without --dry-run to apply.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run)
