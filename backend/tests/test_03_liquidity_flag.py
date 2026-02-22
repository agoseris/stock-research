"""
test_03_liquidity_flag.py

Pre-build verification test 3 of 5.

Verifies the liquidity flag calculation (ADTV = averageVolume × currentPrice)
against three test tickers:

  SDY.L  — Speedy Hire (known liquid Tier 1 operating company)
  JET2.L — Jet2 (known liquid Tier 2 company)
  ADIG.L — abrdn Diversified Income and Growth (investment trust —
            expected to have null/zero revenue, confirming exclusion logic)

PASS criteria:
  - SDY.L and JET2.L: non-null ADTV, classifiable liquidity flag
  - ADIG.L: revenue is null or zero (confirms investment trust exclusion)
  - No exceptions raised during data fetch

Run from the backend/ directory:
    python tests/test_03_liquidity_flag.py
"""

import sys
import time

TEST_TICKERS = [
    ("SDY.L",  "Speedy Hire",                        "operating — expect non-null ADTV"),
    ("JET2.L", "Jet2",                               "operating — expect non-null ADTV"),
    ("ADIG.L", "abrdn Diversified Income and Growth", "investment trust — expect null revenue"),
]

ADTV_LIQUID_THRESHOLD   = 500_000
ADTV_MODERATE_THRESHOLD = 100_000


def liquidity_flag(avg_volume, price) -> str:
    if avg_volume is None or price is None or avg_volume == 0 or price == 0:
        return "UNKNOWN"
    adtv = avg_volume * price
    if adtv > ADTV_LIQUID_THRESHOLD:
        return "LIQUID"
    if adtv > ADTV_MODERATE_THRESHOLD:
        return "MODERATE"
    return "ILLIQUID"


def run_test():
    try:
        import yfinance as yf
    except ImportError:
        print("FAIL — 'yfinance' package not installed. Run: pip install yfinance")
        sys.exit(1)

    print("Test 3: yfinance liquidity flag calculation\n")

    results = []
    for ticker, description, expectation in TEST_TICKERS:
        print(f"  {ticker} — {description} ({expectation})")
        try:
            info = yf.Ticker(ticker).info
        except Exception as e:
            print(f"    FAIL — yfinance raised an exception: {e}\n")
            results.append((ticker, False, str(e)))
            time.sleep(0.5)
            continue

        vol   = info.get("averageVolume")
        price = info.get("currentPrice")
        rev   = info.get("totalRevenue")
        adtv  = (vol * price) if (vol and price) else None
        flag  = liquidity_flag(vol, price)

        print(f"    averageVolume:  {vol}")
        print(f"    currentPrice:   {price}")
        print(f"    ADTV:           £{adtv:,.0f}" if adtv is not None else "    ADTV: unavailable")
        print(f"    liquidity_flag: {flag}")
        print(f"    totalRevenue:   {rev}")

        # Evaluate per-ticker PASS/FAIL
        if ticker in ("SDY.L", "JET2.L"):
            passed = adtv is not None and flag != "UNKNOWN"
            status = "PASS" if passed else "FAIL"
            note   = "" if passed else " — ADTV unavailable"
        else:
            # ADIG.L — expect revenue null or zero
            passed = (rev is None or rev == 0)
            status = "PASS" if passed else "FAIL"
            note   = "" if passed else f" — expected null/zero revenue, got {rev}"

        print(f"    [{status}]{note}\n")
        results.append((ticker, passed, ""))
        time.sleep(0.5)

    all_passed = all(ok for _, ok, _ in results)
    if all_passed:
        print("==> Test 3 PASSED.")
        sys.exit(0)
    else:
        failed = [t for t, ok, _ in results if not ok]
        print(f"==> Test 3 FAILED for: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    run_test()
