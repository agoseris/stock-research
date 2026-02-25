"""
job_runner.py

Headless Firestore job queue worker for the interactive ingestion workflow.

Polls the Firestore `pending_jobs` collection every 10 seconds.
When a job with status "pending" is found:
  1. Sets status to "processing" (prevents double-processing)
  2. Constructs an Announcement object from the job document
  3. Routes to signal queue (in universe) or discovery queue
  4. Runs lens pre_filter; if passes, calls LLM for analysis
  5. Writes result to signal_results or discovery_results
  6. Sets job status to "complete" or "failed"
  7. Sends Telegram notification if appropriate

Jobs are submitted by the Streamlit Ingest tab via frontend/app.py.
This process runs continuously on the VM alongside the existing cron pipeline.

Run:
    cd backend && python job_runner.py

For always-on operation, use the systemd service:
    backend/systemd/job_runner.service
"""

import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from abstractions import Announcement, UniverseCompany
from google.cloud import firestore
from import_universe_csv import _lookup_ch_number
from google.cloud.firestore_v1.base_query import FieldFilter
from lens_regulatory_catalyst import RegulatoryCatalystLens
from llm_gemini import GeminiProvider
from storage_firestore import FirestoreProvider
from storage_firestore_universe import FirestoreUniverseProvider
from telegram_notifier import TelegramNotifier


POLL_INTERVAL = 10  # seconds between Firestore polls
MAX_JOBS_PER_POLL = 5  # jobs to process per poll cycle


