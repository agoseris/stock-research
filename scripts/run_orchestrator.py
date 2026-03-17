"""
run_orchestrator.py

Local daemon that picks up pending signals and runs the deeper agentic
investigation for each signal type.

Must run locally — NOT on the VM — because yfinance requires a residential
IP address. The VM handles classification and simple lens (job_runner.py);
this script handles the deeper investigation from your local machine.

Signal routing (by signal_type field on the signals document):
  director_buying / director_disposal:
    1. Claim signal (agentic_status: "pending" → "claimed")
    2. Fetch transaction data from pdmr_transactions
    3. Fetch announcement + company profile
    4. Run parallel Layer 1–3 agentic investigation
    5. Run synthesis → agentic_status: "complete" or "failed"

  tr1_crossing:
    1. Claim signal (agentic_status: "pending" → "claimed")
    2. Fetch TR-1 data from signals doc (already stored by simple lens)
    3. Run Gemini Flash + Google Search grounding investor research
    4. Persist result → agentic_status: "complete" or "failed"

Usage:
    # Daemon mode — polls continuously
    python scripts/run_orchestrator.py

    # One-shot mode — process pending signals once and exit (useful for testing)
    python scripts/run_orchestrator.py --once

    # Debug mode — sequential layers, verbose logging (director signals only)
    python scripts/run_orchestrator.py --sequential
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent
load_dotenv(_REPO_ROOT / ".env")

# Credentials fallback: .env path → frontend/gcp-credentials.json (local dev)
_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds or not os.path.exists(_creds):
    _fallback = _REPO_ROOT / "frontend" / "gcp-credentials.json"
    if _fallback.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_fallback)

# Add project root to sys.path so utilities.orchestrator imports work
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("orchestrator")

import anthropic
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from utilities.orchestrator.layer_runner import (
    run_agentic_investigation_parallel,
    run_agentic_investigation_sequential,
)
from utilities.orchestrator.synthesis import run_synthesis
from utilities.orchestrator.lens_tr1_investor_research import run_tr1_investor_research

# TelegramNotifier is in backend/ — add it to sys.path if not already present
_BACKEND_DIR = str(_REPO_ROOT / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

try:
    from telegram_notifier import TelegramNotifier as _TelegramNotifier
    _notifier = _TelegramNotifier()
except Exception as _notifier_err:
    _notifier = None
    logger.warning("Telegram notifier unavailable: %s", _notifier_err)

POLL_INTERVAL = 30       # seconds between Firestore polls
MAX_SIGNALS_PER_POLL = 3  # signals to process per poll cycle


# ---------------------------------------------------------------------------
# Firestore data fetchers
# ---------------------------------------------------------------------------

def _claim_signal(db, doc_id: str) -> bool:
    """
    Attempt to claim a signal by transitioning agentic_status: pending → claimed.
    Returns True if successfully claimed.
    Prevents a second orchestrator process from picking up the same signal.
    """
    try:
        db.collection("signals").document(doc_id).update(
            {"agentic_status": "claimed"}
        )
        return True
    except Exception as e:
        logger.error("Could not claim signal %s: %s", doc_id[:8], e)
        return False


def _fetch_transaction(db, rns_article_id: str) -> dict | None:
    """
    Fetch the most significant open-market transaction for this RNS article.

    Queries pdmr_transactions by rns_article_id, filters to open-market
    category, and returns the transaction with the largest total consideration.
    Returns None if no open-market transaction exists.
    """
    docs = list(
        db.collection("pdmr_transactions")
        .where(filter=FieldFilter("rns_article_id", "==", rns_article_id))
        .stream()
    )
    open_market = [
        d.to_dict() for d in docs
        if d.to_dict().get("transaction_category") in (
            "open_market_purchase", "open_market_disposal"
        )
    ]
    if not open_market:
        return None
    return max(open_market, key=lambda t: t.get("total_consideration_gbp") or 0.0)


def _fetch_announcement(db, rns_article_id: str) -> dict:
    """
    Fetch announcement metadata from the announcements collection.
    Returns empty dict if document not found.
    """
    doc = db.collection("announcements").document(rns_article_id).get()
    return doc.to_dict() if doc.exists else {}


def _fetch_company_profile(db, ticker: str) -> dict:
    """
    Fetch company profile from universe_companies.
    Returns a profile dict compatible with the layer runner's expectations.
    """
    doc = db.collection("universe_companies").document(ticker.upper()).get()
    if not doc.exists:
        return {}
    data = doc.to_dict()
    listing = data.get("listing_exchange", "") or ""
    return {
        "ticker": ticker,
        "company_name": data.get("company_name", ""),
        "market_cap_gbp": data.get("market_cap_gbp"),
        "index_membership": "AIM" if listing == "AIM" else "MAIN_MARKET",
        "listing_exchange": listing,
        "tier": data.get("tier"),
    }


def _extract_simple_lens_output(signal_data: dict) -> dict | None:
    """
    Reconstruct simple lens output dict from the signals document's simple_* fields.
    Returns None if the simple lens has not yet been recorded.
    """
    if not signal_data.get("simple_signal_strength"):
        return None
    return {
        "TRANSACTION_NATURE": signal_data.get("simple_transaction_nature"),
        "POSITION_CHANGE_PCT": signal_data.get("simple_position_change_pct"),
        "SIGNAL_STRENGTH": signal_data.get("simple_signal_strength"),
        "SIGNAL_DIRECTION": signal_data.get("simple_signal_direction"),
        "RECOMMENDED_ACTION": signal_data.get("simple_recommended_action"),
        "LIMITATIONS": signal_data.get("simple_limitations"),
        "SUMMARY": signal_data.get("simple_summary"),
    }


def _coerce_published_at(value) -> str:
    """Convert Firestore Timestamp / datetime / str to an ISO string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    # Firestore DatetimeWithNanoseconds
    try:
        return value.isoformat()
    except Exception:
        return str(value)


