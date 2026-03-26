"""
Stage 2: lens_director_simple — one-shot baseline director signal assessment.

A single Gemini Flash call producing a structured assessment of an open-market
director transaction. Analogous in structure to lens_catalyst. Runs before the
agentic investigation and provides the baseline against which it is measured.

The LIMITATIONS field is the most important output: it explicitly states what
cannot be assessed from transaction data alone, which directly maps to what the
agentic investigation addresses.

Usage:
    from utilities.orchestrator.simple_lens import run_simple_lens

    result, token_usage = run_simple_lens(
        rns_article_id="abc123",
        transaction=transaction_dict,
        company_profile=profile_dict,
        db=db,
        config=config,
    )
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from utilities.orchestrator.config import ORCHESTRATOR_CONFIG
from utilities.orchestrator.gemini_call import call_gemini
from utilities.orchestrator.persistence import persist_simple_lens_result

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known response field labels (in output order from the prompt)
# ---------------------------------------------------------------------------

_FIELD_LABELS = [
    "TRANSACTION_NATURE",
    "POSITION_CHANGE_PCT",
    "COMMITMENT_SIZE",
    "HOLDING_SIGNIFICANCE",
    "SIGNAL_STRENGTH",
    "SIGNAL_DIRECTION",
    "REPORTING_LAG_ASSESSMENT",
    "LIMITATIONS",
    "RECOMMENDED_ACTION",
    "SUMMARY",
]

_LABEL_PATTERN = re.compile(
    r"^(" + "|".join(_FIELD_LABELS) + r"):\s*(.*)",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_simple_lens_prompt(transaction: dict, company_profile: dict) -> str:
    """
    Build the lens_director_simple assessment prompt.

    Parameters
    ----------
    transaction : dict
        Extracted transaction fields from pdmr_transactions. Expected keys:
        company_name, ticker, director_name, director_role, transaction_category,
        transaction_date_actual, transaction_date_reported, reporting_lag_days,
        shares_transacted, price_per_share_gbp, total_consideration_gbp,
        previous_holding, resulting_holding, resulting_holding_pct,
        extraction_confidence, confidence_notes.
    company_profile : dict
        Company profile from get_company_profile() or universe_companies.
        Expected keys: market_cap_gbp, market_cap_stale, index_membership.
        Missing fields default to "Unknown".

    Returns
    -------
    str
        Full prompt text for the LLM call.
    """
    # Transaction fields
    company_name = transaction.get("company_name", "")
    ticker = transaction.get("ticker", "")
    director_name = transaction.get("director_name", "")
    director_role = transaction.get("director_role", "")
    transaction_category = transaction.get("transaction_category", "")
    transaction_date = transaction.get("transaction_date_actual", "")
    reporting_date = transaction.get("transaction_date_reported", "")
    reporting_lag = transaction.get("reporting_lag_days", "")
    shares = transaction.get("shares_transacted", "")
    price_gbp = transaction.get("price_per_share_gbp", "")
    consideration = transaction.get("total_consideration_gbp", "")
    prev_holding = transaction.get("previous_holding", "")
    result_holding = transaction.get("resulting_holding", "")
    result_pct = transaction.get("resulting_holding_pct", "")
    confidence = transaction.get("extraction_confidence", "")
    confidence_notes = transaction.get("confidence_notes", "")

    # Company profile fields
    market_cap = company_profile.get("market_cap_gbp", "Unknown")
    market_cap_stale = company_profile.get("market_cap_stale", "Unknown")
    index_membership = company_profile.get("index_membership", "Unknown")

    # Format market cap for readability
    market_cap_display = _format_market_cap(market_cap)

    return f"""You are an investment research analyst specialising in \
LSE-listed small-cap companies.

Your task is to assess a director or PDMR open market share transaction \
and determine whether it represents a meaningful signal worthy of \
further investigation.

IMPORTANT: You have access only to the details of this specific \
transaction and basic company information. You do not have access to \
price history, director transaction history, or live market data. \
Your assessment must be honest about these limitations — the \
LIMITATIONS field is as important as the recommendation itself.

