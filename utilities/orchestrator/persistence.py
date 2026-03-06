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
# Stage 2: signals collection — simple lens output
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
