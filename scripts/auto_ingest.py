#!/usr/bin/env python3
"""
auto_ingest.py

Fully automated LSEG news ingestion script.

  - Fetches today's LSEG index (MCX+SMX+AXX) via Playwright
  - Filters rows using existing parse_helpers logic
  - Deduplicates against announcements + pending_jobs
  - For each new "passed" row: fetches body text and submits an lseg_ingest
    job to the Firestore pending_jobs collection
  - The VM's job_runner picks up jobs for LLM analysis

Intended to be scheduled hourly 07:30-19:30 Mon-Fri via cron on the local
machine (WSL2, residential IP) to avoid GCP IP blocking of LSEG pages.

Usage:
    python scripts/auto_ingest.py           # full run
    python scripts/auto_ingest.py --dry-run # fetch + filter + dedup; no body
                                            # fetches or Firestore writes

Logging: structured print() to stdout; cron redirects to auto_ingest.log.
"""

import argparse
import hashlib
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
# Add frontend/ to sys.path to import lseg_scraper and parse_helpers.
# firestore_helpers is NOT imported — it imports streamlit at module level.
# Standalone Firestore helpers below replicate the 6 needed read operations.

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "frontend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

# The .env carries the VM credentials path. On the local machine the file lives
# in frontend/. If the env-var path doesn't exist, try that local fallback.
_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if _creds_path and not Path(_creds_path).exists():
    _local_creds = _REPO_ROOT / "frontend" / "gcp-credentials.json"
    if _local_creds.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_local_creds)

from google.cloud import firestore  # noqa: E402
from google.cloud.firestore_v1.base_query import FieldFilter  # noqa: E402

from lseg_scraper import fetch_announcement_body, fetch_announcement_index  # noqa: E402
from parse_helpers import _filter_announcement_rows  # noqa: E402


# ── Logging ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"{_ts()}  {msg}", flush=True)


# ── Fingerprint ───────────────────────────────────────────────────────────────
# Mirrors storage_firestore.FirestoreProvider._fingerprint() exactly.
# Used to look up document IDs in the announcements dedup collection.

def _fingerprint(text: str) -> str:
    normalised = " ".join(
        "".join(c for c in text.lower() if c.isalnum() or c.isspace()).split()
    )
    return hashlib.sha256(normalised.encode()).hexdigest()


# ── Standalone Firestore helpers ──────────────────────────────────────────────

def _is_already_processed(db, source_url: str) -> bool:
    """
    Check announcements collection using fingerprint document ID lookup.
    Fast direct get — no index required.
    Mirrors StorageProvider.announcement_exists().
    """
    if not source_url:
        return False
    fp = _fingerprint(source_url)
    try:
        doc = db.collection("announcements").document(fp).get()
        return doc.exists
    except Exception as e:
        log(f"  WARN: announcements lookup failed for {source_url!r}: {e}")
        return False


def _get_inflight_urls(db) -> set:
    """
    Return set of source_url values in pending_jobs with status pending or
    processing. Prevents re-submitting items queued but not yet processed.
    """
    inflight = set()
    for status in ("pending", "processing"):
        try:
            docs = (
                db.collection("pending_jobs")
                .where(filter=FieldFilter("status", "==", status))
                .stream()
            )
            for doc in docs:
                url = doc.to_dict().get("source_url", "")
                if url:
                    inflight.add(url)
        except Exception as e:
            log(f"  WARN: pending_jobs query ({status}) failed: {e}")
    return inflight


def _get_exclusion_lists(db) -> tuple:
    """Return (excluded_types: list, company_keywords: list)."""
    try:
        doc = db.collection("app_config").document("lseg_filters").get()
        if doc.exists:
            d = doc.to_dict()
            return (
                d.get("excluded_announcement_types", []),
                d.get("excluded_company_keywords", []),
            )
    except Exception as e:
        log(f"  WARN: exclusion list fetch failed: {e}")
    return [], []


def _get_universe_tickers(db) -> set:
    """
    Return set of ticker doc IDs from universe_companies.
    Document IDs are the LSE tickers (keyed by ticker_lse at import time).
    """
    try:
        docs = db.collection("universe_companies").select([]).stream()
        return {doc.id for doc in docs}
    except Exception as e:
        log(f"  WARN: universe_companies fetch failed: {e}")
        return set()


def _get_not_of_interest(db) -> set:
    """Return set of ticker doc IDs where not_of_interest == True."""
    try:
        docs = (
            db.collection("universe_companies")
            .where(filter=FieldFilter("not_of_interest", "==", True))
            .select([])
            .stream()
        )
        return {doc.id for doc in docs}
    except Exception as e:
        log(f"  WARN: not_of_interest fetch failed: {e}")
        return set()


