"""
TR-1 Investor Research Layer — Gemini Flash with Google Search grounding.

Classifies the TR-1 notifier and assesses whether the accumulation is
conviction-led. Uses Gemini Flash's native Google Search grounding to
look up the notifier on the open web — single-step query/classify/done,
no multi-step reasoning loop required.

Called by run_orchestrator.py for signals where the simple lens sets
trigger_investor_research=True (i.e. upward crossings by unidentified
or potentially conviction-led investors).

Usage:
    from utilities.orchestrator.lens_tr1_investor_research import run_tr1_investor_research

    result, token_usage = run_tr1_investor_research(
        rns_article_id="abc123",
        signal_data=signal_doc_dict,
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

_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds_path or not os.path.exists(_creds_path):
    _frontend_creds = os.path.join(_PROJECT_ROOT, "frontend", "gcp-credentials.json")
    if os.path.exists(_frontend_creds):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _frontend_creds

from utilities.orchestrator.config import ORCHESTRATOR_CONFIG
from utilities.orchestrator.persistence import persist_tr1_investor_research

logger = logging.getLogger(__name__)

_MODEL = ORCHESTRATOR_CONFIG.get("tr1_investor_research_model", "gemini-2.0-flash")

# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_investor_research_prompt(
    notifier_name: str,
    threshold_crossed: Optional[float],
    issuer_name: str,
    ticker: str,
) -> str:
    """
    Build the investor research prompt for Gemini Flash + Search grounding.

    Parameters
    ----------
    notifier_name : str
        The disclosing party's name from the TR-1 filing.
    threshold_crossed : float | None
        The threshold % that was crossed (e.g. 5.0, 10.0).
    issuer_name : str
        The LSE-listed company name.
    ticker : str
        The LSE ticker.
    """
    threshold_str = f"{threshold_crossed:.0f}%" if threshold_crossed else "a significant"

    return f"""You are a financial research assistant. A Significant Shareholder \
notification (TR-1) has been filed on the LSE by "{notifier_name}" disclosing \
an upward crossing of the {threshold_str} voting rights threshold in \
{issuer_name} ({ticker}).

Using Google Search, research the following and respond with a structured \
JSON assessment:

1. What type of investor is "{notifier_name}"? Is it an investment fund, \
activist investor, private equity firm, family office, private individual, \
custody bank, index fund provider, or something else?

2. Is there publicly available evidence of their investment philosophy \
or track record (value investing, activist approach, sector focus)?

3. Have they previously accumulated positions in similar LSE-listed \
small-cap companies? Any notable prior investments?

4. Is there anything about this notifier or this transaction that \
materially changes the signal interpretation?

Based on your research, produce a conviction assessment:
- "conviction_likely": The notifier is an investment fund, activist, \
family office, or private investor with evidence of active investment \
strategy. Accumulation is likely deliberate and conviction-led.
- "mechanical_likely": The notifier is a custody bank, passive index \
fund, ETF provider, or other mechanical holder. Crossing is likely \
triggered by index rebalancing, client flows, or corporate action.
- "unclear": Cannot determine from available information.

RESPOND IN EXACTLY THIS JSON FORMAT — no preamble, no markdown fences:

{{
  "notifier_type": "activist_fund|value_fund|index_fund|custody_bank|private_investor|family_office|unknown",
  "conviction_assessment": "conviction_likely|mechanical_likely|unclear",
  "track_record_summary": "1-2 sentences on known investment history, or null if nothing found",
  "confidence": "high|medium|low",
  "search_citations": ["url1", "url2"],
  "limitations": "what could not be determined from available sources"
}}

notifier_type values:
  activist_fund     — known for taking activist positions and engaging management
  value_fund        — fundamental value investor, long-only
  index_fund        — passive index tracker (Vanguard, BlackRock iShares, etc.)
  custody_bank      — nominee/custodian holder (Crest, Euroclear, bank nominees)
  private_investor  — individual investor or family holding vehicle
  family_office     — private wealth management vehicle
  unknown           — cannot classify from available information
"""


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_investor_research_response(text: str, citations: list[str]) -> dict:
    """
    Parse the JSON response from the investor research call.

    Injects grounding citations collected from response metadata into the
    search_citations field (overrides any citations in the JSON text, since
    grounding metadata is more reliable than model-generated URLs).

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
        m = re.search(r"\{.*\}", stripped, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group(0))
            except json.JSONDecodeError as e:
                return _empty_result(error=f"JSON parse failed: {e}")
        else:
            return _empty_result(error="No JSON found in response")

    _VALID_TYPES = frozenset([
        "activist_fund", "value_fund", "index_fund", "custody_bank",
        "private_investor", "family_office", "unknown",
    ])
    _VALID_CONVICTIONS = frozenset(
        ["conviction_likely", "mechanical_likely", "unclear"]
    )
    _VALID_CONFIDENCE = frozenset(["high", "medium", "low"])

    if result.get("notifier_type") not in _VALID_TYPES:
        result["notifier_type"] = "unknown"
    if result.get("conviction_assessment") not in _VALID_CONVICTIONS:
        result["conviction_assessment"] = "unclear"
    if result.get("confidence") not in _VALID_CONFIDENCE:
        result["confidence"] = "low"

    # Grounding citations from metadata take precedence
    if citations:
        result["search_citations"] = citations
    elif not isinstance(result.get("search_citations"), list):
        result["search_citations"] = []

    return result