# ---------------------------------------------------------------------------
# TR-1 signal processor
# ---------------------------------------------------------------------------

def process_tr1_signal(
    db,
    doc_id: str,
    signal_data: dict,
) -> None:
    """
    Run investor research for one pending TR-1 crossing signal.

    Reads the notifier data from signal_data (already stored by
    lens_tr1_simple in job_runner.py) and calls Gemini Flash +
    Google Search grounding via run_tr1_investor_research.

    Parameters
    ----------
    db : Firestore client
    doc_id : str
        The signals document_id (= rns_article_id).
    signal_data : dict
        The signals document data (already contains tr1 extraction fields).
    """
    ticker = signal_data.get("ticker", "UNKNOWN")
    notifier = signal_data.get("notifier_name") or "Unknown"
    threshold = signal_data.get("threshold_crossed")
    threshold_str = f"{threshold:.0f}%" if threshold else "?"

    logger.info(
        "[%s] TR-1 investor research — notifier=%s threshold=%s signal %s",
        ticker, notifier[:50], threshold_str, doc_id[:8],
    )

    result, token_usage = run_tr1_investor_research(
        rns_article_id=doc_id,
        signal_data=signal_data,
        db=db,
    )

    if result.get("_error"):
        logger.error("[%s] Investor research failed: %s", ticker, result["_error"])
    else:
        conviction = result.get("conviction_assessment", "")
        confidence = result.get("confidence", "")
        logger.info(
            "[%s] Investor research complete — type=%s conviction=%s confidence=%s",
            ticker,
            result.get("notifier_type"),
            conviction,
            confidence,
        )
        # Telegram notification: conviction_likely + high/medium confidence = act threshold
        if (
            _notifier is not None
            and conviction == "conviction_likely"
            and confidence in ("high", "medium")
        ):
            try:
                tr1_payload = {
                    **signal_data,
                    "investor_research_result": result,
                }
                _notifier.send(
                    _notifier.format_tr1_signal(tr1_payload),
                    priority="high",
                )
                logger.info("[%s] Telegram TR-1 agentic notification sent.", ticker)
            except Exception as _notify_err:
                logger.warning("[%s] TR-1 Telegram notification failed: %s", ticker, _notify_err)


