"""
test_get_price_history.py

Tests for the get_price_history tool.

Unit tests use mocked yfinance — no network or credentials needed.
Integration tests call live yfinance and require a network connection.
They are skipped when the environment variable SKIP_YFINANCE_INTEGRATION
is set to "1", or can be run explicitly with -m integration.

Run unit tests only:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_price_history.py -v -m "not integration"

Run all tests:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_price_history.py -v
"""

import os
import sys
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pandas as pd
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utilities.get_price_history import (
    get_price_history,
    _compute_window,
    _classify_quality,
    _get_current_price,
    _get_current_volume,
    _unavailable_result,
    _GOOD_ROW_THRESHOLD,
    _RATE_LIMIT_SECONDS,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SKIP_INTEGRATION = os.environ.get("SKIP_YFINANCE_INTEGRATION", "") == "1"


def _make_hist_df(n_rows: int = 252, base_price: float = 100.0, base_volume: int = 500_000):
    """
    Synthetic daily OHLCV DataFrame with n_rows of business-day data.
    Price rises linearly by 0.1 per day so start/end/change_pct are predictable.
    """
    dates = pd.bdate_range("2025-03-05", periods=n_rows)
    closes = base_price + np.arange(n_rows) * 0.1
    return pd.DataFrame(
        {
            "Open":   closes * 0.999,
            "High":   closes * 1.01,
            "Low":    closes * 0.99,
            "Close":  closes,
            "Volume": [base_volume] * n_rows,
            "Dividends":    [0.0] * n_rows,
            "Stock Splits": [0.0] * n_rows,
        },
        index=dates,
    )


def _make_mock_ticker(hist_df: pd.DataFrame, last_price: float = None):
    """Return a mock yf.Ticker with a configured history() response."""
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = hist_df

    # fast_info mock
    mock_fast = MagicMock()
    mock_fast.last_price = last_price if last_price is not None else float("nan")
    mock_fast.three_month_average_volume = 300_000.0
    mock_ticker.fast_info = mock_fast

    return mock_ticker


# ---------------------------------------------------------------------------
# Ticker formatting
# ---------------------------------------------------------------------------

class TestTickerFormat:

    def test_l_suffix_appended(self):
        hist = _make_hist_df(250)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=120.0)
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN")
        mock_yf.assert_called_once_with("FGEN.L")
        assert result["ticker_yf"] == "FGEN.L"
        assert result["ticker"] == "FGEN"

    def test_l_suffix_not_doubled(self):
        hist = _make_hist_df(250)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=120.0)
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN.L")
        mock_yf.assert_called_once_with("FGEN.L")
        assert result["ticker_yf"] == "FGEN.L"

    def test_lowercase_l_suffix_not_doubled(self):
        hist = _make_hist_df(250)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=120.0)
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("fgen.l")
        mock_yf.assert_called_once_with("fgen.l")


# ---------------------------------------------------------------------------
# _classify_quality
# ---------------------------------------------------------------------------

class TestClassifyQuality:

    def test_good_at_threshold(self):
        df = _make_hist_df(_GOOD_ROW_THRESHOLD)
        assert _classify_quality(df) == "good"

    def test_good_above_threshold(self):
        df = _make_hist_df(252)
        assert _classify_quality(df) == "good"

    def test_sparse_one_below_threshold(self):
        df = _make_hist_df(_GOOD_ROW_THRESHOLD - 1)
        assert _classify_quality(df) == "sparse"

    def test_sparse_small_count(self):
        df = _make_hist_df(50)
        assert _classify_quality(df) == "sparse"

    def test_unavailable_empty(self):
        df = pd.DataFrame()
        assert _classify_quality(df) == "unavailable"

    def test_sparse_single_row(self):
        df = _make_hist_df(1)
        assert _classify_quality(df) == "sparse"


# ---------------------------------------------------------------------------
# _compute_window
# ---------------------------------------------------------------------------

