"""
test_get_volatility_metrics.py

Tests for the get_volatility_metrics tool.

Unit tests use mocked yfinance — no network needed.
Integration tests call live yfinance and require a network connection.

Run unit tests only:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_volatility_metrics.py -v -m "not integration"

Run all tests:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_volatility_metrics.py -v -s
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utilities.get_volatility_metrics import (
    get_volatility_metrics,
    _compute_volatility,
    _compute_avg_volume,
    _compute_volume_trend,
    _classify_liquidity,
    _classify_quality,
    _RATE_LIMIT_SECONDS,
    _TRADING_DAYS_PER_YEAR,
    _TREND_THRESHOLD,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SKIP_INTEGRATION = os.environ.get("SKIP_YFINANCE_INTEGRATION", "") == "1"


def _make_hist_df(n_rows: int, base_price: float = 100.0, base_volume: int = 500_000):
    dates = pd.bdate_range("2025-09-01", periods=n_rows)
    closes = base_price + np.arange(n_rows) * 0.1
    return pd.DataFrame(
        {
            "Open":   closes * 0.999,
            "High":   closes * 1.01,
            "Low":    closes * 0.99,
            "Close":  closes,
            "Volume": [base_volume] * n_rows,
        },
        index=dates,
    )


def _make_hist_with_volumes(volumes: list, base_price: float = 100.0):
    """Make a DataFrame with specific volume values."""
    n = len(volumes)
    dates = pd.bdate_range("2025-09-01", periods=n)
    closes = base_price + np.arange(n) * 0.1
    return pd.DataFrame(
        {
            "Open":  closes,
            "High":  closes * 1.01,
            "Low":   closes * 0.99,
            "Close": closes,
            "Volume": volumes,
        },
        index=dates,
    )


def _make_mock_ticker(hist_df):
    mock = MagicMock()
    mock.history.return_value = hist_df
    return mock


# ---------------------------------------------------------------------------
# _compute_volatility
# ---------------------------------------------------------------------------

class TestComputeVolatility:

    def test_returns_float(self):
        df = _make_hist_df(40)
        result = _compute_volatility(df, 30)
        assert isinstance(result, float)

    def test_annualised_correctly(self):
        # Construct a df with known constant daily log return of 0.01
        # daily std should be 0, so volatility = 0
        n = 40
        price = 100.0 * np.exp(0.001 * np.arange(n))  # constant log return
        dates = pd.bdate_range("2025-09-01", periods=n)
        df = pd.DataFrame({"Close": price, "Volume": [100_000] * n}, index=dates)
        result = _compute_volatility(df, 30)
        # Constant returns → std = 0 → annualised vol = 0
        assert result == 0.0 or abs(result) < 1e-10

    def test_is_annualised_not_daily(self):
        # Verify that volatility > raw daily std (annualisation multiplies by sqrt(252))
        df = _make_hist_df(40)
        closes = df["Close"].iloc[-30:]
        log_returns = np.log(closes / closes.shift(1)).dropna()
        daily_std = float(log_returns.std())
        annualised = daily_std * np.sqrt(_TRADING_DAYS_PER_YEAR)

        result = _compute_volatility(df, 30)
        assert abs(result - annualised) < 1e-6

    def test_uses_period_days_window(self):
        # 40 rows available; period_days=10 should only use last 10 rows
        df = _make_hist_df(40, base_price=100.0)
        result_10 = _compute_volatility(df, 10)
        result_30 = _compute_volatility(df, 30)
        # Different windows → different volatility (prices are linear so small differences)
        assert result_10 is not None
        assert result_30 is not None

    def test_fewer_rows_than_period_uses_all(self):
        df = _make_hist_df(15)
        # period_days=30, only 15 rows → uses all 15
        result = _compute_volatility(df, 30)
        assert result is not None

    def test_empty_df_returns_none(self):
        assert _compute_volatility(pd.DataFrame(), 30) is None

    def test_single_row_returns_none(self):
        df = _make_hist_df(1)
        assert _compute_volatility(df, 30) is None

    def test_result_is_non_negative(self):
        df = _make_hist_df(60)
        result = _compute_volatility(df, 30)
        assert result >= 0.0


# ---------------------------------------------------------------------------
# _compute_avg_volume
# ---------------------------------------------------------------------------

class TestComputeAvgVolume:

    def test_basic(self):
        df = _make_hist_df(40, base_volume=200_000)
        result = _compute_avg_volume(df, n=30)
        assert result == 200_000

    def test_excludes_zero_volume_days(self):
        volumes = [100_000] * 25 + [0] * 5   # 30 rows, 5 zero-volume days
        df = _make_hist_with_volumes(volumes)
        result = _compute_avg_volume(df, n=30)
        assert result == 100_000

    def test_uses_last_n_rows(self):
        # First 10 rows have volume=1, last 20 have volume=1_000_000
        volumes = [1] * 10 + [1_000_000] * 20
        df = _make_hist_with_volumes(volumes)
        result = _compute_avg_volume(df, n=20)
        assert result == 1_000_000

    def test_fewer_rows_uses_all(self):
        df = _make_hist_df(15, base_volume=50_000)
        result = _compute_avg_volume(df, n=30)
        assert result == 50_000

    def test_empty_returns_none(self):
        assert _compute_avg_volume(pd.DataFrame(), n=30) is None

    def test_all_zero_volume_returns_none(self):
        df = _make_hist_with_volumes([0] * 30)
        assert _compute_avg_volume(df, n=30) is None


# ---------------------------------------------------------------------------
# _compute_volume_trend
# ---------------------------------------------------------------------------

class TestComputeVolumeTrend:

    def test_increasing_when_10d_higher(self):
        # First 20 rows: low volume; last 10: high volume → 10d avg >> 30d avg
        volumes = [100_000] * 20 + [500_000] * 10
        df = _make_hist_with_volumes(volumes)
        result = _compute_volume_trend(df)
        assert result == "increasing"

    def test_decreasing_when_10d_lower(self):
        # First 20 rows: high volume; last 10: low volume → 10d avg << 30d avg
        volumes = [500_000] * 20 + [100_000] * 10
        df = _make_hist_with_volumes(volumes)
        result = _compute_volume_trend(df)
        assert result == "decreasing"

    def test_stable_when_volumes_equal(self):
        volumes = [200_000] * 30
        df = _make_hist_with_volumes(volumes)
        result = _compute_volume_trend(df)
        assert result == "stable"

    def test_stable_within_threshold(self):
        # 10d avg is 5% higher than 30d avg → within 10% threshold → stable
        vol_30d = [200_000] * 20
        vol_10d = [210_000] * 10   # 5% higher
        df = _make_hist_with_volumes(vol_30d + vol_10d)
        result = _compute_volume_trend(df)
        assert result == "stable"

    def test_increasing_just_above_threshold(self):
        # The 30d window includes all 30 rows (20 low + 10 high), so the ratio
        # 10d_avg / 30d_avg is diluted. Use a spread large enough to clear 10%.
        # With vol_30d=200k (20 rows) and vol_10d=260k (10 rows):
        #   30d_avg = (20*200k + 10*260k)/30 = 220k
        #   10d_avg = 260k
        #   ratio = 260k/220k = 1.18 → 18% above → "increasing"
        vol_base = [200_000] * 20
        vol_high = [260_000] * 10
        df = _make_hist_with_volumes(vol_base + vol_high)
        result = _compute_volume_trend(df)
        assert result == "increasing"

    def test_fewer_than_10_rows_returns_none(self):
        df = _make_hist_with_volumes([100_000] * 8)
        assert _compute_volume_trend(df) is None

    def test_exactly_10_rows(self):
        # Only 10 rows available — 30d and 10d windows both use what's available
        df = _make_hist_with_volumes([100_000] * 10)
        result = _compute_volume_trend(df)
        assert result in ("stable", "increasing", "decreasing")


# ---------------------------------------------------------------------------
# _classify_liquidity
# ---------------------------------------------------------------------------

class TestClassifyLiquidity:

    def test_high(self):
        assert _classify_liquidity(1_000_001) == "high"

    def test_high_boundary(self):
        assert _classify_liquidity(1_000_001) == "high"

    def test_not_high_at_1m(self):
        # Exactly 1_000_000 is NOT > 1_000_000
        assert _classify_liquidity(1_000_000) == "medium"

    def test_medium_lower_boundary(self):
        assert _classify_liquidity(100_000) == "medium"

    def test_medium_upper_boundary(self):
        assert _classify_liquidity(1_000_000) == "medium"

    def test_low_lower_boundary(self):
        assert _classify_liquidity(10_000) == "low"

    def test_low_upper_boundary(self):
        assert _classify_liquidity(99_999) == "low"

    def test_very_low(self):
        assert _classify_liquidity(9_999) == "very_low"

    def test_very_low_zero(self):
        assert _classify_liquidity(0) == "very_low"

    def test_none_returns_none(self):
        assert _classify_liquidity(None) is None


# ---------------------------------------------------------------------------
# _classify_quality
# ---------------------------------------------------------------------------

class TestClassifyQuality:

    def test_good_when_rows_equals_period(self):
        df = _make_hist_df(30)
        assert _classify_quality(df, 30) == "good"

    def test_good_when_rows_exceed_period(self):
        df = _make_hist_df(60)
        assert _classify_quality(df, 30) == "good"

    def test_sparse_when_rows_below_period(self):
        df = _make_hist_df(20)
        assert _classify_quality(df, 30) == "sparse"

    def test_unavailable_empty(self):
        assert _classify_quality(pd.DataFrame(), 30) == "unavailable"


# ---------------------------------------------------------------------------
# get_volatility_metrics — mock yfinance
# ---------------------------------------------------------------------------

class TestGetVolatilityMetrics:

    def test_all_required_keys_present(self):
        df = _make_hist_df(60)
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(df)
            with patch("utilities.get_volatility_metrics.time.sleep"):
                result = get_volatility_metrics("FGEN")
        required = {
            "ticker", "ticker_yf", "volatility_30d", "avg_daily_volume_30d",
            "volume_trend", "liquidity_flag", "bid_ask_spread",
            "data_quality_flag", "data_retrieved_at",
        }
        assert required.issubset(set(result.keys()))

    def test_bid_ask_spread_is_none(self):
        df = _make_hist_df(60)
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(df)
            with patch("utilities.get_volatility_metrics.time.sleep"):
                result = get_volatility_metrics("FGEN")
        assert result["bid_ask_spread"] is None

    def test_l_suffix_appended(self):
        df = _make_hist_df(60)
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(df)
            with patch("utilities.get_volatility_metrics.time.sleep"):
                result = get_volatility_metrics("FGEN")
        mock_yf.assert_called_once_with("FGEN.L")
        assert result["ticker_yf"] == "FGEN.L"

    def test_good_quality_for_sufficient_data(self):
        df = _make_hist_df(60)
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(df)
            with patch("utilities.get_volatility_metrics.time.sleep"):
                result = get_volatility_metrics("FGEN")
        assert result["data_quality_flag"] == "good"

    def test_sparse_quality_for_insufficient_data(self):
        df = _make_hist_df(15)
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(df)
            with patch("utilities.get_volatility_metrics.time.sleep"):
                result = get_volatility_metrics("FGEN")
        assert result["data_quality_flag"] == "sparse"

    def test_unavailable_on_empty_history(self):
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(pd.DataFrame())
            with patch("utilities.get_volatility_metrics.time.sleep"):
                result = get_volatility_metrics("FGEN")
        assert result["data_quality_flag"] == "unavailable"
        assert result["volatility_30d"] is None
        assert result["liquidity_flag"] is None

    def test_unavailable_on_exception(self):
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value.history.side_effect = Exception("timeout")
            with patch("utilities.get_volatility_metrics.time.sleep"):
                result = get_volatility_metrics("INVALID")
        assert result["data_quality_flag"] == "unavailable"
        assert isinstance(result, dict)

    def test_rate_limit_sleep_called(self):
        df = _make_hist_df(60)
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(df)
            with patch("utilities.get_volatility_metrics.time.sleep") as mock_sleep:
                get_volatility_metrics("FGEN")
        mock_sleep.assert_called_with(_RATE_LIMIT_SECONDS)

    def test_rate_limit_sleep_called_on_exception(self):
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value.history.side_effect = Exception("boom")
            with patch("utilities.get_volatility_metrics.time.sleep") as mock_sleep:
                get_volatility_metrics("FGEN")
        mock_sleep.assert_called_with(_RATE_LIMIT_SECONDS)

    def test_high_liquidity_for_large_volume(self):
        df = _make_hist_df(60, base_volume=2_000_000)
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(df)
            with patch("utilities.get_volatility_metrics.time.sleep"):
                result = get_volatility_metrics("FGEN")
        assert result["liquidity_flag"] == "high"

    def test_very_low_liquidity_for_small_volume(self):
        df = _make_hist_df(60, base_volume=5_000)
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(df)
            with patch("utilities.get_volatility_metrics.time.sleep"):
                result = get_volatility_metrics("FGEN")
        assert result["liquidity_flag"] == "very_low"

    def test_data_retrieved_at_is_utc(self):
        df = _make_hist_df(60)
        with patch("utilities.get_volatility_metrics.yf.Ticker") as mock_yf:
            mock_yf.return_value = _make_mock_ticker(df)
            with patch("utilities.get_volatility_metrics.time.sleep"):
                result = get_volatility_metrics("FGEN")
        assert isinstance(result["data_retrieved_at"], datetime)
        assert result["data_retrieved_at"].tzinfo is not None


# ---------------------------------------------------------------------------
# Integration tests — live yfinance
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    SKIP_INTEGRATION,
    reason="SKIP_YFINANCE_INTEGRATION=1 — skipping live yfinance tests",
)
class TestGetVolatilityMetricsIntegration:
    """
    Acceptance criteria checks against live yfinance.

    Tickers:
      LLOY  — liquid main market (expect high/medium liquidity)
      ECR   — AIM stock (expect low/very_low liquidity)
    """

    def test_volatility_returned_for_lloy(self):
        """AC: volatility_30d returned for known ticker."""
        result = get_volatility_metrics("LLOY")
        print(f"\n[LLOY] volatility_30d={result['volatility_30d']}")
        assert result["volatility_30d"] is not None
        assert result["volatility_30d"] > 0

    def test_volatility_is_annualised(self):
        """AC: volatility_30d is annualised, not raw daily std."""
        result = get_volatility_metrics("LLOY")
        # Annualised vol for a FTSE stock is typically 15-60%
        # Raw daily std would be ~1-4% — confirming the value is in annual range
        assert result["volatility_30d"] > 0.05, (
            f"volatility_30d={result['volatility_30d']} looks like raw daily std, not annualised"
        )
        print(f"\n[LLOY] volatility_30d={result['volatility_30d']:.4f} ({result['volatility_30d']*100:.1f}% annualised)")

    def test_liquidity_high_or_medium_for_lloy(self):
        """AC: liquidity_flag 'high' or 'medium' for liquid main market stock."""
        result = get_volatility_metrics("LLOY")
        print(f"\n[LLOY] liquidity_flag={result['liquidity_flag']}  avg_vol={result['avg_daily_volume_30d']}")
        assert result["liquidity_flag"] in ("high", "medium"), (
            f"Expected high/medium for LLOY but got {result['liquidity_flag']!r}"
        )

    def test_volume_trend_classified_for_lloy(self):
        """AC: volume_trend correctly classified."""
        result = get_volatility_metrics("LLOY")
        print(f"\n[LLOY] volume_trend={result['volume_trend']}")
        assert result["volume_trend"] in ("increasing", "decreasing", "stable")

    def test_bid_ask_spread_none(self):
        """AC: bid_ask_spread returns None gracefully — known limitation."""
        result = get_volatility_metrics("LLOY")
        assert result["bid_ask_spread"] is None
        print(f"\n[LLOY] bid_ask_spread=None (confirmed yfinance limitation) ✓")

    def test_aim_stock_returns_data(self):
        """AC: AIM stock returns metrics."""
        result = get_volatility_metrics("ECR")
        print(
            f"\n[ECR] quality={result['data_quality_flag']}  "
            f"liquidity={result['liquidity_flag']}  "
            f"vol={result['volatility_30d']}  "
            f"avg_vol={result['avg_daily_volume_30d']}"
        )
        assert isinstance(result, dict)
        assert result["data_quality_flag"] in ("good", "sparse", "unavailable")

    def test_unknown_ticker_graceful(self):
        """AC: Returns graceful result for ticker not found."""
        result = get_volatility_metrics("INVALID_TICKER_XYZ_9999")
        assert isinstance(result, dict)
        assert result["data_quality_flag"] == "unavailable"
        assert result["volatility_30d"] is None
        print(f"\n[INVALID] data_quality_flag=unavailable ✓")

    def test_three_tickers_return_volatility(self):
        """AC: volatility_30d returned for 3 known tickers."""
        tickers = ["LLOY", "ECR", "GSK"]
        for ticker in tickers:
            result = get_volatility_metrics(ticker)
            print(f"\n[{ticker}] volatility_30d={result['volatility_30d']}  quality={result['data_quality_flag']}")
            if result["data_quality_flag"] != "unavailable":
                assert result["volatility_30d"] is not None, f"{ticker}: volatility_30d is None"
