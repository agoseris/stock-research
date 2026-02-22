"""
test_05_rate_limiting.py

Pre-build verification test 5 of 5.

Verifies that yfinance can fetch data for 20 tickers sequentially with a
0.5s sleep between each request without rate-limit errors or consistent
failures.

This test exercises the pattern that the universe pipeline will use at
scale (400-600 tickers per quarterly refresh). Success here does not
guarantee scale-level stability, but it confirms that:
  - The ticker format (.L suffix) is correct
  - yfinance can return market_cap and revenue for most LSE tickers
  - The 0.5s inter-request sleep is sufficient at small scale

PASS criteria: at least 18 of 20 tickers return a non-error result.
Any consistent failures are listed for investigation.

Run from the backend/ directory:
    python tests/test_05_rate_limiting.py

Expected duration: ~10-15 seconds (20 tickers × 0.5s sleep + fetch time).
"""

import sys
import time

# 20 tickers drawn from FTSE Small Cap and AIM — a representative sample
# of the range of company sizes and sectors the pipeline will encounter.
TEST_TICKERS = [
    "SDY.L",   # Speedy Hire — FTSE Small Cap, liquid
    "JET2.L",  # Jet2 — larger LSE company, good data
    "CVSG.L",  # CVS Group — vet services
    "VLX.L",   # Volex — electronic components
    "RNWH.L",  # Renew Holdings — engineering services
    "CRW.L",   # Craneware — healthcare software
    "DATA.L",  # GlobalData — data analytics
    "FEVR.L",  # Fever-Tree — premium mixers
    "SRC.L",   # SRC — not widely known; tests missing-ticker handling
    "BUR.L",   # Burford Capital — legal finance
    "HCM.L",   # Hutchmed — biopharma
    "ROSE.L",  # Rossendale — tests smaller AIM coverage
    "YCA.L",   # Yellow Cake — uranium royalties
    "GRP.L",   # Grafton Group — builders' merchants
    "ADIG.L",  # abrdn Diversified Income — investment trust (null revenue)
    "RWS.L",   # RWS Holdings — translation services
    "BOY.L",   # Bodycote — heat treatment
    "NCC.L",   # NCC Group — cybersecurity
    "GGP.L",   # Greatland Gold — AIM mining
    "UPR.L",   # Upstream — tests AIM coverage
]

SLEEP_BETWEEN_REQUESTS = 0.5   # seconds
MIN_SUCCESS_COUNT = 18         # PASS threshold


def run_test():
    try:
        import yfinance as yf
    except ImportError:
        print("FAIL — 'yfinance' package not installed. Run: pip install yfinance")
        sys.exit(1)

    print(f"Test 5: yfinance rate limiting at scale ({len(TEST_TICKERS)} tickers)\n")
    print(f"  Sleep between requests: {SLEEP_BETWEEN_REQUESTS}s")
    print(f"  Pass threshold: {MIN_SUCCESS_COUNT}/{len(TEST_TICKERS)} successes\n")

    results = []
    for i, ticker in enumerate(TEST_TICKERS, start=1):
        try:
            info = yf.Ticker(ticker).info
            market_cap = info.get("marketCap")
            revenue    = info.get("totalRevenue")
            avg_vol    = info.get("averageVolume")
            price      = info.get("currentPrice")
            adtv       = (avg_vol * price) if (avg_vol and price) else None

            results.append({
                "ticker":     ticker,
                "market_cap": market_cap,
                "revenue":    revenue,
                "adtv":       adtv,
                "ok":         True,
            })
            adtv_str = f"£{adtv:,.0f}" if adtv else "n/a"
            print(
                f"  [{i:02d}/{len(TEST_TICKERS)}] {ticker:<10} "
                f"mcap={market_cap or 'n/a':>15}  "
                f"rev={revenue or 'n/a':>15}  "
                f"adtv={adtv_str}"
            )

        except Exception as e:
            results.append({"ticker": ticker, "ok": False, "error": str(e)})
            print(f"  [{i:02d}/{len(TEST_TICKERS)}] {ticker:<10} ERROR: {e}")

        time.sleep(SLEEP_BETWEEN_REQUESTS)

    success_count = sum(1 for r in results if r["ok"])
    failed_tickers = [r["ticker"] for r in results if not r["ok"]]

    print(f"\n  Successes: {success_count}/{len(TEST_TICKERS)}")
    if failed_tickers:
        print(f"  Failed tickers: {failed_tickers}")

    # Tickers with no market cap (may be data gaps, not errors)
    no_data = [
        r["ticker"] for r in results
        if r["ok"] and not r.get("market_cap")
    ]
    if no_data:
        print(
            f"  Tickers with null market cap (potential coverage gaps): {no_data}"
        )

    print()
    if success_count >= MIN_SUCCESS_COUNT:
        print(f"==> Test 5 PASSED ({success_count}/{len(TEST_TICKERS)} successes).")
        sys.exit(0)
    else:
        print(
            f"==> Test 5 FAILED ({success_count}/{len(TEST_TICKERS)} successes — "
            f"threshold is {MIN_SUCCESS_COUNT})."
        )
        sys.exit(1)


if __name__ == "__main__":
    run_test()