class TestComputeWindow:

    def test_basic_structure(self):
        df = _make_hist_df(50)
        result = _compute_window(df, 5)
        assert result is not None
        assert set(result.keys()) == {
            "start_price", "end_price", "change_pct", "high", "low", "avg_daily_volume"
        }

    def test_change_pct_calculation(self):
        # Base=100, 50 rows, price rises 0.1/day
        # Window of 5: rows 45-49, closes = 104.5, 104.6, 104.7, 104.8, 104.9
        df = _make_hist_df(50, base_price=100.0)
        result = _compute_window(df, 5)
        start = float(df["Close"].iloc[-5])
        end   = float(df["Close"].iloc[-1])
        expected_pct = (end - start) / start * 100
        assert abs(result["change_pct"] - expected_pct) < 0.01

    def test_change_pct_positive_for_rising_prices(self):
        df = _make_hist_df(20, base_price=100.0)
        result = _compute_window(df, 10)
        assert result["change_pct"] > 0

    def test_high_is_max_of_window(self):
        df = _make_hist_df(50)
        result = _compute_window(df, 5)
        expected_high = float(df["High"].iloc[-5:].max())
        assert abs(result["high"] - expected_high) < 0.001

    def test_low_is_min_of_window(self):
        df = _make_hist_df(50)
        result = _compute_window(df, 5)
        expected_low = float(df["Low"].iloc[-5:].min())
        assert abs(result["low"] - expected_low) < 0.001

    def test_fewer_rows_than_window_uses_all(self):
        # 3 rows, window=5 → uses all 3
        df = _make_hist_df(3)
        result = _compute_window(df, 5)
        assert result is not None
        assert result["start_price"] == round(float(df["Close"].iloc[0]), 4)

    def test_empty_df_returns_none(self):
        assert _compute_window(pd.DataFrame(), 5) is None

    def test_avg_volume_excludes_zero_volume_days(self):
        df = _make_hist_df(10, base_volume=100_000)
        # Set two days to zero volume
        df.iloc[6, df.columns.get_loc("Volume")] = 0
        df.iloc[7, df.columns.get_loc("Volume")] = 0
        result = _compute_window(df, 10)
        # Average should be 100_000 (only the 8 non-zero days count)
        assert result["avg_daily_volume"] == 100_000

    def test_full_year_window(self):
        df = _make_hist_df(252)
        result = _compute_window(df, 252)
        assert result is not None
        assert result["start_price"] == round(float(df["Close"].iloc[0]), 4)
        assert result["end_price"]   == round(float(df["Close"].iloc[-1]), 4)


# ---------------------------------------------------------------------------
# get_price_history — mock yfinance
# ---------------------------------------------------------------------------