def _submit_job(db, row: dict, body: str) -> None:
    """
    Write an lseg_ingest job document to the pending_jobs collection.
    Schema mirrors firestore_helpers.submit_job().
    """
    job = {
        "job_type": "lseg_ingest",
        "status": "pending",
        "submitted_at": firestore.SERVER_TIMESTAMP,
        "processed_at": None,
        "ticker": row["ticker"],
        "company_name": row["company_name"],
        "headline": f"{row['company_name']} — {row['announcement_type']}",
        "body": body,
        "source_url": row["source_url"],
        "published_at": row["published_at"],
        "price": row["price_pence"],
        "price_change": row["price_change_pct"],
        "error": None,
    }
    db.collection("pending_jobs").add(job)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(dry_run: bool = False) -> None:
    start = time.monotonic()
    log("AUTO-INGEST START" + (" [DRY RUN]" if dry_run else ""))

    # 1. Init Firestore
    db = firestore.Client()

    # 2. Load config from Firestore
    log("Loading config from Firestore...")
    excluded_types, company_keywords = _get_exclusion_lists(db)
    universe_tickers = _get_universe_tickers(db)
    not_of_interest = _get_not_of_interest(db)
    log(
        f"Config: {len(universe_tickers)} universe tickers · "
        f"{len(not_of_interest)} muted · "
        f"{len(excluded_types)} excluded types · "
        f"{len(company_keywords)} company keywords"
    )

    # 3. Load in-flight URLs (pending_jobs not yet processed)
    inflight_urls = _get_inflight_urls(db)
    log(f"In-flight: {len(inflight_urls)} source URLs in pending/processing jobs")

    # 4. Fetch LSEG index
    log("Fetching LSEG index (MCX+SMX+AXX)...")
    rows = fetch_announcement_index()
    log(f"Index: {len(rows)} rows from LSEG (MCX+SMX+AXX)")

    # 5. Filter
    result = _filter_announcement_rows(
        rows, universe_tickers, excluded_types, company_keywords, not_of_interest
    )
    passed     = result["passed"]
    discovery  = result["discovery"]
    suppressed = result["suppressed"]
    skipped    = result["skipped_source"]
    log(
        f"Filter: {len(passed)} passed · {len(discovery)} discovery · "
        f"{len(suppressed)} suppressed · {skipped} non-RNS"
    )

    # 6. Dedup against announcements collection + in-flight pending_jobs
    new_rows = []
    already_count = 0
    for row in passed:
        url = row.get("source_url", "")
        if url in inflight_urls:
            already_count += 1
            continue
        if _is_already_processed(db, url):
            already_count += 1
            continue
        new_rows.append(row)
    log(f"Dedup: {already_count} already processed · {len(new_rows)} new")

    if not new_rows:
        duration = int(time.monotonic() - start)
        log(
            f"AUTO-INGEST COMPLETE  submitted=0 failed=0 "
            f"skipped={already_count}  duration={duration}s"
        )
        return

    # 7. Dry run: log what would be submitted and exit
    if dry_run:
        log(f"DRY RUN: would submit {len(new_rows)} rows:")
        for i, row in enumerate(new_rows, 1):
            ticker   = row.get("ticker", "?")
            headline = f"{row.get('company_name', '')} — {row.get('announcement_type', '')}"
            log(f"  [{i}/{len(new_rows)}] {ticker:<6}  {headline!r}")
        duration = int(time.monotonic() - start)
        log(f"AUTO-INGEST DRY RUN COMPLETE  would_submit={len(new_rows)}  duration={duration}s")
        return

    # 8. Fetch body + submit for each new row
    submitted = 0
    failed = 0
    for i, row in enumerate(new_rows, 1):
        ticker   = row.get("ticker", "?")
        headline = f"{row.get('company_name', '')} — {row.get('announcement_type', '')}"
        url      = row.get("source_url", "")

        delay = random.uniform(1, 20)
        time.sleep(delay)

        try:
            body = fetch_announcement_body(url)
            _submit_job(db, row, body)
            submitted += 1
            log(f"[{i}/{len(new_rows)}] SUBMITTED  {ticker:<6}  {headline!r}")
        except Exception as e:
            failed += 1
            log(f"[{i}/{len(new_rows)}] FAILED     {ticker:<6}  Error: {e}")

    duration = int(time.monotonic() - start)
    log(
        f"AUTO-INGEST COMPLETE  submitted={submitted} failed={failed} "
        f"skipped={already_count}  duration={duration}s"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Automated LSEG news ingestion — fetch, filter, dedup, submit."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and filter only; no body fetches or Firestore writes.",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run)
