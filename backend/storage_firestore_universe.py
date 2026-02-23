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
