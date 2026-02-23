import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional

import feedparser
import requests

from abstractions import Announcement, AnnouncementProviderBase, UniverseStorageProviderBase

# Timeout in seconds for each HTTP request to Google News RSS.
# feedparser.parse() has no built-in timeout — we fetch via requests first
# so a hung connection is detected and aborted rather than blocking forever.
_FETCH_TIMEOUT = 15


class GoogleNewsRSSProvider(AnnouncementProviderBase):
    """Fetches UK financial and regulatory news via Google News RSS.

    Two-phase ingestion:

    Phase 1 — Topic queries (discovery)
        Five broad topic searches for regulatory/planning catalyst news.
        Results have ticker="UNKNOWN" and are routed to the discovery queue
        unless the company name or ticker matches a universe member.

    Phase 2 — Per-company queries (signal coverage)
        One query per universe company, using the company name and an
        exchange-appropriate qualifier:
          LSE_MAIN:  "<Company Name>" LSE
          AIM:       "<Company Name>" "AIM listed"
        Results have ticker set directly, ensuring reliable signal routing.
        Rate-limited at REQUEST_SLEEP seconds between requests.
        Progress is printed every PROGRESS_INTERVAL companies.

    Requires universe_storage to be injected for Phase 2. If not provided,
    only Phase 1 topic queries run.
    """

    SOURCE_NAME = "Google News RSS"

    # Phase 1 — topic queries for discovery
    TOPIC_QUERIES = [
        "UK plc planning permission regulatory approval shares",
        "UK mining permit licence approval LSE shares plc",
        "UK energy planning permission approved rejected plc",
        "RNS announcement regulatory UK small cap plc",
        "UK plc permit granted refused environment agency",
    ]
    RESULTS_PER_TOPIC = 10  # per query, before URL deduplication

    # Phase 2 — per-company queries
    RESULTS_PER_COMPANY = 3
    # 1.0s between requests — conservative safe rate for undocumented RSS endpoint
    REQUEST_SLEEP = 1.0
    # Print a progress line every N companies
    PROGRESS_INTERVAL = 10

    BASE_URL = "https://news.google.com/rss/search?hl=en-GB&gl=GB&ceid=GB:en&q={}"

    def __init__(self, universe_storage: Optional[UniverseStorageProviderBase] = None):
        self._universe_storage = universe_storage

    def _fetch_feed(self, query: str, max_items: int) -> List[dict]:
        """
        Fetch and parse a single Google News RSS query.

        Fetches via requests (with a timeout) then passes the content to
        feedparser for parsing. This prevents feedparser from hanging
        indefinitely on a slow or blocked connection.

        Returns a list of raw entry dicts, capped at max_items.
        Returns [] on any network or parse error.
        """
        try:
            encoded = query.replace(" ", "+")
            url = self.BASE_URL.format(encoded)
            resp = requests.get(url, timeout=_FETCH_TIMEOUT)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
            return feed.entries[:max_items]
        except requests.exceptions.Timeout:
            print(f"  [GoogleNews] Timeout fetching query: {query[:60]}")
            return []
        except Exception as e:
            print(f"  [GoogleNews] Error fetching query '{query[:60]}': {e}")
            return []

    def _entry_to_announcement(
        self,
        entry: dict,
        ticker: str = "UNKNOWN",
        company_name: Optional[str] = None,
    ) -> Optional[Announcement]:
        """
        Convert a feedparser entry to an Announcement.

        Returns None if the entry is older than 90 days or has no URL.
        """
        link = entry.get("link", "")
        if not link:
            return None

        try:
            published_at = parsedate_to_datetime(entry.get("published", ""))
        except Exception:
            published_at = datetime.now(timezone.utc)

        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        if published_at < cutoff:
            return None

        source = entry.get("source", {})
        source_title = (
            source.get("title", self.SOURCE_NAME)
            if isinstance(source, dict)
            else self.SOURCE_NAME
        )

        return Announcement(
            ticker=ticker,
            company_name=company_name or source_title,
            headline=entry.get("title", ""),
            body=entry.get("summary", ""),
            published_at=published_at,
            source_url=link,
            source_name=self.SOURCE_NAME,
        )

    def get_recent_announcements(self, max_results: int = 50) -> List[Announcement]:
        announcements = []
        seen_urls: set = set()

        # ------------------------------------------------------------------
        # Phase 1: topic queries — broad discovery, no universe dependency
        # ------------------------------------------------------------------
        print(f"  [GoogleNews] Phase 1: running {len(self.TOPIC_QUERIES)} topic queries...")
        for query in self.TOPIC_QUERIES:
            entries = self._fetch_feed(query, self.RESULTS_PER_TOPIC)
            before = len(announcements)
            for entry in entries:
                link = entry.get("link", "")
                if link in seen_urls:
                    continue
                seen_urls.add(link)
                ann = self._entry_to_announcement(entry)
                if ann:
                    announcements.append(ann)
            print(f"  [GoogleNews] Phase 1:   '{query[:50]}' → {len(announcements) - before} items")

        print(f"  [GoogleNews] Phase 1 complete: {len(announcements)} items total")
        if len(announcements) == 0:
            print("  [GoogleNews] WARNING: Phase 1 returned 0 items — "
                  "Google News may be unreachable from this host.")

        # ------------------------------------------------------------------
        # Phase 2: per-company queries — targeted signal coverage
        # ------------------------------------------------------------------
        if self._universe_storage is None:
            print("  [GoogleNews] Phase 2 skipped (no universe_storage provided)")
            return announcements[:max_results]

        companies = self._universe_storage.get_universe()
        total = len(companies)
        phase2_count = 0
        print(f"  [GoogleNews] Phase 2: querying {total} universe companies "
              f"({self.REQUEST_SLEEP}s/request, ~{round(total * self.REQUEST_SLEEP / 60)} min)...")

        for i, company in enumerate(companies, 1):
            if company.listing_exchange == "AIM":
                query = f'"{company.company_name}" "AIM listed"'
            else:
                query = f'"{company.company_name}" LSE'

            time.sleep(self.REQUEST_SLEEP)
            for entry in self._fetch_feed(query, self.RESULTS_PER_COMPANY):
                link = entry.get("link", "")
                if link in seen_urls:
                    continue
                seen_urls.add(link)
                ann = self._entry_to_announcement(
                    entry,
                    ticker=company.ticker_lse,
                    company_name=company.company_name,
                )
                if ann:
                    announcements.append(ann)
                    phase2_count += 1

            if i % self.PROGRESS_INTERVAL == 0 or i == total:
                pct = round(100 * i / total)
                print(f"  [GoogleNews] Phase 2: {i}/{total} ({pct}%) — "
                      f"{phase2_count} items | last: [{company.ticker_lse}] {company.company_name[:30]}")

        print(f"  [GoogleNews] Phase 2 complete: {phase2_count} items "
              f"across {total} companies")

        return announcements


if __name__ == "__main__":
    from storage_firestore_universe import FirestoreUniverseProvider

    universe_storage = FirestoreUniverseProvider()
    provider = GoogleNewsRSSProvider(universe_storage=universe_storage)
    results = provider.get_recent_announcements()
    print(f"\nFetched {len(results)} announcements total\n")
    known = [a for a in results if a.ticker != "UNKNOWN"]
    unknown = [a for a in results if a.ticker == "UNKNOWN"]
    print(f"  With ticker (Phase 2): {len(known)}")
    print(f"  Without ticker (Phase 1): {len(unknown)}")
    print()
    for a in known[:10]:
        print(f"  [{a.ticker}] {a.company_name}")
        print(f"  {a.headline[:80]}")
        print(f"  {a.published_at.date()}")
        print()
