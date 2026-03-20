"""
enrich_universe_sectors.py

Populate the `sector` and `industry` fields on universe_companies documents
using yfinance. Fetches the `.info` dict for each ticker and writes only those
two fields — all other document fields are preserved.

Execution: local machine only.
  yfinance is IP-blocked on the GCP VM. Run this script locally.

Rate limiting: 2-second sleep after every yfinance fetch (consistent with
  other yfinance utilities in this project).

Usage:
    # Enrich all companies in the universe
    python scripts/enrich_universe_sectors.py

    # Enrich specific tickers only
    python scripts/enrich_universe_sectors.py VOD IGG MKS
"""

import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent
load_dotenv(_REPO_ROOT / ".env")

import os  # noqa: E402
_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds or not os.path.exists(_creds):
    _fallback = _REPO_ROOT / "frontend" / "gcp-credentials.json"
    if _fallback.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_fallback)

import yfinance as yf              # noqa: E402
from google.cloud import firestore  # noqa: E402

_COLLECTION = "universe_companies"
_RATE_LIMIT_SECONDS = 2


def _fetch_sector_industry(ticker_yahoo: str) -> tuple[str | None, str | None]:
    """Return (sector, industry) from yfinance, or (None, None) on any failure."""
    try:
        info = yf.Ticker(ticker_yahoo).info
        sector   = info.get("sector")   or None
        industry = info.get("industry") or None
        return sector, industry
    except Exception as e:
        raise RuntimeError(f"yfinance fetch failed: {e}") from e


def enrich(db, tickers: list[str]) -> None:
    total    = len(tickers)
    enriched = 0
    skipped  = 0
    failed   = 0

    print(f"\nEnriching {total} ticker(s) with sector/industry from yfinance...\n")
    print(f"  {'TICKER':<8}  {'SECTOR':<30}  INDUSTRY")
    print(f"  {'─'*8}  {'─'*30}  {'─'*40}")

    for i, ticker in enumerate(tickers, 1):
        ticker_yahoo = f"{ticker}.L"
        try:
            sector, industry = _fetch_sector_industry(ticker_yahoo)
            time.sleep(_RATE_LIMIT_SECONDS)

            if sector is None and industry is None:
                skipped += 1
                print(f"  [{i:3d}/{total}]  {ticker:<8}  (no data — skipped)")
                continue

            db.collection(_COLLECTION).document(ticker).update({
                "sector":   sector,
                "industry": industry,
            })
            enriched += 1
            sec_display = (sector   or "—")[:30]
            ind_display = (industry or "—")[:40]
            print(f"  [{i:3d}/{total}]  {ticker:<8}  {sec_display:<30}  {ind_display}")

        except Exception as e:
            failed += 1
            time.sleep(_RATE_LIMIT_SECONDS)
            print(f"  [{i:3d}/{total}]  {ticker:<8}  ERROR: {e}")

    print(f"\n{'─' * 72}")
    print(f"  Done. {enriched} enriched, {skipped} skipped (no data), {failed} failed.")


def load_all_tickers(db) -> list[str]:
    """Fetch all ticker_lse values from universe_companies."""
    docs = db.collection(_COLLECTION).stream()
    tickers = []
    for doc in docs:
        data = doc.to_dict() or {}
        ticker = data.get("ticker_lse") or doc.id
        if ticker:
            tickers.append(ticker.strip().upper())
    return sorted(tickers)


if __name__ == "__main__":
    db = firestore.Client()

    if len(sys.argv) > 1:
        tickers = [t.strip().upper() for t in sys.argv[1:] if t.strip()]
        print(f"Tickers from args: {', '.join(tickers)}")
    else:
        print("Loading all tickers from universe_companies...")
        tickers = load_all_tickers(db)
        print(f"Found {len(tickers)} companies.")

    if not tickers:
        print("Nothing to enrich.")
        sys.exit(0)

    enrich(db, tickers)
