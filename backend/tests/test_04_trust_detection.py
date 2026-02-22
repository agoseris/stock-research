"""
test_04_trust_detection.py

Pre-build verification test 4 of 5.

Verifies the three-filter investment trust detection logic against a known
investment trust: ADIG.L (abrdn Diversified Income and Growth).

The three filters are applied in order (consistent with brief Section 9.1):
  Filter 1: sector/industry indicates financial vehicle
  Filter 2: company name matches trust keyword list
  Filter 3: revenue is null or zero

PASS criteria: at least two of the three filters independently identify
ADIG.L as an investment trust. If all three fire, that is ideal but not
required — some trusts lack yfinance sector data.

The key MUST-PASS condition is Filter 3 (null/zero revenue), as this is
the pipeline's catch-all backstop.

Run from the backend/ directory:
    python tests/test_04_trust_detection.py
"""

import sys
import time

TEST_TICKER = "ADIG.L"  # abrdn Diversified Income and Growth

TRUST_KEYWORDS = [
    "trust", "fund", "income & growth", "investment company",
    "investment trust", "capital & income", "equity income",
    "growth & income", "managed income", "global income",
]

FINANCIAL_VEHICLE_SECTORS = {
    "asset management", "financial services", "closed-end fund",
    "investment trusts", "fund", "etf",
}


def is_investment_trust_by_sector(info: dict) -> bool:
    sector   = (info.get("sector")   or "").lower()
    industry = (info.get("industry") or "").lower()
    for kw in FINANCIAL_VEHICLE_SECTORS:
        if kw in sector or kw in industry:
            return True
    return False


def is_investment_trust_by_name(name: str) -> bool:
    name_lower = name.lower()
    return any(kw in name_lower for kw in TRUST_KEYWORDS)


def has_zero_revenue(info: dict) -> bool:
    rev = info.get("totalRevenue")
    return rev is None or rev == 0


def run_test():
    try:
        import yfinance as yf
    except ImportError:
        print("FAIL — 'yfinance' package not installed. Run: pip install yfinance")
        sys.exit(1)

    print(f"Test 4: Investment trust detection for {TEST_TICKER}\n")

    try:
        info = yf.Ticker(TEST_TICKER).info
    except Exception as e:
        print(f"FAIL — yfinance raised an exception: {e}")
        sys.exit(1)

    company_name = info.get("longName") or info.get("shortName") or TEST_TICKER

    print(f"  Company name:   {company_name}")
    print(f"  Sector:         {info.get('sector')}")
    print(f"  Industry:       {info.get('industry')}")
    print(f"  Total revenue:  {info.get('totalRevenue')}")
    print()

    # Evaluate each filter
    f1 = is_investment_trust_by_sector(info)
    f2 = is_investment_trust_by_name(company_name)
    f3 = has_zero_revenue(info)

    print(f"  Filter 1 (sector/industry):    {'FIRES' if f1 else 'does not fire'}")
    print(f"  Filter 2 (name pattern):       {'FIRES' if f2 else 'does not fire'}")
    print(f"  Filter 3 (null/zero revenue):  {'FIRES' if f3 else 'does not fire'}")
    print()

    filters_fired = sum([f1, f2, f3])

    # Filter 3 is the mandatory catch-all
    if not f3:
        print(
            "FAIL — Filter 3 (null/zero revenue) did not fire. "
            f"Revenue = {info.get('totalRevenue')}. "
            "This is the pipeline's catch-all backstop — it must trigger."
        )
        sys.exit(1)

    if filters_fired < 2:
        print(
            f"FAIL — only {filters_fired}/3 filters fired. "
            "At least 2 must independently identify this as an investment trust."
        )
        sys.exit(1)

    print(f"==> Test 4 PASSED ({filters_fired}/3 filters fired).")
    sys.exit(0)


if __name__ == "__main__":
    run_test()
