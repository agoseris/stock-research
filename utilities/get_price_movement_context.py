"""
get_price_movement_context.py

Returns price movement context for the period around a director transaction and
its subsequent RNS announcement. Used by Layer 3 (Signal Freshness) to assess
whether the director buying signal has already been priced in by the market.

Execution: local machine only.
  yfinance is sensitive to IP-based rate limiting. Do not run from the GCP VM.

Rate limiting: a 2-second sleep is applied after every yfinance fetch.
  The sleep is skipped when price_data_cache is provided (no fetch is made).

Price units: yfinance returns LSE/AIM stock prices in pence (GBp), not GBP.
  e.g. a stock trading at £1.20 appears as 120.0 in results.
  No unit conversion is applied — callers should be aware of this.
  price_currency is set to "GBp" to reflect this accurately.

Shared price data cache:
  Layer 1 (get_price_history) and Layer 3 (this tool) both call yfinance for the
  same ticker within a single investigation event. To avoid redundant calls, the
  orchestrator can pre-fetch 1y of daily history and pass it as price_data_cache:
      {"hist": pd.DataFrame}  ← from yf.Ticker(ticker_yf).history(period="1y")
  When provided, this tool uses the cached DataFrame without making a yfinance call.

Usage:
    from utilities.get_price_movement_context import get_price_movement_context
    result = get_price_movement_context(
        ticker="FGEN",
        transaction_date=date(2025, 11, 10),
        published_at=date(2025, 11, 14),
        reporting_lag_days=4,
    )
"""

import os
import time
from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

_UTILITIES_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTILITIES_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# Fallback credentials path for local dev where VM path in .env doesn't exist
_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds_path or not os.path.exists(_creds_path):
    _local_creds = os.path.join(_PROJECT_ROOT, "frontend", "gcp-credentials.json")
    if os.path.exists(_local_creds):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _local_creds

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RATE_LIMIT_SECONDS = 2

# Minimum rows for "good" data quality (~80% of 252 expected trading days)
_GOOD_ROW_THRESHOLD = 200

# Short windows (trading days) prior to publication date
_SHORT_WINDOWS = {"3d": 3, "1w": 5, "2w": 10, "1m": 21}

# Direction thresholds: movements within +/-0.5% are classified "flat"
_FLAT_THRESHOLD = 0.5

# Volume anomaly threshold (ratio vs baseline): >1.5 = elevated
_VOLUME_ANOMALY_THRESHOLD = 1.5