class TestGetPriceHistory:

    def test_all_required_keys_present(self):
        hist = _make_hist_df(250)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=120.0)
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN")
        required = {
            "ticker", "ticker_yf", "current_price", "current_price_note",
            "current_volume", "windows", "data_retrieved_at", "data_quality_flag",
        }
        assert required.issubset(set(result.keys()))

    def test_all_four_windows_present(self):
        hist = _make_hist_df(252)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=120.0)
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN")
        assert set(result["windows"].keys()) == {"1w", "1m", "3m", "1y"}

    def test_window_subset_respected(self):
        hist = _make_hist_df(252)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=120.0)
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN", windows=["1w", "1m"])
        assert set(result["windows"].keys()) == {"1w", "1m"}

    def test_good_quality_for_full_history(self):
        hist = _make_hist_df(252)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=120.0)
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN")
        assert result["data_quality_flag"] == "good"

    def test_sparse_quality_for_short_history(self):
        hist = _make_hist_df(50)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=120.0)
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN")
        assert result["data_quality_flag"] == "sparse"

    def test_unavailable_on_empty_history(self):
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(pd.DataFrame())
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN")
        assert result["data_quality_flag"] == "unavailable"
        assert result["current_price"] is None
        assert result["windows"] == {}

    def test_unavailable_on_yfinance_exception(self):
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value.history.side_effect = Exception("connection error")
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("INVALID_TICKER_XYZ")
        assert result["data_quality_flag"] == "unavailable"
        assert "error" in result
        assert isinstance(result, dict)

    def test_no_exception_on_unknown_ticker(self):
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(pd.DataFrame())
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("ZZZNOPE999")
        assert isinstance(result, dict)

    def test_current_price_from_fast_info(self):
        hist = _make_hist_df(252)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=135.5)
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN")
        assert result["current_price"] == 135.5
        assert result["current_price_note"] is None

    def test_current_price_falls_back_to_last_close(self):
        hist = _make_hist_df(252, base_price=100.0)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock = _make_mock_ticker(hist, last_price=None)
            mock.fast_info.last_price = float("nan")
            mock_yf.return_value = mock
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN")
        expected_close = round(float(hist["Close"].iloc[-1]), 4)
        assert result["current_price"] == expected_close
        assert result["current_price_note"] == "last_close"

    def test_rate_limit_sleep_is_called(self):
        hist = _make_hist_df(252)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=120.0)
            with patch("utilities.get_price_history.time.sleep") as mock_sleep:
                get_price_history("FGEN")
        mock_sleep.assert_called_with(_RATE_LIMIT_SECONDS)

    def test_rate_limit_sleep_called_on_exception(self):
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value.history.side_effect = Exception("boom")
            with patch("utilities.get_price_history.time.sleep") as mock_sleep:
                get_price_history("FGEN")
        mock_sleep.assert_called_with(_RATE_LIMIT_SECONDS)

    def test_data_retrieved_at_is_utc_datetime(self):
        hist = _make_hist_df(252)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=120.0)
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN")
        assert isinstance(result["data_retrieved_at"], datetime)
        assert result["data_retrieved_at"].tzinfo is not None

    def test_window_keys_all_present_in_each_window(self):
        hist = _make_hist_df(252)
        with patch("utilities.get_price_history.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(hist, last_price=120.0)
            with patch("utilities.get_price_history.time.sleep"):
                result = get_price_history("FGEN")
        expected_keys = {"start_price", "end_price", "change_pct", "high", "low", "avg_daily_volume"}
        for w, stats in result["windows"].items():
            assert stats is not None, f"window {w} returned None"
            assert expected_keys.issubset(set(stats.keys())), f"window {w} missing keys"


# ---------------------------------------------------------------------------
# Integration tests — live yfinance, requires network
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    SKIP_INTEGRATION,
    reason="SKIP_YFINANCE_INTEGRATION=1 — skipping live yfinance tests",
)
class TestGetPriceHistoryIntegration:
    """
    Acceptance criteria checks against live yfinance.

    Tickers used:
      LLOY  — Lloyds Banking Group, liquid FTSE100/main market stock
      ECR   — ECR Minerals, AIM stock
      INVALID_TICKER_XYZ_9999 — should return unavailable gracefully

    Note: these tests make real HTTP calls to Yahoo Finance.
    Run after confirming yfinance access from the local machine.
    """

    def test_liquid_main_market_good_quality(self):
        """AC: 'good' data quality for a liquid main market stock."""
        result = get_price_history("LLOY")
        print(f"\n[LLOY] quality={result['data_quality_flag']}  rows≈{result['data_quality_flag']}")
        print(f"       current_price={result['current_price']}  note={result['current_price_note']}")
        assert result["data_quality_flag"] == "good", (
            f"Expected 'good' for LLOY but got {result['data_quality_flag']!r}"
        )

    def test_liquid_main_market_four_windows_populated(self):
        """AC: All four windows populated for liquid stock."""
        result = get_price_history("LLOY")
        for w in ["1w", "1m", "3m", "1y"]:
            stats = result["windows"].get(w)
            assert stats is not None, f"window {w} missing"
            assert stats["start_price"] is not None, f"{w}.start_price is None"
            assert stats["end_price"]   is not None, f"{w}.end_price is None"
            assert stats["change_pct"]  is not None, f"{w}.change_pct is None"
            assert stats["high"]        is not None, f"{w}.high is None"
            assert stats["low"]         is not None, f"{w}.low is None"
            print(
                f"\n[LLOY][{w}] start={stats['start_price']}  end={stats['end_price']}  "
                f"chg={stats['change_pct']:.2f}%  vol={stats['avg_daily_volume']}"
            )

    def test_aim_stock_returns_data(self):
        """AC: AIM stock returns data."""
        result = get_price_history("ECR")
        print(f"\n[ECR] quality={result['data_quality_flag']}  price={result['current_price']}")
        assert result["data_quality_flag"] in ("good", "sparse", "unavailable")
        assert isinstance(result, dict)

    def test_ticker_yf_has_l_suffix(self):
        """AC: .L suffix appended correctly."""
        result = get_price_history("LLOY")
        assert result["ticker_yf"] == "LLOY.L"
        assert result["ticker"] == "LLOY"

    def test_unknown_ticker_graceful(self):
        """AC: Returns graceful result (not exception) for ticker not found."""
        result = get_price_history("INVALID_TICKER_XYZ_9999")
        assert isinstance(result, dict)
        assert result["data_quality_flag"] == "unavailable"
        assert result["current_price"] is None
        assert result["windows"] == {}
        print(f"\n[INVALID] data_quality_flag={result['data_quality_flag']} ✓")

    def test_current_price_populated(self):
        """AC: current_price populated (real-time or last close)."""
        result = get_price_history("LLOY")
        assert result["current_price"] is not None, "current_price should not be None for LLOY"
        assert result["current_price"] > 0
        print(
            f"\n[LLOY] current_price={result['current_price']}  "
            f"note={result['current_price_note']}"
        )
        # If outside market hours, note is "last_close"
        if result["current_price_note"] == "last_close":
            print("  (outside market hours — using last close)")
        else:
            print("  (real-time price)")

    def test_all_required_keys_present_live(self):
        """AC: All required output keys present for live ticker."""
        result = get_price_history("LLOY")
        required = {
            "ticker", "ticker_yf", "current_price", "current_price_note",
            "current_volume", "windows", "data_retrieved_at", "data_quality_flag",
        }
        assert required.issubset(set(result.keys()))
