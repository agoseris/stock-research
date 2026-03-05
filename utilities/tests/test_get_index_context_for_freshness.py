"""
Tests for get_index_context_for_freshness.

Tool 13 — Layer 3 Signal Freshness tool.

Wraps get_index_snapshot (Layer 1) and adds historical movement fields.
No duplicate implementation: base fields come directly from get_index_snapshot.

Unit tests: mock Firestore and get_index_snapshot.
Integration tests: live Firestore, seed historical snapshots, verify, clean up.
"""

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(scrape_date: date, index_value: float, index_name: str = "AIM_ALL_SHARE") -> dict:
    """Make a snapshot dict as returned by _fetch_recent_snapshots."""
    return {"scrape_date": scrape_date, "index_value": index_value}


def _make_base_result(
    data_found: bool = True,
    index_name: str = "AIM_ALL_SHARE",
    index_value: float = 1000.0,
    scrape_date: date = None,
) -> dict:
    """Make a result dict as returned by get_index_snapshot."""
    if scrape_date is None:
        scrape_date = date(2026, 3, 5)
    return {
        "data_found": data_found,
        "index_name": index_name,
        "index_value": index_value if data_found else None,
        "week_52_high": 1100.0 if data_found else None,
        "week_52_low": 900.0 if data_found else None,
        "change_1d_pct": 0.5 if data_found else None,
        "scrape_date": scrape_date if data_found else None,
        "snapshot_age_days": 0 if data_found else None,
    }


# ---------------------------------------------------------------------------
# Tests for _compute_movement
# ---------------------------------------------------------------------------

