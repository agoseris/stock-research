"""
Stage 1: Classification and extraction call.

For every candidate article that passes Phase 1 (headline + universe filtering),
this module:
  1. Builds the classification + extraction prompt (full body text — not headline only)
  2. Calls the classification LLM (Claude Sonnet)
  3. Parses the JSON response
  4. Persists the classification back to the announcements document
  5. Routes to the appropriate downstream action

Routing rules (priority order):
  Priority 1 — pdmr_transaction       → persist transactions, trigger lenses for open-market
  Priority 2 — regulatory_catalyst    → trigger existing lens_catalyst
  Priority 3 — substantive_news       → persist summary to company_news_summaries
  Priority 4 — administrative         → update classification only, no child documents

Usage:
    from utilities.orchestrator.classification import classify_article
    result = classify_article(announcement, client, db=db, config=config)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from utilities.orchestrator.config import ORCHESTRATOR_CONFIG
from utilities.orchestrator.gemini_call import call_gemini
from utilities.orchestrator.persistence import (
    update_announcement_classification,
    persist_pdmr_transaction,
    persist_news_summary,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_classification_prompt(announcement: dict) -> str:
    """
    Build the classification + extraction prompt for a single article.

    Parameters
    ----------
    announcement : dict
        Must contain: company_name, ticker, source_name, headline, body.
        All other fields are optional and ignored.

    Returns
    -------
    str
        The full prompt text ready to send to the LLM.
    """
    company_name = announcement.get("company_name", "")
    ticker = announcement.get("ticker", "")
    source_name = announcement.get("source_name", "")
    headline = announcement.get("headline", "")
    body = announcement.get("body", "")

    return f"""You are a financial document classifier and data extractor \
specialising in LSE regulatory announcements.

Your task is to classify this announcement and extract structured data \
from it in a single response.

ANNOUNCEMENT:
Company: {company_name}
Ticker: {ticker}
Source: {source_name}
Headline: {headline}
Body: {body}

STEP 1 — CLASSIFY

You are classifying based on the FULL body text of the article,
not the headline alone. Read the complete content before deciding.

Classify this article as exactly one of:
- pdmr_transaction: A director or PDMR share transaction notification
- tr1_crossing: A Significant Shareholder notification disclosing a \
voting rights threshold crossing (TR-1 standard form). The RNS \
headline typically begins "TR-1:" or is titled "Holding(s) in \
Company". The body follows the standardised TR-1 form and discloses \
the notifier name, issuer name, old and new percentage holdings, and \
the date of the triggering transaction.
- regulatory_catalyst: Regulatory approval, planning permission, \
licence, permit, or related catalyst event
- substantive_news: Trading update, financial results, operational \
news, fundraising, prospecting results, or other material news
- administrative: Meeting notices, date changes, routine filings, \
director appointments/resignations with no transaction, \
or other non-material announcements

CLASSIFICATION PRIORITY RULES:

1. pdmr_transaction takes priority over all other types.
   If the article contains a director or PDMR share transaction,
   classify as pdmr_transaction regardless of other content.
   A filing that is both a TR-1 and a director transaction is
   classified as pdmr_transaction.

2. tr1_crossing is the second priority.
   If the headline begins "TR-1:" or "Holding(s) in Company" AND
   the body discloses a voting rights percentage threshold crossing,
   classify as tr1_crossing. These are NOT director transactions
   and must NOT be classified as administrative.

3. regulatory_catalyst takes priority over substantive_news.
   If the article contains a clear regulatory catalyst event —
   approval, licence, permit, consent, rejection, or similar —
   AND other substantive content, classify as regulatory_catalyst.
   A regulatory catalyst buried in a trading update is still a
   regulatory_catalyst. The presence of surrounding narrative
   does not demote it to substantive_news.

4. substantive_news only if no higher-priority content is present.
   Classify as substantive_news only when the article contains
   no director transaction, no TR-1 crossing, and no regulatory
   catalyst event.

