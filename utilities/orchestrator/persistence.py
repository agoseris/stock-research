"""
Orchestrator persistence layer — Firestore read/write operations.

Stage 1 operations (this file):
  - update_announcement_classification: write article_type, extraction_status,
      summary, expires_at to the announcements document
  - persist_pdmr_transaction: create a pdmr_transactions document
  - persist_news_summary: create a company_news_summaries document

Later stages add:
  - create_signal_document (Stage 2)
  - persist_simple_lens_result (Stage 2)
  - update_agentic_status (Stage 3)
  - persist_layer_outputs (Stage 3)
  - persist_synthesis_result (Stage 5) — complete

All functions accept an optional `db` parameter so tests can inject a mock.
When db=None, the lazy singleton is used.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv

_UTILITIES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = _UTILITIES_DIR
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# Credentials fallback: .env path → frontend/gcp-credentials.json
_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds_path or not os.path.exists(_creds_path):
    _frontend_creds = os.path.join(_PROJECT_ROOT, "frontend", "gcp-credentials.json")
    if os.path.exists(_frontend_creds):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _frontend_creds

from utilities.orchestrator.config import (
    ANNOUNCEMENT_TTL_DAYS,
    PDMR_TRANSACTION_TTL_DAYS,
    COMPANY_NEWS_SUMMARY_TTL_DAYS,
    SIGNAL_TTL_DAYS,
)

SIGNALS_UNIFIED_COLLECTION = "signals_unified"

# ---------------------------------------------------------------------------
# Firestore client — lazy singleton
# ---------------------------------------------------------------------------

_db = None


def _get_db():
    global _db
    if _db is None:
        from google.cloud import firestore
        _db = firestore.Client()
    return _db


def _expires_at(days: Optional[int]) -> Optional[datetime]:
    """Return a UTC datetime `days` from now, or None if days is None."""
    if days is None:
        return None
    return datetime.now(tz=timezone.utc) + timedelta(days=days)


# ---------------------------------------------------------------------------
# Stage 1: announcements collection update
# ---------------------------------------------------------------------------

def update_announcement_classification(
    rns_article_id: str,
    article_type: str,
    extraction_status: str,
    summary: Optional[str] = None,
    db=None,
) -> None:
    """
    Write classification fields back to the announcements document.

    Called immediately after the classification LLM call completes.
    Uses merge=True so existing fields (headline, source_url, etc.) are preserved.

    Parameters
    ----------
    rns_article_id : str
        The announcements document_id (SHA-256 fingerprint).
    article_type : str
        One of: pdmr_transaction, regulatory_catalyst, substantive_news,
        administrative, unclassified.
    extraction_status : str
        One of: complete, failed.
    summary : str, optional
        2-3 sentence narrative summary from the classification output.
    db : Firestore client, optional
        Injected for testing. Uses singleton when None.
    """
    db = db or _get_db()

    ttl_days = ANNOUNCEMENT_TTL_DAYS.get(article_type)
    update = {
        "article_type": article_type,
        "extraction_status": extraction_status,
        "classified_at": datetime.now(tz=timezone.utc),
    }
    if summary is not None:
        update["summary"] = summary
    expires = _expires_at(ttl_days)
    if expires is not None:
        update["expires_at"] = expires

    db.collection("announcements").document(rns_article_id).set(
        update, merge=True
    )


# ---------------------------------------------------------------------------
# Stage 1: pdmr_transactions collection
# ---------------------------------------------------------------------------

def persist_pdmr_transaction(
    rns_article_id: str,
    ticker: str,
    company_name: str,
    transaction: dict,
    db=None,
) -> str:
    """
    Create a pdmr_transactions document for a single director transaction.

    A single RNS filing may contain multiple directors. Call this once per
    transaction dict returned by the classification extraction.

    Parameters
    ----------
    rns_article_id : str
        FK to announcements document_id.
    ticker : str
        LSE/AIM ticker.
    company_name : str
        Company name as stated in the announcement.
    transaction : dict
        Extracted transaction fields from the classification LLM response.
        Keys match the pdmr_transactions schema in data_schema.md.
    db : Firestore client, optional

    Returns
    -------
    str
        The auto-generated Firestore document_id.
    """
    db = db or _get_db()

    is_open_market = transaction.get("transaction_category") in (
        "open_market_purchase",
        "open_market_disposal",
    )

    doc = {
        # Linkage
        "rns_article_id": rns_article_id,
        "ticker": ticker,
        "company_name": company_name,
        # Transaction identity
        "director_name": transaction.get("director_name"),
        "director_role": transaction.get("director_role"),
        "transaction_date_actual": transaction.get("transaction_date_actual"),
        "transaction_date_reported": transaction.get("transaction_date_reported"),
        "reporting_lag_days": transaction.get("reporting_lag_days"),
        # Transaction details
        "transaction_type": transaction.get("transaction_type"),
        "transaction_category": transaction.get("transaction_category"),
        "is_open_market": is_open_market,
        "plan_name": transaction.get("plan_name"),
        # Quantities and prices
        "shares_transacted": transaction.get("shares_transacted"),
        "price_per_share_gbp": transaction.get("price_per_share_gbp"),
        "price_per_share_raw": transaction.get("price_per_share_raw"),
        "total_consideration_gbp": transaction.get("total_consideration_gbp"),
        "currency_original": transaction.get("currency_original"),
        # Holdings
        "previous_holding": transaction.get("previous_holding"),
        "resulting_holding": transaction.get("resulting_holding"),
        "resulting_holding_pct": transaction.get("resulting_holding_pct"),
        # Instrument
        "isin": transaction.get("isin"),
        "lei": transaction.get("lei"),
        "exchange": transaction.get("exchange"),
        # Extraction quality
        "extraction_confidence": transaction.get("extraction_confidence"),
        "confidence_notes": transaction.get("confidence_notes"),
        # Housekeeping
        "created_at": datetime.now(tz=timezone.utc),
        "expires_at": _expires_at(PDMR_TRANSACTION_TTL_DAYS),
    }

    ref = db.collection("pdmr_transactions").document()
    ref.set(doc)
    return ref.id


# ---------------------------------------------------------------------------
# Stage 1: company_news_summaries collection
# ---------------------------------------------------------------------------

def persist_news_summary(
    rns_article_id: str,
    ticker: str,
    published_at,
    classification: dict,
    db=None,
) -> str:
    """
    Create a company_news_summaries document for a substantive_news article.

    Parameters
    ----------
    rns_article_id : str
        FK to announcements document_id.
    ticker : str
        LSE/AIM ticker.
    published_at : datetime | str
        Publication timestamp of the RNS article.
    classification : dict
        Full parsed classification result. Reads summary, article_subtype,
        sentiment, key_topics.
    db : Firestore client, optional

    Returns
    -------
    str
        The auto-generated Firestore document_id.
    """
    db = db or _get_db()

    doc = {
        "rns_article_id": rns_article_id,
        "ticker": ticker,
        "published_at": published_at,
        "article_subtype": classification.get("article_subtype"),
        "sentiment": classification.get("sentiment"),
        "summary": classification.get("summary"),
        "key_topics": classification.get("key_topics") or [],
        "created_at": datetime.now(tz=timezone.utc),
        "expires_at": _expires_at(COMPANY_NEWS_SUMMARY_TTL_DAYS),
    }

    ref = db.collection("company_news_summaries").document()
    ref.set(doc)
    return ref.id


# ---------------------------------------------------------------------------
# Unified schema helpers
# ---------------------------------------------------------------------------

def int_strength_to_unified(val) -> str:
    """Map integer 1–10 strength score to unified string scale."""
    if val is None:
        return "noise"
    try:
        v = int(val)
        if v >= 9:
            return "strong"
        if v >= 7:
            return "substantial"
        if v >= 5:
            return "moderate"
        if v >= 3:
            return "weak"
        return "noise"
    except (TypeError, ValueError):
        return "noise"


def map_director_recommendation(rec: str) -> str:
    """Map Director simple / synthesis recommendation to unified value."""
    r = (rec or "").strip().lower()
    if r in ("investigate further", "investigate"):
        return "act"
    if r == "monitor":
        return "monitor"
    return "ignore"


def map_tr1_recommendation(rec: str) -> str:
    """Map TR-1 simple lens recommendation to unified value."""
    r = (rec or "").strip().lower()
    if r == "investigate":
        return "act"
    if r == "monitor":
        return "monitor"
    return "ignore"


def _build_unified_director_doc(
    rns_article_id: str,
    ticker: str,
    company_name: str,
    published_at,
    signal_type: str,
    simple_result: dict,
) -> dict:
    """
    Build a signals_unified document from Director simple lens output.

    Called by persist_simple_lens_result to dual-write to signals_unified.
    The agentic fields are written later by persist_synthesis_result.
    """
    strength_int = simple_result.get("SIGNAL_STRENGTH")
    signal_strength = int_strength_to_unified(strength_int)
    recommendation = map_director_recommendation(
        simple_result.get("RECOMMENDED_ACTION", "")
    )

    return {
        "ticker": ticker,
        "company_name": company_name,
        "lens_id": "director_purchasing",
        "signal_type": signal_type,
        "stored_at": datetime.now(tz=timezone.utc),
        "published_at": published_at,
        "signal_strength": signal_strength,
        "recommendation": recommendation,
        "summary": simple_result.get("SUMMARY", ""),
        "rationale": simple_result.get("SUMMARY", ""),   # best available at simple stage
        "limitations": simple_result.get("LIMITATIONS", ""),
        "confidence_signal": "",
        "dismissed": False,
        "agentic_status": "pending",
        "expires_at": _expires_at(SIGNAL_TTL_DAYS),
        # Signal quality fields — populated by proposal_agent.py after save
        "signal_maturity":           "first_signal",
        "reinforcement_refs":        [],
        "disqualification_refs":     [],
        "notifier_category":         None,
        "active_lens_count_at_fire": 0,
        "lens_data": {
            "simple_transaction_nature": simple_result.get("TRANSACTION_NATURE"),
            "simple_position_change_pct": simple_result.get("POSITION_CHANGE_PCT"),
            "simple_signal_strength_raw": strength_int,
            "simple_signal_direction": simple_result.get("SIGNAL_DIRECTION"),
            "simple_commitment_size": simple_result.get("COMMITMENT_SIZE"),
            "simple_holding_significance": simple_result.get("HOLDING_SIGNIFICANCE"),
            "simple_reporting_lag_assessment": simple_result.get("REPORTING_LAG_ASSESSMENT"),
        },
    }


def _build_unified_tr1_doc(
    rns_article_id: str,
    ticker: str,
    company_name: str,
    published_at,
    tr1_data: dict,
    simple_result: dict,
) -> dict:
    """
    Build a signals_unified document from TR-1 simple lens output.

    Called by persist_tr1_signal to dual-write to signals_unified.
    The investor_research fields are written later by persist_tr1_investor_research.
    """
    signal_strength = simple_result.get("signal_strength", "noise")
    # Validate against known strength values
    _VALID = {"strong", "moderate", "weak", "noise", "negative", "substantial"}
    if signal_strength not in _VALID:
        signal_strength = "noise"

    recommendation = map_tr1_recommendation(simple_result.get("recommendation", ""))

    tr1_extraction_fields = {
        "notifier_name": tr1_data.get("notifier_name"),
        "issuer_name": tr1_data.get("issuer_name"),
        "direction": tr1_data.get("direction"),
        "old_holding_pct": tr1_data.get("old_holding_pct"),
        "new_holding_pct": tr1_data.get("new_holding_pct"),
        "threshold_crossed": tr1_data.get("threshold_crossed"),
        "nature_of_holding": tr1_data.get("nature_of_holding"),
        "crossing_date": tr1_data.get("crossing_date"),
    }

    return {
        "ticker": ticker,
        "company_name": company_name,
        "lens_id": "tr1_accumulation",
        "signal_type": "tr1_crossing",
        "stored_at": datetime.now(tz=timezone.utc),
        "published_at": published_at,
        "signal_strength": signal_strength,
        "recommendation": recommendation,
        "summary": simple_result.get("rationale", ""),
        "rationale": simple_result.get("rationale", ""),
        "limitations": simple_result.get("limitations", ""),
        "confidence_signal": "",
        "dismissed": False,
        "agentic_status": "pending",
        "expires_at": _expires_at(SIGNAL_TTL_DAYS),
        # Signal quality fields — notifier_category set from simple lens;
        # remaining fields populated by proposal_agent.py after save
        "signal_maturity":           "first_signal",
        "reinforcement_refs":        [],
        "disqualification_refs":     [],
        "notifier_category":         simple_result.get("notifier_category"),
        "active_lens_count_at_fire": 0,
        "lens_data": {
            **tr1_extraction_fields,
            "simple_lens_result": simple_result,
        },
    }


# ---------------------------------------------------------------------------
# TR-1: signals collection — TR-1 signal document
# ---------------------------------------------------------------------------

def persist_tr1_signal(
    rns_article_id: str,
    ticker: str,
    company_name: str,
    published_at,
    tr1_data: dict,
    simple_result: dict,
    db=None,
) -> None:
    """
    Create or update the signals document for a TR-1 crossing.

    Writes tr1 extraction data, simple lens result, and housekeeping fields.
    agentic_status is set to "pending" when investor research should follow,
    or "skipped" if the caller determines research is not warranted — the
    caller should call update_agentic_status("skipped") after this function
    if needed (defaults to "pending" here).

    Parameters
    ----------
    rns_article_id : str
        The signals document_id (deduplication key = SHA-256 fingerprint).
    ticker : str
    company_name : str
    published_at : datetime | str
        Publication timestamp of the RNS filing.
    tr1_data : dict
        Extracted TR-1 fields from classification. Expected keys:
        notifier_name, issuer_name, direction, old_holding_pct,
        new_holding_pct, threshold_crossed, nature_of_holding, crossing_date.
    simple_result : dict
        Parsed output from lens_tr1_simple (signal_strength, direction,
        recommendation, rationale, trigger_investor_research, limitations).
    db : Firestore client, optional
    """
    db = db or _get_db()

    doc = {
        # Identity
        "ticker": ticker,
        "company_name": company_name,
        "published_at": published_at,
        "signal_type": "tr1_crossing",
        # TR-1 extraction data
        "notifier_name": tr1_data.get("notifier_name"),
        "issuer_name": tr1_data.get("issuer_name"),
        "direction": tr1_data.get("direction"),
        "old_holding_pct": tr1_data.get("old_holding_pct"),
        "new_holding_pct": tr1_data.get("new_holding_pct"),
        "threshold_crossed": tr1_data.get("threshold_crossed"),
        "nature_of_holding": tr1_data.get("nature_of_holding"),
        "crossing_date": tr1_data.get("crossing_date"),
        # Simple lens result (nested dict for clean UI display)
        "simple_lens_result": simple_result,
        "simple_completed_at": datetime.now(tz=timezone.utc),
        # Agentic status — caller may override to "skipped" immediately after
        "agentic_status": "pending",
        # Investor decision — pending until human acts
        "investor_action": "pending",
        # Housekeeping
        "created_at": datetime.now(tz=timezone.utc),
        "expires_at": _expires_at(SIGNAL_TTL_DAYS),
    }

    db.collection("signals").document(rns_article_id).set(doc, merge=True)

    # Dual-write to signals_unified (new unified schema)
    try:
        unified_doc = _build_unified_tr1_doc(
            rns_article_id=rns_article_id,
            ticker=ticker,
            company_name=company_name,
            published_at=published_at,
            tr1_data=tr1_data,
            simple_result=simple_result,
        )
        db.collection(SIGNALS_UNIFIED_COLLECTION).document(rns_article_id).set(
            unified_doc, merge=True
        )
    except Exception as e:
        # Dual-write failure must never crash the primary write path
        import logging
        logging.getLogger(__name__).warning(
            "signals_unified dual-write failed for TR-1 %s: %s", rns_article_id[:16], e
        )


def persist_tr1_investor_research(
    rns_article_id: str,
    research_result: dict,
    token_usage: dict,
    db=None,
) -> None:
    """
    Write investor research output to the signals document.

    Called after the investor research layer completes (or fails).
    Sets agentic_status to "complete" on success or "failed" on error.

    Parameters
    ----------
    rns_article_id : str
        The signals document_id.
    research_result : dict
        Parsed output from lens_tr1_investor_research. Contains "_error"
        key if parsing failed.
    token_usage : dict
        {"input": int, "output": int, "model": str}.
    db : Firestore client, optional
    """
    db = db or _get_db()

    status = "failed" if research_result.get("_error") else "complete"

    completed_at = datetime.now(tz=timezone.utc)
    primary_update = {
        "investor_research": research_result,
        "investor_research_completed_at": completed_at,
        "investor_research_token_usage": token_usage,
        "agentic_status": status,
        "agentic_completed_at": completed_at,
    }
    db.collection("signals").document(rns_article_id).set(primary_update, merge=True)

    # Dual-write to signals_unified: update rationale, confidence, and agentic fields
    try:
        unified_update = {
            "agentic_status": status,
            "lens_data.investor_research": research_result,
            "lens_data.investor_research_completed_at": completed_at,
            "lens_data.investor_research_token_usage": token_usage,
            "lens_data.agentic_completed_at": completed_at,
        }
        if not research_result.get("_error"):
            key_reasoning = research_result.get("key_reasoning", "")
            confidence = research_result.get("confidence", "")
            if key_reasoning:
                unified_update["rationale"] = key_reasoning
            if confidence:
                unified_update["confidence_signal"] = confidence
        db.collection(SIGNALS_UNIFIED_COLLECTION).document(rns_article_id).set(
            unified_update, merge=True
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "signals_unified dual-write failed for TR-1 investor research %s: %s",
            rns_article_id[:16], e
        )


# ---------------------------------------------------------------------------
# Stage 2: signals collection — simple lens output (director buying)
# ---------------------------------------------------------------------------

def persist_simple_lens_result(
    rns_article_id: str,
    ticker: str,
    company_name: str,
    published_at,
    signal_type: str,
    simple_result: dict,
    db=None,
) -> None:
    """
    Create or update the signals document with simple lens output.

    The signals document is keyed on rns_article_id — one document per RNS
    article, shared by both the simple lens and agentic investigation outputs.
    merge=True ensures this call never overwrites agentic fields written later.

    Parameters
    ----------
    rns_article_id : str
        The signals document_id (deduplication key).
    ticker : str
    company_name : str
    published_at : datetime | str
        Publication timestamp of the RNS filing.
    signal_type : str
        "director_buying" | "director_disposal"
    simple_result : dict
        Parsed output from parse_simple_lens_response(). Expected keys:
        TRANSACTION_NATURE, POSITION_CHANGE_PCT, SIGNAL_STRENGTH,
        SIGNAL_DIRECTION, RECOMMENDED_ACTION, LIMITATIONS, SUMMARY.
    db : Firestore client, optional
    """
    db = db or _get_db()

    doc = {
        # Identity (written on creation, preserved on update via merge)
        "ticker": ticker,
        "company_name": company_name,
        "published_at": published_at,
        "signal_type": signal_type,
        # Simple lens output
        "simple_completed_at": datetime.now(tz=timezone.utc),
        "simple_transaction_nature": simple_result.get("TRANSACTION_NATURE"),
        "simple_position_change_pct": simple_result.get("POSITION_CHANGE_PCT"),
        "simple_signal_strength": simple_result.get("SIGNAL_STRENGTH"),
        "simple_signal_direction": simple_result.get("SIGNAL_DIRECTION"),
        "simple_recommended_action": simple_result.get("RECOMMENDED_ACTION"),
        "simple_limitations": simple_result.get("LIMITATIONS"),
        "simple_summary": simple_result.get("SUMMARY"),
        # Agentic status — pending until Stage 3+ completes
        "agentic_status": "pending",
        # Investor decision — pending until human acts
        "investor_action": "pending",
        # Housekeeping
        "created_at": datetime.now(tz=timezone.utc),
        "expires_at": _expires_at(SIGNAL_TTL_DAYS),
    }

    db.collection("signals").document(rns_article_id).set(doc, merge=True)

    # Dual-write to signals_unified (new unified schema)
    try:
        unified_doc = _build_unified_director_doc(
            rns_article_id=rns_article_id,
            ticker=ticker,
            company_name=company_name,
            published_at=published_at,
            signal_type=signal_type,
            simple_result=simple_result,
        )
        db.collection(SIGNALS_UNIFIED_COLLECTION).document(rns_article_id).set(
            unified_doc, merge=True
        )
    except Exception as e:
        # Dual-write failure must never crash the primary write path
        import logging
        logging.getLogger(__name__).warning(
            "signals_unified dual-write failed for director signal %s: %s",
            rns_article_id[:16], e
        )


# ---------------------------------------------------------------------------
# Stage 3: signals collection — agentic status + layer outputs
# ---------------------------------------------------------------------------

def update_agentic_status(
    rns_article_id: str,
    status: str,
    db=None,
) -> None:
    """
    Update agentic_status on the signals document.

    Called at investigation start ("running") and end ("complete"|"failed"|"partial").

    Parameters
    ----------
    rns_article_id : str
        The signals document_id.
    status : str
        One of: "pending", "running", "complete", "failed", "partial".
    db : Firestore client, optional
    """
    db = db or _get_db()
    db.collection("signals").document(rns_article_id).set(
        {"agentic_status": status},
        merge=True,
    )


def persist_layer_outputs(
    rns_article_id: str,
    layer1_output: Optional[dict],
    layer2_output: Optional[dict],
    layer3_output: Optional[dict],
    db=None,
) -> None:
    """
    Write the three layer agent outputs to the signals document.

    Called after all three layers complete (or fail) in the sequential or
    parallel runner. None values for failed layers are written as-is —
    the synthesis agent handles them as explicit failure markers.

    Parameters
    ----------
    rns_article_id : str
        The signals document_id.
    layer1_output, layer2_output, layer3_output : dict | None
        Parsed JSON output from each layer agent. None if that layer failed.
    db : Firestore client, optional
    """
    db = db or _get_db()
    db.collection("signals").document(rns_article_id).set(
        {
            "agentic_layer1_output": layer1_output,
            "agentic_layer2_output": layer2_output,
            "agentic_layer3_output": layer3_output,
        },
        merge=True,
    )


# ---------------------------------------------------------------------------
# Stage 5: signals collection — synthesis result
# ---------------------------------------------------------------------------

def persist_synthesis_result(
    rns_article_id: str,
    synthesis_result: dict,
    token_usage: dict,
    db=None,
) -> None:
    """
    Write synthesis agent output to the signals document.

    Called after run_synthesis completes. Sets agentic_status to "complete"
    on success or "failed" if the synthesis result contains a parse error.

    Parameters
    ----------
    rns_article_id : str
        The signals document_id.
    synthesis_result : dict
        Parsed synthesis JSON. Contains "_error" key if parse failed.
    token_usage : dict
        Aggregated token usage from aggregate_token_usage().
    db : Firestore client, optional
    """
    db = db or _get_db()

    if "_error" in synthesis_result:
        update = {
            "agentic_status": "failed",
            "agentic_completed_at": datetime.now(tz=timezone.utc),
            "agentic_synthesis_error": synthesis_result.get("_error"),
            "agentic_token_usage": token_usage,
        }
    else:
        update = {
            "agentic_status": "complete",
            "agentic_completed_at": datetime.now(tz=timezone.utc),
            "agentic_recommendation": synthesis_result.get("recommendation"),
            "agentic_justification": synthesis_result.get("recommendation_justification"),
            "agentic_limitations": synthesis_result.get("limitations"),
            "agentic_synthesis_full": synthesis_result,
            "agentic_token_usage": token_usage,
        }

    db.collection("signals").document(rns_article_id).set(update, merge=True)

    # Dual-write to signals_unified: promote agentic recommendation + rationale
    try:
        if "_error" in synthesis_result:
            unified_update = {
                "agentic_status": "failed",
            }
        else:
            agentic_rec_raw = synthesis_result.get("recommendation", "")
            # Map synthesis recommendation to unified values
            _r = (agentic_rec_raw or "").strip().lower()
            if _r in ("investigate further", "investigate"):
                unified_recommendation = "act"
            elif _r == "monitor":
                unified_recommendation = "monitor"
            else:
                unified_recommendation = "ignore"

            unified_update = {
                "agentic_status": "complete",
                "recommendation": unified_recommendation,  # override simple lens recommendation
                "rationale": synthesis_result.get("recommendation_justification", ""),
                "limitations": synthesis_result.get("limitations", ""),
                "lens_data.agentic_recommendation": agentic_rec_raw,
                "lens_data.agentic_justification": synthesis_result.get("recommendation_justification"),
                "lens_data.agentic_limitations": synthesis_result.get("limitations"),
                "lens_data.agentic_synthesis_full": synthesis_result,
                "lens_data.agentic_token_usage": token_usage,
            }
        db.collection(SIGNALS_UNIFIED_COLLECTION).document(rns_article_id).set(
            unified_update, merge=True
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "signals_unified dual-write failed for synthesis %s: %s",
            rns_article_id[:16], e
        )
