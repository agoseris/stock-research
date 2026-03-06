"""
get_volatility_metrics.py

Returns volatility and liquidity indicators for an LSE/AIM stock using yfinance.
Used by Layer 1 platform assessment tools.

Execution: local machine only.
  yfinance is sensitive to IP-based rate limiting. Do not run from the GCP VM.

Rate limiting: a 2-second sleep is applied after every yfinance fetch.

Volatility calculation:
  annualised_vol = std(daily_log_returns) * sqrt(252)
  where daily_log_returns = ln(close_t / close_t-1)
  computed over the last `period_days` rows of daily history.

Usage:
    from utilities.get_volatility_metrics import get_volatility_metrics
    result = get_volatility_metrics("FGEN")
"""

import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

_UTILITIES_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTILITIES_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RATE_LIMIT_SECONDS = 2

# Trading days per year — used to annualise volatility
_TRADING_DAYS_PER_YEAR = 252

# Volume trend: 10d vs 30d avg; >10% divergence = trend
_TREND_THRESHOLD = 0.10

# Liquidity flag thresholds (provisional — spec notes these are order-of-magnitude)
_LIQUIDITY_HIGH   = 1_000_000
_LIQUIDITY_MEDIUM =   100_000
_LIQUIDITY_LOW    =    10_000
# < _LIQUIDITY_LOW → very_low

