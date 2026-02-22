import requests
from datetime import datetime, timezone, timedelta
from typing import List
from abstractions import AnnouncementProviderBase, Announcement
from dotenv import load_dotenv
from universe import get_companies_house_numbers, get_by_ticker
import os

load_dotenv()

class CompaniesHouseProvider(AnnouncementProviderBase):
    """Fetches UK company filing events from the Companies House API.
    Monitors only companies in the verified universe with confirmed
    Companies House numbers. High reliability source — official UK register."""

    BASE_URL = "https://api.company-information.service.gov.uk"
    SOURCE_NAME = "Companies House"
    SOURCE_RELIABILITY = 0.9

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

    def __init__(self):
        self.api_key = os.getenv("COMPANIES_HOUSE_KEY")
        if not self.api_key:
            raise ValueError("COMPANIES_HOUSE_KEY not found in environment.")
        self.auth = (self.api_key, "")

    def get_filings(self, company_number: str, max_results: int = 5) -> List[dict]:
        try:
            response = requests.get(
                f"{self.BASE_URL}/company/{company_number}/filing-history",
                params={"items_per_page": max_results},
                auth=self.auth,
                timeout=10
            )
            response.raise_for_status()
            return response.json().get("items", [])
        except requests.RequestException as e:
            print(f"Companies House error for {company_number}: {e}")
            return []

    def get_recent_announcements(self, max_results: int = 50) -> List[Announcement]:
        announcements = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)

        # Only monitor companies with verified Companies House numbers
        verified = get_companies_house_numbers()
        print(f"Monitoring {len(verified)} verified companies in universe")

        for ticker, company_number in verified.items():
            company = get_by_ticker(ticker)
            company_name = company["name"] if company else ticker

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
                filing_label = self.RELEVANT_FILING_TYPES.get(
                    filing_type, filing_type
                )

                announcement = Announcement(
                    ticker=ticker,
                    company_name=company_name,
                    headline=f"{filing_label}: {description}",
                    body=(
                        f"Filing type: {filing_type}. "
                        f"Company: {company_name} ({company_number}). "
                        f"Thesis context: {company.get('thesis_notes', '') if company else ''}"
                    ),
                    published_at=published_at,
                    source_url=(
                        f"https://find-and-update.company-information.service.gov.uk"
                        f"/company/{company_number}/filing-history"
                    ),
                    source_name=self.SOURCE_NAME
                )
                announcements.append(announcement)

                if len(announcements) >= max_results:
                    return announcements

        return announcements


if __name__ == "__main__":
    provider = CompaniesHouseProvider()
    results = provider.get_recent_announcements(max_results=20)
    print(f"\nFetched {len(results)} filing announcements\n")
    for a in results:
        print(f"  [{a.ticker}] {a.company_name}")
        print(f"  Filing: {a.headline}")
        print(f"  Date: {a.published_at}")
        print()
