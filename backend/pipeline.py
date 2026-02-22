# Deploy pipeline test
import time
from telegram_notifier import TelegramNotifier
from datetime import datetime, timezone
from typing import List, Tuple
from abstractions import Announcement
from google_news_connector import GoogleNewsRSSProvider
from companies_house_connector import CompaniesHouseProvider
from lens_regulatory_catalyst import RegulatoryCatalystLens
from llm_gemini import GeminiProvider
from abstractions import StorageProviderBase
from storage_firestore import FirestoreProvider
from universe import get_active_companies, get_tickers

class AnalysisPipeline:
    """
    The core analysis pipeline.
    
    Operates two queues:
    
    Signal Queue: announcements from companies already in the universe.
    These go through full pre-filter and LLM analysis.
    
    Discovery Queue: announcements from companies not in the universe.
    These are flagged for human review as potential universe additions.
    Discovery mode is a direct expression of the anti-bias principle —
    the universe expands only through explicit human decision, never
    through implicit system behaviour.
    """

    def __init__(self, storage: StorageProviderBase = None):
        self.providers = [
            GoogleNewsRSSProvider(),
            CompaniesHouseProvider(),
        ]
        self.lenses = [
            RegulatoryCatalystLens(),
        ]
        self.llm = GeminiProvider()
        self.notifier = TelegramNotifier()
        self.storage = storage or FirestoreProvider()

    def _is_in_universe(self, announcement: Announcement) -> bool:
        """Check if an announcement relates to a company in the universe."""
        tickers = [t.upper() for t in get_tickers()]
        names = [c["name"].lower() for c in get_active_companies()]

        # Match by ticker
        if announcement.ticker.upper() in tickers:
            return True

        # Match by company name fragment
        announcement_name = announcement.company_name.lower()
        for name in names:
            # Check both directions — universe name in announcement or vice versa
            core_name = name.replace(" plc", "").replace(" ltd", "").strip()
            if core_name in announcement_name or announcement_name in core_name:
                return True

        return False

    def _assess_discovery(self, announcement: Announcement) -> dict:
        """
        Lightweight LLM assessment for discovery queue items.
        Cheaper and faster than full analysis — just enough to decide
        whether to present this to the user for universe consideration.
        """
        prompt = f"""You are an investment research analyst screening for LSE small-cap opportunities.

A news item has been found that is NOT yet in the monitored universe. Assess briefly whether it warrants the investor's attention as a potential universe addition.

INVESTMENT THESIS: LSE small-caps where informed parties have confidence in upcoming value increase, particularly companies facing regulatory or planning catalysts.

ANNOUNCEMENT:
Source: {announcement.source_name}
Headline: {announcement.headline}
Body: {announcement.body}

Answer these questions briefly:
1. COMPANY: Can you identify the specific company involved? If yes, name and approximate LSE ticker.
2. SMALL_CAP: Is this likely an LSE-listed small-cap (market cap under £500m)?
3. THESIS_FIT: Does this fit the investment thesis? Score 1-10.
4. RECOMMEND_ADD: Should the investor consider adding this company to their monitored universe? Yes/No/Maybe
5. REASON: One sentence explaining your recommendation.

Respond in exactly this format:
COMPANY: [name and ticker or "Cannot identify"]
SMALL_CAP: [Yes/No/Uncertain]
THESIS_FIT: [score]/10
RECOMMEND_ADD: [Yes/No/Maybe]
REASON: [one sentence]"""

        max_retries = 3
        retry_delay = 10
        for attempt in range(max_retries):
            time.sleep(retry_delay)
            response = self.llm.analyse(prompt)
            if response:
                return {
                    "ticker": announcement.ticker,
                    "company_name": announcement.company_name,
                    "headline": announcement.headline,
                    "published_at": str(announcement.published_at),
                    "source": announcement.source_name,
                    "discovery_assessment": response,
                    "queue": "DISCOVERY",
                    "assessed_at": str(datetime.now(timezone.utc)),
                }
            retry_delay *= 2
            print(f"  Rate limited, retrying in {retry_delay}s...")

        return {}

    def run(self, max_announcements: int = 50) -> Tuple[List[dict], List[dict]]:
        """
        Full pipeline run.
        Returns (signal_results, discovery_results).
        """
        signal_results = []
        discovery_results = []
        print(f"\n{'='*60}")
        print(f"Pipeline run started: {datetime.now(timezone.utc)}")
        print(f"{'='*60}\n")

        # Step 1: Ingest from all providers
        all_announcements: List[Announcement] = []
        for provider in self.providers:
            provider_name = provider.__class__.__name__
            announcements = provider.get_recent_announcements(
                max_results=max_announcements
            )
            print(f"Ingested {len(announcements)} announcements "
                  f"from {provider_name}")
            all_announcements.extend(announcements)

        print(f"\nTotal ingested: {len(all_announcements)}")

        # Step 2: Deduplicate, then route into signal queue vs discovery queue.
        # Cross-run deduplication: check Firestore before routing.
        # Announcements already seen in a previous run are skipped entirely,
        # preventing duplicate LLM analysis calls and duplicate Firestore writes.
        # New announcements are persisted to the deduplication store on first sight.
        signal_queue: List[Announcement] = []
        discovery_queue: List[Announcement] = []
        deduplicated = 0

        for announcement in all_announcements:
            if self.storage.headline_exists(announcement.headline):
                deduplicated += 1
                continue
            self.storage.save_announcement(announcement)
            if self._is_in_universe(announcement):
                signal_queue.append(announcement)
            else:
                discovery_queue.append(announcement)

        print(f"Deduplicated (seen in previous runs): {deduplicated}")
        print(f"Signal queue (in universe): {len(signal_queue)}")
        print(f"Discovery queue (not in universe): {len(discovery_queue)}")

        # Step 3: Process signal queue through pre-filter and LLM
        print(f"\n--- SIGNAL QUEUE ---")
        passed = []
        seen_headlines = set()

        for announcement in signal_queue:
            for lens in self.lenses:
                if lens.pre_filter(announcement):
                    key = f"{announcement.ticker}:{announcement.headline[:40]}"
                    if key not in seen_headlines:
                        seen_headlines.add(key)
                        passed.append((announcement, lens))
                    else:
                        print(f"  Skipping duplicate: "
                              f"[{announcement.ticker}] "
                              f"{announcement.headline[:40]}")
                    break

        print(f"Passed pre-filter: {len(passed)} of {len(signal_queue)}")

        for announcement, lens in passed:
            print(f"\n  Analysing: [{announcement.ticker}] "
                  f"{announcement.headline[:60]}...")
            max_retries = 3
            retry_delay = 10
            response = ""
            for attempt in range(max_retries):
                time.sleep(retry_delay)
                response = self.llm.analyse(lens.build_prompt(announcement))
                if response:
                    break
                retry_delay *= 2
                print(f"  Rate limited, retrying in {retry_delay}s...")

            if response:
                result = {
                    "ticker": announcement.ticker,
                    "company_name": announcement.company_name,
                    "headline": announcement.headline,
                    "published_at": str(announcement.published_at),
                    "source": announcement.source_name,
                    "llm_analysis": response,
                    "queue": "SIGNAL",
                    "analysed_at": str(datetime.now(timezone.utc)),
                }
                signal_results.append(result)
                self.storage.save_signal_result(result)
                print(f"\n  {'─'*50}")
                print(f"  [{announcement.ticker}] {announcement.company_name}")
                print(f"  {announcement.headline}")
                print(f"  {'─'*50}")
                print(response)

        # Step 4: Process discovery queue through lightweight assessment
        print(f"\n--- DISCOVERY QUEUE ---")

        # Pre-filter discovery items too — no point assessing garden decking
        discovery_passed = []
        seen_discovery = set()

        for announcement in discovery_queue:
            for lens in self.lenses:
                if lens.pre_filter(announcement):
                    key = announcement.headline[:60]
                    if key not in seen_discovery:
                        seen_discovery.add(key)
                        discovery_passed.append(announcement)
                    break

        print(f"Discovery items passing pre-filter: "
              f"{len(discovery_passed)} of {len(discovery_queue)}")

        for announcement in discovery_passed:
            print(f"\n  Assessing for discovery: "
                  f"{announcement.headline[:60]}...")
            result = self._assess_discovery(announcement)
            if result:
                discovery_results.append(result)
                self.storage.save_discovery_result(result)
                print(f"\n  {'─'*50}")
                print(f"  DISCOVERY: {announcement.headline[:60]}")
                print(f"  {'─'*50}")
                print(result["discovery_assessment"])

        # Summary
        print(f"\n{'='*60}")
        print(f"Pipeline run complete")
        print(f"  Ingested:          {len(all_announcements)}")
        print(f"  Signal queue:      {len(signal_queue)}")
        print(f"  Signal analysed:   {len(signal_results)}")
        print(f"  Discovery queue:   {len(discovery_queue)}")
        print(f"  Discovery assessed:{len(discovery_results)}")
        print(f"{'='*60}\n")

        # Send notifications — must run after results are populated
        print(f"\n--- SENDING NOTIFICATIONS ---")

        for r in signal_results:
            analysis = r.get("llm_analysis", "")
            for line in analysis.splitlines():
                if "RECOMMENDED_ACTION" in line:
                    value = line.split(":", 1)[-1].strip().lower()
                    if value == "yes":
                        self.notifier.send(self.notifier.format_signal(r), priority="high")
                    elif value == "monitor":
                        self.notifier.send(self.notifier.format_signal(r), priority="normal")
                    break

        for r in discovery_results:
            disc_assessment = r.get("discovery_assessment", "")
            for line in disc_assessment.splitlines():
                if "RECOMMEND_ADD" in line:
                    value = line.split(":", 1)[-1].strip().lower()
                    if value in ("yes", "maybe"):
                        self.notifier.send(
                            self.notifier.format_discovery(r), priority="normal"
                        )
                    break

        return signal_results, discovery_results


if __name__ == "__main__":
    # Instantiate the concrete storage provider at the entry point and inject it.
    # The pipeline depends only on StorageProviderBase — swapping storage
    # backends requires changing only this line.
    storage = FirestoreProvider()
    pipeline = AnalysisPipeline(storage=storage)
    signal_results, discovery_results = pipeline.run(max_announcements=30)

    # Surface any strong signals
    print("\n--- RECOMMENDED ACTIONS ---")
    for r in signal_results:
        analysis = r.get("llm_analysis", "")
        if "RECOMMENDED_ACTION: Yes" in analysis:
            print(f"\n  *** SURFACE TO INVESTOR ***")
            print(f"  [{r['ticker']}] {r['headline']}")

    # Surface discovery recommendations
    print("\n--- UNIVERSE ADDITIONS TO CONSIDER ---")
    for r in discovery_results:
        assessment = r.get("discovery_assessment", "")
        if "RECOMMEND_ADD: Yes" in assessment or "RECOMMEND_ADD: Maybe" in assessment:
            print(f"\n  *** CONSIDER ADDING TO UNIVERSE ***")
            print(f"  {r['headline'][:60]}")
            print(f"  {assessment}")
