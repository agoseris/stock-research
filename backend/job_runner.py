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

import hashlib
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import anthropic
from utilities.orchestrator.classification import (
    classify_article,
    get_open_market_transactions,
)
from utilities.orchestrator.persistence import update_agentic_status
from utilities.orchestrator.simple_lens import run_simple_lens

from abstractions import Announcement, UniverseCompany
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from lens_regulatory_catalyst import RegulatoryCatalystLens
from llm_gemini import GeminiProvider
from signal_state import (
    classify_signal_strength,
    is_negative_signal,
    extract_confidence,
    compute_signal_transition,
)
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
        self.anthropic_client = anthropic.Anthropic()

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

    def _get_director_lens_config(self) -> dict:
        """
        Read materiality gate config from app_config/director_lens_config.
        Returns defaults if the document does not exist.

        Firestore fields:
          skip_agentic_on_ignore (bool, default True):
            Skip agentic investigation when simple lens recommends Ignore.
          agentic_min_consideration_gbp (float, default 0):
            Skip agentic investigation when total consideration is below
            this floor in GBP. Set to 0 to disable the floor check.
        """
        _DEFAULTS = {
            "skip_agentic_on_ignore": True,
            "agentic_min_consideration_gbp": 0,
        }
        try:
            doc = self.db.collection("app_config").document("director_lens_config").get()
            if doc.exists:
                return {**_DEFAULTS, **doc.to_dict()}
        except Exception:
            pass
        return _DEFAULTS

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
                    "lens": lens.name,
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

    def _run_pdmr_flow(
        self,
        job_id: str,
        announcement: Announcement,
        classification: dict,
        job: dict,
        rns_article_id: str,
    ):
        """
        Handle a PDMR transaction article: run the simple lens for each
        open-market transaction and persist results to the signals collection.
        The agentic investigation is handled separately by scripts/run_orchestrator.py
        (runs locally due to yfinance residential-IP requirement).
        """
        ticker = announcement.ticker
        open_market_txs = get_open_market_transactions(classification)
        if not open_market_txs:
            print(f"  [{ticker}] PDMR: no open-market transactions — skipping simple lens.")
            self._complete_job(job_id, note="pdmr_no_open_market")
            return

        company = self.universe_storage.get_company(ticker)
        company_profile = _build_company_profile(company)

        cfg = self._get_director_lens_config()
        skip_on_ignore = cfg.get("skip_agentic_on_ignore", True)
        min_consideration = cfg.get("agentic_min_consideration_gbp", 0)

        for tx in open_market_txs:
            tx_enriched = dict(tx)
            tx_enriched["ticker"] = announcement.ticker
            tx_enriched["company_name"] = announcement.company_name
            director = tx.get("director_name", "?")
            try:
                simple_result, _tokens = run_simple_lens(
                    rns_article_id=rns_article_id,
                    transaction=tx_enriched,
                    company_profile=company_profile,
                    client=self.anthropic_client,
                    db=self.db,
                )
                rec = simple_result.get("RECOMMENDED_ACTION", "unknown")
                print(f"  [{ticker}] Simple lens: {rec} (director: {director})")

                # Materiality gate — override agentic_status to "skipped" if:
                #   1. Simple lens recommends Ignore, or
                #   2. Consideration is below the configured floor
                consideration = tx.get("total_consideration_gbp") or 0
                gated_ignore = skip_on_ignore and rec == "Ignore"
                gated_floor = min_consideration > 0 and consideration < min_consideration
                if gated_ignore or gated_floor:
                    reason = "simple_lens_ignore" if gated_ignore else f"below_floor_£{min_consideration:,.0f}"
                    update_agentic_status(rns_article_id, "skipped", db=self.db)
                    print(f"  [{ticker}] Agentic skipped ({reason}) — "
                          f"consideration=£{consideration:,.0f}")
                else:
                    print(f"  [{ticker}] Agentic queued — consideration=£{consideration:,.0f}")

            except Exception as e:
                print(f"  [{ticker}] Simple lens failed for {director}: {e}")

        self._complete_job(
            job_id,
            note=f"pdmr_simple_lens_done:{len(open_market_txs)}_txs",
        )

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
    # Signal state transitions
    # ------------------------------------------------------------------

    def _apply_state_transition(
        self, announcement: "Announcement", result: dict, now: datetime
    ) -> None:
        """
        Classify the LLM result, compute the state transition for the ticker,
        and persist the new state + history record to universe_storage.
        """
        ticker = announcement.ticker
        lens_name = result.get("lens", "unknown")
        try:
            strength = classify_signal_strength(result["llm_analysis"])
            is_neg = is_negative_signal(result["llm_analysis"])

            company = self.universe_storage.get_company(ticker)
            current_state = company.signal_state if company else None
            normalised_current = current_state or "watching"

            new_state = compute_signal_transition(current_state, strength, is_neg)

            if new_state is not None and new_state != normalised_current:
                self.universe_storage.update_signal_state(ticker, new_state, now)
                self.universe_storage.record_signal_transition(ticker, {
                    "timestamp": now,
                    "previous_state": normalised_current,
                    "new_state": new_state,
                    "trigger_source_url": announcement.source_url,
                    "trigger_headline": announcement.headline,
                    "lens": lens_name,
                    "signal_strength": strength,
                    "llm_confidence": extract_confidence(result["llm_analysis"]),
                })
                print(f"  [{ticker}] State: {normalised_current} → {new_state} "
                      f"({strength}{'  [neg]' if is_neg else ''})")
            else:
                # No state change — touch last_signal_at only
                self.universe_storage.update_signal_state(
                    ticker, normalised_current, now, update_since=False
                )
        except Exception as e:
            print(f"  [{ticker}] State transition error: {e}")

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
                not_of_interest=not_of_interest,
            )
            self.universe_storage.save_company(company)
            if not not_of_interest:
                self._universe_tickers.add(ticker)

            if source_discovery_id:
                self.db.collection("discovery_results").document(source_discovery_id).update(
                    {"dismissed": True, "dismissed_at": now.isoformat()}
                )

            self._complete_job(job_id, note="admitted")
            print(f"  [{ticker}] Admitted.")

        except Exception as e:
            print(f"  [{ticker}] universe_admit failed: {e}")
            self._fail_job(job_id, str(e))

    # ------------------------------------------------------------------
    # Bulk universe import
    # ------------------------------------------------------------------

    def _process_universe_bulk_import_job(self, job_id: str, job: dict):
        """
        Handle a universe_bulk_import job.

        Processing order:
          1. Remove  — delete universe_companies documents
          2. Mute    — set not_of_interest=True with merge=True
          3. Update  — update company fields with merge=True (preserves not_of_interest)
          4. New     — CH lookup (0.6s rate limit) + save_company() for each new entry

        job fields:
          new_companies    — list of {ticker, company_name, market_cap_gbp, listing_exchange, tier}
          update_companies — same shape; metadata update only, flags preserved
          remove_tickers   — list of ticker strings to delete from Firestore
          mute_tickers     — list of ticker strings to set not_of_interest=True
        """
        remove_tickers = job.get("remove_tickers", [])
        mute_tickers = job.get("mute_tickers", [])
        update_companies = job.get("update_companies", [])
        new_companies = job.get("new_companies", [])

        print(f"\n[{_ts()}] --- universe_bulk_import {job_id[:8]} ---")
        print(f"  new={len(new_companies)}, update={len(update_companies)}, "
              f"remove={len(remove_tickers)}, mute={len(mute_tickers)}")

        try:
            now = datetime.now(timezone.utc)

            # 1. Remove
            for ticker in remove_tickers:
                ticker = ticker.strip().upper()
                self.db.collection("universe_companies").document(ticker).delete()
                self._universe_tickers.discard(ticker)
                print(f"  [{ticker}] Removed.")

            # 2. Mute
            for ticker in mute_tickers:
                ticker = ticker.strip().upper()
                self.db.collection("universe_companies").document(ticker).set(
                    {"not_of_interest": True}, merge=True
                )
                self._universe_tickers.discard(ticker)
                print(f"  [{ticker}] Muted.")

            # 3. Update — merge=True is critical: preserves not_of_interest flag
            for comp in update_companies:
                ticker = (comp.get("ticker") or "").strip().upper()
                if not ticker:
                    continue
                self.db.collection("universe_companies").document(ticker).set(
                    {
                        "company_name": comp.get("company_name", ""),
                        "market_cap_gbp": comp.get("market_cap_gbp"),
                        "listing_exchange": comp.get("listing_exchange", ""),
                        "tier": comp.get("tier", 2),
                        "last_refreshed": now,
                    },
                    merge=True,
                )
            if update_companies:
                print(f"  Updated {len(update_companies)} companies.")

            # 4. New — save_company()
            for i, comp in enumerate(new_companies, 1):
                ticker = (comp.get("ticker") or "").strip().upper()
                company_name = comp.get("company_name", "")
                if not ticker or not company_name:
                    continue
                listing_exchange = comp.get("listing_exchange", "AIM")
                company = UniverseCompany(
                    ticker_lse=ticker,
                    ticker_yahoo=f"{ticker}.L",
                    company_name=company_name,
                    tier=comp.get("tier", 2),
                    listing_exchange=listing_exchange,
                    entity_type="OPERATING",
                    liquidity_flag="LIQUID" if listing_exchange == "LSE_MAIN" else "ILLIQUID",
                    universe_added_date=now.date(),
                    last_refreshed=now,
                    market_cap_gbp=comp.get("market_cap_gbp"),
                )
                self.universe_storage.save_company(company)
                self._universe_tickers.add(ticker)
                if i % 10 == 0 or i == len(new_companies):
                    print(f"  New: {i}/{len(new_companies)} added.")

            note = (f"bulk_import: {len(new_companies)} added, "
                    f"{len(update_companies)} updated, "
                    f"{len(remove_tickers)} removed, "
                    f"{len(mute_tickers)} muted")
            self._complete_job(job_id, note=note)
            print(f"  [{_ts()}] bulk_import complete: {note}")

        except Exception as e:
            print(f"  universe_bulk_import failed: {e}")
            self._fail_job(job_id, str(e))

    # ------------------------------------------------------------------
    # Job processing
    # ------------------------------------------------------------------

    def _complete_job(self, job_id: str, note: Optional[str] = None):
        now = datetime.now(timezone.utc)
        update = {
            "status": "complete",
            "processed_at": now.isoformat(),
            "expires_at": now + timedelta(days=7),
        }
        if note:
            update["note"] = note
        self.db.collection("pending_jobs").document(job_id).update(update)

    def _fail_job(self, job_id: str, error: str):
        try:
            self.db.collection("pending_jobs").document(job_id).update({
                "status": "failed",
                "error": error,
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
            })
        except Exception as e:
            print(f"  Could not mark job {job_id} as failed: {e}")

    def _process_job(self, job_id: str, job: dict):
        """Process a single pending job end-to-end."""
        job_type = job.get("job_type", "lseg_ingest")
        if job_type == "universe_admit":
            self._process_universe_admit_job(job_id, job)
            return
        if job_type == "universe_bulk_import":
            self._process_universe_bulk_import_job(job_id, job)
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

            now = datetime.now(timezone.utc)
            if ticker.upper() in self._universe_tickers:
                # Classify the article to route PDMR transactions to the director lens
                rns_article_id = _compute_fingerprint(
                    announcement.source_url or announcement.headline
                )
                try:
                    classification, _cls_tokens = classify_article(
                        rns_article_id=rns_article_id,
                        announcement={
                            "company_name": announcement.company_name,
                            "ticker": announcement.ticker,
                            "source_name": announcement.source_name,
                            "headline": announcement.headline,
                            "body": announcement.body,
                            "published_at": str(announcement.published_at),
                        },
                        client=self.anthropic_client,
                        db=self.db,
                    )
                    article_type = classification.get("article_type", "unclassified")
                    print(f"  [{ticker}] Classification: {article_type}")
                except Exception as e:
                    print(f"  [{ticker}] Classification exception: {e} — skipping lens analysis.")
                    self._complete_job(job_id, note="classification_failed")
                    return

                if article_type == "pdmr_transaction":
                    self._run_pdmr_flow(job_id, announcement, classification, job, rns_article_id)
                    return

                if classification.get("extraction_status") == "failed":
                    self._complete_job(job_id, note="classification_failed")
                    print(f"  [{ticker}] Classification failed — skipping lens analysis.")
                    return

                # Non-PDMR: run regulatory catalyst lens
                result = self._run_signal_analysis(announcement)
                if result:
                    # Enrich with price (from LSEG index page) and market cap (from Firestore)
                    result["price_pence"] = job.get("price")
                    result["price_change"] = job.get("price_change")
                    company = self.universe_storage.get_company(ticker)
                    if company:
                        result["market_cap_gbp"] = company.market_cap_gbp
                    self.storage.save_signal_result(result)
                    self._apply_state_transition(announcement, result, now)
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


def _compute_fingerprint(text: str) -> str:
    """
    SHA-256 fingerprint of normalised text.
    Matches FirestoreProvider._fingerprint — used as the rns_article_id
    that links announcements, pdmr_transactions, and signals documents.
    """
    normalised = " ".join(
        "".join(c for c in text.lower() if c.isalnum() or c.isspace()).split()
    )
    return hashlib.sha256(normalised.encode()).hexdigest()


def _build_company_profile(company) -> dict:
    """Build the company_profile dict expected by run_simple_lens."""
    if company is None:
        return {}
    listing = getattr(company, "listing_exchange", "") or ""
    index = "AIM" if listing == "AIM" else "MAIN_MARKET"
    return {
        "market_cap_gbp": getattr(company, "market_cap_gbp", None),
        "market_cap_stale": True,
        "index_membership": index,
    }


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