class TestComputeMovement:
    from utilities.get_index_context_for_freshness import _compute_movement

    def test_returns_none_for_empty_snapshots(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        assert _compute_movement([], 1000.0, date(2026, 3, 5), days=7, tolerance=3) is None

    def test_basic_upward_movement(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        snapshots = [_make_snapshot(date(2026, 2, 26), 950.0)]  # 7 days ago
        result = _compute_movement(snapshots, 1000.0, date(2026, 3, 5), days=7, tolerance=3)
        # (1000 - 950) / 950 * 100 = 5.26...
        assert result is not None
        assert abs(result - 5.26) < 0.01

    def test_basic_downward_movement(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        snapshots = [_make_snapshot(date(2026, 2, 26), 1050.0)]
        result = _compute_movement(snapshots, 1000.0, date(2026, 3, 5), days=7, tolerance=3)
        assert result is not None
        assert result < 0

    def test_flat_movement_returns_zero(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        snapshots = [_make_snapshot(date(2026, 2, 26), 1000.0)]
        result = _compute_movement(snapshots, 1000.0, date(2026, 3, 5), days=7, tolerance=3)
        assert result == 0.0

    def test_result_rounded_to_2dp(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        snapshots = [_make_snapshot(date(2026, 2, 26), 950.0)]
        result = _compute_movement(snapshots, 1000.0, date(2026, 3, 5), days=7, tolerance=3)
        assert result == round(result, 2)

    def test_returns_none_when_no_snapshot_before_current_date(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        # All snapshots are current or future
        snapshots = [
            _make_snapshot(date(2026, 3, 5), 1000.0),
            _make_snapshot(date(2026, 3, 6), 1010.0),
        ]
        result = _compute_movement(snapshots, 1000.0, date(2026, 3, 5), days=7, tolerance=3)
        assert result is None

    def test_skips_current_date_snapshot(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        current_date = date(2026, 3, 5)
        snapshots = [
            _make_snapshot(current_date, 1000.0),        # today — skipped
            _make_snapshot(date(2026, 2, 26), 950.0),    # 7 days ago — used
        ]
        result = _compute_movement(snapshots, 1000.0, current_date, days=7, tolerance=3)
        assert result is not None

    def test_returns_none_when_best_snapshot_outside_tolerance(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        # Closest snapshot to 7-day target is 15 days old — beyond tolerance of 3
        snapshots = [_make_snapshot(date(2026, 2, 18), 950.0)]
        result = _compute_movement(snapshots, 1000.0, date(2026, 3, 5), days=7, tolerance=3)
        assert result is None

    def test_accepts_snapshot_within_tolerance(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        # Target is 7 days ago (Feb 26); snapshot is 2 days off (Feb 24) — within tolerance=3
        snapshots = [_make_snapshot(date(2026, 2, 24), 950.0)]
        result = _compute_movement(snapshots, 1000.0, date(2026, 3, 5), days=7, tolerance=3)
        assert result is not None

    def test_returns_none_for_zero_historical_value(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        snapshots = [_make_snapshot(date(2026, 2, 26), 0.0)]
        result = _compute_movement(snapshots, 1000.0, date(2026, 3, 5), days=7, tolerance=3)
        assert result is None

    def test_picks_closest_snapshot_to_target(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        # Two snapshots: one 5 days ago, one 10 days ago; target is 7 days ago
        # 5d ago: diff=2 (closer); 10d ago: diff=3
        snapshots = [
            _make_snapshot(date(2026, 2, 28), 960.0),  # 5d before Mar 5 — diff=2
            _make_snapshot(date(2026, 2, 23), 940.0),  # 10d before Mar 5 — diff=3
        ]
        result = _compute_movement(snapshots, 1000.0, date(2026, 3, 5), days=7, tolerance=3)
        # Should use 960.0 (closer to target)
        expected = round((1000.0 - 960.0) / 960.0 * 100, 2)
        assert result == expected

    def test_1m_movement_with_large_tolerance(self):
        from utilities.get_index_context_for_freshness import _compute_movement
        # 30d target, tolerance=10 → snapshot 22d old is within tolerance
        snapshots = [_make_snapshot(date(2026, 2, 11), 900.0)]  # 22d before Mar 5
        result = _compute_movement(snapshots, 1000.0, date(2026, 3, 5), days=30, tolerance=10)
        assert result is not None


# ---------------------------------------------------------------------------
# Tests for _fetch_recent_snapshots
# ---------------------------------------------------------------------------

class TestFetchRecentSnapshots:

    def _make_mock_db(self, docs: list) -> MagicMock:
        """Make a mock db that returns given docs from query."""
        mock_doc_objects = []
        for d in docs:
            m = MagicMock()
            m.to_dict.return_value = d
            mock_doc_objects.append(m)

        mock_stream = MagicMock(return_value=iter(mock_doc_objects))
        mock_query = MagicMock()
        mock_query.stream = mock_stream
        mock_query.where.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query

        mock_collection = MagicMock()
        mock_collection.where.return_value = mock_query
        mock_collection.order_by.return_value = mock_query

        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection
        return mock_db

    def test_returns_list_of_dicts(self):
        from utilities.get_index_context_for_freshness import _fetch_recent_snapshots
        docs = [
            {"index_name": "AIM_ALL_SHARE", "scrape_date": "2026-03-05", "index_value": 1000.0},
            {"index_name": "AIM_ALL_SHARE", "scrape_date": "2026-02-26", "index_value": 950.0},
        ]
        db = self._make_mock_db(docs)
        result = _fetch_recent_snapshots(db, "AIM_ALL_SHARE")
        assert len(result) == 2
        assert result[0]["index_value"] == 1000.0
        assert isinstance(result[0]["scrape_date"], date)

    def test_skips_docs_with_missing_index_value(self):
        from utilities.get_index_context_for_freshness import _fetch_recent_snapshots
        docs = [
            {"scrape_date": "2026-03-05", "index_value": None},
            {"scrape_date": "2026-02-26", "index_value": 950.0},
        ]
        db = self._make_mock_db(docs)
        result = _fetch_recent_snapshots(db, "AIM_ALL_SHARE")
        assert len(result) == 1

    def test_skips_docs_with_missing_scrape_date(self):
        from utilities.get_index_context_for_freshness import _fetch_recent_snapshots
        docs = [
            {"scrape_date": None, "index_value": 1000.0},
            {"scrape_date": "2026-02-26", "index_value": 950.0},
        ]
        db = self._make_mock_db(docs)
        result = _fetch_recent_snapshots(db, "AIM_ALL_SHARE")
        assert len(result) == 1

    def test_returns_empty_list_on_exception(self):
        from utilities.get_index_context_for_freshness import _fetch_recent_snapshots
        db = MagicMock()
        db.collection.side_effect = Exception("Firestore error")
        result = _fetch_recent_snapshots(db, "AIM_ALL_SHARE")
        assert result == []

    def test_returns_empty_list_for_no_docs(self):
        from utilities.get_index_context_for_freshness import _fetch_recent_snapshots
        db = self._make_mock_db([])
        result = _fetch_recent_snapshots(db, "AIM_ALL_SHARE")
        assert result == []


# ---------------------------------------------------------------------------
# Tests for get_index_context_for_freshness (mocked)
# ---------------------------------------------------------------------------

class TestGetIndexContextForFreshnessMocked:

    def _base_result(self, **kwargs):
        return _make_base_result(**kwargs)

    def test_returns_all_base_fields_from_get_index_snapshot(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        base = self._base_result()
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base), \
             patch("utilities.get_index_context_for_freshness._fetch_recent_snapshots", return_value=[]):
            result = get_index_context_for_freshness("AIM", db=MagicMock())
        for key in base:
            assert key in result
        assert result["data_found"] is True
        assert result["index_name"] == "AIM_ALL_SHARE"

    def test_adds_movement_fields_to_result(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        base = self._base_result()
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base), \
             patch("utilities.get_index_context_for_freshness._fetch_recent_snapshots", return_value=[]):
            result = get_index_context_for_freshness("AIM", db=MagicMock())
        assert "index_movement_1w" in result
        assert "index_movement_1m" in result
        assert "historical_available" in result

    def test_historical_available_false_when_no_history(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        base = self._base_result()
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base), \
             patch("utilities.get_index_context_for_freshness._fetch_recent_snapshots", return_value=[]):
            result = get_index_context_for_freshness("AIM", db=MagicMock())
        assert result["historical_available"] is False
        assert result["index_movement_1w"] is None
        assert result["index_movement_1m"] is None

    def test_historical_available_true_when_1w_available(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        current_date = date(2026, 3, 5)
        base = self._base_result(index_value=1000.0, scrape_date=current_date)
        snapshots = [_make_snapshot(date(2026, 2, 26), 950.0)]  # 7d ago
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base), \
             patch("utilities.get_index_context_for_freshness._fetch_recent_snapshots", return_value=snapshots):
            result = get_index_context_for_freshness("AIM", db=MagicMock())
        assert result["historical_available"] is True
        assert result["index_movement_1w"] is not None

    def test_historical_available_true_when_1m_available_only(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        current_date = date(2026, 3, 5)
        base = self._base_result(index_value=1000.0, scrape_date=current_date)
        # Only a snapshot 30 days ago — no 1w snapshot within tolerance
        snapshots = [_make_snapshot(date(2026, 2, 3), 900.0)]  # 30d ago
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base), \
             patch("utilities.get_index_context_for_freshness._fetch_recent_snapshots", return_value=snapshots):
            result = get_index_context_for_freshness("AIM", db=MagicMock())
        assert result["historical_available"] is True
        assert result["index_movement_1m"] is not None
        assert result["index_movement_1w"] is None

    def test_data_not_found_returns_false_fields(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        base = self._base_result(data_found=False)
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base):
            result = get_index_context_for_freshness("AIM", db=MagicMock())
        assert result["data_found"] is False
        assert result["index_movement_1w"] is None
        assert result["index_movement_1m"] is None
        assert result["historical_available"] is False

    def test_null_index_value_returns_historical_available_false(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        base = self._base_result()
        base["index_value"] = None
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base):
            result = get_index_context_for_freshness("AIM", db=MagicMock())
        assert result["historical_available"] is False

    def test_null_scrape_date_returns_historical_available_false(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        base = self._base_result()
        base["scrape_date"] = None
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base):
            result = get_index_context_for_freshness("AIM", db=MagicMock())
        assert result["historical_available"] is False

    def test_movement_values_computed_correctly(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        current_date = date(2026, 3, 5)
        base = self._base_result(index_value=1000.0, scrape_date=current_date)
        snapshots = [
            _make_snapshot(date(2026, 2, 26), 950.0),   # 7d ago
            _make_snapshot(date(2026, 2, 3),  900.0),   # 30d ago
        ]
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base), \
             patch("utilities.get_index_context_for_freshness._fetch_recent_snapshots", return_value=snapshots):
            result = get_index_context_for_freshness("AIM", db=MagicMock())
        expected_1w = round((1000.0 - 950.0) / 950.0 * 100, 2)
        expected_1m = round((1000.0 - 900.0) / 900.0 * 100, 2)
        assert result["index_movement_1w"] == expected_1w
        assert result["index_movement_1m"] == expected_1m

    def test_get_index_snapshot_called_with_correct_args(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        base = self._base_result(index_name="FTSE_SMALL_CAP")
        mock_db = MagicMock()
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base) as mock_snap, \
             patch("utilities.get_index_context_for_freshness._fetch_recent_snapshots", return_value=[]):
            get_index_context_for_freshness("MAIN_MARKET", market_cap_gbp=200_000_000, db=mock_db)
        mock_snap.assert_called_once_with("MAIN_MARKET", 200_000_000, mock_db)

    def test_identical_output_to_get_index_snapshot_when_no_history(self):
        """Base fields must be identical to what get_index_snapshot returns."""
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        base = self._base_result()
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base), \
             patch("utilities.get_index_context_for_freshness._fetch_recent_snapshots", return_value=[]):
            result = get_index_context_for_freshness("AIM", db=MagicMock())
        for key, val in base.items():
            assert result[key] == val, f"Field {key} differs"

    def test_aim_index_selected_for_aim_company(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        base = self._base_result(index_name="AIM_ALL_SHARE")
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base) as mock_snap, \
             patch("utilities.get_index_context_for_freshness._fetch_recent_snapshots", return_value=[]):
            result = get_index_context_for_freshness("AIM", db=MagicMock())
        assert result["index_name"] == "AIM_ALL_SHARE"

    def test_fetch_called_with_correct_index_name(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        base = self._base_result(index_name="FTSE_250")
        with patch("utilities.get_index_context_for_freshness.get_index_snapshot", return_value=base), \
             patch("utilities.get_index_context_for_freshness._fetch_recent_snapshots", return_value=[]) as mock_fetch:
            get_index_context_for_freshness("MAIN_MARKET", market_cap_gbp=600_000_000, db=MagicMock())
        mock_fetch.assert_called_once()
        args = mock_fetch.call_args[0]
        assert args[1] == "FTSE_250"


# ---------------------------------------------------------------------------
# Integration tests — live Firestore
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestGetIndexContextForFreshnessIntegration:
    """
    Integration tests against live Firestore.

    Tests confirm:
    1. historical_available=False is the expected state during early PoC
       (scraper hasn't been running 30+ days)
    2. When historical snapshots are seeded, movement fields populate correctly
    3. Base fields match get_index_snapshot for same input
    4. All required output keys present

    Seeds two historical index_snapshots documents for movement testing,
    cleans them up after all tests in this class complete.
    """

    _SEED_INDEX = "AIM_ALL_SHARE"
    _SEED_DATE_7D = None   # set in setup
    _SEED_DATE_30D = None  # set in setup
    _SEED_IDS = []

    @pytest.fixture(scope="class", autouse=True)
    def seed_historical_snapshots(self):
        """Seed 2 historical snapshots for movement testing, clean up after."""
        from google.cloud import firestore
        from utilities.get_index_snapshot import _COLLECTION

        db = firestore.Client()
        today = date.today()

        # Use dates clearly in the past and within tolerance windows
        date_7d = today - timedelta(days=7)
        date_30d = today - timedelta(days=30)

        # Get current index value for reference
        from utilities.get_index_snapshot import get_index_snapshot
        current = get_index_snapshot("AIM")
        current_value = current.get("index_value") or 1000.0

        # Seed values: 1% below and 2% below current
        value_7d = round(current_value * 0.99, 2)
        value_30d = round(current_value * 0.98, 2)

        id_7d = f"{self._SEED_INDEX}_{date_7d.isoformat()}_SEED_TEST"
        id_30d = f"{self._SEED_INDEX}_{date_30d.isoformat()}_SEED_TEST"

        db.collection(_COLLECTION).document(id_7d).set({
            "index_name": self._SEED_INDEX,
            "scrape_date": date_7d.isoformat(),
            "index_value": value_7d,
            "week_52_high": current_value * 1.1,
            "week_52_low": current_value * 0.9,
            "change_1d_pct": 0.0,
        })
        db.collection(_COLLECTION).document(id_30d).set({
            "index_name": self._SEED_INDEX,
            "scrape_date": date_30d.isoformat(),
            "index_value": value_30d,
            "week_52_high": current_value * 1.1,
            "week_52_low": current_value * 0.9,
            "change_1d_pct": 0.0,
        })

        TestGetIndexContextForFreshnessIntegration._SEED_IDS = [id_7d, id_30d]
        TestGetIndexContextForFreshnessIntegration._SEED_DATE_7D = date_7d
        TestGetIndexContextForFreshnessIntegration._SEED_DATE_30D = date_30d

        yield

        # Teardown — delete seeded documents
        for doc_id in [id_7d, id_30d]:
            db.collection(_COLLECTION).document(doc_id).delete()

    def test_returns_data_found_true_for_aim(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        result = get_index_context_for_freshness("AIM")
        assert result["data_found"] is True

    def test_all_required_keys_present(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        result = get_index_context_for_freshness("AIM")
        required_keys = [
            "data_found", "index_name", "index_value",
            "week_52_high", "week_52_low", "change_1d_pct",
            "scrape_date", "snapshot_age_days",
            "index_movement_1w", "index_movement_1m", "historical_available",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_historical_available_true_after_seeding(self):
        """With seeded historical snapshots, at least one movement field should populate."""
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        result = get_index_context_for_freshness("AIM")
        assert result["historical_available"] is True

    def test_index_movement_1w_populated_after_seeding(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        result = get_index_context_for_freshness("AIM")
        assert result["index_movement_1w"] is not None
        assert isinstance(result["index_movement_1w"], float)

    def test_index_movement_1m_populated_after_seeding(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        result = get_index_context_for_freshness("AIM")
        assert result["index_movement_1m"] is not None
        assert isinstance(result["index_movement_1m"], float)

    def test_base_fields_match_get_index_snapshot(self):
        """Result must be identical to get_index_snapshot for the same input."""
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        from utilities.get_index_snapshot import get_index_snapshot
        context_result = get_index_context_for_freshness("AIM")
        snapshot_result = get_index_snapshot("AIM")
        base_keys = [
            "data_found", "index_name", "index_value",
            "week_52_high", "week_52_low", "change_1d_pct",
            "scrape_date", "snapshot_age_days",
        ]
        for key in base_keys:
            assert context_result[key] == snapshot_result[key], f"Field {key} differs"

    def test_no_exception_for_main_market_company(self):
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        result = get_index_context_for_freshness("MAIN_MARKET", market_cap_gbp=200_000_000)
        assert "data_found" in result
        assert "historical_available" in result

    def test_graceful_result_for_unknown_membership(self):
        """Unknown membership defaults to FTSE_SMALL_CAP — no exception raised."""
        from utilities.get_index_context_for_freshness import get_index_context_for_freshness
        result = get_index_context_for_freshness("UNKNOWN")
        assert "data_found" in result
        assert "historical_available" in result
