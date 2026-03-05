"""
Tests for get_relative_performance.py

Unit tests: mocked yfinance — no network calls, no Firestore.
Integration tests: live yfinance — marked with @pytest.mark.integration.

Run unit tests only:
    scripts/venv/bin/python -m pytest utilities/tests/test_get_relative_performance.py -v -m "not integration"

Run all tests (requires network):
    scripts/venv/bin/python -m pytest utilities/tests/test_get_relative_performance.py -v
"""

import os
import time
from datetime import datetime, date, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from utilities.get_relative_performance import (
    get_relative_performance,
    _fetch_index_history,
    _get_stock_change,
    _compute_index_change,
    _compute_position_in_52wk_range,
    _INDEX_YF_TICKERS,
    _WINDOW_ROWS,
    _DEFAULT_WINDOWS,
)


# ---------------------------------------------------------------------------
# Fixtures — synthetic price_history and index_snapshot
# ---------------------------------------------------------------------------

def _make_price_history(
    ticker="TEST",
    current_price=100.0,
    windows=None,
):
    """Synthetic price_history dict matching get_price_history output shape."""
    if windows is None:
        windows = {
            "1w": {"start_price": 95.0,  "end_price": 100.0, "change_pct": 5.2632,
                   "high": 101.0, "low": 94.0, "avg_daily_volume": 500_000},
            "1m": {"start_price": 90.0,  "end_price": 100.0, "change_pct": 11.1111,
                   "high": 102.0, "low": 89.0, "avg_daily_volume": 480_000},
            "3m": {"start_price": 80.0,  "end_price": 100.0, "change_pct": 25.0,
                   "high": 105.0, "low": 78.0, "avg_daily_volume": 450_000},
            "1y": {"start_price": 70.0,  "end_price": 100.0, "change_pct": 42.8571,
                   "high": 110.0, "low": 65.0, "avg_daily_volume": 400_000},
        }
    return {
        "ticker": ticker,
        "ticker_yf": f"{ticker}.L",
        "current_price": current_price,
        "current_price_note": None,
        "current_volume": 600_000,
        "windows": windows,
        "data_retrieved_at": datetime.now(timezone.utc),
        "data_quality_flag": "good",
    }


def _make_index_snapshot(index_name="FTSE_SMALL_CAP", data_found=True):
    """Synthetic index_snapshot dict matching get_index_snapshot output shape."""
    if not data_found:
        return {
            "data_found": False,
            "index_name": None,
            "index_value": None,
            "week_52_high": None,
            "week_52_low": None,
            "change_1d_pct": None,
            "scrape_date": None,
            "snapshot_age_days": None,
        }
    return {
        "data_found": True,
        "index_name": index_name,
        "index_value": 7500.0,
        "week_52_high": 8200.0,
        "week_52_low": 6500.0,
        "change_1d_pct": 0.12,
        "scrape_date": date.today(),
        "snapshot_age_days": 0,
    }


def _make_index_hist(n_rows=65, start=7000.0, end=7500.0):
    """Synthetic index Close history DataFrame."""
    closes = np.linspace(start, end, n_rows)
    idx = pd.date_range(end=pd.Timestamp.today(), periods=n_rows, freq="B")
    return pd.DataFrame({"Close": closes, "Volume": [1_000_000] * n_rows}, index=idx)


# ---------------------------------------------------------------------------
# Tests: _INDEX_YF_TICKERS mapping
# ---------------------------------------------------------------------------

class TestIndexYfTickers:
    def test_aim_mapped(self):
        assert _INDEX_YF_TICKERS["AIM_ALL_SHARE"] == "^FTAI"

    def test_small_cap_mapped(self):
        assert _INDEX_YF_TICKERS["FTSE_SMALL_CAP"] == "^FTSC"

    def test_ftse_250_mapped(self):
        assert _INDEX_YF_TICKERS["FTSE_250"] == "^FTMC"

    def test_three_entries(self):
        assert len(_INDEX_YF_TICKERS) == 3


# ---------------------------------------------------------------------------
# Tests: _get_stock_change
# ---------------------------------------------------------------------------

