"""
get_price_history.py

Returns price history across four time windows using yfinance.
Used by Layer 1 platform assessment tools.

Execution: local machine only.
  yfinance is sensitive to IP-based rate limiting. Do not run from the GCP VM.

Rate limiting: a 2-second sleep is applied after every yfinance fetch.
  When calling this tool in a loop, no additional delay is required.

Price units: yfinance returns LSE/AIM stock prices in pence (GBp), not GBP.
  e.g. a stock trading at £1.20 appears as 120.0 in results.
  No unit conversion is applied — callers should be aware of this.

Usage:
    from utilities.get_price_history import get_price_history
    result = get_price_history("FGEN", windows=["1w", "1m", "3m", "1y"])
"""

import os
import time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

# Load credentials (needed transitively if Firestore is called from the same session)
_UTILITIES_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTILITIES_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, "backend", ".env"))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum sleep after each yfinance fetch (rate limiting)
_RATE_LIMIT_SECONDS = 2

# Approximate trading days per window — history is fetched as 1y and sliced
_WINDOW_ROWS = {
    "1w":  5,
    "1m":  21,
    "3m":  63,
    "1y":  252,
}

_ALL_WINDOWS = ["1w", "1m", "3m", "1y"]

# Data quality: "good" requires at least this many rows in the 1y fetch
_GOOD_ROW_THRESHOLD = 200  # ~80% of 252 expected trading days


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_price_history(
    ticker: str,
    windows: list = None,
) -> dict:
    """
    Return price history across time windows for an LSE/AIM stock.

    Parameters
    ----------
    ticker : str
        LSE ticker WITHOUT the .L suffix (e.g. "FGEN", "LLOY", "ECR").
        The .L suffix is appended automatically for the yfinance call.
        If the ticker already ends in ".L" it is used as-is.
    windows : list, optional
        Subset of ["1w", "1m", "3m", "1y"]. Defaults to all four.

    Returns
    -------
    dict with keys:
        ticker              str     original ticker (without .L)
        ticker_yf           str     ticker passed to yfinance (with .L)
        current_price       float | None
        current_price_note  str | None   "last_close" when real-time price
                                         unavailable (outside market hours)
        current_volume      int | None
        windows             dict    keyed by window name, each containing:
                                      start_price, end_price, change_pct,
                                      high, low, avg_daily_volume
        data_retrieved_at   datetime (UTC)
        data_quality_flag   str     "good" | "sparse" | "unavailable"
    """
    if windows is None:
        windows = _ALL_WINDOWS

    ticker_yf = ticker if ticker.upper().endswith(".L") else f"{ticker}.L"
    retrieved_at = datetime.now(timezone.utc)

    hist = None
    try:
        yf_ticker = yf.Ticker(ticker_yf)
        hist = yf_ticker.history(period="1y", interval="1d")
    except Exception as e:
        time.sleep(_RATE_LIMIT_SECONDS)
        return _unavailable_result(ticker, ticker_yf, retrieved_at, error=str(e))

    time.sleep(_RATE_LIMIT_SECONDS)

    if hist is None or hist.empty:
        return _unavailable_result(ticker, ticker_yf, retrieved_at)

    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        return _unavailable_result(ticker, ticker_yf, retrieved_at)

    # Current price + volume
    current_price, current_price_note = _get_current_price(yf_ticker, hist)
    current_volume = _get_current_volume(yf_ticker, hist)

    # Per-window stats
    window_results = {}
    for w in windows:
        if w in _WINDOW_ROWS:
            window_results[w] = _compute_window(hist, _WINDOW_ROWS[w])

    return {
        "ticker":             ticker,
        "ticker_yf":          ticker_yf,
        "current_price":      current_price,
        "current_price_note": current_price_note,
        "current_volume":     current_volume,
        "windows":            window_results,
        "data_retrieved_at":  retrieved_at,
        "data_quality_flag":  _classify_quality(hist),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_current_price(yf_ticker, hist) -> tuple:
    """
    Return (price, note) where note is None for real-time price or
    "last_close" when falling back to the most recent historical close.
    """
    try:
        fast = yf_ticker.fast_info
        price = getattr(fast, "last_price", None)
        if price is not None and not pd.isna(price) and float(price) > 0:
            return float(price), None
    except Exception:
        pass

    # Fallback: most recent close from history
    try:
        val = hist["Close"].iloc[-1]
        if not pd.isna(val):
            return float(val), "last_close"
    except Exception:
        pass

    return None, None


def _get_current_volume(yf_ticker, hist) -> int:
    """Return current/recent volume, falling back to last row in history."""
    try:
        fast = yf_ticker.fast_info
        vol = getattr(fast, "three_month_average_volume", None)
        if vol is not None and not pd.isna(vol) and vol > 0:
            return int(vol)
    except Exception:
        pass

    try:
        vol = hist["Volume"].iloc[-1]
        if not pd.isna(vol):
            return int(vol)
    except Exception:
        pass

    return None


def _compute_window(hist: pd.DataFrame, n_rows: int) -> dict:
    """
    Compute OHLCV stats for the last n_rows rows of history.
    If history has fewer rows than n_rows, uses all available rows.
    Returns None if computation fails.
    """
    if hist.empty:
        return None

    window = hist.iloc[-n_rows:] if len(hist) >= n_rows else hist

    try:
        start_price = float(window["Close"].iloc[0])
        end_price   = float(window["Close"].iloc[-1])
        change_pct  = (end_price - start_price) / start_price * 100 if start_price else None
        high        = float(window["High"].max())
        low         = float(window["Low"].min())

        # Average daily volume — exclude zero-volume days (market holidays, etc.)
        non_zero = window["Volume"].replace(0, pd.NA).dropna()
        avg_volume = int(non_zero.mean()) if not non_zero.empty else None

        return {
            "start_price":      round(start_price, 4),
            "end_price":        round(end_price, 4),
            "change_pct":       round(change_pct, 4) if change_pct is not None else None,
            "high":             round(high, 4),
            "low":              round(low, 4),
            "avg_daily_volume": avg_volume,
        }
    except Exception:
        return None


def _classify_quality(hist: pd.DataFrame) -> str:
    n = len(hist)
    if n == 0:
        return "unavailable"
    if n >= _GOOD_ROW_THRESHOLD:
        return "good"
    return "sparse"


def _unavailable_result(
    ticker: str, ticker_yf: str, retrieved_at: datetime, error: str = None
) -> dict:
    result = {
        "ticker":             ticker,
        "ticker_yf":          ticker_yf,
        "current_price":      None,
        "current_price_note": None,
        "current_volume":     None,
        "windows":            {},
        "data_retrieved_at":  retrieved_at,
        "data_quality_flag":  "unavailable",
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

    print("get_price_history — smoke test\n")
    for ticker, desc in test_tickers:
        print(f"--- {ticker} ({desc}) ---")
        result = get_price_history(ticker)
        print(f"  ticker_yf:          {result['ticker_yf']}")
        print(f"  data_quality_flag:  {result['data_quality_flag']}")
        print(f"  current_price:      {result['current_price']}  note={result['current_price_note']}")
        print(f"  current_volume:     {result['current_volume']}")
        for w, stats in result.get("windows", {}).items():
            if stats:
                print(
                    f"  [{w}] start={stats['start_price']}  end={stats['end_price']}  "
                    f"chg={stats['change_pct']}%  vol={stats['avg_daily_volume']}"
                )
            else:
                print(f"  [{w}] no data")
        if "error" in result:
            print(f"  error: {result['error']}")
        print()
