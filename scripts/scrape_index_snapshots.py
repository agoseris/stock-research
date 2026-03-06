#!/usr/bin/env python3
"""
scrape_index_snapshots.py

Daily scraper for LSEG index pages. Writes one document per index per day to
the Firestore index_snapshots collection.

Indices scraped:
  AIM All-Share  → doc_id: "AIM_ALL_SHARE_{YYYY-MM-DD}"
  FTSE Small Cap → doc_id: "FTSE_SMALL_CAP_{YYYY-MM-DD}"
  FTSE 250       → doc_id: "FTSE_250_{YYYY-MM-DD}"

Scheduled via cron to run once daily at 17:00 UTC (after UK market close).
Must run on a local machine with a residential IP to avoid LSEG blocking.

Usage:
    python scripts/scrape_index_snapshots.py             # full run
    python scripts/scrape_index_snapshots.py --dry-run   # scrape, print, no Firestore write
    python scripts/scrape_index_snapshots.py --probe     # screenshot + DOM dump, no extraction

For --probe mode: screenshots are saved to scripts/debug_index_{name}.png.
Run this first to discover the DOM structure before changing extraction selectors.
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
# Add frontend/ to sys.path so we can import lseg_scraper helpers.
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_REPO_ROOT / "frontend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

# Handle the credentials path: the .env carries the VM path; on local machine
# fall back to frontend/gcp-credentials.json if the env-var is unset or the
# path it points to doesn't exist.
_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds_path or not Path(_creds_path).exists():
    _local_creds = _REPO_ROOT / "frontend" / "gcp-credentials.json"
    if _local_creds.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_local_creds)

from playwright.sync_api import sync_playwright                    # noqa: E402
from playwright.sync_api import TimeoutError as PlaywrightTimeout  # noqa: E402

# ── Configuration ─────────────────────────────────────────────────────────────

# Shared cookie store with the LSEG news scraper so the challenge gate is only
# solved once across all LSEG page visits.
_COOKIES_PATH = _REPO_ROOT / "frontend" / "lseg_cookies.json"

_CHALLENGE_BODY_CLASS   = "block-scroll"
_PRIVATE_INVESTOR_TEXT  = "a private investor"

_GOTO_TIMEOUT      = 45_000  # ms
_ELEMENT_TIMEOUT   = 20_000  # ms
_CHALLENGE_TIMEOUT = 10_000  # ms

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Firestore collection
_COLLECTION = "index_snapshots"

# Index definitions: name (Firestore key), URL, human label
_INDICES = [
    {
        "name":  "AIM_ALL_SHARE",
        "url":   "https://www.londonstockexchange.com/indices/ftse-aim-all-share",
        "label": "AIM All-Share",
    },
    {
        "name":  "FTSE_SMALL_CAP",
        "url":   "https://www.londonstockexchange.com/indices/ftse-smallcap",
        "label": "FTSE Small Cap",
    },
    {
        "name":  "FTSE_250",
        "url":   "https://www.londonstockexchange.com/indices/ftse-250",
        "label": "FTSE 250",
    },
]

# ── Logging ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"{_ts()}  {msg}", flush=True)


# ── Playwright helpers ────────────────────────────────────────────────────────

def _load_page(page, url: str) -> None:
    try:
        page.goto(url, wait_until="networkidle", timeout=_GOTO_TIMEOUT)
    except PlaywrightTimeout:
        # Angular apps sometimes don't settle; proceed and check content.
        pass


def _handle_challenge_if_present(page, context) -> None:
    body_classes = page.locator("body").get_attribute("class") or ""
    if _CHALLENGE_BODY_CLASS not in body_classes:
        return
    log("  challenge gate detected — clicking 'a private investor'")
    try:
        page.get_by_text(_PRIVATE_INVESTOR_TEXT).first.click(timeout=_CHALLENGE_TIMEOUT)
        page.wait_for_function(
            f'!document.body.classList.contains("{_CHALLENGE_BODY_CLASS}")',
            timeout=_CHALLENGE_TIMEOUT,
        )
    except PlaywrightTimeout:
        raise RuntimeError(
            "Challenge gate did not dismiss. Page structure may have changed."
        )
    context.storage_state(path=str(_COOKIES_PATH))
    log("  challenge gate dismissed — cookies saved")


# ── DOM extraction ────────────────────────────────────────────────────────────
#
# Extraction uses regex against page.inner_text("body"), which matches the
# plain-text structure observed via --probe.
#
# Confirmed page text structure (from probe, March 2026):
#
#   IndexWhat's this?
#   Value
#   795.20  0.08%          ← index_value + change_1d_pct on same line
#   FTSE AIM All-Share
#   Net variation of the value
#   0.64
#   High / Low
#   795.60 / 791.31        ← today's intraday high/low
#   Previous close
#   794.56
#   ...
#   52 Week High           ← further down the page
#   850.00
#   52 Week Low
#   700.00
#
# If the LSEG page structure changes, re-run --probe to capture updated text
# and revise the patterns below.


def _extract_index_data(page, label: str) -> dict:
    """
    Extract index statistics from the current page via regex over page text.

    Returns a dict with keys: index_value, change_1d_pct, week_52_high, week_52_low.
    Values are floats where extracted, None where not found.
    Tolerant of missing values — partial documents are still written to Firestore.
    """
    result = {
        "index_value":   None,
        "change_1d_pct": None,
        "week_52_high":  None,
        "week_52_low":   None,
    }

    try:
        page_text = page.inner_text("body")
    except Exception as e:
        log(f"  [{label}] ERROR: cannot read page text — {e}")
        return result

    # ── Index value + daily change % ──────────────────────────────────────────
    # Pattern: "Value\n795.20  0.08%" — value and change% are on the same line
    # after the "Value" label. \s+ handles the newline + any spaces/nbsp between them.
    m = re.search(
        r'\bValue\b\s+([\d,]+\.?\d*)\s+([+-]?[\d]+\.?\d*)%',
        page_text,
    )
    if m:
        result["index_value"]   = _parse_float(m.group(1))
        result["change_1d_pct"] = _parse_float(m.group(2))
        log(
            f"  [{label}] index_value={result['index_value']}  "
            f"change_1d_pct={result['change_1d_pct']}%"
        )
    else:
        log(f"  [{label}] WARN: 'Value' pattern not matched — check --probe output")

    # ── 52-week high / low ────────────────────────────────────────────────────
    # Primary: explicit "52 Week High / Low" labels (appear further down the page).
    # Fallback: today's intraday "High / Low" if 52wk labels absent.

    m_52h = re.search(r'52\s*[Ww]ee?k\s+[Hh]igh\s+([\d,]+\.?\d*)', page_text)
    m_52l = re.search(r'52\s*[Ww]ee?k\s+[Ll]ow\s+([\d,]+\.?\d*)',  page_text)

    if m_52h:
        result["week_52_high"] = _parse_float(m_52h.group(1))
        log(f"  [{label}] week_52_high={result['week_52_high']} (52wk label)")
    if m_52l:
        result["week_52_low"] = _parse_float(m_52l.group(1))
        log(f"  [{label}] week_52_low={result['week_52_low']} (52wk label)")

    # Alternative format: "52WK range\n{low} - {high}" (used by FTSE SmallCap page)
    if result["week_52_high"] is None or result["week_52_low"] is None:
        m_range = re.search(
            r'52WK\s+range\s+([\d,]+\.?\d*)\s+-\s+([\d,]+\.?\d*)',
            page_text,
            re.IGNORECASE,
        )
        if m_range:
            if result["week_52_low"] is None:
                result["week_52_low"]  = _parse_float(m_range.group(1))
                log(f"  [{label}] week_52_low={result['week_52_low']} (52WK range)")
            if result["week_52_high"] is None:
                result["week_52_high"] = _parse_float(m_range.group(2))
                log(f"  [{label}] week_52_high={result['week_52_high']} (52WK range)")

    # Fallback: "High / Low\n{high} / {low}" (today's intraday range)
    if result["week_52_high"] is None or result["week_52_low"] is None:
        m_hl = re.search(
            r'High\s*/\s*Low\s+([\d,]+\.?\d*)\s*/\s*([\d,]+\.?\d*)',
            page_text,
        )
        if m_hl:
            if result["week_52_high"] is None:
                result["week_52_high"] = _parse_float(m_hl.group(1))
                log(f"  [{label}] week_52_high={result['week_52_high']} (intraday High/Low fallback)")
            if result["week_52_low"] is None:
                result["week_52_low"] = _parse_float(m_hl.group(2))
                log(f"  [{label}] week_52_low={result['week_52_low']} (intraday High/Low fallback)")

    return result


# ── Probe mode ───────────────────────────────────────────────────────────────

def _probe_index_page(page, index_config: dict) -> None:
    """
    In --probe mode: take a screenshot, dump page title and key text.
    Used to discover DOM structure before writing extraction selectors.
    """
    name  = index_config["name"]
    label = index_config["label"]

    screenshot_path = str(_SCRIPTS_DIR / f"debug_index_{name}.png")
    page.screenshot(path=screenshot_path, full_page=True)
    log(f"  [{label}] screenshot saved → {screenshot_path}")

    title = page.title()
    log(f"  [{label}] page title: {title!r}")

    # Dump classes on the body element
    body_classes = page.locator("body").get_attribute("class") or ""
    log(f"  [{label}] body classes: {body_classes!r}")

    # Try common stat-related selectors and report what was found
    probe_selectors = [
        "h1",
        "[class*='stat']",
        "[class*='figure']",
        "[class*='summary']",
        "[class*='price']",
        "[class*='value']",
        "[class*='change']",
        "[class*='high']",
        "[class*='low']",
        "[class*='range']",
        "dt",
        "dd",
    ]
    log(f"  [{label}] --- DOM probe ---")
    for sel in probe_selectors:
        try:
            elements = page.locator(sel).all()
            if elements:
                texts = []
                for el in elements[:5]:  # limit to first 5 matches
                    try:
                        t = el.inner_text(timeout=1_000).strip()
                        if t:
                            texts.append(repr(t[:80]))
                    except Exception:
                        pass
                if texts:
                    log(f"    {sel!r}: {', '.join(texts)}")
        except Exception as e:
            log(f"    {sel!r}: error — {e}")

    # Dump all visible page text (first 3000 chars)
    try:
        page_text = page.inner_text("body")
        log(f"  [{label}] --- page text (first 3000 chars) ---")
        log(page_text[:3000])
    except Exception as e:
        log(f"  [{label}] page text dump failed: {e}")


# ── Firestore write ───────────────────────────────────────────────────────────

def _write_to_firestore(db, index_name: str, scrape_date: str, data: dict) -> None:
    from google.cloud import firestore as _firestore
    doc_id = f"{index_name}_{scrape_date}"
    doc = {
        "index_name":   index_name,
        "index_value":  data["index_value"],
        "change_1d_pct": data["change_1d_pct"],
        "week_52_high": data["week_52_high"],
        "week_52_low":  data["week_52_low"],
        "scrape_date":  scrape_date,
        "scraped_at":   _firestore.SERVER_TIMESTAMP,
    }
    db.collection(_COLLECTION).document(doc_id).set(doc)
    log(f"  Written → {_COLLECTION}/{doc_id}")


# ── Numeric parsing ───────────────────────────────────────────────────────────

def _parse_float(text: str) -> float:
    """
    Parse a financial number string to float.
    Handles: commas as thousands separators, leading +/-, trailing % sign.
    Returns None for empty, '-', or unparseable strings.
    """
    if not text:
        return None
    s = str(text).strip()
    # Strip trailing % and surrounding whitespace
    s = s.rstrip("%").strip()
    if not s or s == "-":
        return None
    # Remove thousands separators (commas, non-breaking spaces)
    s = s.replace(",", "").replace("\xa0", "").replace(" ", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main(dry_run: bool = False, probe: bool = False) -> None:
    start = time.monotonic()
    mode = "[PROBE]" if probe else ("[DRY RUN]" if dry_run else "")
    log(f"INDEX SNAPSHOT SCRAPER START {mode}")

    today = datetime.now(timezone.utc).date().isoformat()
    log(f"Scrape date: {today}")

    # Initialise Firestore only if we intend to write
    db = None
    if not dry_run and not probe:
        from google.cloud import firestore
        db = firestore.Client()
        log("Firestore client initialised")

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for idx in _INDICES:
            name  = idx["name"]
            url   = idx["url"]
            label = idx["label"]

            log(f"Fetching {label} — {url}")

            storage_state = str(_COOKIES_PATH) if _COOKIES_PATH.exists() else None
            context = browser.new_context(
                storage_state=storage_state,
                user_agent=_USER_AGENT,
            )
            page = context.new_page()

            try:
                _load_page(page, url)
                _handle_challenge_if_present(page, context)

                if probe:
                    _probe_index_page(page, idx)
                    context.close()
                    continue

                log(f"  Extracting data for {label}...")
                data = _extract_index_data(page, label)

                extracted_count = sum(1 for v in data.values() if v is not None)
                log(
                    f"  {label}: "
                    f"value={data['index_value']} "
                    f"change={data['change_1d_pct']} "
                    f"52wk_high={data['week_52_high']} "
                    f"52wk_low={data['week_52_low']} "
                    f"({extracted_count}/4 fields extracted)"
                )
                results[name] = data

                if not dry_run and db is not None:
                    _write_to_firestore(db, name, today, data)

            except Exception as e:
                log(f"  ERROR scraping {label}: {e}")
                results[name] = {"index_value": None, "change_1d_pct": None,
                                 "week_52_high": None, "week_52_low": None}
            finally:
                context.close()

        browser.close()

    duration = int(time.monotonic() - start)

    if probe:
        log(f"PROBE COMPLETE  duration={duration}s  screenshots in scripts/debug_index_*.png")
        return

    # Summary
    if dry_run:
        log("DRY RUN — no Firestore writes performed")

    written = sum(1 for d in results.values() if d.get("index_value") is not None)
    log(f"INDEX SNAPSHOT COMPLETE  written={written}/{len(_INDICES)}  duration={duration}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape LSEG index pages and write daily snapshots to Firestore."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and print values; do not write to Firestore.",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help=(
            "Navigate to each index page, take screenshots, and dump page structure. "
            "Use to discover DOM selectors. Does not extract data or write to Firestore."
        ),
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run, probe=args.probe)