class TestGetStockChange:
    def test_returns_change_pct_for_window(self):
        ph = _make_price_history()
        assert _get_stock_change(ph, "1w") == pytest.approx(5.2632)

    def test_returns_none_for_missing_window(self):
        ph = _make_price_history()
        assert _get_stock_change(ph, "3y") is None  # "3y" is not a recognised window

    def test_returns_none_when_window_not_present(self):
        ph = _make_price_history(windows={"1m": {"change_pct": 3.0, "high": 100.0, "low": 90.0}})
        assert _get_stock_change(ph, "1w") is None

    def test_returns_none_when_windows_key_absent(self):
        ph = {"ticker": "X", "current_price": 100.0}
        assert _get_stock_change(ph, "1w") is None

    def test_returns_none_when_change_pct_absent(self):
        ph = _make_price_history(windows={"1w": {"high": 100.0, "low": 90.0}})
        assert _get_stock_change(ph, "1w") is None

    def test_negative_change(self):
        ph = _make_price_history(windows={"1w": {"change_pct": -3.5, "high": 100.0, "low": 90.0}})
        assert _get_stock_change(ph, "1w") == pytest.approx(-3.5)

    def test_zero_change(self):
        ph = _make_price_history(windows={"1w": {"change_pct": 0.0, "high": 100.0, "low": 90.0}})
        assert _get_stock_change(ph, "1w") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: _compute_index_change
# ---------------------------------------------------------------------------

class TestComputeIndexChange:
    def test_simple_5pct_gain(self):
        hist = _make_index_hist(n_rows=10, start=100.0, end=105.0)
        result = _compute_index_change(hist, 10)
        assert result == pytest.approx(5.0, abs=0.5)

    def test_uses_last_n_rows_only(self):
        # First half flat at 100, second half rises to 110
        half = 20
        closes = [100.0] * half + [110.0] * half
        idx = pd.date_range(end=pd.Timestamp.today(), periods=len(closes), freq="B")
        hist = pd.DataFrame({"Close": closes}, index=idx)
        # With n_rows=half, we only see the last 20 rows (all at 110 → no change)
        result = _compute_index_change(hist, half)
        assert result == pytest.approx(0.0, abs=0.01)

    def test_uses_all_rows_when_fewer_than_n(self):
        hist = _make_index_hist(n_rows=3, start=200.0, end=210.0)
        result = _compute_index_change(hist, 21)  # n_rows=21, only 3 available
        assert result is not None
        assert result == pytest.approx((210.0 - 200.0) / 200.0 * 100, abs=0.5)

    def test_returns_none_for_none_hist(self):
        assert _compute_index_change(None, 5) is None

    def test_returns_none_for_empty_df(self):
        assert _compute_index_change(pd.DataFrame(), 5) is None

    def test_returns_none_for_single_row(self):
        hist = _make_index_hist(n_rows=1, start=100.0, end=100.0)
        assert _compute_index_change(hist, 5) is None

    def test_returns_none_for_zero_start(self):
        hist = _make_index_hist(n_rows=5, start=0.0, end=100.0)
        assert _compute_index_change(hist, 5) is None

    def test_returns_none_when_close_column_absent(self):
        hist = pd.DataFrame({"Volume": [1000, 2000]})
        assert _compute_index_change(hist, 2) is None

    def test_negative_change(self):
        hist = _make_index_hist(n_rows=10, start=110.0, end=100.0)
        result = _compute_index_change(hist, 10)
        assert result is not None
        assert result < 0

    def test_result_is_rounded_to_4dp(self):
        hist = _make_index_hist(n_rows=2, start=3.0, end=4.0)
        result = _compute_index_change(hist, 2)
        assert result == round(result, 4)


# ---------------------------------------------------------------------------
# Tests: _compute_position_in_52wk_range
# ---------------------------------------------------------------------------

