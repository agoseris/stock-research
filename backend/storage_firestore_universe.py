"""
storage_firestore_universe.py

Implements UniverseStorageProviderBase using Google Cloud Firestore.

Collections:
  universe_companies   — one document per company, keyed by ticker_lse.
                         Full UniverseCompany schema. Overwritten on each
                         successful quarterly rebuild.
  universe_refresh_log — one document per pipeline run, keyed by run_id.
                         Append-only. Used for health metrics and the
                         DATA_QUALITY upgrade trigger.

All writes are non-blocking best-effort — a Firestore failure never
crashes the pipeline. Errors are logged to stdout.

Credentials are resolved from the GOOGLE_APPLICATION_CREDENTIALS
environment variable, consistent with FirestoreProvider.
"""

import os
from datetime import date, datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv
from google.cloud import firestore

from abstractions import RefreshLog, UniverseCompany, UniverseStorageProviderBase

load_dotenv()

_DEFAULT_SIGNAL_CONFIG = {
    "monitor_decay_days": 30,
    "active_confirmation_window_days": 90,
    "reinforced_staleness_days": 180,
    "mixed_resolution_days": 30,
    "negative_decay_days": 90,
    "deferred_nudge_days": 60,
    "deferred_decay_days": 14,
}


class FirestoreUniverseProvider(UniverseStorageProviderBase):
    """
    Firestore implementation of UniverseStorageProviderBase.

    Instantiated once at the entry point and injected into the universe
    pipeline. Never instantiated inside pipeline logic.
    """

    UNIVERSE_COLLECTION = "universe_companies"
    REFRESH_LOG_COLLECTION = "universe_refresh_log"

    def __init__(self):
        self.db = firestore.Client()
        print("FirestoreUniverseProvider initialised.")

    # --- Internal helpers ---

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _company_to_dict(self, company: UniverseCompany) -> dict:
        """Serialise a UniverseCompany to a Firestore-compatible dict."""
        return {
            "ticker_lse": company.ticker_lse,
            "ticker_yahoo": company.ticker_yahoo,
            "company_name": company.company_name,
            "tier": company.tier,
            "listing_exchange": company.listing_exchange,
            "entity_type": company.entity_type,
            "liquidity_flag": company.liquidity_flag,
            "universe_added_date": company.universe_added_date.isoformat(),
            "last_refreshed": company.last_refreshed.isoformat(),
            # Optional fields — stored as None when not available
            "isin": company.isin,
            "market_cap_gbp": company.market_cap_gbp,
            "revenue_ttm": company.revenue_ttm,
            "revenue_growth_yoy": company.revenue_growth_yoy,
            "sector": company.sector,
            "industry": company.industry,
            "adtv_gbp": company.adtv_gbp,
            "last_price": company.last_price,
            "last_price_date": (
                company.last_price_date.isoformat()
                if company.last_price_date else None
            ),
            "fifty_two_week_high": company.fifty_two_week_high,
            "fifty_two_week_low": company.fifty_two_week_low,
            "companies_house_number": company.companies_house_number,
            "companies_house_confidence": company.companies_house_confidence,
            "not_of_interest": company.not_of_interest,
            # Signal / position state
            "signal_state": company.signal_state,
            "signal_state_since": (
                company.signal_state_since.isoformat()
                if company.signal_state_since else None
            ),
            "last_signal_at": (
                company.last_signal_at.isoformat()
                if company.last_signal_at else None
            ),
            "position_state": company.position_state,
            "position_state_since": (
                company.position_state_since.isoformat()
                if company.position_state_since else None
            ),
        }

    def _dict_to_company(self, data: dict) -> UniverseCompany:
        """Deserialise a Firestore dict back to a UniverseCompany."""
        def _parse_date(v):
            if v is None:
                return None
            if isinstance(v, date):
                return v
            return date.fromisoformat(v)

        def _parse_datetime(v):
            if v is None:
                return datetime.now(timezone.utc)
            if isinstance(v, datetime):
                return v
            return datetime.fromisoformat(v)

        def _parse_optional_datetime(v):
            if v is None:
                return None
            if isinstance(v, datetime):
                return v
            return datetime.fromisoformat(v)

        return UniverseCompany(
            ticker_lse=data["ticker_lse"],
            ticker_yahoo=data["ticker_yahoo"],
            company_name=data["company_name"],
            tier=data["tier"],
            listing_exchange=data["listing_exchange"],
            entity_type=data["entity_type"],
            liquidity_flag=data["liquidity_flag"],
            universe_added_date=_parse_date(data.get("universe_added_date")) or date.today(),
            last_refreshed=_parse_datetime(data.get("last_refreshed")),
            isin=data.get("isin"),
            market_cap_gbp=data.get("market_cap_gbp"),
            revenue_ttm=data.get("revenue_ttm"),
            revenue_growth_yoy=data.get("revenue_growth_yoy"),
            sector=data.get("sector"),
            industry=data.get("industry"),
            adtv_gbp=data.get("adtv_gbp"),
            last_price=data.get("last_price"),
            last_price_date=_parse_date(data.get("last_price_date")),
            fifty_two_week_high=data.get("fifty_two_week_high"),
            fifty_two_week_low=data.get("fifty_two_week_low"),
            companies_house_number=data.get("companies_house_number"),
            companies_house_confidence=data.get("companies_house_confidence"),
            not_of_interest=data.get("not_of_interest", False),
            signal_state=data.get("signal_state"),
            signal_state_since=_parse_optional_datetime(data.get("signal_state_since")),
            last_signal_at=_parse_optional_datetime(data.get("last_signal_at")),
            position_state=data.get("position_state"),
            position_state_since=_parse_optional_datetime(data.get("position_state_since")),
        )

    def _log_to_dict(self, log: RefreshLog) -> dict:
        """Serialise a RefreshLog to a Firestore-compatible dict."""
        return {
            "run_id": log.run_id,
            "run_timestamp": log.run_timestamp.isoformat(),
            "tier1_count": log.tier1_count,
            "tier2_count": log.tier2_count,
            "data_quality_count": log.data_quality_count,
            "lower_bound_gbp": log.lower_bound_gbp,
            "success": log.success,
            "errors": log.errors,
        }

    def _dict_to_log(self, data: dict) -> RefreshLog:
        """Deserialise a Firestore dict back to a RefreshLog."""
        def _parse_datetime(v):
            if isinstance(v, datetime):
                return v
            return datetime.fromisoformat(v)

        return RefreshLog(
            run_id=data["run_id"],
            run_timestamp=_parse_datetime(data["run_timestamp"]),
            tier1_count=data.get("tier1_count", 0),
            tier2_count=data.get("tier2_count", 0),
            data_quality_count=data.get("data_quality_count", 0),
            lower_bound_gbp=data.get("lower_bound_gbp", 0.0),
            success=data.get("success", False),
            errors=data.get("errors", []),
        )

    # --- Universe ---

    def save_universe(self, companies: List[UniverseCompany]) -> None:
        """
        Persist the full universe to Firestore, overwriting the previous snapshot.

        Each company is written as a separate document keyed by ticker_lse.
        After writing, any documents whose ticker is not in the new universe
        are deleted — this ensures the collection is a true snapshot of the
        current universe rather than an accumulation of all past imports.

        Uses individual set()/delete() calls so that partial Firestore failures
        are isolated to individual documents and logged without aborting the
        rest of the write.

        This method must only be called after both tiers are fully built.
        """
        try:
            written = 0
            failed = 0
            for company in companies:
                try:
                    doc_ref = (
                        self.db.collection(self.UNIVERSE_COLLECTION)
                        .document(company.ticker_lse)
                    )
                    doc_ref.set(self._company_to_dict(company))
                    written += 1
                except Exception as e:
                    print(f"  [FirestoreUniverse] save_universe: "
                          f"failed to write {company.ticker_lse}: {e}")
                    failed += 1

            print(f"  [FirestoreUniverse] save_universe: "
                  f"{written} written, {failed} failed.")

            # Delete stale documents — tickers present in Firestore but not
            # in the new universe (e.g. removed by market cap filter or delisted)
            new_tickers = {c.ticker_lse for c in companies}
            existing_docs = self.db.collection(self.UNIVERSE_COLLECTION).stream()
            deleted = 0
            for doc in existing_docs:
                if doc.id not in new_tickers:
                    try:
                        doc.reference.delete()
                        deleted += 1
                    except Exception as e:
                        print(f"  [FirestoreUniverse] save_universe: "
                              f"failed to delete stale {doc.id}: {e}")
            if deleted:
                print(f"  [FirestoreUniverse] save_universe: "
                      f"{deleted} stale documents deleted.")

        except Exception as e:
            print(f"  [FirestoreUniverse] save_universe outer failure: {e}")

    def get_universe(self, tier: Optional[int] = None) -> List[UniverseCompany]:
        """
        Retrieve the current universe from Firestore.

        If tier is provided, filters to that tier only.
        Returns an empty list if the collection is empty or unavailable.
        """
        try:
            col = self.db.collection(self.UNIVERSE_COLLECTION)
            if tier is not None:
                query = col.where("tier", "==", tier)
            else:
                query = col
            docs = query.stream()
            return [self._dict_to_company(doc.to_dict()) for doc in docs]
        except Exception as e:
            print(f"  [FirestoreUniverse] get_universe failed: {e}")
            return []

    def get_company(self, ticker_lse: str) -> Optional[UniverseCompany]:
        """
        Retrieve a single company by LSE ticker.

        Returns None if not found or if Firestore is unavailable.
        """
        try:
            doc = (
                self.db.collection(self.UNIVERSE_COLLECTION)
                .document(ticker_lse)
                .get()
            )
            if doc.exists:
                return self._dict_to_company(doc.to_dict())
            return None
        except Exception as e:
            print(f"  [FirestoreUniverse] get_company({ticker_lse}) failed: {e}")
            return None

    def save_company(self, company: UniverseCompany) -> None:
        """Upsert a single company document without affecting other universe documents."""
        try:
            self.db.collection(self.UNIVERSE_COLLECTION).document(
                company.ticker_lse
            ).set(self._company_to_dict(company))
        except Exception as e:
            print(f"  [FirestoreUniverse] save_company({company.ticker_lse}) failed: {e}")

    # --- Signal / position state ---

    def update_signal_state(
        self,
        ticker_lse: str,
        new_state: str,
        timestamp: datetime,
        update_since: bool = True,
    ) -> None:
        """
        Merge-update signal_state and last_signal_at on the company doc.
        When update_since=True (default), also updates signal_state_since.
        """
        try:
            update = {
                "signal_state": new_state,
                "last_signal_at": timestamp.isoformat(),
            }
            if update_since:
                update["signal_state_since"] = timestamp.isoformat()
            self.db.collection(self.UNIVERSE_COLLECTION).document(ticker_lse).set(
                update, merge=True
            )
        except Exception as e:
            print(f"  [FirestoreUniverse] update_signal_state({ticker_lse}) failed: {e}")

    def update_position_state(
        self, ticker_lse: str, new_state: str, timestamp: datetime
    ) -> None:
        """Merge-update position_state and position_state_since on the company doc."""
        try:
            self.db.collection(self.UNIVERSE_COLLECTION).document(ticker_lse).set(
                {
                    "position_state": new_state,
                    "position_state_since": timestamp.isoformat(),
                },
                merge=True,
            )
        except Exception as e:
            print(f"  [FirestoreUniverse] update_position_state({ticker_lse}) failed: {e}")

    def record_signal_transition(self, ticker_lse: str, transition: dict) -> None:
        """Append a signal_history document under universe_companies/{ticker}."""
        try:
            # Serialise datetime → ISO string so Firestore stores consistently
            doc = {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in transition.items()
            }
            (
                self.db.collection(self.UNIVERSE_COLLECTION)
                .document(ticker_lse)
                .collection("signal_history")
                .add(doc)
            )
        except Exception as e:
            print(f"  [FirestoreUniverse] record_signal_transition({ticker_lse}) failed: {e}")

    def record_position_transition(self, ticker_lse: str, transition: dict) -> None:
        """Append a position_history document under universe_companies/{ticker}."""
        try:
            doc = {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in transition.items()
            }
            (
                self.db.collection(self.UNIVERSE_COLLECTION)
                .document(ticker_lse)
                .collection("position_history")
                .add(doc)
            )
        except Exception as e:
            print(f"  [FirestoreUniverse] record_position_transition({ticker_lse}) failed: {e}")

    def get_signal_history(
        self, ticker_lse: str, limit: int = 50
    ) -> List[dict]:
        """Return signal history for a company, most recent first."""
        try:
            docs = (
                self.db.collection(self.UNIVERSE_COLLECTION)
                .document(ticker_lse)
                .collection("signal_history")
                .order_by("timestamp", direction=firestore.Query.DESCENDING)
                .limit(limit)
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"  [FirestoreUniverse] get_signal_history({ticker_lse}) failed: {e}")
            return []

    def get_position_history(
        self, ticker_lse: str, limit: int = 50
    ) -> List[dict]:
        """Return position history for a company, most recent first."""
        try:
            docs = (
                self.db.collection(self.UNIVERSE_COLLECTION)
                .document(ticker_lse)
                .collection("position_history")
                .order_by("timestamp", direction=firestore.Query.DESCENDING)
                .limit(limit)
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"  [FirestoreUniverse] get_position_history({ticker_lse}) failed: {e}")
            return []

    def get_decayed_companies(self, decay_config: dict) -> List[UniverseCompany]:
        """
        Return companies whose signal_state has been in its current state
        longer than the decay window in decay_config.

        Loads the full universe in Python and filters — no composite index needed.
        """
        # Decay windows: state → config key
        _STATE_DECAY_KEY = {
            "monitor": "monitor_decay_days",
            "signal_active": "active_confirmation_window_days",
            "signal_reinforced": "reinforced_staleness_days",
            "signal_mixed": "mixed_resolution_days",
            "signal_negative": "negative_decay_days",
        }
        try:
            companies = self.get_universe()
            now = datetime.now(timezone.utc)
            decayed = []
            for company in companies:
                state = company.signal_state
                config_key = _STATE_DECAY_KEY.get(state)
                if config_key is None:
                    continue
                days = decay_config.get(config_key, 0)
                if days <= 0 or company.signal_state_since is None:
                    continue
                since = company.signal_state_since
                if since.tzinfo is None:
                    since = since.replace(tzinfo=timezone.utc)
                if (now - since).days >= days:
                    decayed.append(company)
            return decayed
        except Exception as e:
            print(f"  [FirestoreUniverse] get_decayed_companies failed: {e}")
            return []

    def get_signal_config(self) -> dict:
        """
        Return signal state configuration from app_config/signal_config.
        Seeds default values in Firestore on first call.
        """
        try:
            doc = self.db.collection("app_config").document("signal_config").get()
            if doc.exists:
                # Merge stored values over defaults so new keys are picked up
                return {**_DEFAULT_SIGNAL_CONFIG, **doc.to_dict()}
            # First call — seed defaults
            self.db.collection("app_config").document("signal_config").set(
                _DEFAULT_SIGNAL_CONFIG, merge=True
            )
            return dict(_DEFAULT_SIGNAL_CONFIG)
        except Exception as e:
            print(f"  [FirestoreUniverse] get_signal_config failed: {e}")
            return dict(_DEFAULT_SIGNAL_CONFIG)

    # --- Refresh log ---

    def save_refresh_log(self, log: RefreshLog) -> None:
        """
        Append a refresh log entry to universe_refresh_log.

        Document ID is the run_id (ISO timestamp string), so entries
        sort lexicographically by run time.
        """
        try:
            self.db.collection(self.REFRESH_LOG_COLLECTION).document(
                log.run_id
            ).set(self._log_to_dict(log))
        except Exception as e:
            print(f"  [FirestoreUniverse] save_refresh_log failed: {e}")

    def get_last_refresh_log(self) -> Optional[RefreshLog]:
        """
        Return the most recent refresh log entry.

        Returns None if no runs have been logged yet or if Firestore
        is unavailable.
        """
        try:
            docs = (
                self.db.collection(self.REFRESH_LOG_COLLECTION)
                .order_by("run_timestamp", direction=firestore.Query.DESCENDING)
                .limit(1)
                .stream()
            )
            for doc in docs:
                return self._dict_to_log(doc.to_dict())
            return None
        except Exception as e:
            print(f"  [FirestoreUniverse] get_last_refresh_log failed: {e}")
            return None


