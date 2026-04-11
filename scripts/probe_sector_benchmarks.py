"""
probe_sector_benchmarks.py

Investigates the viability of sector-relative performance benchmarking.

Two questions answered:
  1. What GICS sectors are present in our universe, and how many companies
     per sector (broken down by listing exchange)?
  2. Which candidate yfinance sector benchmark tickers return usable price
     history for each GICS sector?

Candidate tickers come from two families:
  - FTSE All-Share sector indices (^NMX series, ICB classification)
  - Broad-market sector ETFs listed on LSE (iShares, Xtrackers)

Because yfinance uses GICS taxonomy for `.info['sector']` but FTSE indices
use ICB taxonomy, a mapping is required.  This script tests all candidates
and reports which survive — the output drives the implementation decision.

Execution: local machine only (yfinance is IP-blocked on GCP VM).

Usage:
    python scripts/probe_sector_benchmarks.py
    python scripts/probe_sector_benchmarks.py --lookback 365   # default
    python scripts/probe_sector_benchmarks.py --no-firestore   # skip universe fetch
"""

import argparse
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent
load_dotenv(_REPO_ROOT / ".env")

_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds or not os.path.exists(_creds):
    _fallback = _REPO_ROOT / "frontend" / "gcp-credentials.json"
    if _fallback.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_fallback)

import yfinance as yf  # noqa: E402

_RATE_LIMIT_SECONDS = 2

# ---------------------------------------------------------------------------
# GICS sector → candidate yfinance benchmark tickers
#
# Each entry is a list of candidates in preference order.  The probe fetches
# all of them and flags which have data; the first that returns ≥60 days of
# history in the lookback window is considered "viable".
#
# NMX series: FTSE All-Share ICB sector sub-indices (broad UK market).
# ETFs: LSE-listed ETFs that proxy a UK or global sector.
# ---------------------------------------------------------------------------

_GICS_TO_CANDIDATES: dict[str, list[tuple[str, str]]] = {
    # (ticker, description)
    "Energy": [
        ("^NMX0530", "FTSE All-Share Oil & Gas Producers"),
        ("^NMX0570", "FTSE All-Share Oil Equipment & Services"),
        ("INRG.L",   "iShares Global Clean Energy ETF (LSE)"),
    ],
    "Basic Materials": [
        ("^NMX1350", "FTSE All-Share Chemicals"),
        ("^NMX1730", "FTSE All-Share Forestry & Paper"),
        ("^NMX1750", "FTSE All-Share Industrial Metals & Mining"),
        ("^NMX1770", "FTSE All-Share Mining"),
    ],
    "Industrials": [
        ("^NMX2710", "FTSE All-Share Construction & Materials"),
        ("^NMX7530", "FTSE All-Share General Industrials"),
        ("^NMX7570", "FTSE All-Share Electronic & Electrical Equipment"),
        ("^NMX7720", "FTSE All-Share Industrial Engineering"),
        ("^NMX7730", "FTSE All-Share Industrial Transportation"),
        ("^NMX7740", "FTSE All-Share Support Services"),
    ],
    "Consumer Cyclical": [
        ("^NMX2350", "FTSE All-Share Automobiles & Parts"),
        ("^NMX3740", "FTSE All-Share Leisure Goods"),
        ("^NMX3760", "FTSE All-Share Personal Goods"),
        ("^NMX8530", "FTSE All-Share General Retailers"),
        ("^NMX8730", "FTSE All-Share Travel & Leisure"),
    ],
    "Consumer Defensive": [
        ("^NMX3350", "FTSE All-Share Beverages"),
        ("^NMX3530", "FTSE All-Share Food Producers"),
        ("^NMX3720", "FTSE All-Share Household Goods"),
        ("^NMX3780", "FTSE All-Share Tobacco"),
        ("^NMX8350", "FTSE All-Share Food & Drug Retailers"),
    ],
    "Healthcare": [
        ("^NMX4530", "FTSE All-Share Health Care Equipment & Services"),
        ("^NMX4570", "FTSE All-Share Pharmaceuticals & Biotechnology"),
        ("HEAL.L",   "iShares Healthcare Innovation ETF (LSE)"),
    ],
    "Financial Services": [
        ("^NMX6530", "FTSE All-Share Banks"),
        ("^NMX6570", "FTSE All-Share Nonlife Insurance"),
        ("^NMX6720", "FTSE All-Share Life Insurance"),
        ("^NMX8770", "FTSE All-Share Financial Services"),
    ],
    "Real Estate": [
        ("^NMX6750", "FTSE All-Share Real Estate Investment & Services"),
        ("^NMX6760", "FTSE All-Share Real Estate Investment Trusts"),
        ("IUKP.L",   "iShares UK Property ETF (LSE)"),
    ],
    "Technology": [
        ("^NMX9530", "FTSE All-Share Software & Computer Services"),
        ("^NMX9570", "FTSE All-Share Technology Hardware & Equipment"),
        ("WTCH.L",   "WisdomTree Cloud Computing ETF (LSE)"),
    ],
    "Communication Services": [
        ("^NMX5330", "FTSE All-Share Fixed Line Telecommunications"),
        ("^NMX5370", "FTSE All-Share Mobile Telecommunications"),
        ("^NMX5550", "FTSE All-Share Media"),  # Media often sits here in ICB
    ],
    "Utilities": [
        ("^NMX7510", "FTSE All-Share Utilities (supersector)"),
        ("^NMX5550", "FTSE All-Share Electricity"),
        ("^NMX5750", "FTSE All-Share Gas, Water & Multiutilities"),
    ],
}