# ---------------------------------------------------------------------------
# Director signal processor
# ---------------------------------------------------------------------------

def process_signal(
    db,
    client: anthropic.Anthropic,
    doc_id: str,
    signal_data: dict,
    sequential: bool = False,
) -> None:
    """
    Run the full agentic investigation + synthesis for one pending signal.

    Parameters
    ----------
    db : Firestore client
    client : anthropic.Anthropic
    doc_id : str
        The signals document_id (= rns_article_id).
    signal_data : dict
        The signals document data.
    sequential : bool
        If True, run layers sequentially (debug mode). Default: parallel.
    """
    ticker = signal_data.get("ticker", "UNKNOWN")
    logger.info("[%s] Starting investigation — signal %s", ticker, doc_id[:8])

    # --- Fetch transaction data ---
    transaction = _fetch_transaction(db, doc_id)
    if transaction is None:
        logger.warning("[%s] No open-market pdmr_transaction found — marking failed.", ticker)
        db.collection("signals").document(doc_id).set(
            {"agentic_status": "failed", "agentic_synthesis_error": "no_open_market_transaction"},
            merge=True,
        )
        return

    logger.info(
        "[%s] Transaction: %s by %s  consideration=£%s",
        ticker,
        transaction.get("transaction_category", "?"),
        transaction.get("director_name", "?"),
        f"{transaction.get('total_consideration_gbp', 0):,.0f}" if transaction.get("total_consideration_gbp") else "?",
    )

    # --- Fetch announcement metadata ---
    announcement_data = _fetch_announcement(db, doc_id)
    announcement = {
        "headline": announcement_data.get("headline", ""),
        "summary": announcement_data.get("summary", ""),
        "key_topics": [],   # PDMR transactions have no topics
        "source_url": announcement_data.get("source_url", ""),
        "rns_article_id": doc_id,
    }

    # --- Fetch company profile ---
    company_profile = _fetch_company_profile(db, ticker)

    # --- Reconstruct simple lens output ---
    simple_lens_output = _extract_simple_lens_output(signal_data)
    if simple_lens_output:
        logger.info("[%s] Simple lens: %s", ticker, simple_lens_output.get("RECOMMENDED_ACTION"))
    else:
        logger.warning("[%s] No simple lens output found — synthesis will proceed without it.", ticker)

    # --- Enrich transaction with published_at for synthesis prompt ---
    transaction["published_at"] = _coerce_published_at(
        signal_data.get("published_at") or transaction.get("transaction_date_reported")
    )

    # --- Run agentic investigation ---
    runner = run_agentic_investigation_sequential if sequential else run_agentic_investigation_parallel
    mode = "sequential" if sequential else "parallel"
    logger.info("[%s] Running %s investigation...", ticker, mode)

    investigation_result = runner(
        rns_article_id=doc_id,
        announcement=announcement,
        transaction=transaction,
        company_profile=company_profile,
        client=client,
        db=db,
    )

    l1_ok = investigation_result.get("layer1", {}).get("output") is not None
    l2_ok = investigation_result.get("layer2", {}).get("output") is not None
    l3_ok = investigation_result.get("layer3", {}).get("output") is not None
    logger.info("[%s] Layers complete — L1=%s L2=%s L3=%s", ticker,
                "ok" if l1_ok else "failed",
                "ok" if l2_ok else "failed",
                "ok" if l3_ok else "failed")

    # --- Run synthesis ---
    logger.info("[%s] Running synthesis...", ticker)
    synthesis_result = run_synthesis(
        rns_article_id=doc_id,
        transaction=transaction,
        investigation_result=investigation_result,
        simple_lens_output=simple_lens_output,
        client=client,
        db=db,
    )

    if "_error" in synthesis_result:
        logger.error("[%s] Synthesis parse failed: %s", ticker, synthesis_result["_error"])
    else:
        rec = synthesis_result.get("recommendation", "?")
        logger.info("[%s] Synthesis complete — recommendation: %s", ticker, rec)

        # Telegram notification — 'act' threshold only ("Investigate further")
        _r = (rec or "").strip().lower()
        if _notifier is not None and _r in ("investigate further", "investigate"):
            try:
                director_payload = {
                    **signal_data,
                    # Enrich with agentic recommendation fields for notifier
                    "simple_recommended_action": rec,
                    "simple_summary": synthesis_result.get("recommendation_justification", ""),
                }
                _notifier.send(
                    _notifier.format_director_signal(director_payload),
                    priority="high",
                )
                logger.info("[%s] Telegram Director agentic notification sent.", ticker)
            except Exception as _notify_err:
                logger.warning(
                    "[%s] Director Telegram notification failed: %s", ticker, _notify_err
                )


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

