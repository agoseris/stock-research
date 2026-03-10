"""
find_small_universe_companies.py

Report universe companies with a market cap below a threshold, or with
a missing/null market cap. Output only — no writes to Firestore.

These companies are candidates for removal from the universe on the
grounds of excessive volatility, illiquidity, and wide bid/ask spreads.
Removal is done manually via the Universe tab in the Streamlit UI.

Usage:
    # Default threshold: £10m
    python scripts/find_small_universe_companies.py

    # Custom threshold
    python scripts/find_small_universe_companies.py --threshold 15

    # Include already-muted companies in the report
    python scripts/find_small_universe_companies.py --include-muted

    # Write to CSV
    python scripts/find_small_universe_companies.py --csv small_caps.csv
"""

import argparse
import csv
import os
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


def _fmt_cap(value) -> str:
    """Format raw GBP market cap for display."""
    if value is None:
        return "—"
    try:
        m = float(value)
        if m == 0:
            return "£0"
        if m >= 1_000_000_000:
            return f"£{m / 1_000_000_000:.2f}bn"
        return f"£{m / 1_000_000:.2f}m"
    except (TypeError, ValueError):
        return str(value)


def find_candidates(
    db,
    threshold_gbp: float,
    include_muted: bool,
) -> tuple[list[dict], list[dict]]:
    """
    Scan universe_companies and return two lists:
      - below_threshold: market_cap_gbp is set but < threshold_gbp
      - missing_cap:     market_cap_gbp is None, 0, or absent

    Both lists are sorted by company name.
    Muted companies (not_of_interest=True) are excluded unless include_muted=True.
    """
    print("Scanning universe_companies collection...")
    docs = list(db.collection("universe_companies").stream())
    print(f"  {len(docs)} documents fetched.")

    below, missing = [], []

    for doc in docs:
        data = doc.to_dict()

        muted = data.get("not_of_interest", False)
        if muted and not include_muted:
            continue

        cap = data.get("market_cap_gbp")
        ticker    = (data.get("ticker_lse") or doc.id or "").upper()
        name      = data.get("company_name") or ""
        exchange  = data.get("listing_exchange") or "?"
        sig_state = data.get("signal_state") or ""
        pos_state = data.get("position_state") or ""

        row = {
            "ticker":        ticker,
            "company_name":  name,
            "market_cap_gbp": cap,
            "market_cap_fmt": _fmt_cap(cap),
            "listing_exchange": exchange,
            "signal_state":  sig_state,
            "position_state": pos_state,
            "muted":         muted,
        }

        if cap is None or cap == 0:
            missing.append(row)
        else:
            try:
                if float(cap) < threshold_gbp:
                    below.append(row)
            except (TypeError, ValueError):
                missing.append(row)

    below.sort(key=lambda r: float(r["market_cap_gbp"] or 0))
    missing.sort(key=lambda r: r["company_name"].lower())

    return below, missing


def print_report(
    below: list[dict],
    missing: list[dict],
    threshold_gbp: float,
) -> None:
    threshold_str = f"£{threshold_gbp / 1_000_000:.0f}m"

    print(f"\n{'─' * 72}")
    print(f"  BELOW THRESHOLD  (market_cap_gbp < {threshold_str})  —  {len(below)} companies")
    print(f"{'─' * 72}")
    if below:
        for r in below:
            flags = []
            if r["muted"]:      flags.append("muted")
            if r["signal_state"]:  flags.append(f"sig:{r['signal_state']}")
            if r["position_state"]: flags.append(f"pos:{r['position_state']}")
            flag_str = f"  [{', '.join(flags)}]" if flags else ""
            print(
                f"  {r['ticker']:8s}  {r['market_cap_fmt']:>10s}  "
                f"{r['listing_exchange']:10s}  {r['company_name'][:45]}{flag_str}"
            )
    else:
        print("  None.")

    print(f"\n{'─' * 72}")
    print(f"  MISSING MARKET CAP  (null / zero / unparseable)  —  {len(missing)} companies")
    print(f"{'─' * 72}")
    if missing:
        for r in missing:
            flags = []
            if r["muted"]:       flags.append("muted")
            if r["signal_state"]:   flags.append(f"sig:{r['signal_state']}")
            if r["position_state"]:  flags.append(f"pos:{r['position_state']}")
            flag_str = f"  [{', '.join(flags)}]" if flags else ""
            print(
                f"  {r['ticker']:8s}  {'—':>10s}  "
                f"{r['listing_exchange']:10s}  {r['company_name'][:45]}{flag_str}"
            )
    else:
        print("  None.")

    print(f"\n{'─' * 72}")
    print(f"  SUMMARY")
    print(f"{'─' * 72}")
    print(f"  Below {threshold_str}:     {len(below)}")
    print(f"  Missing market cap: {len(missing)}")
    print(f"  Total candidates:   {len(below) + len(missing)}")

    acted   = sum(1 for r in below + missing if r["position_state"] == "acted")
    deferred = sum(1 for r in below + missing if r["position_state"] == "deferred")
    if acted or deferred:
        print(f"\n  Note: {acted} acted / {deferred} deferred position(s) in candidate list.")
        print( "  Review these before removing from universe.")


def write_csv(below: list[dict], missing: list[dict], path: str) -> None:
    fieldnames = [
        "ticker", "company_name", "market_cap_fmt", "market_cap_gbp",
        "listing_exchange", "signal_state", "position_state", "muted",
    ]
    rows = below + missing
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nCSV written: {path}  ({len(rows)} rows)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Report universe companies below a market cap threshold."
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=10.0,
        metavar="MILLIONS",
        help="Market cap threshold in £m (default: 10). Companies below this are listed.",
    )
    parser.add_argument(
        "--include-muted",
        action="store_true",
        help="Include already-muted (not_of_interest) companies in the report.",
    )
    parser.add_argument(
        "--csv",
        metavar="FILE",
        default=None,
        help="Write results to a CSV file.",
    )
    args = parser.parse_args()

    threshold_gbp = args.threshold * 1_000_000

    db = firestore.Client()
    below, missing = find_candidates(db, threshold_gbp, args.include_muted)
    print_report(below, missing, threshold_gbp)

    if args.csv:
        write_csv(below, missing, args.csv)
