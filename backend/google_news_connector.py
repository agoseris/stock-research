import os
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import requests
from dotenv import load_dotenv

from abstractions import Announcement, AnnouncementProviderBase

load_dotenv()

# Maximum results per API request — hard limit imposed by the Custom Search API.
_MAX_PER_REQUEST = 10


class GoogleNewsProvider(AnnouncementProviderBase):
    """Fetches UK financial news via the Google Custom Search JSON API (news mode).

    Uses tbm=nws (news search) with UK geo-targeting, running the same five
    topic queries used previously for discovery. Being a first-party Google API,
    it is not subject to the IP-range blocking that affects Google News RSS
    scraping from GCP.

    Free tier: 100 queries/day. Five topic queries per pipeline run leaves
    95 queries/day in reserve with no additional cost.

    Requires two environment variables:
      GOOGLE_CSE_KEY  — API key created in GCP Console → APIs & Services → Credentials
      GOOGLE_CSE_ID   — Programmable Search Engine ID from programmablesearchengine.google.com

    If either variable is missing, get_recent_announcements() returns [] with a
    warning rather than raising — the pipeline continues with Companies House data only.
    """

    SOURCE_NAME = "Google News"

    TOPIC_QUERIES = [
        "UK plc planning permission regulatory approval shares",
        "UK mining permit licence approval LSE shares plc",
        "UK energy planning permission approved rejected plc",
        "RNS announcement regulatory UK small cap plc",
        "UK plc permit granted refused environment agency",
    ]

    # Small sleep between queries — well within the 100/day free tier
    REQUEST_SLEEP = 0.5

    CSE_URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self):
        self.api_key = os.getenv("GOOGLE_CSE_KEY", "")
        self.cse_id = os.getenv("GOOGLE_CSE_ID", "")

    def _fetch_news(self, query: str) -> List[dict]:
        """
        Call the Custom Search API for one query in news mode.

        Returns a list of result item dicts. Returns [] on any error.
        """
        try:
            resp = requests.get(
                self.CSE_URL,
                params={
                    "key": self.api_key,
                    "cx": self.cse_id,
                    "q": query,
                    "num": _MAX_PER_REQUEST,
                    "gl": "GB",
                    "hl": "en-GB",
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("items", [])
        except requests.exceptions.Timeout:
            print(f"  [GoogleCSE] Timeout on query: {query[:60]}")
            return []
        except Exception as e:
            print(f"  [GoogleCSE] Error on query '{query[:60]}': {e}")
            return []

    def _item_to_announcement(self, item: dict) -> Optional[Announcement]:
        """
        Convert a Custom Search result item to an Announcement.

        Publication date is extracted from pagemap metadata where available;
        falls back to now() if absent. Returns None if the article is older
        than 90 days or has no URL.
        """
        link = item.get("link", "")
        if not link:
            return None

        # Attempt to extract publication date from pagemap schema data
        published_at: Optional[datetime] = None
        pagemap = item.get("pagemap", {})

        # Try NewsArticle schema first
        for article in pagemap.get("newsarticle", []):
            date_str = article.get("datepublished", "")
            if date_str:
                try:
                    published_at = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    )
                    break
                except ValueError:
                    pass

        # Fall back to Open Graph / meta tags
        if published_at is None:
            for tag in pagemap.get("metatags", []):
                for key in ("article:published_time", "og:article:published_time",
                            "datePublished", "date"):
                    date_str = tag.get(key, "")
                    if date_str:
                        try:
                            published_at = datetime.fromisoformat(
                                date_str.replace("Z", "+00:00")
                            )
                            break
                        except ValueError:
                            pass
                if published_at:
                    break

        if published_at is None:
            published_at = datetime.now(timezone.utc)

        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        if published_at < cutoff:
            return None

        return Announcement(
            ticker="UNKNOWN",
            company_name=item.get("displayLink", self.SOURCE_NAME),
            headline=item.get("title", ""),
            body=item.get("snippet", ""),
            published_at=published_at,
            source_url=link,
            source_name=self.SOURCE_NAME,
        )

    def get_recent_announcements(self, max_results: int = 50) -> List[Announcement]:
        if not self.api_key or not self.cse_id:
            print("  [GoogleCSE] Skipped: GOOGLE_CSE_KEY or GOOGLE_CSE_ID not set in .env")
            return []

        announcements = []
        seen_urls: set = set()

        print(f"  [GoogleCSE] Running {len(self.TOPIC_QUERIES)} topic queries...")

        for query in self.TOPIC_QUERIES:
            time.sleep(self.REQUEST_SLEEP)
            items = self._fetch_news(query)
            before = len(announcements)
            for item in items:
                link = item.get("link", "")
                if link in seen_urls:
                    continue
                seen_urls.add(link)
                ann = self._item_to_announcement(item)
                if ann:
                    announcements.append(ann)
            print(f"  [GoogleCSE]   '{query[:50]}' → {len(announcements) - before} items")

        print(f"  [GoogleCSE] Complete: {len(announcements)} items from "
              f"{len(self.TOPIC_QUERIES)} queries")
        return announcements


if __name__ == "__main__":
    provider = GoogleNewsProvider()
    results = provider.get_recent_announcements()
    print(f"\nFetched {len(results)} announcements\n")
    for a in results[:10]:
        print(f"  {a.company_name}")
        print(f"  {a.headline[:80]}")
        print(f"  {a.published_at.date()}")
        print()
