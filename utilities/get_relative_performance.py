"""
get_relative_performance.py

Calculates stock performance relative to a market benchmark across time windows.

Derived from outputs of get_price_history and get_index_snapshot.
Uses yfinance to retrieve benchmark index history for intrawindow comparison.

Benchmark tickers (all confirmed available in yfinance):
  ^FTAI  — AIM All-Share
  ^FTSC  — FTSE Small Cap
  ^FTMC  — FTSE 250

Execution: local machine only (yfinance calls).

Rate limiting: a 2-second sleep is applied after the index yfinance fetch.

Usage:
    from utilities.get_relative_performance import get_relative_performance
    result = get_relative_performance(price_history, index_snapshot)
"""

import os
import time
from datetime import datetime, timezone

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

# yfinance ticker symbols for each benchmark index
_INDEX_YF_TICKERS = {
    "AIM_ALL_SHARE": "^FTAI",
    "FTSE_SMALL_CAP": "^FTSC",
    "FTSE_250": "^FTMC",
}

# Approximate trading days per window
_WINDOW_ROWS = {
    "1w": 5,
    "1m": 21,
    "3m": 63,
}

_DEFAULT_WINDOWS = ["1w", "1m", "3m"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_relative_performance(
    price_history: dict,
    index_snapshot: dict,
    windows: list = None,
) -> dict:
    """
    Calculate stock performance relative to its market benchmark.

    Parameters
    ----------
    price_history : dict
        Output of get_price_history(). Must include "ticker", "current_price",
        and "windows" dict with per-window stats.
    index_snapshot : dict
        Output of get_index_snapshot(). Must include "index_name".
        May be None or have data_found=False — handled gracefully.
    windows : list, optional
        Subset of ["1w", "1m", "3m"]. Defaults to all three.

    Returns
    -------
    dict with keys:
        ticker                  str
        index_name              str | None
        vs_index_available      bool       True if index history retrieved
        windows                 dict       per-window results:
                                             stock_change_pct      float | None
                                             index_change_pct      float | None
                                             relative_performance  float | None
        position_in_52wk_range  float | None  0-100; stock vs its own 52wk range
        data_retrieved_at       datetime (UTC)
    """
    if windows is None:
        windows = _DEFAULT_WINDOWS

    retrieved_at = datetime.now(timezone.utc)
    ticker = price_history.get("ticker", "")
    index_name = index_snapshot.get("index_name") if index_snapshot else None

    # --- Fetch benchmark index history from yfinance ---
    index_hist = _fetch_index_history(index_name)
    vs_index_available = index_hist is not None and not index_hist.empty

    # --- Per-window calculations ---
    window_results = {}
    for w in windows:
        if w not in _WINDOW_ROWS:
            continue

        stock_change = _get_stock_change(price_history, w)
        index_change = (
            _compute_index_change(index_hist, _WINDOW_ROWS[w])
            if vs_index_available
            else None
        )

        if stock_change is not None and index_change is not None:
            rel = round(stock_change - index_change, 4)
        else:
            rel = None

        window_results[w] = {
            "stock_change_pct":    stock_change,
            "index_change_pct":    index_change,
            "relative_performance": rel,
        }

    # --- Position in 52-week range (stock vs its own 52wk range) ---
    pos_52wk = _compute_position_in_52wk_range(price_history)

    return {
        "ticker":                  ticker,
        "index_name":              index_name,
        "vs_index_available":      vs_index_available,
        "windows":                 window_results,
        "position_in_52wk_range":  pos_52wk,
        "data_retrieved_at":       retrieved_at,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_index_history(index_name: str) -> "pd.DataFrame | None":
    """
    Fetch 3 months of daily history for the benchmark index via yfinance.
    Returns None on failure or if no data available.
    Always sleeps _RATE_LIMIT_SECONDS after the fetch attempt.
    """
    yf_ticker = _INDEX_YF_TICKERS.get(index_name) if index_name else None
    if not yf_ticker:
        return None

    hist = None
    try:
        idx = yf.Ticker(yf_ticker)
        hist = idx.history(period="3mo", interval="1d")
    except Exception:
        hist = None

    time.sleep(_RATE_LIMIT_SECONDS)

    if hist is None or hist.empty:
        return None

    hist = hist.dropna(subset=["Close"])
    return hist if not hist.empty else None


def _get_stock_change(price_history: dict, window: str) -> "float | None":
    """Extract change_pct for a window from price_history output."""
    windows = price_history.get("windows", {})
    w_data = windows.get(window)
    if not w_data:
        return None
    return w_data.get("change_pct")


def _compute_index_change(index_hist: "pd.DataFrame", n_rows: int) -> "float | None":
    """
    Compute index percentage change over the last n_rows trading days.
    Uses all available rows if fewer than n_rows exist.
    Returns None if fewer than 2 data points or start price is zero.
    """
    if index_hist is None or index_hist.empty:
        return None
    if "Close" not in index_hist.columns:
        return None

    closes = (
        index_hist["Close"].iloc[-n_rows:]
        if len(index_hist) >= n_rows
        else index_hist["Close"]
    )
    if len(closes) < 2:
        return None

    start = float(closes.iloc[0])
    end = float(closes.iloc[-1])
    if start == 0:
        return None

    return round((end - start) / start * 100, 4)


def _compute_position_in_52wk_range(price_history: dict) -> "float | None":
    """
    Calculate current price as % position within the stock's 52-week range.

    Formula: (current_price - 52wk_low) / (52wk_high - 52wk_low) * 100
    Clamped to [0, 100].
    Uses the high and low from the "1y" window of price_history.
    Returns None if current_price or 1y window data is unavailable,
    or if the 52wk range is zero.
    """
    current_price = price_history.get("current_price")
    if current_price is None:
        return None

    yr_window = price_history.get("windows", {}).get("1y")
    if not yr_window:
        return None

    high_52wk = yr_window.get("high")
    low_52wk = yr_window.get("low")
    if high_52wk is None or low_52wk is None:
        return None

    range_width = high_52wk - low_52wk
    if range_width <= 0:
        return None

    position = (current_price - low_52wk) / range_width * 100
    position = max(0.0, min(100.0, position))
    return round(position, 2)


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from utilities.get_price_history import get_price_history
    from utilities.get_company_profile import get_company_profile
    from utilities.get_index_snapshot import get_index_snapshot

    test_cases = [
        ("LLOY", "Lloyds — liquid main market"),
        ("ECR",  "ECR Minerals — AIM"),
    ]

    print("get_relative_performance — smoke test\n")
    for ticker, desc in test_cases:
        print(f"--- {ticker} ({desc}) ---")
        ph = get_price_history(ticker)
        profile = get_company_profile(ticker)
        snap = get_index_snapshot(
            index_membership=profile.get("index_membership"),
            market_cap_gbp=profile.get("market_cap_gbp"),
        )
        result = get_relative_performance(ph, snap)
        print(f"  index_name:             {result['index_name']}")
        print(f"  vs_index_available:     {result['vs_index_available']}")
        print(f"  position_in_52wk_range: {result['position_in_52wk_range']}%")
        for w, stats in result.get("windows", {}).items():
            print(
                f"  [{w}] stock={stats['stock_change_pct']}%  "
                f"index={stats['index_change_pct']}%  "
                f"rel={stats['relative_performance']}%"
            )
        print()