5. administrative only if the content is genuinely non-material.
   Routine filings, process announcements, and housekeeping notices
   with no transaction, catalyst, TR-1 crossing, or substantive
   operational content.

KNOWN LIMITATION — COMBINED FILINGS:
This prompt returns a single article_type. A filing that genuinely
contains both a regulatory catalyst and substantive news will be
classified as regulatory_catalyst (per priority rule 3). The
substantive news summary will not be stored in this case. This is
an accepted PoC limitation — the more important signal (catalyst)
is preserved. This limitation is pinned for future handling.

STEP 2 — EXTRACT TRANSACTIONS (only if pdmr_transaction)

If article_type is pdmr_transaction, extract ALL transactions present.
A single filing may contain multiple directors and multiple transaction
dates — extract each as a separate transaction object.

MIXED-TYPE FILINGS:
A single filing may contain transactions of different categories (e.g.
open-market purchases AND share award vestings for the same or different
directors). Extract EVERY distinct transaction as its own object —
do not merge or omit transactions because they differ in category.

If a filing announces open-market purchases but also mentions prior
award vestings or option exercises (e.g. to explain how the resulting
holding was reached), extract each as a separate transaction object
with its own date, share count, price, and category.

If vesting or award details are cited purely as background context —
with no transaction date, no consideration, and no separate RNS
disclosure — do NOT extract them as transactions. Record them in
confidence_notes on the relevant purchase transaction instead.

Category priority rule: if the filing headline or opening section
describes purchases or acquisitions, expect open_market_purchase
transactions to be present. Do not reclassify them as award or
vesting transactions on the basis of surrounding context.

For each transaction, extract:
  director_name: Full name as stated
  director_role: Role/position as stated
  transaction_type: "purchase" | "disposal" | "award" | "other"
  transaction_category: Classify as exactly one of:
    - open_market_purchase: Discretionary open market buy
    - open_market_disposal: Discretionary open market sell
    - drip_purchase: Dividend reinvestment plan purchase
    - sip_purchase: Share incentive plan purchase
    - sip_award: Share incentive plan award (granted at £0)
    - option_exercise: Exercise of share options
    - share_award_vesting: Vesting of share awards
    - other: Anything not fitting above — explain in confidence_notes
  transaction_date_actual: Date transaction occurred (YYYY-MM-DD)
  transaction_date_reported: Date of this RNS filing (YYYY-MM-DD)
  reporting_lag_days: Integer days between actual and reported
  shares_transacted: Number of shares (float — may be fractional)
  price_per_share_raw: Price exactly as stated in the document
  price_per_share_gbp: Price normalised to GBP pounds (not pence)
    - If stated in pence: divide by 100
    - If stated in USD: convert using approximate rate, flag in notes
    - If award at £0: set 0.00
  total_consideration_gbp: Total value in GBP pounds
  currency_original: Currency as stated (e.g. "GBP", "USD")
  previous_holding: Shares held before transaction (float)
  resulting_holding: Shares held after transaction (float)
  resulting_holding_pct: Percentage of issued capital after (float)
  isin: ISIN code if present
  lei: LEI code if present
  exchange: Exchange name as stated
  plan_name: Name of share plan if applicable, null otherwise
  extraction_confidence: "high" | "medium" | "low"
  confidence_notes: Note any anomalies, ambiguities, or data quality
    issues. Examples:
    - "Price stated in pence, divided by 100 to normalise to GBP"
    - "Fractional shares — dividend reinvestment transaction"
    - "Price expressed in USD, approximate GBP conversion applied"
    - "Previous holding not stated — set to null"
    - "Double 'pp' typo in source document for price field"
    - "Reporting lag of X days — transaction occurred [date]"

STEP 2b — EXTRACT TR-1 DATA (only if tr1_crossing)