COMPANY INFORMATION:
Company: {company_name}
Ticker: {ticker}
Market Cap (may be stale): {market_cap_display}
Market Cap Stale: {market_cap_stale}
Index: {index_membership}

TRANSACTION DETAILS:
Director/PDMR: {director_name}
Role: {director_role}
Transaction Category: {transaction_category}
Transaction Date: {transaction_date}
Reporting Date: {reporting_date}
Reporting Lag (days): {reporting_lag}
Shares Transacted: {shares}
Price Per Share (GBP): {price_gbp}
Total Consideration (GBP): {consideration}
Previous Holding: {prev_holding}
Resulting Holding: {result_holding}
Resulting Holding (% of issued capital): {result_pct}
Extraction Confidence: {confidence}
Extraction Notes: {confidence_notes}

ASSESSMENT INSTRUCTIONS:

1. TRANSACTION_NATURE
   Classify as exactly one of:
   - First entry: previous holding was zero — director investing \
for the first time. Strong conviction signal.
   - Position increase: director adding to existing holding. \
Note relative size of addition.
   - Position decrease: partial disposal. Note proportion sold.
   - Complete exit: resulting holding is zero or near-zero. \
Strong negative signal.
   - Other: explain

2. POSITION_CHANGE_PCT
   Calculate the percentage change in holding this transaction \
represents relative to previous holding.
   If previous holding is zero, state "New position".
   Show your calculation.

3. COMMITMENT_SIZE
   Assess the significance of the total consideration in context.
   A director of a £20m market cap company spending £50,000 is \
more significant than the same amount at a £500m company.
   Is this a token gesture or a material financial commitment?

4. HOLDING_SIGNIFICANCE
   Assess the resulting holding percentage. Does the director \
now have a meaningful personal financial stake in the company?
   Consider both absolute value and percentage of issued capital.

5. SIGNAL_STRENGTH
   Score 1-10:
   - First entry with material consideration: 7-9
   - Large position increase (>20% of existing holding): 6-8
   - Small position increase (<5% of existing holding): 3-5
   - Token purchase, plan-driven, or very small consideration: 1-3
   - Partial disposal: 2-4
   - Complete exit: 1-2
   Adjust for reporting lag: > 30 days lag reduces score by 1-2 points.
   Adjust for extraction confidence: low confidence reduces score by 1.

6. SIGNAL_DIRECTION
   - Positive: purchase or first entry
   - Negative: disposal or exit
   - Neutral: ambiguous, token size, or insufficient information

7. REPORTING_LAG_ASSESSMENT
   If reporting lag is within 0-5 days: "Within normal range"
   If 6-30 days: note explicitly — moderately affects freshness
   If > 30 days: flag prominently — materially affects signal \
freshness. The market may already have reacted.

8. LIMITATIONS
   List explicitly and specifically what cannot be assessed from \
this transaction alone. Be precise — this field directly informs \
what the agentic investigation will address.

   Always include:
   - Whether current share price represents good or poor value
   - Whether other insiders are moving in the same direction
   - Whether the company is a suitable platform (liquidity, \
volatility, spread)
   - Whether this signal has already been priced in by the market
   - Director's track record and credibility \
(unless role/holding makes this less relevant)

   Add any additional limitations specific to this transaction.

9. RECOMMENDED_ACTION
   - Investigate further: warrants deeper analysis — \
signal is meaningful and direction is positive
   - Monitor: mildly interesting — watch for corroborating signals \
before acting
   - Ignore: insufficient signal, noise, very small consideration, \
or negative signal

10. SUMMARY
    2-3 sentences. Lead with the single most important factor \
driving your recommendation. Be direct.

