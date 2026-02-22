"""
market_data_yfinance.py

Implements MarketDataProviderBase using yfinance.

yfinance is an unofficial scraper of Yahoo Finance's undocumented internal
endpoints. It is not an official API and can break without warning. All
pipeline logic calls MarketDataProviderBase — YFinanceProvider is the PoC
implementation behind that interface. Swapping to EODHD or any other source
is a contained change to this file only.

Rate limiting: 0.5s sleep after every get_info() call. This is enforced
here, in the provider, not in the pipeline. At 400-600 tickers per quarterly
refresh the total wall time is 3-5 minutes — acceptable for a batch job.

Ticker format for UK stocks: Yahoo Finance uses the .L suffix.
SDY (LSE) → SDY.L (Yahoo). The pipeline is responsible for converting
tickers before calling this provider.

All methods are non-blocking best-effort — exceptions are caught, logged to
stdout, and an empty result is returned. The pipeline must handle empty
returns gracefully.
"""

import time
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from abstractions import MarketDataProviderBase

try:
    import yfinance as yf
    import pandas as pd
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "yfinance and pandas are required for YFinanceProvider. "
        "Install with: pip install yfinance pandas"
    ) from e

load_dotenv()

# Fields to extract from yfinance .info dict.
# Documented in brief Section 6.2.
_INFO_FIELDS = [
    "longName",
    "marketCap",
    "totalRevenue",
    "revenueGrowth",
    "sector",
    "industry",
    "averageVolume",
    "currentPrice",
    "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow",
    "trailingPE",
    "trailingEps",
    "isin",
]

# Delay between get_info() calls (seconds).
# 0.5s is the rate-limit budget specified in the brief (Section 12).
_RATE_LIMIT_SLEEP = 0.5


class YFinanceProvider(MarketDataProviderBase):
    """
    yfinance implementation of MarketDataProviderBase.

    Concrete providers are injected at the entry point — never instantiated
    inside pipeline logic or other providers.
    """

    def get_info(self, ticker_yahoo: str) -> dict:
        """
        Fetch market data for the given Yahoo Finance ticker.

        Returns a dict with keys drawn from _INFO_FIELDS where available.
        Fields absent from the yfinance response are omitted from the dict
        (callers use .get() with a default of None).

        A 0.5s sleep is applied after every call to pace requests at scale.
        """
        try:
            raw = yf.Ticker(ticker_yahoo).info
            result = {k: raw.get(k) for k in _INFO_FIELDS if k in raw}
            return result
        except Exception as e:
            print(f"  [YFinance] get_info({ticker_yahoo}) failed: {e}")
            return {}
        finally:
            # Rate limiting enforced here — not in the pipeline.
            time.sleep(_RATE_LIMIT_SLEEP)

    def get_price_history(self, ticker_yahoo: str, period: str = "1y") -> "pd.DataFrame":
        """
        Fetch OHLCV price history for the given Yahoo Finance ticker.

        period follows yfinance conventions: '1y', '2y', '5y', 'max', etc.
        Returns an empty DataFrame on failure.

        No rate-limit sleep here — price history pulls are done sparingly
        and callers manage their own pacing when batching history requests.
        """
        try:
            ticker = yf.Ticker(ticker_yahoo)
            df = ticker.history(period=period)
            return df
        except Exception as e:
            print(f"  [YFinance] get_price_history({ticker_yahoo}, {period}) failed: {e}")
            return pd.DataFrame()


if __name__ == "__main__":
    """
    Standalone test — run from backend/ directory.

    Tests get_info() and get_price_history() against SDY.L (Speedy Hire),
    a known FTSE Small Cap operating company.

    Expected: non-null marketCap, totalRevenue, averageVolume, currentPrice.
    """
    print("YFinanceProvider standalone test\n")

    provider = YFinanceProvider()

    TEST_TICKER = "SDY.L"
    print(f"Fetching info for {TEST_TICKER}...")
    info = provider.get_info(TEST_TICKER)

    if not info:
        print("FAIL — get_info() returned empty dict.")
    else:
        print(f"  longName:      {info.get('longName')}")
        print(f"  marketCap:     {info.get('marketCap')}")
        print(f"  totalRevenue:  {info.get('totalRevenue')}")
        print(f"  sector:        {info.get('sector')}")
        print(f"  industry:      {info.get('industry')}")
        print(f"  averageVolume: {info.get('averageVolume')}")
        print(f"  currentPrice:  {info.get('currentPrice')}")

        required = ["marketCap", "totalRevenue", "averageVolume", "currentPrice"]
        missing = [f for f in required if not info.get(f)]
        if missing:
            print(f"\nFAIL — missing required fields: {missing}")
        else:
            print("\nget_info() PASS")

    print(f"\nFetching 1y price history for {TEST_TICKER}...")
    df = provider.get_price_history(TEST_TICKER, period="1y")
    if df.empty:
        print("FAIL — get_price_history() returned empty DataFrame.")
    else:
        print(f"  Rows: {len(df)}")
        print(f"  Columns: {list(df.columns)}")
        print(f"  Latest row:\n{df.tail(1)}")
        print("\nget_price_history() PASS")