if __name__ == "__main__":
    """
    Standalone connectivity test.

    Writes a synthetic UniverseCompany and RefreshLog to Firestore,
    reads them back, then cleans up. Run on the VM to confirm the
    collections and credentials are correctly configured before wiring
    into the pipeline.

    Expected output:
      FirestoreUniverseProvider initialised.
      Writing test company...
      Reading test company back...
      OK: TEST_CO read back correctly.
      Writing test refresh log...
      Reading last refresh log...
      OK: RefreshLog read back correctly.
      Cleaning up test documents...
      Connectivity test PASSED.
    """
    from abstractions import UniverseCompany, RefreshLog
    from datetime import date, datetime, timezone

    print("Running FirestoreUniverseProvider connectivity test...\n")
    provider = FirestoreUniverseProvider()

    # --- Test UniverseCompany round-trip ---
    test_company = UniverseCompany(
        ticker_lse="TEST_CO",
        ticker_yahoo="TEST_CO.L",
        company_name="Test Company PLC",
        tier=1,
        listing_exchange="LSE_MAIN",
        entity_type="OPERATING",
        liquidity_flag="LIQUID",
        universe_added_date=date.today(),
        last_refreshed=datetime.now(timezone.utc),
        market_cap_gbp=500_000_000.0,
        revenue_ttm=100_000_000.0,
    )

    print("Writing test company...")
    provider.save_universe([test_company])

    print("Reading test company back...")
    retrieved = provider.get_company("TEST_CO")
    if retrieved and retrieved.company_name == "Test Company PLC":
        print("OK: TEST_CO read back correctly.")
    else:
        print("FAIL: could not read back test company.")
        exit(1)

    # --- Test RefreshLog round-trip ---
    test_log = RefreshLog(
        run_id="TEST_RUN_001",
        run_timestamp=datetime.now(timezone.utc),
        tier1_count=250,
        tier2_count=120,
        data_quality_count=3,
        lower_bound_gbp=45_000_000.0,
        success=True,
        errors=[],
    )

    print("Writing test refresh log...")
    provider.save_refresh_log(test_log)

    print("Reading last refresh log...")
    last_log = provider.get_last_refresh_log()
    if last_log and last_log.run_id == "TEST_RUN_001":
        print("OK: RefreshLog read back correctly.")
    else:
        print("FAIL: could not read back test refresh log.")
        exit(1)

    # --- Clean up ---
    print("Cleaning up test documents...")
    provider.db.collection(provider.UNIVERSE_COLLECTION).document("TEST_CO").delete()
    provider.db.collection(provider.REFRESH_LOG_COLLECTION).document("TEST_RUN_001").delete()

    print("\nConnectivity test PASSED.")