def _empty_result(error: str = "") -> dict:
    return {
        "notifier_type": "unknown",
        "conviction_assessment": "unclear",
        "track_record_summary": None,
        "confidence": "low",
        "search_citations": [],
        "limitations": "Investor research could not be completed.",
        "_error": error,
    }


# ---------------------------------------------------------------------------
# Gemini + Search grounding call
# ---------------------------------------------------------------------------

def call_investor_research(
    notifier_name: str,
    threshold_crossed: Optional[float],
    issuer_name: str,
    ticker: str,
) -> tuple[dict, dict]:
    """
    Call Gemini Flash with Google Search grounding for investor research.

    Returns (result, token_usage). On API failure, returns a safe empty
    result with _error set.
    """
    api_key = os.environ.get("GEMINI_KEY", "")
    if not api_key:
        return _empty_result(error="GEMINI_KEY not set"), {
            "input": 0, "output": 0, "model": _MODEL
        }

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return _empty_result(error="google-genai package not installed"), {
            "input": 0, "output": 0, "model": _MODEL
        }

    prompt = build_investor_research_prompt(
        notifier_name, threshold_crossed, issuer_name, ticker
    )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.3,
                max_output_tokens=1500,
            ),
        )
    except Exception as e:
        logger.error("Investor research API call failed: %s", e)
        return _empty_result(error=str(e)), {"input": 0, "output": 0, "model": _MODEL}

    citations = _extract_grounding_citations(response)
    token_usage = _extract_token_usage(response)
    text = response.text or ""

    result = parse_investor_research_response(text, citations)

    logger.info(
        "tr1_investor_research: %s — type=%s conviction=%s confidence=%s citations=%d",
        notifier_name[:50],
        result.get("notifier_type"),
        result.get("conviction_assessment"),
        result.get("confidence"),
        len(citations),
    )

    return result, token_usage


def _extract_grounding_citations(response) -> list[str]:
    """Extract web citation URLs from Gemini grounding metadata."""
    citations = []
    try:
        for candidate in response.candidates or []:
            gm = getattr(candidate, "grounding_metadata", None)
            if not gm:
                continue
            for chunk in getattr(gm, "grounding_chunks", []) or []:
                web = getattr(chunk, "web", None)
                if web:
                    uri = getattr(web, "uri", None)
                    if uri:
                        citations.append(uri)
    except Exception as e:
        logger.debug("Could not extract grounding citations: %s", e)
    return citations


def _extract_token_usage(response) -> dict:
    try:
        usage = response.usage_metadata
        return {
            "input": getattr(usage, "prompt_token_count", 0) or 0,
            "output": getattr(usage, "candidates_token_count", 0) or 0,
            "model": _MODEL,
        }
    except Exception:
        return {"input": 0, "output": 0, "model": _MODEL}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_tr1_investor_research(
    rns_article_id: str,
    signal_data: dict,
    db=None,
) -> tuple[dict, dict]:
    """
    Full TR-1 investor research pipeline for one pending signal.

    Reads notifier data from signal_data (the Firestore signals document),
    calls Gemini Flash + Search grounding, persists the result, and
    sets agentic_status to "complete" or "failed".

    Parameters
    ----------
    rns_article_id : str
        The signals document_id.
    signal_data : dict
        The Firestore signals document data. Expected keys: notifier_name,
        threshold_crossed, issuer_name, ticker.
    db : Firestore client, optional

    Returns
    -------
    (result, token_usage) : tuple[dict, dict]
        result — parsed investor research dict.
        token_usage — {"input": int, "output": int, "model": str}.
    """
    notifier_name = signal_data.get("notifier_name") or "Unknown"
    threshold_crossed = signal_data.get("threshold_crossed")
    issuer_name = signal_data.get("issuer_name") or signal_data.get("company_name") or ""
    ticker = signal_data.get("ticker") or ""

    result, token_usage = call_investor_research(
        notifier_name=notifier_name,
        threshold_crossed=threshold_crossed,
        issuer_name=issuer_name,
        ticker=ticker,
    )

    persist_tr1_investor_research(
        rns_article_id=rns_article_id,
        research_result=result,
        token_usage=token_usage,
        db=db,
    )

    return result, token_usage