If article_type is tr1_crossing, extract:
  tr1_notifier_name: Name of the disclosing party (the shareholder,
    not the listed company)
  tr1_issuer_name: Name of the listed company in which the interest
    is held
  tr1_old_holding_pct: Holding percentage BEFORE the triggering
    transaction (float or null if not stated)
  tr1_new_holding_pct: Holding percentage AFTER the triggering
    transaction (float or null if not stated)
  tr1_direction: "upward" if new_pct > old_pct;
    "downward" if new_pct < old_pct; "unknown" otherwise
  tr1_threshold_crossed: The specific threshold crossed as a float
    (3, 5, 10, 15, 20, 25, or 30) — or null if cannot be determined
  tr1_nature_of_holding: Nature and quality of the holding as stated
    in the filing (e.g. "Ordinary shares", "Financial instruments",
    "Combination of shares and financial instruments"). Quote directly
    from the filing if possible.
  tr1_crossing_date: Date of the triggering transaction (YYYY-MM-DD
    or null)

For multi-issuer TR-1 filings (group-level disclosures covering
holdings in multiple companies): extract data for the primary or
first issuer only and note this in tr1_nature_of_holding.

If article_type is not tr1_crossing, set all tr1_* fields to null.

STEP 3 — SUMMARISE

Provide a 2-3 sentence summary appropriate to the article type:
- pdmr_transaction: Who transacted, what they did, resulting position
- regulatory_catalyst: What event, what decision, what outcome
- substantive_news: Key facts, figures, and sentiment
- administrative: One sentence only

For substantive_news also provide:
  sentiment: "positive" | "neutral" | "negative"
  key_topics: List of 2-5 topic tags (e.g. ["drilling results",
    "funding", "regulatory approval", "trading update"])
  article_subtype: "trading_update" | "results" | "operational" |
    "fundraising" | "regulatory" | "other"

RESPOND IN EXACTLY THIS JSON FORMAT — no preamble, no markdown fences:

{{
  "article_type": "",
  "summary": "",
  "sentiment": null,
  "key_topics": null,
  "article_subtype": null,
  "transactions": [
    {{
      "director_name": "",
      "director_role": "",
      "transaction_type": "",
      "transaction_category": "",
      "transaction_date_actual": "",
      "transaction_date_reported": "",
      "reporting_lag_days": 0,
      "shares_transacted": 0.0,
      "price_per_share_raw": "",
      "price_per_share_gbp": 0.0,
      "total_consideration_gbp": 0.0,
      "currency_original": "",
      "previous_holding": 0.0,
      "resulting_holding": 0.0,
      "resulting_holding_pct": 0.0,
      "isin": "",
      "lei": "",
      "exchange": "",
      "plan_name": null,
      "extraction_confidence": "",
      "confidence_notes": ""
    }}
  ],
  "tr1_notifier_name": null,
  "tr1_issuer_name": null,
  "tr1_old_holding_pct": null,
  "tr1_new_holding_pct": null,
  "tr1_direction": null,
  "tr1_threshold_crossed": null,
  "tr1_nature_of_holding": null,
  "tr1_crossing_date": null
}}

