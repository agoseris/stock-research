"""
TR-1 Simple Lens — one-shot signal assessment for Significant Shareholder
threshold crossings.

Uses Gemini Flash (free tier) to assess whether a TR-1 crossing represents
a meaningful signal. The simple lens classifies signal strength and direction,
and decides whether to trigger the investor research layer.

The investor identity is the crux of TR-1 analysis — the simple lens cannot
determine this from the filing alone. trigger_investor_research=True delegates
identity classification to lens_tr1_investor_research.

Usage:
    from utilities.orchestrator.lens_tr1_simple import run_tr1_simple_lens

    result = run_tr1_simple_lens(
        rns_article_id="abc123",
        tr1_data=tr1_data_dict,
        company_profile=profile_dict,
        db=db,
    )
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

_UTILITIES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = _UTILITIES_DIR
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# Credentials fallback for local dev
_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds_path or not os.path.exists(_creds_path):
    _frontend_creds = os.path.join(_PROJECT_ROOT, "frontend", "gcp-credentials.json")
    if os.path.exists(_frontend_creds):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _frontend_creds

from utilities.orchestrator.config import ORCHESTRATOR_CONFIG
from utilities.orchestrator.gemini_call import call_gemini
from utilities.orchestrator.persistence import (
    persist_tr1_signal,
    update_agentic_status,
)

logger = logging.getLogger(__name__)

_MODEL = ORCHESTRATOR_CONFIG.get("tr1_simple_lens_model", "gemini-2.0-flash")

# ---------------------------------------------------------------------------
# Known mechanical / passive holders — skip investor research if matched
# ---------------------------------------------------------------------------

_MECHANICAL_KEYWORDS = frozenset([
    "blackrock", "vanguard", "state street", "ssga", "legal & general",
    "l&g", "invesco", "northern trust", "bank of new york", "bny mellon",
    "jp morgan", "jpmorgan", "hsbc", "barclays", "citi", "citigroup",
    "deutsche bank", "ubs", "credit suisse", "nomura",
    "crest nominees", "computershare", "link nominees",
    "euroclear", "clearstream", "dfa", "dimensional",
    "ishares", "spdr", "xtrackers", "lyxor", "amundi index",
])


def _is_mechanical_holder(notifier_name: str) -> bool:
    """Return True if the notifier name matches known custody/index providers."""
    if not notifier_name:
        return False
    name_lower = notifier_name.lower()
    return any(kw in name_lower for kw in _MECHANICAL_KEYWORDS)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_tr1_simple_lens_prompt(tr1_data: dict, company_profile: dict) -> str:
    """
    Build the TR-1 simple lens assessment prompt.

    Parameters
    ----------
    tr1_data : dict
        Extracted TR-1 fields. Expected keys: notifier_name, issuer_name,
        direction, old_holding_pct, new_holding_pct, threshold_crossed,
        nature_of_holding, crossing_date, ticker, company_name.
    company_profile : dict
        Company profile. Expected keys: market_cap_gbp, index_membership.

    Returns
    -------
    str
        Full prompt text for the Gemini Flash call.
    """
    notifier_name = tr1_data.get("notifier_name") or "Unknown"
    issuer_name = tr1_data.get("issuer_name") or tr1_data.get("company_name") or "Unknown"
    ticker = tr1_data.get("ticker") or ""
    direction = tr1_data.get("direction") or "unknown"
    old_pct = tr1_data.get("old_holding_pct")
    new_pct = tr1_data.get("new_holding_pct")
    threshold = tr1_data.get("threshold_crossed")
    nature = tr1_data.get("nature_of_holding") or "Not stated"
    crossing_date = tr1_data.get("crossing_date") or "Not stated"

    old_pct_str = f"{old_pct:.2f}%" if old_pct is not None else "Not stated"
    new_pct_str = f"{new_pct:.2f}%" if new_pct is not None else "Not stated"
    threshold_str = f"{threshold:.0f}%" if threshold is not None else "Unknown"

    market_cap = company_profile.get("market_cap_gbp")
    index = company_profile.get("index_membership") or "Unknown"
    if market_cap is not None:
        try:
            m = float(market_cap)
            if m >= 1_000_000_000:
                market_cap_str = f"£{m / 1_000_000_000:.1f}bn"
            else:
                market_cap_str = f"£{m / 1_000_000:.0f}m"
        except (TypeError, ValueError):
            market_cap_str = str(market_cap)
    else:
        market_cap_str = "Unknown"

    return f"""You are an investment research analyst assessing an LSE Significant \
Shareholder (TR-1) notification.

Your task is to classify the notifier, assess signal strength, and determine \
whether it warrants further investigation.

IMPORTANT: You cannot determine investor identity from the filing alone. \
The trigger_investor_research field delegates that to a separate research \
step. Your assessment must be honest about this limitation.