class JobRunner:
    """
    Headless worker that processes pending jobs from the Firestore `pending_jobs`
    collection.

    All concrete providers are instantiated here (the entry point) and injected
    per the seven-abstraction architecture rule. No provider is instantiated
    inside pipeline or storage logic.
    """

    def __init__(self):
        print(f"[{_ts()}] Initialising job runner...")
        self.db = firestore.Client()
        self.storage = FirestoreProvider()
        self.universe_storage = FirestoreUniverseProvider()
        self.lenses = [RegulatoryCatalystLens()]
        self.llm = GeminiProvider()
        self.notifier = TelegramNotifier()

        self._universe_tickers = self._load_universe_tickers()
        print(f"[{_ts()}] Job runner ready. Polling pending_jobs every {POLL_INTERVAL}s.")

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _load_universe_tickers(self) -> set:
        """Load the set of universe tickers at startup for in-universe routing."""
        try:
            companies = self.universe_storage.get_universe()
            tickers = {c.ticker_lse.upper() for c in companies}
            print(f"[{_ts()}] Universe loaded: {len(tickers)} tickers.")
            return tickers
        except Exception as e:
            print(f"[{_ts()}] Warning: could not load universe ({e}). "
                  "All jobs will be routed to discovery queue.")
            return set()

    # ------------------------------------------------------------------
    # Job claim and construction
    # ------------------------------------------------------------------

    def _claim_job(self, job_id: str) -> bool:
        """
        Attempt to claim a job by transitioning it from "pending" to "processing".
        Returns True if successfully claimed.

        This prevents a second runner process from picking up the same job
        in the unlikely event of concurrent execution.
        """
        try:
            self.db.collection("pending_jobs").document(job_id).update({
                "status": "processing",
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            })
            return True
        except Exception as e:
            print(f"  Could not claim job {job_id}: {e}")
            return False

    def _build_announcement(self, job: dict) -> Announcement:
        """Construct an Announcement from a pending_jobs document."""
        raw_published = job.get("published_at")
        if raw_published is None:
            published_at = datetime.now(timezone.utc)
        elif isinstance(raw_published, datetime):
            published_at = raw_published if raw_published.tzinfo else raw_published.replace(tzinfo=timezone.utc)
        else:
            # Firestore DatetimeWithNanoseconds — treat as datetime
            try:
                published_at = raw_published.replace(tzinfo=timezone.utc) if raw_published.tzinfo is None else raw_published
            except Exception:
                published_at = datetime.now(timezone.utc)

        return Announcement(
            ticker=job.get("ticker", ""),
            company_name=job.get("company_name", ""),
            headline=job.get("headline", ""),
            body=job.get("body", ""),
            published_at=published_at,
            source_url=job.get("source_url", ""),
            source_name="LSEG RNS",
        )

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _run_signal_analysis(self, announcement: Announcement) -> Optional[dict]:
        """
        Run pre_filter then full LLM analysis via the regulatory catalyst lens.
        Returns a signal result dict, or None if pre_filter rejects or LLM fails.
        """
        lens = next((l for l in self.lenses if l.pre_filter(announcement)), None)
        if lens is None:
            print(f"  [{announcement.ticker}] Did not pass pre_filter — no LLM call.")
            return None

        print(f"  [{announcement.ticker}] Passed pre_filter. Running LLM analysis...")
        retry_delay = 10
        for attempt in range(3):
            time.sleep(retry_delay)
            response = self.llm.analyse(lens.build_prompt(announcement))
            if response:
                return {
                    "ticker": announcement.ticker,
                    "company_name": announcement.company_name,
                    "headline": announcement.headline,
                    "published_at": str(announcement.published_at),
                    "source": announcement.source_name,
                    "source_url": announcement.source_url,
                    "llm_analysis": response,
                    "queue": "SIGNAL",
                    "analysed_at": str(datetime.now(timezone.utc)),
                    "dismissed": False,
                }
            retry_delay *= 2
            print(f"  Rate limited, retrying in {retry_delay}s...")

        print(f"  [{announcement.ticker}] LLM analysis failed after 3 attempts.")
        return None

    def _run_discovery_assessment(self, announcement: Announcement) -> Optional[dict]:
        """
        Lightweight LLM assessment for non-universe announcements.
        Routes to discovery_results so the human can consider universe admission.
        Same prompt as AnalysisPipeline._assess_discovery in pipeline.py.
        """
        lens = next((l for l in self.lenses if l.pre_filter(announcement)), None)
        if lens is None:
            print(f"  [{announcement.ticker}] Discovery item did not pass pre_filter.")
            return None

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

        retry_delay = 10
        for attempt in range(3):
            time.sleep(retry_delay)
            response = self.llm.analyse(prompt)
            if response:
                return {
                    "ticker": announcement.ticker,
                    "company_name": announcement.company_name,
                    "headline": announcement.headline,
                    "published_at": str(announcement.published_at),
                    "source": announcement.source_name,
                    "source_url": announcement.source_url,
                    "discovery_assessment": response,
                    "queue": "DISCOVERY",
                    "assessed_at": str(datetime.now(timezone.utc)),
                    "dismissed": False,
                }
            retry_delay *= 2
            print(f"  Rate limited, retrying in {retry_delay}s...")

        print(f"  [{announcement.ticker}] Discovery assessment failed after 3 attempts.")
        return None

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _notify_signal(self, result: dict):
        """Send Telegram notification for high-confidence signal results."""
        analysis = result.get("llm_analysis", "")
        for line in analysis.splitlines():
            if "RECOMMENDED_ACTION" in line:
                value = line.split(":", 1)[-1].strip().lower()
                if value == "yes":
                    self.notifier.send(self.notifier.format_signal(result), priority="high")
                elif value == "monitor":
                    self.notifier.send(self.notifier.format_signal(result), priority="normal")
                break

    def _notify_discovery(self, result: dict):
        """Send Telegram notification for discovery results worth considering."""
        assessment = result.get("discovery_assessment", "")
        for line in assessment.splitlines():
            if "RECOMMEND_ADD" in line:
                value = line.split(":", 1)[-1].strip().lower()
                if value in ("yes", "maybe"):
                    self.notifier.send(self.notifier.format_discovery(result), priority="normal")
                break

    # ------------------------------------------------------------------
    # Universe admission
    # ------------------------------------------------------------------

    def _process_universe_admit_job(self, job_id: str, job: dict):
        """
        Handle a universe_admit job: run CH lookup and upsert the company.

        job fields: ticker, company_name, market_cap_gbp (optional),
                    listing_exchange, not_of_interest (bool), source_discovery_id (optional).
        """
        ticker = job.get("ticker", "").strip().upper()
        company_name = job.get("company_name", "")
        market_cap_gbp = job.get("market_cap_gbp") or None
        listing_exchange = job.get("listing_exchange", "AIM")
        not_of_interest = job.get("not_of_interest", False)
        source_discovery_id = job.get("source_discovery_id")

        print(f"\n[{_ts()}] --- universe_admit {job_id[:8]} ---")
        print(f"  [{ticker}] {company_name}  not_of_interest={not_of_interest}")

        try:
            api_key = os.environ.get("COMPANIES_HOUSE_KEY", "")
            ch_number, ch_confidence = None, 0.0
            if api_key:
                ch_number, ch_confidence = _lookup_ch_number(company_name, api_key)

            now = datetime.now(timezone.utc)
            company = UniverseCompany(
                ticker_lse=ticker,
                ticker_yahoo=f"{ticker}.L",
                company_name=company_name,
                tier=1 if listing_exchange == "LSE_MAIN" else 2,
                listing_exchange=listing_exchange,
                entity_type="OPERATING",
                liquidity_flag="LIQUID" if listing_exchange == "LSE_MAIN" else "ILLIQUID",
                universe_added_date=now.date(),
                last_refreshed=now,
                market_cap_gbp=market_cap_gbp,
                companies_house_number=ch_number,
                companies_house_confidence=ch_confidence if ch_number else None,
                not_of_interest=not_of_interest,
            )
            self.universe_storage.save_company(company)
            if not not_of_interest:
                self._universe_tickers.add(ticker)

            if source_discovery_id:
                self.db.collection("discovery_results").document(source_discovery_id).update(
                    {"dismissed": True, "dismissed_at": now.isoformat()}
                )

            self._complete_job(job_id, note=f"admitted: CH={'matched' if ch_number else 'none'}")
            print(f"  [{ticker}] Admitted. CH: {ch_number or 'none'} ({ch_confidence:.2f})")

        except Exception as e:
            print(f"  [{ticker}] universe_admit failed: {e}")
            self._fail_job(job_id, str(e))

    # ------------------------------------------------------------------
    # Job processing
    # ------------------------------------------------------------------

    def _complete_job(self, job_id: str, note: Optional[str] = None):
        update = {"status": "complete", "processed_at": datetime.now(timezone.utc).isoformat()}
        if note:
            update["note"] = note
        self.db.collection("pending_jobs").document(job_id).update(update)

    def _fail_job(self, job_id: str, error: str):
        try:
            self.db.collection("pending_jobs").document(job_id).update({
                "status": "failed",
                "error": error,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            print(f"  Could not mark job {job_id} as failed: {e}")

    def _process_job(self, job_id: str, job: dict):
        """Process a single pending job end-to-end."""
        job_type = job.get("job_type", "lseg_ingest")
        if job_type == "universe_admit":
            self._process_universe_admit_job(job_id, job)
            return

        ticker = job.get("ticker", "UNKNOWN")
        headline = job.get("headline", "")

        print(f"\n[{_ts()}] --- Job {job_id[:8]} ---")
        print(f"  [{ticker}] {headline[:80]}")

        try:
            announcement = self._build_announcement(job)

            # Deduplication — skip if this announcement has already been processed
            if self.storage.announcement_exists(announcement.source_url, announcement.headline):
                print(f"  [{ticker}] Already processed (deduplication) — skipping.")
                self._complete_job(job_id, note="deduplicated")
                return

            # Fingerprint immediately so re-submissions are caught even if pre_filter
            # or LLM rejects this announcement
            self.storage.save_announcement(announcement)

            if ticker.upper() in self._universe_tickers:
                result = self._run_signal_analysis(announcement)
                if result:
                    self.storage.save_signal_result(result)
                    self._notify_signal(result)
                    self._complete_job(job_id)
                    print(f"  [{ticker}] Signal result saved.")
                else:
                    self._complete_job(job_id, note="no_result")
                    print(f"  [{ticker}] No signal result (pre_filter or LLM).")
            else:
                result = self._run_discovery_assessment(announcement)
                if result:
                    self.storage.save_discovery_result(result)
                    self._notify_discovery(result)
                    self._complete_job(job_id)
                    print(f"  [{ticker}] Discovery result saved.")
                else:
                    self._complete_job(job_id, note="no_result")
                    print(f"  [{ticker}] No discovery result (pre_filter or LLM).")

        except Exception as e:
            print(f"  [{ticker}] Job failed: {e}")
            self._fail_job(job_id, str(e))

    # ------------------------------------------------------------------
    # Main polling loop
    # ------------------------------------------------------------------

    def run(self):
        """Poll Firestore for pending jobs. Runs until interrupted."""
        print(f"\n[{_ts()}] Job runner started. Press Ctrl+C to stop.\n")

        while True:
            try:
                docs = list(
                    self.db.collection("pending_jobs")
                    .where(filter=FieldFilter("status", "==", "pending"))
                    .order_by("submitted_at")
                    .limit(MAX_JOBS_PER_POLL)
                    .stream()
                )

                if docs:
                    print(f"[{_ts()}] {len(docs)} pending job(s) found.")
                    for doc in docs:
                        job = doc.to_dict()
                        if job.get("status") != "pending":
                            continue  # re-check after possible race
                        if self._claim_job(doc.id):
                            self._process_job(doc.id, job)
                else:
                    # Overwrite the idle line on next poll
                    print(f"[{_ts()}] idle", end="\r", flush=True)

                time.sleep(POLL_INTERVAL)

            except KeyboardInterrupt:
                print(f"\n[{_ts()}] Job runner stopped.")
                break
            except Exception as e:
                print(f"[{_ts()}] Poll error: {e}")
                time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    """Current UTC time as HH:MM:SS for log prefixes."""
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        runner = JobRunner()
        runner.run()
    except Exception as e:
        print(f"Fatal error during job runner initialisation: {e}")
        sys.exit(1)
