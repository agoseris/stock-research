"""
test_02_yfinance_basics.py

Pre-build verification test 2 of 5.

Verifies that yfinance returns the four core fields required for pipeline
logic from a known Tier 1 operating company: SDY.L (Speedy Hire).

Required fields (MUST be non-null for PASS):
  marketCap, totalRevenue, averageVolume, currentPrice

Optional fields (logged but do not affect PASS/FAIL):
  sector, industry, fiftyTwoWeekHigh, fiftyTwoWeekLow

Run from the backend/ directory:
    python tests/test_02_yfinance_basics.py

Expected outcome: PASS — all four required fields are non-null.
Sector/industry may or may not be populated — either is acceptable.
"""

import sys


REQUIRED_FIELDS = ["marketCap", "totalRevenue", "averageVolume", "currentPrice"]
OPTIONAL_FIELDS = ["sector", "industry", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
                   "trailingPE", "trailingEps", "isin"]
TEST_TICKER = "SDY.L"  # Speedy Hire — known FTSE Small Cap operating company


def run_test():
    try:
        import yfinance as yf
    except ImportError:
        print("FAIL — 'yfinance' package not installed. Run: pip install yfinance")
        sys.exit(1)

    print(f"Test 2: yfinance market data for {TEST_TICKER} (Speedy Hire)\n")

    try:
        info = yf.Ticker(TEST_TICKER).info
    except Exception as e:
        print(f"FAIL — yfinance raised an exception: {e}")
        sys.exit(1)

    if not info:
        print("FAIL — yfinance returned an empty dict.")
        sys.exit(1)

    # Print all required and optional fields
    print("  Required fields:")
    missing = []
    for field in REQUIRED_FIELDS:
        value = info.get(field)
        status = "OK" if value else "MISSING"
        print(f"    {field}: {value}  [{status}]")
        if not value:
            missing.append(field)

    print("\n  Optional fields:")
    for field in OPTIONAL_FIELDS:
        value = info.get(field)
        print(f"    {field}: {value}")

    print()
    if missing:
        print(f"FAIL — missing required fields: {missing}")
        sys.exit(1)
    else:
        print("==> Test 2 PASSED.")
        sys.exit(0)


if __name__ == "__main__":
    run_test()