TR-1 FILING DETAILS:
Notifier (disclosing party): {notifier_name}
Issuer (listed company):     {issuer_name} ({ticker})
Market cap (may be stale):   {market_cap_str}
Index:                       {index}
Direction:                   {direction}
Old holding:                 {old_pct_str}
New holding:                 {new_pct_str}
Threshold crossed:           {threshold_str}
Nature of holding:           {nature}
Crossing date:               {crossing_date}

STEP 1 — NOTIFIER CATEGORY:
Classify the notifier as exactly one of:

- "active_manager": Named fund, asset manager, activist investor, or family
  office with a visible investment mandate. Examples: Saba Capital, Cobas
  Asset Management, Gresham House, Artemis, Fulcrum Asset Management,
  Van Eck, Frasers Group, Aberforth, Herald Investment Management.
  Max signal_strength allowed: "strong". Max recommendation allowed: "Investigate".

- "passive_custody": Custody bank, index fund provider, ETF issuer, or
  passive manager. Examples: BlackRock (index arm), Vanguard, State Street,
  JPMorgan Chase (custody), Deutsche Bank, UBS prime brokerage.
  Max signal_strength allowed: "noise". Max recommendation allowed: "Ignore".

- "named_individual": A real person's name with no corporate wrapper (likely
  a PDMR, major shareholder, or founder). Examples: "John Smith", "Dr. Jane
  Williams". This is not a nominee or SPV.
  Max signal_strength allowed: "strong". Max recommendation allowed: "Investigate".

- "opaque": Nominee company, generic corporate entity, non-specific SPV, or
  any name that does not clearly indicate the beneficial owner's investment
  style. Examples: "Rock Nominees Limited", "XYZ Holdings Ltd", "ABC Capital
  (Nominees)".
  Max signal_strength allowed: "moderate". Max recommendation allowed: "Monitor".

CRITICAL RULE: If you are uncertain whether the notifier is an active investor,
you MUST classify as "opaque". Uncertainty is not evidence of conviction —
opacity should reduce confidence, not leave it unchanged.

STEP 2 — SIGNAL STRENGTH CLASSIFICATION:
Assess signal_strength as exactly one of (subject to the category cap above):

- "strong": Upward crossing. Notifier is an identified active manager or named
  individual. Crossing initiates a new position (old_pct < 3%, new_pct >= 3%)
  or represents a material increment above an existing position.

- "moderate": Upward crossing. Notifier is opaque/unclassified, OR a modest
  increment above an existing position by an active manager.

- "weak": Upward crossing driven by passive mechanics — share issuance,
  corporate action, or nature_of_holding indicates derivative/pledge rather
  than open-market purchase. Or: minimal increment suggesting index rebalancing.

- "noise": Downward crossing caused by corporate action, OR notifier is a
  passive/custody holder.

- "negative": Downward crossing where the notifier is a known active investment
  fund, especially dropping below a threshold or exiting entirely.

STEP 3 — TRIGGER INVESTOR RESEARCH:
Set trigger_investor_research to true when:
  - direction is "upward" AND
  - signal_strength (after applying category cap) is "strong" or "moderate" AND
  - notifier_category is NOT "passive_custody"

Set trigger_investor_research to false for all other cases.

RESPOND IN EXACTLY THIS JSON FORMAT — no preamble, no markdown fences:

{{
  "notifier_category": "active_manager|passive_custody|named_individual|opaque",
  "signal_strength": "strong|moderate|weak|noise|negative",
  "direction": "upward|downward|unknown",
  "threshold_crossed": {threshold if threshold is not None else "null"},
  "recommendation": "Investigate|Monitor|Ignore",
  "rationale": "2-3 sentence explanation including why you chose this notifier category",
  "trigger_investor_research": true,
  "limitations": "what cannot be assessed without knowing investor identity"
}}

Recommendation mapping (after applying category cap):
  strong or moderate + upward → "Investigate"
  weak + upward → "Monitor"
  noise, negative, or downward → "Ignore"
  opaque category cap → maximum "Monitor" (never "Investigate")
  passive_custody category cap → always "Ignore"
