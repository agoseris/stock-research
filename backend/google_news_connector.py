import feedparser
from datetime import datetime, timezone, timedelta
from typing import List
from abstractions import AnnouncementProviderBase, Announcement
from email.utils import parsedate_to_datetime

class GoogleNewsRSSProvider(AnnouncementProviderBase):
    """Fetches UK financial and regulatory news via Google News RSS.
    Implements AnnouncementProviderBase so this provider can be
    swapped without changing the rest of the system."""

    SOURCE_NAME = "Google News RSS"
    SOURCE_RELIABILITY = 0.5  # Variable - depends on underlying source

    QUERIES = [
        "UK plc planning permission regulatory approval shares",
        "UK mining permit licence approval LSE shares plc",
        "UK energy planning permission approved rejected plc",
        "RNS announcement regulatory UK small cap plc",
        "UK plc permit granted refused environment agency",
    ]

    BASE_URL = "https://news.google.com/rss/search?hl=en-GB&gl=GB&ceid=GB:en&q={}"

    def get_recent_announcements(self, max_results: int = 50) -> List[Announcement]:
        announcements = []
        seen_urls = set()
        results_per_query = max(1, max_results // len(self.QUERIES))

        for query in self.QUERIES:
            try:
                encoded_query = query.replace(" ", "+")
                url = self.BASE_URL.format(encoded_query)
                feed = feedparser.parse(url)

                for entry in feed.entries[:results_per_query]:
                    link = entry.get("link", "")
                    if link in seen_urls:
                        continue
                    seen_urls.add(link)

                    # Parse publication date
                    try:
                        published_at = parsedate_to_datetime(entry.get("published", ""))
                    except Exception:
                        published_at = datetime.now()

                    # Extract source name from entry if available
                    source = entry.get("source", {})
                    source_name = source.get("title", "Unknown") if isinstance(source, dict) else self.SOURCE_NAME
		    # Discard articles older than 30 days
                    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
                    if published_at.tzinfo is None:
                        published_at = published_at.replace(tzinfo=timezone.utc)
                    if published_at < cutoff:
                        continue
                    announcement = Announcement(
                        ticker="UNKNOWN",
                        company_name=source_name,
                        headline=entry.get("title", ""),
                        body=entry.get("summary", ""),
                        published_at=published_at,
                        source_url=link,
                        source_name=self.SOURCE_NAME
                    )
                    announcements.append(announcement)

            except Exception as e:
                print(f"Google News RSS error for query '{query}': {e}")

        return announcements[:max_results]


if __name__ == "__main__":
    provider = GoogleNewsRSSProvider()
    results = provider.get_recent_announcements(max_results=20)
    print(f"\nFetched {len(results)} announcements\n")
    for a in results:
        print(f"  Source: {a.company_name}")
        print(f"  Headline: {a.headline}")
        print(f"  Published: {a.published_at}")
        print()