Respond in exactly this format:
TRANSACTION_NATURE: [classification]
POSITION_CHANGE_PCT: [percentage or "New position"] — [calculation]
COMMITMENT_SIZE: [assessment]
HOLDING_SIGNIFICANCE: [assessment]
SIGNAL_STRENGTH: [score]/10 — [brief justification]
SIGNAL_DIRECTION: [Positive/Negative/Neutral]
REPORTING_LAG_ASSESSMENT: [assessment]
LIMITATIONS: [explicit numbered list]
RECOMMENDED_ACTION: [Investigate further/Monitor/Ignore]
SUMMARY: [2-3 sentences]"""


def _format_market_cap(market_cap) -> str:
    """Format raw GBP market cap value for display in prompt."""
    if market_cap is None or market_cap == "Unknown":
        return "Unknown"
    try:
        m = float(market_cap)
        if m >= 1_000_000_000:
            return f"£{m / 1_000_000_000:.1f}bn"
        return f"£{m / 1_000_000:.0f}m"
    except (TypeError, ValueError):
        return str(market_cap)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_simple_lens_response(text: str) -> dict:
    """
    Parse the labeled-field response from lens_director_simple.

    Extracts the ten labeled fields from the structured response text.
    Multi-line fields (LIMITATIONS, SUMMARY) are fully captured.
    Returns a dict with all field values plus post-processed helpers
    (SIGNAL_STRENGTH as int, POSITION_CHANGE_PCT as float or None).

    On partial or malformed responses, missing fields default to empty string
    rather than raising. This keeps the pipeline running; thin output is
    surfaced as data quality flags upstream.

    Parameters
    ----------
    text : str
        Raw LLM response text.

    Returns
    -------
    dict
        Keys: TRANSACTION_NATURE, POSITION_CHANGE_PCT (float|None),
        COMMITMENT_SIZE, HOLDING_SIGNIFICANCE, SIGNAL_STRENGTH (int|None),
        SIGNAL_DIRECTION, REPORTING_LAG_ASSESSMENT, LIMITATIONS,
        RECOMMENDED_ACTION, SUMMARY, _raw (dict of unprocessed strings).
    """
    matches = list(_LABEL_PATTERN.finditer(text))

    raw: dict[str, str] = {}
    for i, m in enumerate(matches):
        label = m.group(1)
        first_line = m.group(2).strip()

        # Gather any continuation lines until the next labelled field
        if i + 1 < len(matches):
            continuation = text[m.end() : matches[i + 1].start()].strip()
        else:
            continuation = text[m.end() :].strip()

        raw[label] = (first_line + ("\n" + continuation if continuation else "")).strip()

    return {
        "TRANSACTION_NATURE": raw.get("TRANSACTION_NATURE", ""),
        "POSITION_CHANGE_PCT": _parse_position_change(raw.get("POSITION_CHANGE_PCT", "")),
        "COMMITMENT_SIZE": raw.get("COMMITMENT_SIZE", ""),
        "HOLDING_SIGNIFICANCE": raw.get("HOLDING_SIGNIFICANCE", ""),
        "SIGNAL_STRENGTH": _parse_signal_strength(raw.get("SIGNAL_STRENGTH", "")),
        "SIGNAL_DIRECTION": raw.get("SIGNAL_DIRECTION", ""),
        "REPORTING_LAG_ASSESSMENT": raw.get("REPORTING_LAG_ASSESSMENT", ""),
        "LIMITATIONS": raw.get("LIMITATIONS", ""),
        "RECOMMENDED_ACTION": raw.get("RECOMMENDED_ACTION", ""),
        "SUMMARY": raw.get("SUMMARY", ""),
        "_raw": raw,
    }


def _parse_signal_strength(text: str) -> Optional[int]:
    """Extract integer score from '7/10 — justification' → 7."""
    m = re.match(r"(\d+)\s*/\s*10", text.strip())
    if m:
        val = int(m.group(1))
        return max(1, min(10, val))  # clamp to valid range
    return None


def _parse_position_change(text: str) -> Optional[float]:
    """
    Extract float from '25% — calculation' → 25.0.
    Returns None for 'New position' or unparseable values.
    """
    if not text or "new position" in text.lower():
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", text)
    if m:
        return float(m.group(1))
    return None


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def call_simple_lens(
    transaction: dict,
    company_profile: dict,
    config: Optional[dict] = None,
) -> tuple[dict, dict]:
    """
    Call lens_director_simple for a single open-market transaction.

    Parameters
    ----------
    transaction : dict
        Transaction fields from pdmr_transactions.
    company_profile : dict
        Company profile from get_company_profile() or universe_companies.
        Pass {} if not available — prompt will show "Unknown" for missing fields.
    config : dict, optional
        Orchestrator config. Uses ORCHESTRATOR_CONFIG defaults if None.

    Returns
    -------
    (result, token_usage) : tuple[dict, dict]
        result — parsed simple lens output dict. On API/parse failure:
        all fields are empty string / None, with _parse_error key set.
        token_usage — {"input": int, "output": int, "model": str}.
    """
    cfg = config or ORCHESTRATOR_CONFIG
    model = cfg["simple_lens_model"]
    prompt = build_simple_lens_prompt(transaction, company_profile)

    try:
        text, token_usage = call_gemini(prompt, model)
    except Exception as e:
        logger.error("Simple lens API call failed: %s", e)
        return _empty_result(error=str(e)), {"input": 0, "output": 0, "model": model}

    result = parse_simple_lens_response(text)

    logger.info(
        "simple_lens: %s %s — strength=%s direction=%s action=%s",
        transaction.get("ticker", ""),
        transaction.get("director_name", ""),
        result.get("SIGNAL_STRENGTH"),
        result.get("SIGNAL_DIRECTION"),
        result.get("RECOMMENDED_ACTION"),
    )

    return result, token_usage


def _empty_result(error: str = "") -> dict:
    """Return a safe empty result when the simple lens fails."""
    return {
        "TRANSACTION_NATURE": "",
        "POSITION_CHANGE_PCT": None,
        "COMMITMENT_SIZE": "",
        "HOLDING_SIGNIFICANCE": "",
        "SIGNAL_STRENGTH": None,
        "SIGNAL_DIRECTION": "",
        "REPORTING_LAG_ASSESSMENT": "",
        "LIMITATIONS": "",
        "RECOMMENDED_ACTION": "",
        "SUMMARY": "",
        "_raw": {},
        "_parse_error": error,
    }


# ---------------------------------------------------------------------------
# Main entry point for Stage 2
# ---------------------------------------------------------------------------

def run_simple_lens(
    rns_article_id: str,
    transaction: dict,
    company_profile: dict,
    db=None,
    config: Optional[dict] = None,
) -> tuple[dict, dict]:
    """
    Full Stage 2 pipeline for one open-market transaction.

    Calls the simple lens LLM, parses the response, persists all simple_*
    fields to the signals Firestore document (keyed on rns_article_id),
    and returns the result for the caller.

    Parameters
    ----------
    rns_article_id : str
        The announcements document_id — used as the signals document_id
        (deduplication key: one signals doc per RNS article).
    transaction : dict
        Transaction fields from pdmr_transactions. Must include ticker,
        company_name, transaction_category, transaction_date_reported.
    company_profile : dict
        Company profile from get_company_profile(). Pass {} if unavailable.
    db : Firestore client, optional
    config : dict, optional

    Returns
    -------
    (result, token_usage) : tuple[dict, dict]
        result — parsed simple lens output dict.
        token_usage — {"input": int, "output": int, "model": str}.

    Caller responsibilities
    -----------------------
    After this call, agentic_status on the signals document is "pending".
    The caller proceeds to run the agentic investigation (Stage 3+) and
    update the document when complete.
    """
    result, token_usage = call_simple_lens(transaction, company_profile, config)

    signal_type = (
        "director_buying"
        if transaction.get("transaction_category") == "open_market_purchase"
        else "director_disposal"
    )

    persist_simple_lens_result(
        rns_article_id=rns_article_id,
        ticker=transaction.get("ticker", ""),
        company_name=transaction.get("company_name", ""),
        published_at=transaction.get("transaction_date_reported"),
        signal_type=signal_type,
        simple_result=result,
        db=db,
    )

    return result, token_usage