"""


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_tr1_simple_lens_response(text: str) -> dict:
    """
    Parse the JSON response from the TR-1 simple lens call.

    Returns a dict with all expected fields. On parse failure, returns a
    safe dict with _parse_error key set.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = [ln for ln in lines if not ln.startswith("```")]
        stripped = "\n".join(lines).strip()

    try:
        result = json.loads(stripped)
    except json.JSONDecodeError:
        # Try extracting JSON block from surrounding text
        m = re.search(r"\{.*\}", stripped, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group(0))
            except json.JSONDecodeError as e:
                return _empty_result(error=f"JSON parse failed: {e}")
        else:
            return _empty_result(error="No JSON found in response")

    _VALID_STRENGTHS = frozenset(
        ["strong", "moderate", "weak", "noise", "negative"]
    )
    _VALID_RECOMMENDATIONS = frozenset(["Investigate", "Monitor", "Ignore"])
    _VALID_CATEGORIES = frozenset(
        ["active_manager", "passive_custody", "named_individual", "opaque"]
    )

    if result.get("signal_strength") not in _VALID_STRENGTHS:
        result["signal_strength"] = "noise"
    if result.get("recommendation") not in _VALID_RECOMMENDATIONS:
        result["recommendation"] = "Ignore"
    if not isinstance(result.get("trigger_investor_research"), bool):
        result["trigger_investor_research"] = False

    # Validate and default notifier_category; enforce caps
    category = result.get("notifier_category")
    if category not in _VALID_CATEGORIES:
        category = "opaque"
    result["notifier_category"] = category

    if category == "passive_custody":
        result["signal_strength"] = "noise"
        result["recommendation"] = "Ignore"
        result["trigger_investor_research"] = False
    elif category == "opaque":
        if result["signal_strength"] == "strong":
            result["signal_strength"] = "moderate"
        if result["recommendation"] == "Investigate":
            result["recommendation"] = "Monitor"

    return result


def _empty_result(error: str = "") -> dict:
    return {
        "notifier_category": "opaque",
        "signal_strength": "noise",
        "direction": "unknown",
        "threshold_crossed": None,
        "recommendation": "Ignore",
        "rationale": "",
        "trigger_investor_research": False,
        "limitations": "",
        "_parse_error": error,
    }


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------

def call_tr1_simple_lens(
    tr1_data: dict,
    company_profile: dict,
) -> tuple[dict, dict]:
    """
    Call Gemini Flash for TR-1 simple lens assessment.

    Returns (result, token_usage). On API failure, returns a safe empty
    result with _parse_error set.
    """
    notifier_name = tr1_data.get("notifier_name") or ""
    if _is_mechanical_holder(notifier_name):
        logger.info(
            "tr1_simple_lens: %s — mechanical holder detected, returning noise",
            tr1_data.get("ticker", ""),
        )
        return {
            "notifier_category": "passive_custody",
            "signal_strength": "noise",
            "direction": tr1_data.get("direction") or "unknown",
            "threshold_crossed": tr1_data.get("threshold_crossed"),
            "recommendation": "Ignore",
            "rationale": f"{notifier_name} is a known custody bank or passive index provider.",
            "trigger_investor_research": False,
            "limitations": "Mechanical holder pre-screening applied — no LLM call made.",
        }, {"input": 0, "output": 0, "model": _MODEL}

    prompt = build_tr1_simple_lens_prompt(tr1_data, company_profile)

    try:
        text, token_usage = call_gemini(prompt, _MODEL)
    except Exception as e:
        logger.error("TR-1 simple lens API call failed: %s", e)
        return _empty_result(error=str(e)), {"input": 0, "output": 0, "model": _MODEL}

    result = parse_tr1_simple_lens_response(text)

    logger.info(
        "tr1_simple_lens: %s [%s] — strength=%s recommendation=%s trigger_research=%s",
        tr1_data.get("ticker", ""),
        notifier_name[:40],
        result.get("signal_strength"),
        result.get("recommendation"),
        result.get("trigger_investor_research"),
    )

    return result, token_usage


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_tr1_simple_lens(
    rns_article_id: str,
    tr1_data: dict,
    company_profile: dict,
    db=None,
) -> dict:
    """
    Full TR-1 simple lens pipeline for one TR-1 crossing.

    Calls Gemini Flash, parses the result, persists the signals document
    (with signal_type="tr1_crossing"), and returns the parsed result dict.

    If trigger_investor_research is False, also sets agentic_status="skipped"
    on the signals doc immediately. If True, leaves status as "pending" for
    run_orchestrator.py to process.

    Parameters
    ----------
    rns_article_id : str
        The announcements document_id (= signals doc_id).
    tr1_data : dict
        Extracted TR-1 fields. Must include ticker, company_name.
    company_profile : dict
        Company profile from universe_companies. Pass {} if unavailable.
    db : Firestore client, optional

    Returns
    -------
    dict
        Parsed simple lens result (signal_strength, direction, recommendation,
        rationale, trigger_investor_research, limitations).
    """
    result, _token_usage = call_tr1_simple_lens(tr1_data, company_profile)

    persist_tr1_signal(
        rns_article_id=rns_article_id,
        ticker=tr1_data.get("ticker", ""),
        company_name=tr1_data.get("company_name", ""),
        published_at=tr1_data.get("crossing_date"),
        tr1_data=tr1_data,
        simple_result=result,
        db=db,
    )

    if not result.get("trigger_investor_research", False):
        update_agentic_status(rns_article_id, "skipped", db=db)

    return result
