"""
run_orchestrator.py

Local daemon that picks up director buying signals and runs the agentic
investigation (Layer 1–3) plus synthesis.

Must run locally — NOT on the VM — because yfinance requires a residential
IP address. The VM handles classification and simple lens (job_runner.py);
this script handles the deeper investigation from your local machine.

Flow for each pending signal document:
  1. Claim it (agentic_status: "pending" → "claimed")
  2. Fetch transaction data from pdmr_transactions
  3. Fetch announcement metadata from announcements
  4. Fetch company profile from universe_companies
  5. Reconstruct simple_lens_output from signals doc simple_* fields
  6. Run parallel agentic investigation (sets status → "running")
  7. Run synthesis (sets status → "complete" or "failed")

Usage:
    # Daemon mode — polls continuously
    python scripts/run_orchestrator.py

    # One-shot mode — process pending signals once and exit (useful for testing)
    python scripts/run_orchestrator.py --once

    # Debug mode — sequential layers, verbose logging
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

import anthropic
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from utilities.orchestrator.layer_runner import (
    run_agentic_investigation_parallel,
    run_agentic_investigation_sequential,
)
from utilities.orchestrator.synthesis import run_synthesis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("orchestrator")

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
# Signal processor
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
                    if _claim_signal(db, doc.id):
                        try:
                            process_signal(db, client, doc.id, signal_data, sequential=sequential)
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