# Fallback: broad UK market for any sector without a viable candidate
_BROAD_UK = ("^FTAS", "FTSE All-Share (broad fallback)")


# ---------------------------------------------------------------------------
# Universe fetch
# ---------------------------------------------------------------------------

def _load_universe_sectors(db) -> list[dict]:
    """Return list of {ticker, sector, exchange} from universe_companies."""
    rows = []
    for doc in db.collection("universe_companies").stream():
        d = doc.to_dict() or {}
        rows.append({
            "ticker":   d.get("ticker_lse") or doc.id,
            "sector":   d.get("sector"),
            "exchange": d.get("listing_exchange"),
        })
    return rows


# ---------------------------------------------------------------------------
# yfinance probe
# ---------------------------------------------------------------------------

def _probe_ticker(ticker: str, start: date, end: date) -> dict:
    """Fetch closing prices for ticker over [start, end].  Returns probe result."""
    result = {
        "ticker":       ticker,
        "viable":       False,
        "days_returned": 0,
        "date_range":   None,
        "error":        None,
    }
    try:
        hist = yf.Ticker(ticker).history(
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
        )
        if hist.empty:
            result["error"] = "empty response"
            return result
        closes = hist["Close"].dropna()
        n = len(closes)
        result["days_returned"] = n
        result["viable"] = n >= 60
        if n:
            result["date_range"] = (
                str(closes.index[0].date()),
                str(closes.index[-1].date()),
            )
    except Exception as e:
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def _sector_summary(rows: list[dict]) -> None:
    """Print sector × exchange breakdown from universe data."""
    # sector → exchange → count
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        sector = r["sector"] or "(none)"
        exch   = r["exchange"] or "(unknown)"
        matrix[sector][exch] += 1

    all_exchanges = sorted({r["exchange"] or "(unknown)" for r in rows})
    sectors_sorted = sorted(matrix.keys(), key=lambda s: (s == "(none)", s))

    col_w = 14
    hdr   = f"  {'SECTOR':<36}" + "".join(f"  {e:<{col_w}}" for e in all_exchanges) + "  TOTAL"
    sep   = "  " + "─" * (len(hdr) - 2)

    print(hdr)
    print(sep)
    for sector in sectors_sorted:
        row_str = f"  {sector:<36}"
        total   = 0
        for exch in all_exchanges:
            n = matrix[sector].get(exch, 0)
            total += n
            row_str += f"  {n if n else '—':<{col_w}}"
        row_str += f"  {total}"
        print(row_str)

    n_none = sum(1 for r in rows if not r["sector"])
    print(f"\n  {len(rows)} companies total — {n_none} without sector data "
          f"({100 * n_none // len(rows) if rows else 0}% coverage gap)")


