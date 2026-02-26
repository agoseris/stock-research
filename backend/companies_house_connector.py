import os
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import requests
from dotenv import load_dotenv

from abstractions import (
    Announcement,
    AnnouncementProviderBase,
    UniverseStorageProviderBase,
)

load_dotenv()


class CompaniesHouseProvider(AnnouncementProviderBase):
    """Fetches UK company filing events from the Companies House API.

    Monitors companies in the Firestore universe that have a Companies House
    number (populated by import_universe_csv.py). A companies_house_confidence
    score is attached to each Announcement, reflecting how reliably the CH
    number was matched to the company during import (1.0 = exact name match,
    lower = fuzzy match accepted above threshold).

    Requires universe_storage to be injected at construction — it is never
    instantiated internally, per the seven-abstraction architecture rule.
    """

    BASE_URL = "https://api.company-information.service.gov.uk"
    SOURCE_NAME = "Companies House"
    # 0.6s between requests — comfortably within the 600 req/5-min rate limit
    REQUEST_SLEEP = 0.6

    RELEVANT_FILING_TYPES = {
        "AA": "Annual Accounts",
        "CS01": "Confirmation Statement",
        "SH01": "Return of Allotment of Shares",
        "MR01": "Charge Created",
        "MR04": "Charge Satisfied",
        "AP01": "Director Appointed",
        "TM01": "Director Resigned",
        "PSC01": "Person of Significant Control",
        "CERTNM": "Company Name Changed",
        "AD01": "Registered Office Changed",
    }

    def __init__(self, universe_storage: Optional[UniverseStorageProviderBase] = None):
        self.api_key = os.getenv("COMPANIES_HOUSE_KEY")
        if not self.api_key:
            raise ValueError("COMPANIES_HOUSE_KEY not found in environment.")
        self.auth = (self.api_key, "")
        self._universe_storage = universe_storage

    def get_filings(self, company_number: str, max_results: int = 5) -> List[dict]:
        try:
            response = requests.get(
                f"{self.BASE_URL}/company/{company_number}/filing-history",
                params={"items_per_page": max_results},
                auth=self.auth,
                timeout=10,
            )
            response.raise_for_status()
            return response.json().get("items", [])
        except requests.RequestException as e:
            print(f"Companies House error for {company_number}: {e}")
            return []

    def get_recent_announcements(self, max_results: int = 500) -> List[Announcement]:
        announcements = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=2)

        # Load CH numbers and confidence scores from Firestore universe
        if self._universe_storage is None:
            print("Warning: no universe_storage provided to CompaniesHouseProvider "
                  "— no filings will be fetched.")
            return []

        universe_companies = self._universe_storage.get_universe()
        # Build: ticker -> (ch_number, confidence, company_name)
        verified = {
            c.ticker_lse: (
                c.companies_house_number,
                c.companies_house_confidence,
                c.company_name,
            )
            for c in universe_companies
            if c.companies_house_number
        }
        print(f"Monitoring {len(verified)} companies with Companies House numbers "
              f"(out of {len(universe_companies)} in universe)")

        for ticker, (company_number, ch_confidence, company_name) in verified.items():
            time.sleep(self.REQUEST_SLEEP)
            filings = self.get_filings(company_number, max_results=5)
            for filing in filings:
                date_str = filing.get("date", "")
                try:
                    published_at = datetime.fromisoformat(date_str).replace(
                        tzinfo=timezone.utc
                    )
                except (ValueError, AttributeError):
                    published_at = datetime.now(timezone.utc)

                if published_at < cutoff:
                    continue

                filing_type = filing.get("type", "")
                description = filing.get("description", "")
                filing_label = self.RELEVANT_FILING_TYPES.get(filing_type, filing_type)

                announcement = Announcement(
                    ticker=ticker,
                    company_name=company_name,
                    headline=f"{filing_label}: {description}",
                    body=(
                        f"Filing type: {filing_type}. "
                        f"Company: {company_name} ({company_number})."
                    ),
                    published_at=published_at,
                    source_url=(
                        f"https://find-and-update.company-information.service.gov.uk"
                        f"/company/{company_number}/filing-history"
                    ),
                    source_name=self.SOURCE_NAME,
                    companies_house_confidence=ch_confidence,
                )
                announcements.append(announcement)

        return announcements


if __name__ == "__main__":
    from storage_firestore_universe import FirestoreUniverseProvider

    universe_storage = FirestoreUniverseProvider()
    provider = CompaniesHouseProvider(universe_storage=universe_storage)
    results = provider.get_recent_announcements(max_results=20)
    print(f"\nFetched {len(results)} filing announcements\n")
    for a in results:
        conf = f"  CH confidence: {a.companies_house_confidence}" if a.companies_house_confidence is not None else ""
        print(f"  [{a.ticker}] {a.company_name}")
        print(f"  Filing: {a.headline}")
        print(f"  Date: {a.published_at}{conf}")
        print()
