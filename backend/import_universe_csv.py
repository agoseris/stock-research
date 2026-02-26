"""
import_universe_csv.py

Manual universe import — reads the AIM and FTSE All-Share CSV files from docs/
and writes the full universe to Firestore via FirestoreUniverseProvider.

Run this:
  - Once initially, to populate Firestore from the provided CSV data
  - Again whenever the CSVs are updated with fresh data

Usage:
    cd backend && python import_universe_csv.py

After a successful run, pipeline.py will load the universe from Firestore
on subsequent runs. The static universe.py list remains as a last-resort
fallback for environments without Firestore connectivity.

CSV sources (most recent version of each pattern is used):
  docs/AIM_data_complete_*.csv        — all AIM-listed stocks
  docs/FTSE_AllShare_complete_*.csv   — all FTSE All-Share stocks (LSE Main Market)

Both files are committed to git to maintain a version history of universe
composition over time.
"""

import csv
import difflib
import glob
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from typing import List, Optional, Tuple

from dotenv import load_dotenv

# Load .env from the same directory as this script (backend/.env),
# regardless of the working directory the script is invoked from.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Path to docs/ relative to this file
_DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")

# ---------------------------------------------------------------------------
# Companies House lookup
# ---------------------------------------------------------------------------

# Minimum similarity score (0–1) to accept a CH name match.
# Below this threshold the company is imported without a CH number.
# Set to 0.85 — empirical testing showed 0.733 produced a false positive
# (dissolved company matched to a live AIM stock).
CH_CONFIDENCE_THRESHOLD = 0.85

# Rate limit: 600 requests per 5 minutes = 2/sec. 0.6s sleep is comfortably
# within limits and leaves headroom for other API callers on the same key.
CH_REQUEST_SLEEP = 0.6

CH_SEARCH_URL = "https://api.company-information.service.gov.uk/search/companies"


def _normalise_name(name: str) -> str:
    """
    Normalise a company name for comparison.

    Strips share-class suffixes (ORD, NPV, etc.), legal-entity suffixes
    (PLC, LIMITED, LTD, etc.), extraneous punctuation, and whitespace.
    Returns a lowercase string suitable for similarity scoring.

    Examples:
      "GAMING REALMS PLC ORD 0.1P"  → "gaming realms"
      "3I INFRASTRUCTURE PLC ORD NPV" → "3i infrastructure"
      "AIRTEL AFRICA PLC ORD USD0.50" → "airtel africa"
    """
    # Strip everything from " ORD" onwards (share class, nominal value)
    name = re.sub(r'\s+ORD\b.*$', '', name, flags=re.IGNORECASE)
    # Strip common legal-entity suffixes
    name = re.sub(
        r'\b(PLC|PUBLIC LIMITED COMPANY|LIMITED|LTD|INC|CORP|CORPORATION'
        r'|SE|SA|AG|NV|BV|SCA|PTE|PTY)\b\.?',
        '', name, flags=re.IGNORECASE
    )
    # Collapse whitespace
    return re.sub(r'\s+', ' ', name).strip().lower()