class TestComputePositionIn52wkRange:
    def _ph(self, current_price, high, low):
        return {
            "current_price": current_price,
            "windows": {
                "1y": {"high": high, "low": low, "change_pct": 0.0,
                       "start_price": low, "end_price": current_price}
            },
        }

    def test_at_52wk_low(self):
        result = _compute_position_in_52wk_range(self._ph(65.0, 110.0, 65.0))
        assert result == pytest.approx(0.0)

    def test_at_52wk_high(self):
        result = _compute_position_in_52wk_range(self._ph(110.0, 110.0, 65.0))
        assert result == pytest.approx(100.0)

    def test_at_midpoint(self):
        result = _compute_position_in_52wk_range(self._ph(87.5, 110.0, 65.0))
        assert result == pytest.approx(50.0)

    def test_above_52wk_high_clamped_to_100(self):
        result = _compute_position_in_52wk_range(self._ph(120.0, 110.0, 65.0))
        assert result == pytest.approx(100.0)

    def test_below_52wk_low_clamped_to_0(self):
        result = _compute_position_in_52wk_range(self._ph(50.0, 110.0, 65.0))
        assert result == pytest.approx(0.0)

    def test_zero_range_returns_none(self):
        result = _compute_position_in_52wk_range(self._ph(100.0, 100.0, 100.0))
        assert result is None

    def test_no_current_price_returns_none(self):
        ph = {"current_price": None, "windows": {"1y": {"high": 100.0, "low": 80.0}}}
        assert _compute_position_in_52wk_range(ph) is None

    def test_no_1y_window_returns_none(self):
        ph = {"current_price": 90.0, "windows": {"1w": {"high": 100.0, "low": 80.0}}}
        assert _compute_position_in_52wk_range(ph) is None

    def test_no_windows_key_returns_none(self):
        ph = {"current_price": 90.0}
        assert _compute_position_in_52wk_range(ph) is None

    def test_missing_high_returns_none(self):
        ph = {"current_price": 90.0, "windows": {"1y": {"low": 80.0}}}
        assert _compute_position_in_52wk_range(ph) is None

    def test_missing_low_returns_none(self):
        ph = {"current_price": 90.0, "windows": {"1y": {"high": 100.0}}}
        assert _compute_position_in_52wk_range(ph) is None

    def test_result_is_between_0_and_100(self):
        result = _compute_position_in_52wk_range(self._ph(87.5, 110.0, 65.0))
        assert 0.0 <= result <= 100.0

    def test_result_rounded_to_2dp(self):
        result = _compute_position_in_52wk_range(self._ph(87.5, 110.0, 65.0))
        assert result == round(result, 2)


# ---------------------------------------------------------------------------
# Tests: _fetch_index_history (mocked yfinance)
# ---------------------------------------------------------------------------

class TestFetchIndexHistory:
    @patch("utilities.get_relative_performance.yf.Ticker")
    @patch("utilities.get_relative_performance.time.sleep")
    def test_returns_df_on_success(self, mock_sleep, mock_ticker_cls):
        hist = _make_index_hist()
        mock_ticker_cls.return_value.history.return_value = hist
        result = _fetch_index_history("AIM_ALL_SHARE")
        assert result is not None
        assert not result.empty

    @patch("utilities.get_relative_performance.yf.Ticker")
    @patch("utilities.get_relative_performance.time.sleep")
    def test_returns_none_for_empty_hist(self, mock_sleep, mock_ticker_cls):
        mock_ticker_cls.return_value.history.return_value = pd.DataFrame()
        result = _fetch_index_history("FTSE_SMALL_CAP")
        assert result is None

    @patch("utilities.get_relative_performance.yf.Ticker")
    @patch("utilities.get_relative_performance.time.sleep")
    def test_returns_none_on_exception(self, mock_sleep, mock_ticker_cls):
        mock_ticker_cls.return_value.history.side_effect = Exception("network error")
        result = _fetch_index_history("FTSE_250")
        assert result is None

    @patch("utilities.get_relative_performance.yf.Ticker")
    @patch("utilities.get_relative_performance.time.sleep")
    def test_sleep_called_on_success(self, mock_sleep, mock_ticker_cls):
        mock_ticker_cls.return_value.history.return_value = _make_index_hist()
        _fetch_index_history("AIM_ALL_SHARE")
        mock_sleep.assert_called_once()

    @patch("utilities.get_relative_performance.yf.Ticker")
    @patch("utilities.get_relative_performance.time.sleep")
    def test_sleep_called_on_exception(self, mock_sleep, mock_ticker_cls):
        mock_ticker_cls.return_value.history.side_effect = Exception("fail")
        _fetch_index_history("FTSE_SMALL_CAP")
        mock_sleep.assert_called_once()

    @patch("utilities.get_relative_performance.time.sleep")
    def test_returns_none_for_unknown_index(self, mock_sleep):
        result = _fetch_index_history("UNKNOWN_INDEX")
        assert result is None
        mock_sleep.assert_not_called()

    @patch("utilities.get_relative_performance.time.sleep")
    def test_returns_none_for_none_index_name(self, mock_sleep):
        result = _fetch_index_history(None)
        assert result is None
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: get_relative_performance (full function, mocked yfinance)
# ---------------------------------------------------------------------------