# Data quality: "good" if rows >= period_days, else "sparse", else "unavailable"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_volatility_metrics(
    ticker: str,
    period_days: int = 30,
) -> dict:
    """
    Return volatility and liquidity indicators for an LSE/AIM stock.

    Parameters
    ----------
    ticker : str
        LSE ticker WITHOUT the .L suffix (e.g. "FGEN", "LLOY").
        The .L suffix is appended automatically.
    period_days : int
        Lookback window in trading days for volatility calculation. Default 30.

    Returns
    -------
    dict with keys:
        ticker              str
        ticker_yf           str
        volatility_30d      float | None    annualised std of daily log returns
        avg_daily_volume_30d int | None      mean volume over last 30 trading days
        volume_trend        str | None       "increasing" | "decreasing" | "stable"
        liquidity_flag      str | None       "high" | "medium" | "low" | "very_low"
        bid_ask_spread      None             not available via yfinance
        data_quality_flag   str             "good" | "sparse" | "unavailable"
        data_retrieved_at   datetime (UTC)
    """
    ticker_yf = ticker if ticker.upper().endswith(".L") else f"{ticker}.L"
    retrieved_at = datetime.now(timezone.utc)

    hist = None
    try:
        yf_ticker = yf.Ticker(ticker_yf)
        # Fetch 3 months to cover: period_days volatility window + 30d volume avg + 10d trend
        hist = yf_ticker.history(period="3mo", interval="1d")
    except Exception as e:
        time.sleep(_RATE_LIMIT_SECONDS)
        return _unavailable_result(ticker, ticker_yf, retrieved_at, error=str(e))

    time.sleep(_RATE_LIMIT_SECONDS)

    if hist is None or hist.empty:
        return _unavailable_result(ticker, ticker_yf, retrieved_at)

    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        return _unavailable_result(ticker, ticker_yf, retrieved_at)

    volatility    = _compute_volatility(hist, period_days)
    avg_volume    = _compute_avg_volume(hist, n=30)
    volume_trend  = _compute_volume_trend(hist)
    liquidity     = _classify_liquidity(avg_volume)
    data_quality  = _classify_quality(hist, period_days)

    return {
        "ticker":               ticker,
        "ticker_yf":            ticker_yf,
        "volatility_30d":       volatility,
        "avg_daily_volume_30d": avg_volume,
        "volume_trend":         volume_trend,
        "liquidity_flag":       liquidity,
        "bid_ask_spread":       None,   # not available via yfinance — known limitation
        "data_quality_flag":    data_quality,
        "data_retrieved_at":    retrieved_at,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_volatility(hist: pd.DataFrame, period_days: int) -> float:
    """
    Annualised standard deviation of daily log returns.
    Uses the last `period_days` rows. Returns None if insufficient data.
    """
    if hist.empty or "Close" not in hist.columns:
        return None
    closes = hist["Close"].iloc[-period_days:] if len(hist) >= period_days else hist["Close"]
    if len(closes) < 2:
        return None

    log_returns = np.log(closes / closes.shift(1)).dropna()
    if len(log_returns) < 2:
        return None

    return round(float(log_returns.std() * np.sqrt(_TRADING_DAYS_PER_YEAR)), 6)


def _compute_avg_volume(hist: pd.DataFrame, n: int = 30) -> int:
    """
    Mean daily volume over the last n rows, excluding zero-volume days.
    Returns None if no non-zero volume rows available.
    """
    if hist.empty or "Volume" not in hist.columns:
        return None
    window = hist["Volume"].iloc[-n:] if len(hist) >= n else hist["Volume"]
    non_zero = window.replace(0, pd.NA).dropna()
    if non_zero.empty:
        return None
    return int(non_zero.mean())


def _compute_volume_trend(hist: pd.DataFrame) -> str:
    """
    Classify volume trend by comparing 10d avg vs 30d avg.
    Returns "increasing", "decreasing", "stable", or None if insufficient data.
    """
    if len(hist) < 10:
        return None

    vol_30d = hist["Volume"].iloc[-30:].replace(0, pd.NA).dropna() if len(hist) >= 30 else hist["Volume"].replace(0, pd.NA).dropna()
    vol_10d = hist["Volume"].iloc[-10:].replace(0, pd.NA).dropna()

    if vol_30d.empty or vol_10d.empty:
        return None

    avg_30d = float(vol_30d.mean())
    avg_10d = float(vol_10d.mean())

    if avg_30d == 0:
        return None

    ratio = avg_10d / avg_30d
    if ratio > 1 + _TREND_THRESHOLD:
        return "increasing"
    if ratio < 1 - _TREND_THRESHOLD:
        return "decreasing"
    return "stable"


def _classify_liquidity(avg_volume: int) -> str:
    """Assign liquidity flag from avg daily volume thresholds."""
    if avg_volume is None:
        return None
    if avg_volume > _LIQUIDITY_HIGH:
        return "high"
    if avg_volume >= _LIQUIDITY_MEDIUM:
        return "medium"
    if avg_volume >= _LIQUIDITY_LOW:
        return "low"
    return "very_low"


def _classify_quality(hist: pd.DataFrame, period_days: int) -> str:
    n = len(hist)
    if n == 0:
        return "unavailable"
    if n >= period_days:
        return "good"
    return "sparse"


def _unavailable_result(
    ticker: str, ticker_yf: str, retrieved_at: datetime, error: str = None
) -> dict:
    result = {
        "ticker":               ticker,
        "ticker_yf":            ticker_yf,
        "volatility_30d":       None,
        "avg_daily_volume_30d": None,
        "volume_trend":         None,
        "liquidity_flag":       None,
        "bid_ask_spread":       None,
        "data_quality_flag":    "unavailable",
        "data_retrieved_at":    retrieved_at,
    }
    if error:
        result["error"] = error
    return result


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_tickers = [
        ("LLOY", "Lloyds — liquid main market"),
        ("ECR",  "ECR Minerals — AIM"),
        ("INVALID_TICKER_XYZ", "unknown ticker"),
    ]

    print("get_volatility_metrics — smoke test\n")
    for ticker, desc in test_tickers:
        print(f"--- {ticker} ({desc}) ---")
        result = get_volatility_metrics(ticker)
        print(f"  data_quality_flag:    {result['data_quality_flag']}")
        print(f"  volatility_30d:       {result['volatility_30d']}")
        print(f"  avg_daily_volume_30d: {result['avg_daily_volume_30d']}")
        print(f"  volume_trend:         {result['volume_trend']}")
        print(f"  liquidity_flag:       {result['liquidity_flag']}")
        print(f"  bid_ask_spread:       {result['bid_ask_spread']}")
        if "error" in result:
            print(f"  error: {result['error']}")
        print()