def run(sequential: bool = False, once: bool = False) -> None:
    """
    Poll Firestore for pending director signals and process them.

    Parameters
    ----------
    sequential : bool
        Use sequential layer runner instead of parallel (slower, better for debug).
    once : bool
        Process pending signals once then exit, instead of polling continuously.
    """
    mode_str = "sequential" if sequential else "parallel"
    logger.info(
        "Director buying orchestrator started [mode=%s, once=%s]. Polling every %ds.",
        mode_str, once, POLL_INTERVAL,
    )

    client = anthropic.Anthropic()
    db = firestore.Client()

    while True:
        try:
            # Query pending signals — no order_by to avoid composite index requirement
            docs = list(
                db.collection("signals")
                .where(filter=FieldFilter("agentic_status", "==", "pending"))
                .limit(MAX_SIGNALS_PER_POLL)
                .stream()
            )

            if docs:
                logger.info("%d pending signal(s) found.", len(docs))
                for doc in docs:
                    signal_data = doc.to_dict()
                    # Re-check status after fetch (race condition guard)
                    if signal_data.get("agentic_status") != "pending":
                        continue
                    if not _claim_signal(db, doc.id):
                        continue

                    signal_type = signal_data.get("signal_type", "")
                    try:
                        if signal_type == "tr1_crossing":
                            process_tr1_signal(db, doc.id, signal_data)
                        elif signal_type in ("director_buying", "director_disposal", ""):
                            # Empty signal_type: backwards-compatible — treat as director
                            process_signal(db, client, doc.id, signal_data, sequential=sequential)
                        else:
                            logger.warning(
                                "Unknown signal_type %r on signal %s — skipping.",
                                signal_type, doc.id[:8],
                            )
                            db.collection("signals").document(doc.id).set(
                                {"agentic_status": "skipped",
                                 "agentic_synthesis_error": f"unknown signal_type: {signal_type}"},
                                merge=True,
                            )
                    except Exception as e:
                        logger.error("Signal %s failed unexpectedly: %s", doc.id[:8], e)
                        try:
                            db.collection("signals").document(doc.id).set(
                                {"agentic_status": "failed", "agentic_synthesis_error": str(e)},
                                merge=True,
                            )
                        except Exception:
                            pass
            else:
                if not once:
                    print(
                        f"[{datetime.now(tz=timezone.utc).strftime('%H:%M:%S')}] idle",
                        end="\r", flush=True,
                    )

            if once:
                logger.info("--once mode: exiting after one poll.")
                break

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Orchestrator stopped.")
            break
        except Exception as e:
            logger.error("Poll error: %s", e)
            if once:
                break
            time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Director buying orchestrator — runs locally.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process pending signals once and exit (useful for testing).",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Run layers sequentially instead of in parallel (debug mode).",
    )
    args = parser.parse_args()
    run(sequential=args.sequential, once=args.once)