If article_type is not pdmr_transaction, set transactions to an \
empty array [].
If article_type is not tr1_crossing, set all tr1_* fields to null.
If sentiment, key_topics, or article_subtype are not applicable \
to the article_type, set to null."""


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_VALID_ARTICLE_TYPES = frozenset(
    ["pdmr_transaction", "tr1_crossing", "regulatory_catalyst",
     "substantive_news", "administrative"]
)

_OPEN_MARKET_CATEGORIES = frozenset(
    ["open_market_purchase", "open_market_disposal"]
)


def parse_classification_response(text: str) -> dict:
    """
    Parse the JSON response from the classification LLM call.

    On success: returns the parsed dict with extraction_status="complete".
    On JSON parse failure or missing article_type: returns a safe fallback
    dict with extraction_status="failed" and article_type="administrative".

    Parameters
    ----------
    text : str
        Raw text from LLM response (should be bare JSON, no markdown fences).

    Returns
    -------
    dict
        Parsed and validated classification result. Always has keys:
        article_type, extraction_status, summary, transactions.
    """
    # Strip markdown fences if the model adds them despite instructions
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Remove opening and closing fence lines
        lines = [l for l in lines if not l.startswith("```")]
        stripped = "\n".join(lines).strip()

    try:
        result = json.loads(stripped)
    except json.JSONDecodeError as e:
        logger.warning("Classification JSON parse failed: %s", e)
        return _failed_result(raw_response=text)

    article_type = result.get("article_type", "")
    if article_type not in _VALID_ARTICLE_TYPES:
        logger.warning(
            "Unrecognised article_type %r — treating as administrative", article_type
        )
        result["article_type"] = "administrative"

    result["extraction_status"] = "complete"

    # Ensure transactions is always a list
    if not isinstance(result.get("transactions"), list):
        result["transactions"] = []

    return result


def _failed_result(raw_response: str = "") -> dict:
    """Return a safe fallback result when classification fails."""
    return {
        "article_type": "administrative",
        "extraction_status": "failed",
        "summary": None,
        "sentiment": None,
        "key_topics": None,
        "article_subtype": None,
        "transactions": [],
        "_raw_response": raw_response,
    }


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def call_classification(
    announcement: dict,
    config: Optional[dict] = None,
) -> tuple[dict, dict]:
    """
    Call the classification and extraction LLM for a single announcement.

    Parameters
    ----------
    announcement : dict
        Must contain company_name, ticker, source_name, headline, body.
    config : dict, optional
        Orchestrator config dict. Uses ORCHESTRATOR_CONFIG defaults if None.

    Returns
    -------
    (result, token_usage) : tuple[dict, dict]
        result — parsed classification dict (always returned, even on failure).
        token_usage — {"input": int, "output": int, "model": str}.
        On API error: result has extraction_status="failed", token_usage zeros.
    """
    cfg = config or ORCHESTRATOR_CONFIG
    model = cfg["classification_model"]
    prompt = build_classification_prompt(announcement)

    try:
        text, token_usage = call_gemini(prompt, model)
    except Exception as e:
        logger.error("Classification API call failed: %s", e)
        return _failed_result(), {"input": 0, "output": 0, "model": model}

    result = parse_classification_response(text)

    return result, token_usage


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route_classification(
    rns_article_id: str,
    announcement: dict,
    classification: dict,
    db=None,
) -> str:
    """
    Persist classification results and return the routing decision.

    Handles all Firestore writes triggered by a classification result:
    - Always: updates the announcements document with article_type +
      extraction_status
    - pdmr_transaction: creates pdmr_transactions documents (one per tx)
    - substantive_news: creates a company_news_summaries document

    Does NOT trigger the orchestrator or lens_catalyst — the caller is
    responsible for acting on the returned article_type.

    Parameters
    ----------
    rns_article_id : str
        The announcements document_id (deduplication key).
    announcement : dict
        Original announcement dict (must contain ticker, company_name,
        published_at).
    classification : dict
        Parsed classification result from parse_classification_response().
    db : Firestore client, optional

    Returns
    -------
    str
        The article_type string for the caller to act on:
        "pdmr_transaction" | "tr1_crossing" | "regulatory_catalyst" |
        "substantive_news" | "administrative"

    Side effects
    ------------
    classification dict is mutated: "persisted_transaction_ids" key is added
    for pdmr_transaction articles (list of Firestore doc IDs created).
    """
    article_type = classification.get("article_type", "administrative")
    extraction_status = classification.get("extraction_status", "complete")
    ticker = announcement.get("ticker", "")
    company_name = announcement.get("company_name", "")
    published_at = announcement.get("published_at")

    # Always: update the base announcements record
    update_announcement_classification(
        rns_article_id=rns_article_id,
        article_type=article_type,
        extraction_status=extraction_status,
        summary=classification.get("summary"),
        db=db,
    )

    if article_type == "pdmr_transaction":
        tx_ids = []
        for tx in classification.get("transactions", []):
            doc_id = persist_pdmr_transaction(
                rns_article_id=rns_article_id,
                ticker=ticker,
                company_name=company_name,
                transaction=tx,
                db=db,
            )
            tx_ids.append(doc_id)
        classification["persisted_transaction_ids"] = tx_ids
        logger.info(
            "classification: pdmr_transaction — %d transaction(s) persisted for %s",
            len(tx_ids),
            ticker,
        )

    elif article_type == "substantive_news":
        persist_news_summary(
            rns_article_id=rns_article_id,
            ticker=ticker,
            published_at=published_at,
            classification=classification,
            db=db,
        )
        logger.info("classification: substantive_news summary persisted for %s", ticker)

    elif article_type == "tr1_crossing":
        logger.info(
            "classification: tr1_crossing for %s — notifier=%s direction=%s",
            ticker,
            classification.get("tr1_notifier_name"),
            classification.get("tr1_direction"),
        )

    elif article_type == "regulatory_catalyst":
        logger.info(
            "classification: regulatory_catalyst for %s — route to lens_catalyst",
            ticker,
        )

    else:  # administrative or unclassified
        logger.info("classification: %s for %s — no child documents", article_type, ticker)

    return article_type


# ---------------------------------------------------------------------------
# Convenience: combined classify + route
# ---------------------------------------------------------------------------

def classify_article(
    rns_article_id: str,
    announcement: dict,
    db=None,
    config: Optional[dict] = None,
) -> tuple[dict, dict]:
    """
    Full Stage 1 pipeline for one article: classify, persist, route.

    Parameters
    ----------
    rns_article_id : str
        The announcements document_id.
    announcement : dict
        Must contain: company_name, ticker, source_name, headline, body,
        published_at.
    db : Firestore client, optional
    config : dict, optional

    Returns
    -------
    (classification, token_usage) : tuple[dict, dict]
        classification — enriched result dict after routing. Includes
        article_type, extraction_status, transactions (with persisted doc IDs),
        and routing metadata.
        token_usage — {"input": int, "output": int, "model": str}.

    Caller responsibilities
    -----------------------
    - For pdmr_transaction: iterate classification["transactions"] and call
      the orchestrator for those with open_market category.
    - For tr1_crossing: call get_tr1_data(classification) and route to
      the TR-1 flow (lens_tr1_simple + investor research).
    - For regulatory_catalyst: call existing lens_catalyst.
    - For substantive_news / administrative: nothing further required.
    """
    classification, token_usage = call_classification(announcement, config)

    article_type = route_classification(
        rns_article_id=rns_article_id,
        announcement=announcement,
        classification=classification,
        db=db,
    )

    classification["routed_as"] = article_type
    return classification, token_usage


# ---------------------------------------------------------------------------
# Helper: identify transactions that should trigger the director lenses
# ---------------------------------------------------------------------------

def get_tr1_data(classification: dict) -> dict:
    """
    Extract the TR-1 crossing data from a tr1_crossing classification result.

    Returns a flat dict with clean key names (without the tr1_ prefix)
    suitable for passing to the TR-1 simple lens and persistence functions.
    """
    return {
        "notifier_name": classification.get("tr1_notifier_name"),
        "issuer_name": classification.get("tr1_issuer_name"),
        "old_holding_pct": classification.get("tr1_old_holding_pct"),
        "new_holding_pct": classification.get("tr1_new_holding_pct"),
        "direction": classification.get("tr1_direction"),
        "threshold_crossed": classification.get("tr1_threshold_crossed"),
        "nature_of_holding": classification.get("tr1_nature_of_holding"),
        "crossing_date": classification.get("tr1_crossing_date"),
    }


def get_open_market_transactions(classification: dict) -> list:
    """
    Return only the transactions that should trigger lens_director_simple
    and lens_director_agentic.

    Filters to open_market_purchase and open_market_disposal only.
    All other categories (sip_purchase, drip_purchase, option_exercise, etc.)
    are stored in pdmr_transactions but do not trigger the lenses.
    """
    return [
        tx for tx in classification.get("transactions", [])
        if tx.get("transaction_category") in _OPEN_MARKET_CATEGORIES
    ]
