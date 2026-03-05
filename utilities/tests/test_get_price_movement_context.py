"""
Tests for get_price_movement_context.py

Unit tests: mocked yfinance — no network calls.
Integration tests: live yfinance — marked with @pytest.mark.integration.

Run unit tests only:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_price_movement_context.py -v -m "not integration"

Run all tests (requires network):
    scripts/venv/bin/python -m pytest utilities/tests/test_get_price_movement_context.py -v
"""

import os
import sys
import time
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utilities.get_price_movement_context import (
    get_price_movement_context,
    _price_on_date,
    _current_price,
    _compute_movement,
    _compute_lag_movement,
    _compute_baseline_volume,
    _compute_short_windows,
    _compute_window_stats,
    _compute_52wk_position,
    _classify_direction,
    _classify_quality,
    _build_date_adjustment_note,
    _unavailable_result,
    _FLAT_THRESHOLD,
    _VOLUME_ANOMALY_THRESHOLD,
    _SHORT_LAG_THRESHOLD,
    _SHORT_WINDOWS,
    _GOOD_ROW_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

def _make_hist(
    n_rows: int = 252,
    base_price: float = 100.0,
    base_volume: int = 500_000,
    start_date: str = "2024-03-05",
) -> pd.DataFrame:
    """
    Synthetic daily OHLCV DataFrame with business-day dates.
    Price rises linearly by 0.1 per day for predictable start/end values.
    """
    dates = pd.bdate_range(start_date, periods=n_rows)
    closes = base_price + np.arange(n_rows) * 0.1
    return pd.DataFrame(
        {
            "Open":         closes * 0.999,
            "High":         closes * 1.01,
            "Low":          closes * 0.99,
            "Close":        closes,
            "Volume":       [base_volume] * n_rows,
            "Dividends":    [0.0] * n_rows,
            "Stock Splits": [0.0] * n_rows,
        },
        index=dates,
    )


def _make_hist_with_date(target_date: date, price: float = 150.0, n_rows: int = 252) -> pd.DataFrame:
    """Return a hist DataFrame that contains the given target_date as a trading day."""
    # Build around target_date: n_rows business days ending at target_date
    end_ts = pd.Timestamp(target_date)
    dates = pd.bdate_range(end=end_ts, periods=n_rows)
    closes = np.linspace(100.0, price, n_rows)
    return pd.DataFrame(
        {
            "Open":  closes * 0.999,
            "High":  closes * 1.01,
            "Low":   closes * 0.99,
            "Close": closes,
            "Volume": [400_000] * n_rows,
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# Tests: _classify_direction
# ---------------------------------------------------------------------------

class TestClassifyDirection:
    def test_positive_above_threshold_is_up(self):
        assert _classify_direction(1.0) == "up"

    def test_negative_below_threshold_is_down(self):
        assert _classify_direction(-1.0) == "down"

    def test_zero_is_flat(self):
        assert _classify_direction(0.0) == "flat"

    def test_just_above_flat_threshold_is_up(self):
        assert _classify_direction(_FLAT_THRESHOLD + 0.01) == "up"

    def test_just_below_negative_flat_threshold_is_down(self):
        assert _classify_direction(-_FLAT_THRESHOLD - 0.01) == "down"

    def test_exactly_at_positive_threshold_is_flat(self):
        assert _classify_direction(_FLAT_THRESHOLD) == "flat"

    def test_exactly_at_negative_threshold_is_flat(self):
        assert _classify_direction(-_FLAT_THRESHOLD) == "flat"

    def test_none_returns_none(self):
        assert _classify_direction(None) is None


# ---------------------------------------------------------------------------
# Tests: _classify_quality
# ---------------------------------------------------------------------------

class TestClassifyQuality:
    def test_good_when_at_threshold(self):
        hist = _make_hist(n_rows=_GOOD_ROW_THRESHOLD)
        assert _classify_quality(hist) == "good"

    def test_good_when_above_threshold(self):
        hist = _make_hist(n_rows=252)
        assert _classify_quality(hist) == "good"

    def test_sparse_when_below_threshold(self):
        hist = _make_hist(n_rows=_GOOD_ROW_THRESHOLD - 1)
        assert _classify_quality(hist) == "sparse"

    def test_sparse_for_single_row(self):
        hist = _make_hist(n_rows=1)
        assert _classify_quality(hist) == "sparse"

    def test_unavailable_for_empty(self):
        assert _classify_quality(pd.DataFrame()) == "unavailable"


# ---------------------------------------------------------------------------
# Tests: _price_on_date
# ---------------------------------------------------------------------------

class TestPriceOnDate:
    def test_exact_date_returns_price_and_not_adjusted(self):
        hist = _make_hist(n_rows=30, start_date="2025-01-02")
        # First row is 2025-01-02 (Thursday, business day)
        first_date = hist.index[0].date()
        price, adjusted = _price_on_date(hist, first_date)
        assert price is not None
        assert adjusted is False

    def test_exact_date_price_matches_close(self):
        hist = _make_hist(n_rows=30, start_date="2025-01-02")
        first_date = hist.index[0].date()
        expected_close = round(float(hist["Close"].iloc[0]), 4)
        price, _ = _price_on_date(hist, first_date)
        assert price == pytest.approx(expected_close)

    def test_weekend_date_uses_nearest_prior_trading_day(self):
        hist = _make_hist(n_rows=30, start_date="2025-01-02")
        # Find the last business day in hist, then pick the following Saturday
        last_trading_day = hist.index[-1].date()
        import datetime as dt
        # Move to next Saturday after last trading day
        days_ahead = (5 - last_trading_day.weekday()) % 7  # days to Saturday
        if days_ahead == 0:
            days_ahead = 7
        saturday = last_trading_day + dt.timedelta(days=days_ahead + 1)
        # Saturday is not in hist; nearest prior is last_trading_day
        price, adjusted = _price_on_date(hist, saturday)
        assert adjusted is True
        # Price should be the last close (nearest prior)
        expected = round(float(hist["Close"].iloc[-1]), 4)
        assert price == pytest.approx(expected)

    def test_date_before_history_returns_earliest_available(self):
        hist = _make_hist(n_rows=30, start_date="2025-06-01")
        very_early = date(2020, 1, 1)
        price, adjusted = _price_on_date(hist, very_early)
        # No prior rows, so falls back to first available (after target)
        assert adjusted is True
        assert price is not None

    def test_date_after_history_returns_last_available(self):
        hist = _make_hist(n_rows=30, start_date="2024-01-01")
        very_late = date(2030, 1, 1)
        price, adjusted = _price_on_date(hist, very_late)
        assert adjusted is True
        assert price is not None

    def test_empty_hist_returns_none_not_adjusted(self):
        price, adjusted = _price_on_date(pd.DataFrame(), date(2025, 1, 1))
        assert price is None
        assert adjusted is False

    def test_none_hist_returns_none_not_adjusted(self):
        price, adjusted = _price_on_date(None, date(2025, 1, 1))
        assert price is None
        assert adjusted is False

    def test_price_is_rounded_to_4dp(self):
        hist = _make_hist(n_rows=10, base_price=123.456789)
        first_date = hist.index[0].date()
        price, _ = _price_on_date(hist, first_date)
        assert price == round(price, 4)


# ---------------------------------------------------------------------------
# Tests: _current_price
# ---------------------------------------------------------------------------

class TestCurrentPrice:
    def test_returns_last_close(self):
        hist = _make_hist(n_rows=10, base_price=200.0)
        result = _current_price(hist)
        expected = round(float(hist["Close"].iloc[-1]), 4)
        assert result == pytest.approx(expected)

    def test_returns_none_for_empty_hist(self):
        assert _current_price(pd.DataFrame()) is None

    def test_returns_none_for_none(self):
        assert _current_price(None) is None


# ---------------------------------------------------------------------------
# Tests: _compute_movement
# ---------------------------------------------------------------------------

class TestComputeMovement:
    def test_positive_change_direction_up(self):
        result = _compute_movement(100.0, 110.0)
        assert result["direction"] == "up"

    def test_negative_change_direction_down(self):
        result = _compute_movement(100.0, 90.0)
        assert result["direction"] == "down"

    def test_flat_change_direction_flat(self):
        result = _compute_movement(100.0, 100.0)
        assert result["direction"] == "flat"

    def test_formula_correct(self):
        result = _compute_movement(100.0, 115.0)
        assert result["change_pct"] == pytest.approx(15.0)

    def test_formula_negative(self):
        result = _compute_movement(200.0, 150.0)
        assert result["change_pct"] == pytest.approx(-25.0)

    def test_none_from_price_returns_nulls(self):
        result = _compute_movement(None, 100.0)
        assert result["change_pct"] is None
        assert result["direction"] is None

    def test_none_to_price_returns_nulls(self):
        result = _compute_movement(100.0, None)
        assert result["change_pct"] is None
        assert result["direction"] is None

    def test_zero_from_price_returns_nulls(self):
        result = _compute_movement(0.0, 100.0)
        assert result["change_pct"] is None

    def test_change_pct_rounded_to_4dp(self):
        result = _compute_movement(3.0, 4.0)
        assert result["change_pct"] == round(result["change_pct"], 4)

    def test_both_keys_present(self):
        result = _compute_movement(100.0, 105.0)
        assert "change_pct" in result
        assert "direction" in result


# ---------------------------------------------------------------------------
# Tests: _compute_lag_movement
# ---------------------------------------------------------------------------

class TestComputeLagMovement:
    def test_lag_zero_returns_none_change_pct(self):
        result = _compute_lag_movement(100.0, 105.0, 0)
        assert result["change_pct"] is None
        assert result["direction"] is None

    def test_lag_zero_note_is_descriptive(self):
        result = _compute_lag_movement(100.0, 105.0, 0)
        assert result["note"] is not None
        assert len(result["note"]) > 10

    def test_lag_greater_than_threshold_computes_change(self):
        result = _compute_lag_movement(100.0, 108.0, 10)
        assert result["change_pct"] == pytest.approx(8.0)

    def test_lag_greater_than_threshold_note_mentions_lag(self):
        result = _compute_lag_movement(100.0, 108.0, 10)
        assert "10" in result["note"] or "lag" in result["note"].lower()

    def test_short_lag_within_threshold_note_mentions_noise(self):
        result = _compute_lag_movement(100.0, 103.0, _SHORT_LAG_THRESHOLD)
        assert result["note"] is not None
        # change_pct should still be computed for short lags
        assert result["change_pct"] == pytest.approx(3.0)

    def test_none_prices_returns_none_change(self):
        result = _compute_lag_movement(None, 105.0, 7)
        assert result["change_pct"] is None

    def test_returns_three_keys(self):
        result = _compute_lag_movement(100.0, 110.0, 5)
        assert "change_pct" in result
        assert "direction" in result
        assert "note" in result

    def test_direction_up_for_rising_lag(self):
        result = _compute_lag_movement(100.0, 115.0, 8)
        assert result["direction"] == "up"

    def test_direction_down_for_falling_lag(self):
        result = _compute_lag_movement(100.0, 85.0, 8)
        assert result["direction"] == "down"


# ---------------------------------------------------------------------------
# Tests: _compute_baseline_volume
# ---------------------------------------------------------------------------

class TestComputeBaselineVolume:
    def test_returns_mean_of_volumes(self):
        hist = _make_hist(n_rows=30, base_volume=600_000)
        result = _compute_baseline_volume(hist)
        assert result == pytest.approx(600_000.0)

    def test_excludes_zero_volumes(self):
        hist = _make_hist(n_rows=10, base_volume=500_000)
        hist.loc[hist.index[0], "Volume"] = 0  # one zero row
        hist.loc[hist.index[1], "Volume"] = 0  # another zero row
        result = _compute_baseline_volume(hist)
        # All non-zero rows have 500_000 volume
        assert result == pytest.approx(500_000.0)

    def test_returns_none_for_empty_hist(self):
        assert _compute_baseline_volume(pd.DataFrame()) is None

    def test_returns_none_for_none(self):
        assert _compute_baseline_volume(None) is None

    def test_returns_none_when_all_zero_volume(self):
        hist = _make_hist(n_rows=5, base_volume=0)
        result = _compute_baseline_volume(hist)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _compute_window_stats
# ---------------------------------------------------------------------------

class TestComputeWindowStats:
    def test_change_pct_computed_correctly(self):
        hist = _make_hist(n_rows=5, base_price=100.0)
        start = float(hist["Close"].iloc[0])
        end = float(hist["Close"].iloc[-1])
        expected_chg = (end - start) / start * 100
        result = _compute_window_stats(hist, 500_000.0)
        assert result["change_pct"] == pytest.approx(expected_chg, abs=0.01)

    def test_avg_daily_volume_computed(self):
        hist = _make_hist(n_rows=5, base_volume=300_000)
        result = _compute_window_stats(hist, 300_000.0)
        assert result["avg_daily_volume"] == 300_000

    def test_volume_vs_30d_avg_ratio(self):
        hist = _make_hist(n_rows=5, base_volume=600_000)
        baseline = 400_000.0
        result = _compute_window_stats(hist, baseline)
        assert result["volume_vs_30d_avg"] == pytest.approx(600_000 / 400_000, abs=0.01)

    def test_volume_anomaly_true_above_threshold(self):
        hist = _make_hist(n_rows=5, base_volume=1_000_000)
        baseline = 500_000.0  # ratio = 2.0 > 1.5
        result = _compute_window_stats(hist, baseline)
        assert result["volume_anomaly"] is True

    def test_volume_anomaly_false_below_threshold(self):
        hist = _make_hist(n_rows=5, base_volume=500_000)
        baseline = 500_000.0  # ratio = 1.0
        result = _compute_window_stats(hist, baseline)
        assert result["volume_anomaly"] is False

    def test_volume_anomaly_false_at_threshold(self):
        # Exactly 1.5 is NOT > 1.5
        hist = _make_hist(n_rows=5, base_volume=750_000)
        baseline = 500_000.0  # ratio = 1.5 (not > 1.5)
        result = _compute_window_stats(hist, baseline)
        assert result["volume_anomaly"] is False

    def test_returns_none_for_empty_window(self):
        assert _compute_window_stats(pd.DataFrame(), 500_000.0) is None

    def test_volume_vs_30d_avg_none_when_no_baseline(self):
        hist = _make_hist(n_rows=5, base_volume=500_000)
        result = _compute_window_stats(hist, None)
        assert result["volume_vs_30d_avg"] is None

    def test_four_keys_present(self):
        hist = _make_hist(n_rows=5)
        result = _compute_window_stats(hist, 500_000.0)
        for key in ("change_pct", "avg_daily_volume", "volume_vs_30d_avg", "volume_anomaly"):
            assert key in result


# ---------------------------------------------------------------------------
# Tests: _compute_short_windows
# ---------------------------------------------------------------------------

class TestComputeShortWindows:
    def _hist_ending_before(self, pub_date: date, n_rows: int = 60) -> pd.DataFrame:
        """Hist where all rows are strictly before pub_date."""
        end_ts = pd.Timestamp(pub_date) - pd.Timedelta(days=1)
        dates = pd.bdate_range(end=end_ts, periods=n_rows)
        actual_n = len(dates)  # bdate_range may produce fewer rows if end is non-business day
        closes = np.linspace(100.0, 120.0, actual_n)
        return pd.DataFrame(
            {
                "Open":  closes * 0.999,
                "High":  closes * 1.01,
                "Low":   closes * 0.99,
                "Close": closes,
                "Volume": [500_000] * actual_n,
            },
            index=dates,
        )

    def test_all_four_windows_present(self):
        pub = date(2025, 6, 15)
        hist = self._hist_ending_before(pub, n_rows=60)
        result = _compute_short_windows(hist, pub, 500_000.0)
        for name in ("3d", "1w", "2w", "1m"):
            assert name in result

    def test_rows_on_or_after_pub_excluded(self):
        pub = date(2025, 6, 16)  # Monday
        # Build hist that includes the pub date itself
        dates = pd.bdate_range(end=pd.Timestamp(pub), periods=30)
        closes = np.linspace(100.0, 130.0, 30)
        hist = pd.DataFrame(
            {
                "Open":  closes,
                "High":  closes * 1.01,
                "Low":   closes * 0.99,
                "Close": closes,
                "Volume": [500_000] * 30,
            },
            index=dates,
        )
        result = _compute_short_windows(hist, pub, 500_000.0)
        # All windows should be computed without the pub day data
        assert result["3d"] is not None

    def test_returns_all_none_for_empty_hist(self):
        result = _compute_short_windows(pd.DataFrame(), date(2025, 1, 1), 500_000.0)
        for v in result.values():
            assert v is None

    def test_fewer_rows_than_window_uses_all_available(self):
        pub = date(2025, 6, 20)
        # Only 2 rows before pub
        dates = pd.bdate_range(end=pd.Timestamp(pub) - pd.Timedelta(days=1), periods=2)
        hist = pd.DataFrame(
            {
                "Open":  [100.0, 101.0],
                "High":  [101.0, 102.0],
                "Low":   [99.0, 100.0],
                "Close": [100.0, 101.0],
                "Volume": [500_000, 500_000],
            },
            index=dates,
        )
        result = _compute_short_windows(hist, pub, 500_000.0)
        # 3d window needs 3 rows but only 2 available — should return data not None
        assert result["3d"] is not None

    def test_volume_anomaly_propagates_to_windows(self):
        pub = date(2025, 6, 20)
        hist = self._hist_ending_before(pub, n_rows=60)
        # Set volume very high for last few rows (within the 3d window)
        hist.loc[hist.index[-3:], "Volume"] = 2_000_000
        baseline = 500_000.0  # ratio = 4.0 > 1.5
        result = _compute_short_windows(hist, pub, baseline)
        assert result["3d"]["volume_anomaly"] is True


# ---------------------------------------------------------------------------
# Tests: _compute_52wk_position
# ---------------------------------------------------------------------------

class TestCompute52wkPosition:
    def _hist(self, high: float, low: float, n_rows: int = 252) -> pd.DataFrame:
        closes = np.linspace(low, high, n_rows)
        dates = pd.bdate_range("2024-03-05", periods=n_rows)
        return pd.DataFrame(
            {
                "Open":  closes * 0.999,
                "High":  [high] * n_rows,
                "Low":   [low] * n_rows,
                "Close": closes,
                "Volume": [500_000] * n_rows,
            },
            index=dates,
        )

    def test_at_52wk_low_returns_0(self):
        hist = self._hist(high=110.0, low=65.0)
        result = _compute_52wk_position(hist, 65.0)
        assert result == pytest.approx(0.0)

    def test_at_52wk_high_returns_100(self):
        hist = self._hist(high=110.0, low=65.0)
        result = _compute_52wk_position(hist, 110.0)
        assert result == pytest.approx(100.0)

    def test_at_midpoint_returns_50(self):
        hist = self._hist(high=110.0, low=65.0)
        mid = (110.0 + 65.0) / 2
        result = _compute_52wk_position(hist, mid)
        assert result == pytest.approx(50.0, abs=0.1)

    def test_above_52wk_high_clamped_to_100(self):
        hist = self._hist(high=110.0, low=65.0)
        result = _compute_52wk_position(hist, 130.0)
        assert result == pytest.approx(100.0)

    def test_below_52wk_low_clamped_to_0(self):
        hist = self._hist(high=110.0, low=65.0)
        result = _compute_52wk_position(hist, 40.0)
        assert result == pytest.approx(0.0)

    def test_zero_range_returns_none(self):
        hist = self._hist(high=100.0, low=100.0)
        result = _compute_52wk_position(hist, 100.0)
        assert result is None

    def test_none_current_price_returns_none(self):
        hist = self._hist(high=110.0, low=65.0)
        result = _compute_52wk_position(hist, None)
        assert result is None

    def test_empty_hist_returns_none(self):
        result = _compute_52wk_position(pd.DataFrame(), 100.0)
        assert result is None

    def test_result_between_0_and_100(self):
        hist = self._hist(high=110.0, low=65.0)
        result = _compute_52wk_position(hist, 90.0)
        assert 0.0 <= result <= 100.0

    def test_result_rounded_to_2dp(self):
        hist = self._hist(high=110.0, low=65.0)
        result = _compute_52wk_position(hist, 88.0)
        assert result == round(result, 2)


# ---------------------------------------------------------------------------
# Tests: _build_date_adjustment_note
# ---------------------------------------------------------------------------

class TestBuildDateAdjustmentNote:
    def test_no_adjustment_returns_none(self):
        result = _build_date_adjustment_note(
            date(2025, 1, 6), False, date(2025, 1, 10), False
        )
        assert result is None

    def test_tx_adjusted_returns_note(self):
        result = _build_date_adjustment_note(
            date(2025, 1, 4), True, date(2025, 1, 10), False
        )
        assert result is not None
        assert "transaction_date" in result

    def test_pub_adjusted_returns_note(self):
        result = _build_date_adjustment_note(
            date(2025, 1, 6), False, date(2025, 1, 11), True
        )
        assert result is not None
        assert "published_at" in result

    def test_both_adjusted_returns_combined_note(self):
        result = _build_date_adjustment_note(
            date(2025, 1, 4), True, date(2025, 1, 11), True
        )
        assert result is not None
        assert "transaction_date" in result
        assert "published_at" in result


# ---------------------------------------------------------------------------
# Tests: get_price_movement_context — full function (mocked yfinance)
# ---------------------------------------------------------------------------

class TestGetPriceMovementContextMocked:
    """Full-function tests with mocked yfinance. No network calls."""

    def _make_mock_ticker_cls(self, hist: pd.DataFrame):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist
        mock_cls = MagicMock(return_value=mock_ticker)
        return mock_cls

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_returns_dict_on_success(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        result = get_price_movement_context("TEST", tx_date, pub_date, 25)
        assert isinstance(result, dict)

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_all_expected_keys_present(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        result = get_price_movement_context("TEST", tx_date, pub_date, 25)
        for key in (
            "ticker", "ticker_yf", "price_at_transaction_date", "price_at_publication",
            "price_now", "price_currency", "movement_since_transaction",
            "movement_since_publication", "movement_during_lag", "short_window_analysis",
            "volume_anomaly_detected", "volume_anomaly_note", "position_in_52wk_range",
            "data_quality_flag", "date_adjustment_applied", "date_adjustment_note",
            "cache_used", "data_retrieved_at",
        ):
            assert key in result, f"Missing key: {key}"

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_ticker_without_suffix_gets_dot_l(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        result = get_price_movement_context("FGEN", tx_date, pub_date, 25)
        assert result["ticker_yf"] == "FGEN.L"
        assert result["ticker"] == "FGEN"

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_ticker_with_suffix_not_doubled(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        result = get_price_movement_context("FGEN.L", tx_date, pub_date, 25)
        assert result["ticker_yf"] == "FGEN.L"
        assert result["ticker"] == "FGEN"

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_unavailable_for_empty_hist(self, mock_sleep, mock_ticker_cls):
        mock_ticker_cls.return_value.history.return_value = pd.DataFrame()
        result = get_price_movement_context("TEST", date(2025, 1, 10), date(2025, 1, 14), 4)
        assert result["data_quality_flag"] == "unavailable"
        assert result["price_at_transaction_date"] is None
        assert result["price_now"] is None

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_unavailable_on_exception(self, mock_sleep, mock_ticker_cls):
        mock_ticker_cls.return_value.history.side_effect = Exception("network error")
        result = get_price_movement_context("TEST", date(2025, 1, 10), date(2025, 1, 14), 4)
        assert result["data_quality_flag"] == "unavailable"
        assert "error" in result

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_price_currency_is_gbp(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        result = get_price_movement_context("TEST", tx_date, pub_date, 25)
        assert result["price_currency"] == "GBp"

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_data_quality_good_for_252_rows(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        result = get_price_movement_context("TEST", tx_date, pub_date, 25)
        assert result["data_quality_flag"] == "good"

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_rate_limit_sleep_called(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        get_price_movement_context("TEST", tx_date, pub_date, 25)
        mock_sleep.assert_called()

    def test_cache_used_skips_yfinance_call(self):
        """When price_data_cache is provided, yf.Ticker should NOT be called."""
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        cache = {"hist": hist}

        with patch("utilities.get_price_movement_context.yf.Ticker") as mock_yf:
            result = get_price_movement_context("TEST", tx_date, pub_date, 25, price_data_cache=cache)
            mock_yf.assert_not_called()

        assert result["cache_used"] is True

    def test_cache_not_used_calls_yfinance(self):
        """When price_data_cache is None, yf.Ticker should be called."""
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()

        with patch("utilities.get_price_movement_context.yf.Ticker") as mock_yf:
            with patch("utilities.get_price_movement_context.time.sleep"):
                mock_yf.return_value.history.return_value = hist
                result = get_price_movement_context("TEST", tx_date, pub_date, 25, price_data_cache=None)
                mock_yf.assert_called_once()

        assert result["cache_used"] is False

    def test_cache_used_no_sleep(self):
        """No rate-limit sleep when cache is used (no yfinance call)."""
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        cache = {"hist": hist}

        with patch("utilities.get_price_movement_context.time.sleep") as mock_sleep:
            get_price_movement_context("TEST", tx_date, pub_date, 25, price_data_cache=cache)
            mock_sleep.assert_not_called()

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_movement_since_transaction_formula(self, mock_sleep, mock_ticker_cls):
        """movement_since_transaction = (price_now - price_at_tx) / price_at_tx * 100."""
        hist = _make_hist(n_rows=252, base_price=100.0, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        tx_date = hist.index[0].date()   # earliest row, price ≈ 100
        pub_date = hist.index[10].date()
        result = get_price_movement_context("TEST", tx_date, pub_date, 10)
        # price_now is the last close; price_at_tx is the first close
        price_tx = result["price_at_transaction_date"]
        price_now = result["price_now"]
        if price_tx and price_now:
            expected_chg = (price_now - price_tx) / price_tx * 100
            assert result["movement_since_transaction"]["change_pct"] == pytest.approx(
                expected_chg, abs=0.1
            )

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_short_window_analysis_has_four_windows(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        result = get_price_movement_context("TEST", tx_date, pub_date, 25)
        swa = result["short_window_analysis"]
        for name in ("3d", "1w", "2w", "1m"):
            assert name in swa

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_volume_anomaly_detected_when_high_volume(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, base_volume=500_000, start_date="2024-03-05")
        # Spike volume in last 3 rows before pub_date
        hist.loc[hist.index[-8:-5], "Volume"] = 3_000_000  # 6x baseline
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-60].date()
        result = get_price_movement_context("TEST", tx_date, pub_date, 55)
        assert result["volume_anomaly_detected"] is True
        assert result["volume_anomaly_note"] is not None
        # Confirm observation-only language (no leakage assertion)
        note = result["volume_anomaly_note"].lower()
        assert "observation" in note or "no inference" in note

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_volume_anomaly_note_none_when_not_detected(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, base_volume=500_000, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        result = get_price_movement_context("TEST", tx_date, pub_date, 25)
        assert result["volume_anomaly_note"] is None

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_position_in_52wk_range_between_0_and_100(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, base_price=100.0, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        result = get_price_movement_context("TEST", tx_date, pub_date, 25)
        pos = result["position_in_52wk_range"]
        if pos is not None:
            assert 0.0 <= pos <= 100.0

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_lag_zero_movement_during_lag_change_pct_none(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        same_date = hist.index[-10].date()
        result = get_price_movement_context("TEST", same_date, same_date, 0)
        assert result["movement_during_lag"]["change_pct"] is None

    @patch("utilities.get_price_movement_context.yf.Ticker")
    @patch("utilities.get_price_movement_context.time.sleep")
    def test_data_retrieved_at_is_utc(self, mock_sleep, mock_ticker_cls):
        hist = _make_hist(n_rows=252, start_date="2024-03-05")
        mock_ticker_cls.return_value.history.return_value = hist
        pub_date = hist.index[-5].date()
        tx_date = hist.index[-30].date()
        result = get_price_movement_context("TEST", tx_date, pub_date, 25)
        assert isinstance(result["data_retrieved_at"], datetime)
        assert result["data_retrieved_at"].tzinfo is not None


# ---------------------------------------------------------------------------
# Integration tests — live yfinance (require network, skip if offline)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestGetPriceMovementContextIntegration:
    """
    Live integration tests using real yfinance data.
    These make real network calls and take ~5-10 seconds each.
    Run with: scripts/venv/bin/python -m pytest ... -v (no -m filter)
    """

    def test_lloy_known_date_returns_price(self):
        """LLOY: verify price_at_transaction_date is returned for a known recent weekday."""
        # Use a date roughly 3 months ago (should be within 1y window)
        tx_date = date(2024, 12, 2)   # Monday
        pub_date = date(2024, 12, 9)  # Monday, 7-day lag
        result = get_price_movement_context("LLOY", tx_date, pub_date, 7)

        assert result["data_quality_flag"] in ("good", "sparse")
        assert result["price_at_transaction_date"] is not None
        assert result["price_at_publication"] is not None
        assert result["price_now"] is not None
        assert result["price_currency"] == "GBp"

    def test_lloy_movement_formula_correct(self):
        """Verify movement_since_transaction formula: (now - tx) / tx * 100."""
        tx_date = date(2024, 12, 2)
        pub_date = date(2024, 12, 9)
        result = get_price_movement_context("LLOY", tx_date, pub_date, 7)

        p_tx = result["price_at_transaction_date"]
        p_now = result["price_now"]
        if p_tx and p_now and p_tx != 0:
            expected = (p_now - p_tx) / p_tx * 100
            assert result["movement_since_transaction"]["change_pct"] == pytest.approx(
                expected, abs=0.01
            )

    def test_lloy_short_windows_populated(self):
        """All short windows should be populated for a liquid stock."""
        tx_date = date(2024, 12, 2)
        pub_date = date(2025, 1, 15)  # well past tx, enough history before pub
        result = get_price_movement_context("LLOY", tx_date, pub_date, 44)

        swa = result["short_window_analysis"]
        for name in ("3d", "1w", "2w", "1m"):
            assert name in swa
            if swa[name] is not None:
                assert "change_pct" in swa[name]
                assert "avg_daily_volume" in swa[name]
                assert "volume_vs_30d_avg" in swa[name]
                assert "volume_anomaly" in swa[name]

    def test_lloy_lag_greater_than_5_movement_during_lag_populated(self):
        """With a 7-day lag, movement_during_lag should be populated."""
        tx_date = date(2024, 12, 2)
        pub_date = date(2024, 12, 9)
        result = get_price_movement_context("LLOY", tx_date, pub_date, 7)

        lag = result["movement_during_lag"]
        assert "change_pct" in lag
        assert "note" in lag
        assert lag["note"] is not None
        assert len(lag["note"]) > 10

    def test_lloy_position_in_52wk_range_valid(self):
        """position_in_52wk_range should be in [0, 100]."""
        tx_date = date(2024, 12, 2)
        pub_date = date(2024, 12, 9)
        result = get_price_movement_context("LLOY", tx_date, pub_date, 7)

        pos = result["position_in_52wk_range"]
        assert pos is not None
        assert 0.0 <= pos <= 100.0

    def test_weekend_transaction_date_handled_gracefully(self):
        """A Saturday transaction_date should be adjusted to nearest trading day."""
        # Saturday 2024-12-07
        tx_date = date(2024, 12, 7)   # Saturday
        pub_date = date(2024, 12, 9)  # Monday
        result = get_price_movement_context("LLOY", tx_date, pub_date, 2)

        assert result["data_quality_flag"] in ("good", "sparse")
        assert result["date_adjustment_applied"] is True
        assert result["date_adjustment_note"] is not None
        assert "transaction_date" in result["date_adjustment_note"]

    def test_unknown_ticker_returns_unavailable_gracefully(self):
        """An unknown ticker should return data_quality_flag='unavailable', not raise."""
        result = get_price_movement_context(
            "INVALID_TICKER_XYZ_99999",
            date(2025, 1, 10),
            date(2025, 1, 14),
            4,
        )
        assert result["data_quality_flag"] == "unavailable"
        assert result["price_at_transaction_date"] is None
        assert result["price_now"] is None

    def test_cache_prevents_yfinance_call(self):
        """With a real hist in cache, no yfinance call should be made."""
        import yfinance as yf
        # Fetch real data once
        t = yf.Ticker("LLOY.L")
        hist = t.history(period="1y", interval="1d")
        time.sleep(2)

        if hist.empty:
            pytest.skip("No yfinance data for LLOY — network issue")

        cache = {"hist": hist}
        tx_date = date(2024, 12, 2)
        pub_date = date(2024, 12, 9)

        with patch("utilities.get_price_movement_context.yf.Ticker") as mock_yf:
            result = get_price_movement_context("LLOY", tx_date, pub_date, 7, price_data_cache=cache)
            mock_yf.assert_not_called()

        assert result["cache_used"] is True
        assert result["data_quality_flag"] in ("good", "sparse")