def _benchmark_summary(
    results: dict[str, list[dict]],
    viable_map: dict[str, tuple[str, str] | None],
) -> None:
    """Print per-sector benchmark probe results."""
    for gics_sector, probes in sorted(results.items()):
        best = viable_map.get(gics_sector)
        status = f"VIABLE → {best[0]} ({best[1]})" if best else "NO VIABLE BENCHMARK"
        print(f"\n  {gics_sector}")
        print(f"    Status: {status}")
        for p in probes:
            ok     = "✓" if p["viable"] else "✗"
            days   = p["days_returned"]
            dr     = f"{p['date_range'][0]} → {p['date_range'][1]}" if p["date_range"] else "—"
            err    = f"  [{p['error']}]" if p["error"] else ""
            print(f"    {ok} {p['ticker']:<16} {days:>4} days   {dr}{err}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lookback", type=int, default=365,
        help="Days of price history to request per candidate (default: 365)"
    )
    parser.add_argument(
        "--no-firestore", action="store_true",
        help="Skip universe fetch (useful if Firestore credentials unavailable)"
    )
    args = parser.parse_args()

    end   = date.today()
    start = end - timedelta(days=args.lookback)

    # ------------------------------------------------------------------
    # Section 1: Universe sector distribution
    # ------------------------------------------------------------------
    if not args.no_firestore:
        print("\n" + "═" * 72)
        print("  SECTION 1 — Universe sector distribution (from Firestore)")
        print("═" * 72 + "\n")
        try:
            from google.cloud import firestore
            db   = firestore.Client()
            rows = _load_universe_sectors(db)
            _sector_summary(rows)
        except Exception as e:
            print(f"  Firestore unavailable: {e}")
            print("  Re-run with --no-firestore to skip this section.")
    else:
        print("\n  [Skipping Section 1 — --no-firestore flag set]")

    # ------------------------------------------------------------------
    # Section 2: Benchmark ticker probe
    # ------------------------------------------------------------------
    print("\n" + "═" * 72)
    print(f"  SECTION 2 — yfinance benchmark probe  ({start} → {end})")
    print("═" * 72)
    print(f"\n  Probing {sum(len(v) for v in _GICS_TO_CANDIDATES.values())} "
          f"candidate tickers across {len(_GICS_TO_CANDIDATES)} GICS sectors...\n")

    all_results: dict[str, list[dict]] = {}
    viable_map:  dict[str, tuple[str, str] | None] = {}

    for gics_sector, candidates in _GICS_TO_CANDIDATES.items():
        probes: list[dict] = []
        best: tuple[str, str] | None = None

        for ticker, description in candidates:
            print(f"  Probing {ticker:<18} ({gics_sector}) ...", end=" ", flush=True)
            result = _probe_ticker(ticker, start, end)
            result["description"] = description
            probes.append(result)

            if result["viable"] and best is None:
                best = (ticker, description)
                print(f"✓ {result['days_returned']} days")
            elif result["error"]:
                print(f"✗ error: {result['error']}")
            else:
                print(f"✗ {result['days_returned']} days (below threshold)")

            time.sleep(_RATE_LIMIT_SECONDS)

        all_results[gics_sector] = probes
        viable_map[gics_sector]  = best

    # Also probe the broad fallback
    print(f"\n  Probing broad fallback: {_BROAD_UK[0]} ...", end=" ", flush=True)
    broad_result = _probe_ticker(_BROAD_UK[0], start, end)
    viable_broad = broad_result["viable"]
    print(f"{'✓' if viable_broad else '✗'} {broad_result['days_returned']} days")

    # ------------------------------------------------------------------
    # Section 3: Summary
    # ------------------------------------------------------------------
    print("\n" + "═" * 72)
    print("  SECTION 3 — Results summary")
    print("═" * 72)

    _benchmark_summary(all_results, viable_map)

    print("\n" + "─" * 72)
    n_viable = sum(1 for v in viable_map.values() if v)
    n_total  = len(viable_map)
    print(f"\n  {n_viable}/{n_total} GICS sectors have a viable benchmark ticker.")
    if n_viable < n_total:
        missing = [s for s, v in viable_map.items() if not v]
        print(f"  Sectors without a viable benchmark: {', '.join(missing)}")
        print(f"  Broad UK fallback ({_BROAD_UK[0]}): "
              f"{'viable' if viable_broad else 'NOT viable'}")

    print("\n  Recommendation key:")
    print("    ✓ viable  = ≥60 days of closing prices returned in the lookback window")
    print("    ✗ not viable = empty response, error, or insufficient history")
    print()


if __name__ == "__main__":
    main()