class TestGetRelativePerformanceMocked:
    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_vs_index_available_true_when_hist_present(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        ph = _make_price_history()
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        assert result["vs_index_available"] is True

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_vs_index_available_false_when_hist_none(self, mock_fetch):
        mock_fetch.return_value = None
        ph = _make_price_history()
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        assert result["vs_index_available"] is False

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_vs_index_available_false_when_data_found_false(self, mock_fetch):
        # data_found=False means index_name=None; _fetch_index_history returns None for None
        mock_fetch.return_value = None
        ph = _make_price_history()
        snap = _make_index_snapshot(data_found=False)
        result = get_relative_performance(ph, snap)
        assert result["vs_index_available"] is False

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_vs_index_available_false_when_snapshot_none(self, mock_fetch):
        mock_fetch.return_value = None
        ph = _make_price_history()
        result = get_relative_performance(ph, None)
        assert result["vs_index_available"] is False

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_relative_performance_calculated_correctly(self, mock_fetch):
        # stock_change_pct 1w = 5.2632; index should produce a known change
        hist = _make_index_hist(n_rows=65, start=7000.0, end=7140.0)  # +2%
        mock_fetch.return_value = hist
        ph = _make_price_history()
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        w = result["windows"]["1w"]
        assert w["stock_change_pct"] == pytest.approx(5.2632)
        assert w["index_change_pct"] is not None
        assert w["relative_performance"] == pytest.approx(
            w["stock_change_pct"] - w["index_change_pct"], abs=0.001
        )

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_relative_performance_none_when_index_unavailable(self, mock_fetch):
        mock_fetch.return_value = None
        ph = _make_price_history()
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        for w_data in result["windows"].values():
            assert w_data["relative_performance"] is None
            assert w_data["index_change_pct"] is None

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_relative_performance_none_when_stock_change_none(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        # Price history with no 1w window data
        ph = _make_price_history(windows={"1m": {"change_pct": 5.0, "high": 110.0, "low": 90.0}})
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        w = result["windows"].get("1w", {})
        assert w.get("stock_change_pct") is None
        assert w.get("relative_performance") is None

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_all_three_default_windows_present(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        ph = _make_price_history()
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        for w in ["1w", "1m", "3m"]:
            assert w in result["windows"]

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_window_subset_respected(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        ph = _make_price_history()
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap, windows=["1w"])
        assert list(result["windows"].keys()) == ["1w"]

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_position_in_52wk_range_present_and_correct(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        # current_price=100, 1y high=110, low=65 → position=(100-65)/(110-65)*100=77.78%
        ph = _make_price_history(current_price=100.0)
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        expected = (100.0 - 65.0) / (110.0 - 65.0) * 100
        assert result["position_in_52wk_range"] == pytest.approx(expected, abs=0.01)

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_position_in_52wk_range_between_0_and_100(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        ph = _make_price_history(current_price=90.0)
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        pos = result["position_in_52wk_range"]
        assert pos is not None
        assert 0.0 <= pos <= 100.0

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_ticker_passed_through(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        ph = _make_price_history(ticker="LLOY")
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        assert result["ticker"] == "LLOY"

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_index_name_passed_through(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        ph = _make_price_history()
        snap = _make_index_snapshot(index_name="AIM_ALL_SHARE")
        result = get_relative_performance(ph, snap)
        assert result["index_name"] == "AIM_ALL_SHARE"

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_data_retrieved_at_is_utc_datetime(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        ph = _make_price_history()
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        assert isinstance(result["data_retrieved_at"], datetime)
        assert result["data_retrieved_at"].tzinfo is not None

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_no_exception_when_price_history_missing_windows(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        ph = {"ticker": "X", "current_price": None, "windows": {}}
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        assert isinstance(result, dict)

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_no_exception_when_index_snapshot_none(self, mock_fetch):
        mock_fetch.return_value = None
        ph = _make_price_history()
        result = get_relative_performance(ph, None)
        assert isinstance(result, dict)
        assert result["vs_index_available"] is False

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_unknown_window_in_list_ignored(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        ph = _make_price_history()
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap, windows=["1w", "invalid"])
        assert "1w" in result["windows"]
        assert "invalid" not in result["windows"]

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_per_window_keys_present(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        ph = _make_price_history()
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        for w_data in result["windows"].values():
            assert "stock_change_pct" in w_data
            assert "index_change_pct" in w_data
            assert "relative_performance" in w_data

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_sign_and_direction_of_relative_performance(self, mock_fetch):
        """Stock up 10%, index up 5% → relative_performance should be positive ~5%."""
        # Build hist where 1w window (last 5 rows) goes from 100 to 105 (+5%)
        # We need 65 rows total (3mo), last 5 show the +5% window
        n = 65
        # First 60 rows flat at 100, last 5 rise from 100 to 105
        closes = [100.0] * (n - 5) + list(np.linspace(100.0, 105.0, 5))
        idx = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
        hist = pd.DataFrame({"Close": closes}, index=idx)
        mock_fetch.return_value = hist

        # Stock 1w change = +10%
        ph = _make_price_history(windows={
            "1w":  {"change_pct": 10.0,  "high": 110.0, "low": 65.0},
            "1m":  {"change_pct": 8.0,   "high": 110.0, "low": 65.0},
            "3m":  {"change_pct": 15.0,  "high": 110.0, "low": 65.0},
            "1y":  {"change_pct": 42.0,  "high": 110.0, "low": 65.0},
        })
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        w1 = result["windows"]["1w"]
        # stock=+10%, index 1w = last 5 rows: 100→105 = +5%
        assert w1["stock_change_pct"] == pytest.approx(10.0)
        assert w1["index_change_pct"] == pytest.approx(5.0, abs=0.01)
        assert w1["relative_performance"] == pytest.approx(5.0, abs=0.01)

    @patch("utilities.get_relative_performance._fetch_index_history")
    def test_default_windows_are_1w_1m_3m(self, mock_fetch):
        mock_fetch.return_value = _make_index_hist()
        ph = _make_price_history()
        snap = _make_index_snapshot()
        result = get_relative_performance(ph, snap)
        assert set(result["windows"].keys()) == {"1w", "1m", "3m"}
        assert "1y" not in result["windows"]


# ---------------------------------------------------------------------------
# Integration tests — live yfinance (require network, skip if offline)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestGetRelativePerformanceIntegration:
    """
    Live integration tests using real yfinance data.
    These make real network calls and take ~10 seconds per test.
    Run with: python -m pytest ... -v (no -m filter)
    """

    def _real_price_history(self, ticker, index_membership):
        """Minimal synthetic price_history for integration testing."""
        import yfinance as yf
        ticker_yf = f"{ticker}.L"
        t = yf.Ticker(ticker_yf)
        hist = t.history(period="1y", interval="1d")
        time.sleep(2)
        if hist.empty:
            pytest.skip(f"No yfinance data for {ticker}")

        hist = hist.dropna(subset=["Close"])
        windows = {}
        for w, n in [("1w", 5), ("1m", 21), ("3m", 63), ("1y", 252)]:
            window = hist.iloc[-n:] if len(hist) >= n else hist
            if len(window) >= 2:
                s = float(window["Close"].iloc[0])
                e = float(window["Close"].iloc[-1])
                windows[w] = {
                    "change_pct": round((e - s) / s * 100, 4) if s else None,
                    "high": float(window["High"].max()),
                    "low": float(window["Low"].min()),
                }
        current_price = float(hist["Close"].iloc[-1])
        return {
            "ticker": ticker,
            "current_price": current_price,
            "windows": windows,
            "data_quality_flag": "good",
        }

    def _real_index_snapshot(self, index_name):
        """Minimal synthetic index_snapshot for integration testing."""
        return {
            "data_found": True,
            "index_name": index_name,
        }

    def test_lloy_vs_ftse_small_cap(self):
        """LLOY (main market) benchmarked against FTSE Small Cap."""
        ph = self._real_price_history("LLOY", "MAIN_MARKET")
        snap = self._real_index_snapshot("FTSE_SMALL_CAP")
        result = get_relative_performance(ph, snap, windows=["1w", "1m", "3m"])

        assert result["ticker"] == "LLOY"
        assert result["index_name"] == "FTSE_SMALL_CAP"
        assert result["vs_index_available"] is True
        for w in ["1w", "1m", "3m"]:
            assert w in result["windows"]
            w_data = result["windows"][w]
            assert w_data["stock_change_pct"] is not None
            assert w_data["index_change_pct"] is not None
            assert w_data["relative_performance"] is not None
            # Verify formula: rel = stock - index
            assert w_data["relative_performance"] == pytest.approx(
                w_data["stock_change_pct"] - w_data["index_change_pct"], abs=0.001
            )

    def test_ecr_vs_aim_all_share(self):
        """ECR (AIM) benchmarked against AIM All-Share."""
        ph = self._real_price_history("ECR", "AIM")
        snap = self._real_index_snapshot("AIM_ALL_SHARE")
        result = get_relative_performance(ph, snap, windows=["1w", "1m", "3m"])

        assert result["ticker"] == "ECR"
        assert result["index_name"] == "AIM_ALL_SHARE"
        assert result["vs_index_available"] is True

    def test_position_in_52wk_range_between_0_and_100(self):
        """position_in_52wk_range must be in [0, 100] for a real ticker."""
        ph = self._real_price_history("LLOY", "MAIN_MARKET")
        snap = self._real_index_snapshot("FTSE_SMALL_CAP")
        result = get_relative_performance(ph, snap)

        pos = result["position_in_52wk_range"]
        assert pos is not None
        assert 0.0 <= pos <= 100.0

    def test_vs_index_available_false_when_no_index_name(self):
        """No index_name in snapshot → vs_index_available = False."""
        ph = self._real_price_history("LLOY", "MAIN_MARKET")
        snap = {"data_found": False, "index_name": None}
        result = get_relative_performance(ph, snap)
        assert result["vs_index_available"] is False

    def test_no_exception_for_sparse_aim_stock(self):
        """Thinly traded AIM stock should not raise exceptions."""
        # RNEW has sparse data; even if empty, should return gracefully
        import yfinance as yf
        ph = {
            "ticker": "RNEW",
            "current_price": None,
            "windows": {},
        }
        snap = self._real_index_snapshot("AIM_ALL_SHARE")
        result = get_relative_performance(ph, snap)
        assert isinstance(result, dict)
        assert result["vs_index_available"] in (True, False)

    def test_sign_of_relative_performance_consistent(self):
        """If stock outperformed index, relative_performance > 0."""
        ph = self._real_price_history("LLOY", "MAIN_MARKET")
        snap = self._real_index_snapshot("FTSE_SMALL_CAP")
        result = get_relative_performance(ph, snap, windows=["1m"])
        w = result["windows"]["1m"]
        if w["stock_change_pct"] is not None and w["index_change_pct"] is not None:
            expected_sign = w["stock_change_pct"] > w["index_change_pct"]
            actual_positive = w["relative_performance"] > 0
            # If stock outperformed, rel should be positive (and vice versa)
            if abs(w["relative_performance"]) > 0.01:  # avoid floating point noise
                assert expected_sign == actual_positive