# Reporting lag threshold: lags <= this value produce noise, not signal
_SHORT_LAG_THRESHOLD = 5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_price_movement_context(
    ticker: str,
    transaction_date: date,
    published_at: date,
    reporting_lag_days: int,
    price_data_cache: dict = None,
) -> dict:
    """
    Return price movement context for the period around a director transaction.

    Parameters
    ----------
    ticker : str
        LSE ticker with or without the .L suffix (e.g. "FGEN" or "FGEN.L").
        The .L suffix is appended automatically if absent.
    transaction_date : date
        Actual date the director executed the transaction.
    published_at : date
        Date the RNS announcement was published.
    reporting_lag_days : int
        Days between transaction_date and published_at (from pdmr_transaction record).
    price_data_cache : dict, optional
        Pre-fetched price data from the orchestrator to avoid redundant yfinance calls.
        Expected format: {"hist": pd.DataFrame}  (1y daily history for this ticker).
        When provided, no yfinance call is made.

    Returns
    -------
    dict with keys:
        ticker                      str     original ticker (without .L)
        ticker_yf                   str     ticker passed to yfinance (with .L)
        price_at_transaction_date   float | None
        price_at_publication        float | None
        price_now                   float | None
        price_currency              str     "GBp" (pence — yfinance convention for LSE)
        movement_since_transaction  dict    {change_pct, direction}
        movement_since_publication  dict    {change_pct, direction}
        movement_during_lag         dict    {change_pct, direction, note}
        short_window_analysis       dict    keyed by "3d"|"1w"|"2w"|"1m"
                                              each: {change_pct, avg_daily_volume,
                                                     volume_vs_30d_avg, volume_anomaly}
        volume_anomaly_detected     bool    True if any window has volume_vs_30d_avg > 1.5
        volume_anomaly_note         str | None  observation-only note (no leakage assertion)
        position_in_52wk_range      float | None  0% = 52wk low, 100% = 52wk high
        data_quality_flag           str     "good" | "sparse" | "unavailable"
        date_adjustment_applied     bool    True if transaction_date or published_at
                                              was not a trading day and was adjusted
        date_adjustment_note        str | None  describes which dates were adjusted
        cache_used                  bool    True if price_data_cache was used
        data_retrieved_at           datetime (UTC)
    """
    ticker_yf = ticker if ticker.upper().endswith(".L") else f"{ticker}.L"
    # Store original ticker without .L for output
    ticker_clean = ticker[:-2] if ticker.upper().endswith(".L") else ticker
    retrieved_at = datetime.now(timezone.utc)

    # --- Acquire history (cache or yfinance) ---
    cache_used = False
    hist = None

    if price_data_cache is not None and "hist" in price_data_cache:
        hist = price_data_cache["hist"]
        cache_used = True
    else:
        try:
            yf_ticker_obj = yf.Ticker(ticker_yf)
            hist = yf_ticker_obj.history(period="1y", interval="1d")
        except Exception as exc:
            time.sleep(_RATE_LIMIT_SECONDS)
            return _unavailable_result(ticker_clean, ticker_yf, retrieved_at, error=str(exc))
        time.sleep(_RATE_LIMIT_SECONDS)

    if hist is None or hist.empty:
        return _unavailable_result(ticker_clean, ticker_yf, retrieved_at)

    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        return _unavailable_result(ticker_clean, ticker_yf, retrieved_at)

    # --- Locate prices ---
    price_at_tx, tx_adjusted = _price_on_date(hist, transaction_date)
    price_at_pub, pub_adjusted = _price_on_date(hist, published_at)
    price_now = _current_price(hist)

    date_adjustment_applied = tx_adjusted or pub_adjusted
    date_adjustment_note = _build_date_adjustment_note(
        transaction_date, tx_adjusted, published_at, pub_adjusted
    )

    # --- Movements ---
    movement_since_tx = _compute_movement(price_at_tx, price_now)
    movement_since_pub = _compute_movement(price_at_pub, price_now)
    movement_during_lag = _compute_lag_movement(price_at_tx, price_at_pub, reporting_lag_days)

    # --- Short window analysis (prior to publication) ---
    baseline_volume = _compute_baseline_volume(hist)
    short_windows = _compute_short_windows(hist, published_at, baseline_volume)

    # --- Volume anomaly (top-level) ---
    volume_anomaly_detected = any(
        w.get("volume_anomaly", False)
        for w in short_windows.values()
        if w is not None
    )
    volume_anomaly_note = (
        "Elevated trading volume observed in the period prior to publication. "
        "This is an observation only — no inference about the cause is made. "
        "Elevated pre-announcement volume may have many explanations."
    ) if volume_anomaly_detected else None

    # --- Position in 52-week range ---
    position_52wk = _compute_52wk_position(hist, price_now)

    return {
        "ticker":                     ticker_clean,
        "ticker_yf":                  ticker_yf,
        "price_at_transaction_date":  price_at_tx,
        "price_at_publication":       price_at_pub,
        "price_now":                  price_now,
        "price_currency":             "GBp",
        "movement_since_transaction": movement_since_tx,
        "movement_since_publication": movement_since_pub,
        "movement_during_lag":        movement_during_lag,
        "short_window_analysis":      short_windows,
        "volume_anomaly_detected":    volume_anomaly_detected,
        "volume_anomaly_note":        volume_anomaly_note,
        "position_in_52wk_range":     position_52wk,
        "data_quality_flag":          _classify_quality(hist),
        "date_adjustment_applied":    date_adjustment_applied,
        "date_adjustment_note":       date_adjustment_note,
        "cache_used":                 cache_used,
        "data_retrieved_at":          retrieved_at,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _price_on_date(hist: pd.DataFrame, target_date: date) -> tuple:
    """
    Return (price, date_adjusted) where:
      - price is the closing price on target_date, or the nearest prior trading day
      - date_adjusted is True if target_date was not available in history
        (weekend, public holiday, or outside the data window)

    Falls back to the most recent trading day before target_date. If none exists
    before the target, uses the earliest available trading day after it.

    Returns (None, False) if hist is empty or price cannot be determined.
    """
    if hist is None or hist.empty:
        return None, False

    hist_dates = list(hist.index.date)  # list of datetime.date objects

    if target_date in hist_dates:
        mask = [d == target_date for d in hist.index.date]
        row = hist.iloc[[i for i, m in enumerate(mask) if m]]
        return round(float(row["Close"].iloc[-1]), 4), False

    # Not an exact match — find nearest (prefer most recent prior trading day)
    before = [d for d in hist_dates if d < target_date]
    after = [d for d in hist_dates if d > target_date]

    if before:
        nearest = max(before)
    elif after:
        nearest = min(after)
    else:
        return None, True

    mask = [d == nearest for d in hist.index.date]
    row = hist.iloc[[i for i, m in enumerate(mask) if m]]
    return round(float(row["Close"].iloc[-1]), 4), True


def _current_price(hist: pd.DataFrame) -> "float | None":
    """Return the most recent closing price from history."""
    if hist is None or hist.empty:
        return None
    try:
        val = hist["Close"].iloc[-1]
        return round(float(val), 4) if not pd.isna(val) else None
    except Exception:
        return None


def _compute_movement(from_price: "float | None", to_price: "float | None") -> dict:
    """
    Compute percentage change and direction between two prices.

    Returns:
        {"change_pct": float | None, "direction": str | None}
    direction: "up" | "down" | "flat" | None
    """
    if from_price is None or to_price is None or from_price == 0:
        return {"change_pct": None, "direction": None}

    change_pct = round((to_price - from_price) / from_price * 100, 4)
    direction = _classify_direction(change_pct)
    return {"change_pct": change_pct, "direction": direction}


def _compute_lag_movement(
    price_at_tx: "float | None",
    price_at_pub: "float | None",
    reporting_lag_days: int,
) -> dict:
    """
    Compute movement between transaction date and publication date.

    Returns dict with change_pct, direction, note.
    change_pct is None when lag=0 (no movement to measure).
    note is always descriptive.
    """
    if reporting_lag_days == 0:
        return {
            "change_pct": None,
            "direction":  None,
            "note":       "Reporting lag is 0 days — transaction date and publication date are the same.",
        }

    movement = _compute_movement(price_at_tx, price_at_pub)
    change_pct = movement["change_pct"]
    direction = movement["direction"]

    if change_pct is None:
        note = "Price data unavailable for one or both dates — lag movement cannot be calculated."
    elif reporting_lag_days <= _SHORT_LAG_THRESHOLD:
        note = (
            f"Reporting lag is {reporting_lag_days} days. Price moved "
            f"{change_pct:+.2f}% between transaction and publication. "
            f"Short lags (≤{_SHORT_LAG_THRESHOLD} days) may reflect noise rather than signal."
        )
    else:
        direction_word = {"up": "rose", "down": "fell", "flat": "was flat"}.get(direction or "", "moved")
        note = (
            f"Price {direction_word} {abs(change_pct):.2f}% between transaction date and publication "
            f"({reporting_lag_days}-day lag) — signal may be partially priced in."
        )

    return {"change_pct": change_pct, "direction": direction, "note": note}


def _compute_baseline_volume(hist: pd.DataFrame) -> "float | None":
    """
    Return mean daily volume from the full 1y history, excluding zero-volume days.
    Used as the baseline denominator for volume_vs_30d_avg ratios.
    Returns None if no valid volume data.
    """
    if hist is None or hist.empty or "Volume" not in hist.columns:
        return None

    non_zero = hist["Volume"].replace(0, pd.NA).dropna()
    if non_zero.empty:
        return None

    return float(non_zero.mean())


def _compute_short_windows(
    hist: pd.DataFrame,
    published_at: date,
    baseline_volume: "float | None",
) -> dict:
    """
    Compute per-window statistics for the period prior to published_at.

    Windows: 3d (3 trading days), 1w (5), 2w (10), 1m (21).
    All windows use rows strictly before published_at.

    Returns dict keyed by window name. Each value is a dict with:
        change_pct, avg_daily_volume, volume_vs_30d_avg, volume_anomaly
    Value is None if insufficient data for that window.
    """
    if hist is None or hist.empty:
        return {name: None for name in _SHORT_WINDOWS}

    # Rows strictly before publication date
    pre_pub_mask = [d < published_at for d in hist.index.date]
    pre_pub = hist.iloc[[i for i, m in enumerate(pre_pub_mask) if m]]

    result = {}
    for name, n_rows in _SHORT_WINDOWS.items():
        if pre_pub.empty:
            result[name] = None
            continue

        window = pre_pub.iloc[-n_rows:] if len(pre_pub) >= n_rows else pre_pub
        result[name] = _compute_window_stats(window, baseline_volume)

    return result


def _compute_window_stats(window: pd.DataFrame, baseline_volume: "float | None") -> "dict | None":
    """
    Compute change_pct, avg_daily_volume, volume_vs_30d_avg, volume_anomaly
    for a given price history window.
    Returns None on failure.
    """
    if window is None or window.empty:
        return None

    try:
        start_price = float(window["Close"].iloc[0])
        end_price = float(window["Close"].iloc[-1])
        change_pct = (
            round((end_price - start_price) / start_price * 100, 4)
            if start_price != 0
            else None
        )

        non_zero = window["Volume"].replace(0, pd.NA).dropna()
        avg_vol = int(non_zero.mean()) if not non_zero.empty else None

        volume_vs_30d_avg = None
        volume_anomaly = False
        if avg_vol is not None and baseline_volume and baseline_volume > 0:
            volume_vs_30d_avg = round(avg_vol / baseline_volume, 4)
            volume_anomaly = volume_vs_30d_avg > _VOLUME_ANOMALY_THRESHOLD

        return {
            "change_pct":        change_pct,
            "avg_daily_volume":  avg_vol,
            "volume_vs_30d_avg": volume_vs_30d_avg,
            "volume_anomaly":    volume_anomaly,
        }
    except Exception:
        return None


def _compute_52wk_position(hist: pd.DataFrame, current_price: "float | None") -> "float | None":
    """
    Calculate current price as % position within the stock's 52-week range.

    Formula: (current_price - 52wk_low) / (52wk_high - 52wk_low) * 100
    Clamped to [0, 100].
    Returns None if current_price is None or 52wk range is zero.
    """
    if current_price is None or hist is None or hist.empty:
        return None

    try:
        high_52wk = float(hist["High"].max())
        low_52wk = float(hist["Low"].min())
    except Exception:
        return None

    range_width = high_52wk - low_52wk
    if range_width <= 0:
        return None

    position = (current_price - low_52wk) / range_width * 100
    position = max(0.0, min(100.0, position))
    return round(position, 2)


def _classify_direction(change_pct: "float | None") -> "str | None":
    """Classify a percentage change as "up", "down", or "flat"."""
    if change_pct is None:
        return None
    if change_pct > _FLAT_THRESHOLD:
        return "up"
    if change_pct < -_FLAT_THRESHOLD:
        return "down"
    return "flat"


def _classify_quality(hist: pd.DataFrame) -> str:
    """Classify data quality based on number of rows in 1y history."""
    n = len(hist)
    if n == 0:
        return "unavailable"
    if n >= _GOOD_ROW_THRESHOLD:
        return "good"
    return "sparse"


def _build_date_adjustment_note(
    transaction_date: date,
    tx_adjusted: bool,
    published_at: date,
    pub_adjusted: bool,
) -> "str | None":
    """Build a descriptive note when one or both dates required adjustment."""
    parts = []
    if tx_adjusted:
        parts.append(
            f"transaction_date {transaction_date} was not a trading day — "
            "nearest prior trading day used"
        )
    if pub_adjusted:
        parts.append(
            f"published_at {published_at} was not a trading day — "
            "nearest prior trading day used"
        )
    return "; ".join(parts) if parts else None


def _unavailable_result(
    ticker: str,
    ticker_yf: str,
    retrieved_at: datetime,
    error: str = None,
) -> dict:
    """Return a well-formed unavailable result without raising."""
    result = {
        "ticker":                     ticker,
        "ticker_yf":                  ticker_yf,
        "price_at_transaction_date":  None,
        "price_at_publication":       None,
        "price_now":                  None,
        "price_currency":             "GBp",
        "movement_since_transaction": {"change_pct": None, "direction": None},
        "movement_since_publication": {"change_pct": None, "direction": None},
        "movement_during_lag":        {"change_pct": None, "direction": None, "note": None},
        "short_window_analysis":      {name: None for name in _SHORT_WINDOWS},
        "volume_anomaly_detected":    False,
        "volume_anomaly_note":        None,
        "position_in_52wk_range":     None,
        "data_quality_flag":          "unavailable",
        "date_adjustment_applied":    False,
        "date_adjustment_note":       None,
        "cache_used":                 False,
        "data_retrieved_at":          retrieved_at,
    }
    if error:
        result["error"] = error
    return result


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import date

    test_cases = [
        {
            "ticker": "LLOY",
            "transaction_date": date(2025, 11, 10),
            "published_at": date(2025, 11, 14),
            "reporting_lag_days": 4,
            "desc": "Lloyds — main market, 4-day lag",
        },
        {
            "ticker": "ECR",
            "transaction_date": date(2025, 10, 1),
            "published_at": date(2025, 10, 15),
            "reporting_lag_days": 14,
            "desc": "ECR Minerals — AIM, 14-day lag",
        },
        {
            "ticker": "INVALID_TICKER_XYZ",
            "transaction_date": date(2025, 11, 10),
            "published_at": date(2025, 11, 14),
            "reporting_lag_days": 4,
            "desc": "Unknown ticker — should return unavailable gracefully",
        },
    ]

    print("get_price_movement_context — smoke test\n")
    for tc in test_cases:
        print(f"--- {tc['ticker']} ({tc['desc']}) ---")
        result = get_price_movement_context(
            ticker=tc["ticker"],
            transaction_date=tc["transaction_date"],
            published_at=tc["published_at"],
            reporting_lag_days=tc["reporting_lag_days"],
        )
        print(f"  data_quality_flag:          {result['data_quality_flag']}")
        print(f"  price_at_transaction_date:  {result['price_at_transaction_date']}")
        print(f"  price_at_publication:       {result['price_at_publication']}")
        print(f"  price_now:                  {result['price_now']}")
        print(f"  date_adjustment_applied:    {result['date_adjustment_applied']}")
        print(f"  date_adjustment_note:       {result['date_adjustment_note']}")
        m_tx = result["movement_since_transaction"]
        print(f"  movement_since_transaction: {m_tx['change_pct']}% ({m_tx['direction']})")
        m_lag = result["movement_during_lag"]
        print(f"  movement_during_lag:        {m_lag['change_pct']}% — {m_lag['note']}")
        print(f"  volume_anomaly_detected:    {result['volume_anomaly_detected']}")
        print(f"  position_in_52wk_range:     {result['position_in_52wk_range']}%")
        print(f"  cache_used:                 {result['cache_used']}")
        for w, stats in result.get("short_window_analysis", {}).items():
            if stats:
                print(
                    f"  [{w}] chg={stats['change_pct']}%  "
                    f"vol={stats['avg_daily_volume']}  "
                    f"vs30d={stats['volume_vs_30d_avg']}  "
                    f"anomaly={stats['volume_anomaly']}"
                )
            else:
                print(f"  [{w}] no data")
        if "error" in result:
            print(f"  error: {result['error']}")
        print()