def _score_name_match(csv_name: str, ch_name: str) -> float:
    """
    Return a similarity score 0.0–1.0 between two company names.

    1.0 = exact match after normalisation.
    Uses SequenceMatcher ratio for partial matches.
    """
    a = _normalise_name(csv_name)
    b = _normalise_name(ch_name)
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _lookup_ch_number(
    csv_name: str, api_key: str
) -> Tuple[Optional[str], float]:
    """
    Search Companies House for a company by name.

    Queries /search/companies with the normalised name, scores the top
    results against the CSV name, and returns the best match above
    CH_CONFIDENCE_THRESHOLD.

    Only active companies are considered — dissolved, liquidated, or
    struck-off entities are excluded to prevent false positives where a
    defunct company name closely resembles a live AIM/FTSE stock.

    Returns (companies_house_number, confidence) or (None, 0.0) if no
    confident match is found or if the API call fails.

    Note: offshore-incorporated AIM companies (Cayman Islands, Jersey,
    Isle of Man, etc.) will not appear in Companies House and will
    correctly return (None, 0.0).
    """
    import requests

    search_term = _normalise_name(csv_name)
    if not search_term:
        return None, 0.0

    try:
        resp = requests.get(
            CH_SEARCH_URL,
            params={"q": search_term, "items_per_page": 5},
            auth=(api_key, ""),
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception:
        return None, 0.0

    best_number: Optional[str] = None
    best_score = 0.0

    for item in items:
        # Skip dissolved, liquidated, or struck-off companies
        if item.get("company_status", "active") != "active":
            continue
        ch_name = item.get("title", "")
        score = _score_name_match(csv_name, ch_name)
        if score > best_score:
            best_score = score
            best_number = item.get("company_number")

    if best_score < CH_CONFIDENCE_THRESHOLD:
        return None, 0.0

    return best_number, round(best_score, 3)


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def _parse_market_cap(raw: str) -> Optional[float]:
    """
    Convert a market cap string from the CSV to GBP.

    Both CSVs express market cap in millions of GBP (the Currency column
    refers to the price, not the market cap). Values may contain commas
    as thousands separators (e.g. "3,242.06"). Returns None if unparseable.
    """
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip().replace(",", "")
    try:
        return float(cleaned) * 1_000_000  # millions → GBP
    except ValueError:
        return None


def _read_csv(filepath: str, listing_exchange: str, tier: int) -> List[dict]:
    """
    Read one holdings CSV and return a list of raw company dicts.

    Returns dicts with keys: ticker, name, market_cap_gbp, listing_exchange, tier.
    Rows with missing or empty tickers are skipped.
    """
    companies = []
    mcap_col = "Market Cap (m)" if listing_exchange == "LSE_MAIN" else "Market Cap"

    try:
        with open(filepath, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = (row.get("Code") or "").strip()
                name = (row.get("Name") or "").strip()
                if not ticker or not name:
                    continue
                market_cap_gbp = _parse_market_cap(row.get(mcap_col, ""))
                companies.append({
                    "ticker": ticker,
                    "name": name,
                    "market_cap_gbp": market_cap_gbp,
                    "listing_exchange": listing_exchange,
                    "tier": tier,
                })
    except Exception as e:
        print(f"  Error reading {os.path.basename(filepath)}: {e}")

    return companies


def load_csv_universe() -> List[dict]:
    """
    Load all companies from the most recent AIM and FTSE All-Share CSVs.

    No market cap ceiling is applied — the file is expected to be pre-filtered
    at source (e.g. by the LSEG screener) before being committed to docs/.
    Companies with no market cap data are retained (benefit of the doubt).

    Returns a merged, deduplicated list of company dicts. FTSE entries take
    precedence over AIM entries if the same ticker appears in both.
    """
    aim_files = sorted(glob.glob(os.path.join(_DOCS_DIR, "AIM_data_complete_*.csv")))
    ftse_files = sorted(glob.glob(os.path.join(_DOCS_DIR, "FTSE_AllShare_complete_*.csv")))

    if not aim_files and not ftse_files:
        print("ERROR: No CSV files found in docs/.")
        print("  Expected: docs/AIM_data_complete_*.csv")
        print("  Expected: docs/FTSE_AllShare_complete_*.csv")
        return []

    rows: dict = {}  # ticker → dict (FTSE overwrites AIM for duplicates)

    if aim_files:
        aim_path = aim_files[-1]
        print(f"  AIM source:  {os.path.basename(aim_path)}")
        for row in _read_csv(aim_path, listing_exchange="AIM", tier=2):
            rows[row["ticker"]] = row

    if ftse_files:
        ftse_path = ftse_files[-1]
        print(f"  FTSE source: {os.path.basename(ftse_path)}")
        for row in _read_csv(ftse_path, listing_exchange="LSE_MAIN", tier=1):
            rows[row["ticker"]] = row  # overwrites AIM entry if ticker appears in both

    return list(rows.values())


# ---------------------------------------------------------------------------
# UniverseCompany construction
# ---------------------------------------------------------------------------

def _build_universe_companies(rows: List[dict], run_timestamp: datetime) -> list:
    """
    Convert raw CSV rows to UniverseCompany dataclass instances.

    All optional enrichment fields (sector, industry, price history, ADTV,
    ISIN) are left as None — this is a lightweight structural import, not a
    full yfinance enrichment run. Enrichment fields remain out of scope
    for this importer.
    """
    from abstractions import UniverseCompany

    companies = []
    for row in rows:
        company = UniverseCompany(
            ticker_lse=row["ticker"],
            ticker_yahoo=row["ticker"] + ".L",
            company_name=row["name"],
            tier=row["tier"],
            listing_exchange=row["listing_exchange"],
            entity_type="OPERATING",       # default — no trust filtering at import
            liquidity_flag="LIQUID" if row["listing_exchange"] == "LSE_MAIN" else "ILLIQUID",
            universe_added_date=run_timestamp.date(),
            last_refreshed=run_timestamp,
            market_cap_gbp=row.get("market_cap_gbp"),
            # All other optional fields left as None
        )
        companies.append(company)

    return companies


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_import() -> bool:
    """
    Execute the CSV → Firestore import.

    Returns True on success, False on failure.
    """
    from abstractions import RefreshLog
    from storage_firestore_universe import FirestoreUniverseProvider

    run_timestamp = datetime.now(timezone.utc)
    run_id = run_timestamp.strftime("%Y%m%dT%H%M%SZ")

    print(f"\n{'='*60}")
    print(f"Universe CSV import started: {run_timestamp.isoformat()}")
    print(f"Run ID: {run_id}")
    print(f"{'='*60}\n")

    # Load from CSVs
    print("Loading CSV sources...")
    rows = load_csv_universe()
    if not rows:
        print("\nIMPORT FAILED: no CSV data found.")
        return False

    aim_count = sum(1 for r in rows if r["listing_exchange"] == "AIM")
    ftse_count = sum(1 for r in rows if r["listing_exchange"] == "LSE_MAIN")
    print(f"\n  AIM companies:  {aim_count}")
    print(f"  FTSE companies: {ftse_count}")
    print(f"  Total (deduped): {len(rows)}")

    # Build UniverseCompany objects
    companies = _build_universe_companies(rows, run_timestamp)

    # Companies House lookup (optional — skipped if no API key configured)
    ch_key = os.environ.get("COMPANIES_HOUSE_KEY", "")
    if ch_key:
        print(f"\nRunning Companies House lookup for {len(companies)} companies...")
        print(f"  Rate limit: {CH_REQUEST_SLEEP}s per request  |  "
              f"Confidence threshold: {CH_CONFIDENCE_THRESHOLD}")
        matched = 0
        unmatched = 0
        for i, company in enumerate(companies, 1):
            ch_number, confidence = _lookup_ch_number(company.company_name, ch_key)
            company.companies_house_number = ch_number
            company.companies_house_confidence = confidence if ch_number else None
            if ch_number:
                matched += 1
            else:
                unmatched += 1
            if i % 50 == 0 or i == len(companies):
                pct = round(100 * i / len(companies))
                print(f"  {i}/{len(companies)} ({pct}%) — matched: {matched}, "
                      f"unmatched: {unmatched}")
            time.sleep(CH_REQUEST_SLEEP)

        print(f"\n  CH lookup complete: {matched} matched, {unmatched} unmatched "
              f"out of {len(companies)}.")
        scores = [
            c.companies_house_confidence
            for c in companies
            if c.companies_house_confidence is not None
        ]
        if scores:
            high = sum(1 for s in scores if s >= 0.9)
            mid  = sum(1 for s in scores if 0.8 <= s < 0.9)
            low  = sum(1 for s in scores if s < 0.8)
            print(f"  Confidence distribution: "
                  f"{high} high (≥0.90), {mid} medium (0.80–0.89), {low} low (<0.80)")
    else:
        print("\nSkipping Companies House lookup (COMPANIES_HOUSE_KEY not set).")

    # Check whether Firestore already has a universe (refresh vs initial import)
    universe_storage = FirestoreUniverseProvider()
    existing_count = 0
    try:
        existing = universe_storage.get_universe()
        existing_count = len(existing)
    except Exception:
        pass  # treat as empty — Firestore error will surface on write attempt

    if existing_count > 0:
        print(f"\n  Existing Firestore universe: {existing_count} companies (will be replaced).")
    else:
        print("\n  No existing Firestore universe (initial import).")

    # Write to Firestore
    print(f"\nWriting {len(companies)} companies to Firestore...")
    try:
        universe_storage.save_universe(companies)
        print("  Universe written.")
    except Exception as e:
        if existing_count > 0:
            print(f"\nWARNING: Failed to update universe in Firestore: {e}")
            print(f"  Previous universe ({existing_count} companies) is intact.")
            print("  pipeline.py will continue to use the previous universe.")
            print("  Fix the error above and re-run import_universe_csv.py.")
        else:
            print(f"\nERROR: Failed to write initial universe to Firestore: {e}")
            print("  Firestore universe is still empty. pipeline.py will not run.")
            print("  Fix the error above and re-run import_universe_csv.py.")
        return False

    # Write refresh log
    log = RefreshLog(
        run_id=run_id,
        run_timestamp=run_timestamp,
        tier1_count=ftse_count,
        tier2_count=aim_count,
        data_quality_count=0,
        lower_bound_gbp=0.0,  # not computed in CSV import
        success=True,
        errors=[],
    )
    try:
        universe_storage.save_refresh_log(log)
        print("  Refresh log written.")
    except Exception as e:
        print(f"  Warning: could not write refresh log: {e}")

    print(f"\n{'='*60}")
    print(f"Import complete. {len(companies)} companies in Firestore.")
    print(f"  pipeline.py will use this universe on the next run.")
    print(f"{'='*60}\n")
    return True


if __name__ == "__main__":
    success = run_import()
    sys.exit(0 if success else 1)
