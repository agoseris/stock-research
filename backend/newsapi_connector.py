import requests
from datetime import datetime
from typing import List
from dotenv import load_dotenv
from abstractions import AnnouncementProviderBase, Announcement
import os

load_dotenv()

class NewsAPIProvider(AnnouncementProviderBase):
    """Fetches UK financial and regulatory news via NewsAPI.org.
    Implements AnnouncementProviderBase so this provider can be
    swapped for CityFALCON or any other provider without changing
    the rest of the system."""

    BASE_URL = "https://newsapi.org/v2/everything"
    SOURCE_NAME = "NewsAPI"
    SOURCE_RELIABILITY = 0.6  # Medium reliability - aggregated sources vary in quality

    # Query terms targeting regulatory and planning catalyst signals
    QUERIES = [
        "RNS announcement planning permission UK plc",
        "FCA regulatory approval UK plc shares",
        "Environment Agency permit mining UK plc",
        "Planning Inspectorate decision UK energy plc",
        "UK plc regulatory news LSE FTSE small cap",
    ]

    def __init__(self):
        self.api_key = os.getenv("NEWSAPI_KEY")
        if not self.api_key:
            raise ValueError("NEWSAPI_KEY not found in environment. Check your .env file.")

    def get_recent_announcements(self, max_results: int = 50) -> List[Announcement]:
        announcements = []
        seen_urls = set()
        results_per_query = max(1, max_results // len(self.QUERIES))

        for query in self.QUERIES:
            try:
                params = {
                    "q": query,
                    "language": "en",
                    "pageSize": min(results_per_query, 100),
                    "sortBy": "publishedAt",
                    "apiKey": self.api_key
                }
                response = requests.get(self.BASE_URL, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()

                for article in data.get("articles", []):
                    url = article.get("url", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    # Parse publication date
                    published_str = article.get("publishedAt", "")
                    try:
                        published_at = datetime.fromisoformat(
                            published_str.replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        published_at = datetime.now()

                    announcement = Announcement(
                        ticker="UNKNOWN",  # NewsAPI doesn't provide tickers
                        company_name=article.get("source", {}).get("name", "Unknown"),
                        headline=article.get("title", ""),
                        body=article.get("description", "") or article.get("content", ""),
                        published_at=published_at,
                        source_url=url,
                        source_name=self.SOURCE_NAME
                    )
                    announcements.append(announcement)

            except requests.RequestException as e:
                print(f"NewsAPI fetch error for query '{query}': {e}")
            except Exception as e:
                print(f"Unexpected error for query '{query}': {e}")

        return announcements[:max_results]


if __name__ == "__main__":
    provider = NewsAPIProvider()
    results = provider.get_recent_announcements(max_results=15)
    print(f"\nFetched {len(results)} announcements\n")
    for a in results:
        print(f"  Source: {a.company_name}")
        print(f"  Headline: {a.headline}")
        print(f"  Published: {a.published_at}")
        print(f"  URL: {a.source_url}")
        print()
