"""
Stage 3/4: Layer agent execution — tool-use loop for all three layers.

Provides:
  - Tool definitions (LAYER1_TOOLS, LAYER2_TOOLS, LAYER3_TOOLS)
  - Executor factories per layer (close over price_cache for L1/L3)
  - Input builders (build_layer1_inputs, build_layer2_inputs, build_layer3_inputs)
  - Prompt builders (_build_layer1_prompt, _build_layer2_prompt, _build_layer3_prompt)
  - Tool-use loop (run_layer_agent)
  - Sequential orchestrator (run_agentic_investigation_sequential)
  - Helpers (should_trigger_credibility_research, prefetch_price_data)
"""

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Optional

from utilities.orchestrator.config import ORCHESTRATOR_CONFIG
from utilities.orchestrator.persistence import (
    update_agentic_status,
    persist_layer_outputs,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions — Anthropic tool_use schema
# ---------------------------------------------------------------------------

LAYER1_TOOLS = [
    {
        "name": "get_company_profile",
        "description": "Retrieve basic company identity and classification from Firestore.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "LSE ticker (e.g. 'FGEN', without .L suffix)",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_price_history",
        "description": "Retrieve price history across time windows using yfinance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "LSE ticker (e.g. 'FGEN', without .L suffix)",
                },
                "windows": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Time windows e.g. ['1w', '1m', '3m', '1y']",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_volatility_metrics",
        "description": "Retrieve volatility and liquidity indicators using yfinance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "LSE ticker (e.g. 'FGEN', without .L suffix)",
                },
                "period_days": {
                    "type": "integer",
                    "description": "Number of days for volatility calculation. Default 30.",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_index_snapshot",
        "description": (
            "Retrieve the most recent daily index snapshot from Firestore "
            "for relative performance benchmarking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "index_membership": {
                    "type": "string",
                    "description": "'AIM' or 'MAIN_MARKET'",
                },
                "market_cap_gbp": {
                    "type": "number",
                    "description": "Market cap in GBP, used to select the appropriate index",
                },
            },
            "required": ["index_membership"],
        },
    },
    {
        "name": "get_relative_performance",
        "description": (
            "Calculate stock performance relative to market benchmark across time windows. "
            "Pass the outputs from get_price_history and get_index_snapshot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "price_history": {
                    "type": "object",
                    "description": "Output from get_price_history",
                },
                "index_snapshot": {
                    "type": "object",
                    "description": "Output from get_index_snapshot",
                },
                "windows": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Time windows e.g. ['1w', '1m', '3m']",
                },
            },
            "required": ["price_history", "index_snapshot"],
        },
    },
]

LAYER2_TOOLS = [
    {
        "name": "get_director_transaction_history",
        "description": (
            "Retrieve all known transactions for this specific director at this company "
            "from the pdmr_transactions Firestore collection."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "director_name": {
                    "type": "string",
                    "description": "Raw director name from the current transaction",
                },
                "exclude_id": {
                    "type": "string",
                    "description": "rns_article_id of current transaction to exclude from history",
                },
            },
            "required": ["ticker", "director_name"],
        },
    },
    {
        "name": "get_company_insider_activity",
        "description": (
            "Retrieve all recent open market transactions across ALL directors at this "
            "company to detect corroborating insider momentum."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "days_lookback": {
                    "type": "integer",
                    "description": "Days lookback. Default 90.",
                },
                "exclude_id": {
                    "type": "string",
                    "description": "rns_article_id of current transaction to exclude",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_company_ch_filings",
        "description": (
            "Retrieve recent Companies House filings for the company from Firestore. "
            "Provides director tenure context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "days_lookback": {
                    "type": "integer",
                    "description": "Days lookback. Default 365.",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_director_companies_house_profile",
        "description": (
            "Retrieve Companies House profile for the director. "
            "CONDITIONAL — only call when credibility research is required: "
            "first entry (previous_holding == 0), resulting_holding_pct < 0.5%, "
            "or no prior transaction history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "director_name": {
                    "type": "string",
                    "description": "Director name (raw from filing)",
                },
                "company_name": {
                    "type": "string",
                    "description": "Company name, used to disambiguate common names",
                },
                "ticker": {"type": "string"},
            },
            "required": ["director_name", "company_name", "ticker"],
        },
    },
]

LAYER3_TOOLS = [
    {
        "name": "get_company_news_history",
        "description": (
            "Retrieve recent news summaries for this company from Firestore. "
            "Used to assess whether the announcement topic has prior coverage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "days_lookback": {
                    "type": "integer",
                    "description": "Days lookback. Default 90.",
                },
                "exclude_id": {
                    "type": "string",
                    "description": "rns_article_id to exclude from results",
                },
                "current_topics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Topics from current article for topic_match computation",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_price_movement_context",
        "description": (
            "Retrieve recent price movement focused on the period around the director "
            "transaction and announcement dates. Only call if pre-fetched data not available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "LSE ticker (without .L suffix)",
                },
                "transaction_date": {
                    "type": "string",
                    "description": "Actual date director transacted (YYYY-MM-DD)",
                },
                "published_at": {
                    "type": "string",
                    "description": "Date of RNS announcement (YYYY-MM-DD)",
                },
                "reporting_lag_days": {
                    "type": "integer",
                    "description": "Reporting lag in days",
                },
            },
            "required": ["ticker", "transaction_date", "published_at"],
        },
    },
    {
        "name": "get_index_context_for_freshness",
        "description": (
            "Retrieve index snapshot for normalisation context. "
            "Used to contextualise whether price movement is company-specific or market-wide."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "index_membership": {
                    "type": "string",
                    "description": "'AIM' or 'MAIN_MARKET'",
                },
                "market_cap_gbp": {
                    "type": "number",
                    "description": "Market cap in GBP",
                },
            },
            "required": ["index_membership"],
        },
    },
]


# ---------------------------------------------------------------------------
# Lazy utility imports
# ---------------------------------------------------------------------------

def _get_utility(module: str, fn: str):
    """Lazily import a utility function by module and function name."""
    import importlib
    mod = importlib.import_module(f"utilities.{module}")
    return getattr(mod, fn)


# ---------------------------------------------------------------------------
# Executor factories
# ---------------------------------------------------------------------------

def _make_layer1_executor(price_cache: Optional[dict] = None):
    """
    Return an executor callable for Layer 1 tools.

    price_cache: {"price_history": ..., "price_movement": ...}
    If price_history is present, get_price_history returns cached data (no yfinance call).
    """
    def executor(tool_name: str, tool_input: dict) -> dict:
        if tool_name == "get_company_profile":
            fn = _get_utility("get_company_profile", "get_company_profile")
            return fn(ticker=tool_input["ticker"])

        elif tool_name == "get_price_history":
            if price_cache and price_cache.get("price_history"):
                logger.info("layer1: get_price_history — returning pre-fetched cache")
                return price_cache["price_history"]
            fn = _get_utility("get_price_history", "get_price_history")
            windows = tool_input.get("windows", ["1w", "1m", "3m", "1y"])
            return fn(ticker=tool_input["ticker"], windows=windows)

        elif tool_name == "get_volatility_metrics":
            fn = _get_utility("get_volatility_metrics", "get_volatility_metrics")
            period_days = tool_input.get("period_days", 30)
            return fn(ticker=tool_input["ticker"], period_days=period_days)

        elif tool_name == "get_index_snapshot":
            fn = _get_utility("get_index_snapshot", "get_index_snapshot")
            return fn(
                index_membership=tool_input["index_membership"],
                market_cap_gbp=tool_input.get("market_cap_gbp"),
            )

        elif tool_name == "get_relative_performance":
            fn = _get_utility("get_relative_performance", "get_relative_performance")
            windows = tool_input.get("windows", ["1w", "1m", "3m"])
            return fn(
                price_history=tool_input["price_history"],
                index_snapshot=tool_input["index_snapshot"],
                windows=windows,
            )

        else:
            logger.warning("layer1: unknown tool '%s'", tool_name)
            return {"error": f"Unknown tool: {tool_name}"}

    return executor


def _make_layer2_executor():
    """Return an executor callable for Layer 2 tools."""
    def executor(tool_name: str, tool_input: dict) -> dict:
        if tool_name == "get_director_transaction_history":
            fn = _get_utility(
                "get_director_transaction_history", "get_director_transaction_history"
            )
            return fn(
                ticker=tool_input["ticker"],
                director_name=tool_input["director_name"],
                exclude_id=tool_input.get("exclude_id"),
            )

        elif tool_name == "get_company_insider_activity":
            fn = _get_utility("get_company_insider_activity", "get_company_insider_activity")
            return fn(
                ticker=tool_input["ticker"],
                days_lookback=tool_input.get("days_lookback", 90),
                exclude_id=tool_input.get("exclude_id"),
            )

        elif tool_name == "get_company_ch_filings":
            fn = _get_utility("get_company_ch_filings", "get_company_ch_filings")
            return fn(
                ticker=tool_input["ticker"],
                days_lookback=tool_input.get("days_lookback", 365),
            )

        elif tool_name == "get_director_companies_house_profile":
            fn = _get_utility(
                "get_director_companies_house_profile",
                "get_director_companies_house_profile",
            )
            return fn(
                director_name=tool_input["director_name"],
                company_name=tool_input["company_name"],
                ticker=tool_input["ticker"],
            )

        else:
            logger.warning("layer2: unknown tool '%s'", tool_name)
            return {"error": f"Unknown tool: {tool_name}"}

    return executor


def _make_layer3_executor(price_cache: Optional[dict] = None):
    """
    Return an executor callable for Layer 3 tools.

    price_cache: {"price_history": ..., "price_movement": ...}
    If price_movement is present, get_price_movement_context returns cached data.
    """
    def executor(tool_name: str, tool_input: dict) -> dict:
        if tool_name == "get_company_news_history":
            fn = _get_utility("get_company_news_history", "get_company_news_history")
            return fn(
                ticker=tool_input["ticker"],
                days_lookback=tool_input.get("days_lookback", 90),
                exclude_id=tool_input.get("exclude_id"),
                current_topics=tool_input.get("current_topics"),
            )

        elif tool_name == "get_price_movement_context":
            if price_cache and price_cache.get("price_movement"):
                logger.info("layer3: get_price_movement_context — returning pre-fetched cache")
                return price_cache["price_movement"]
            fn = _get_utility("get_price_movement_context", "get_price_movement_context")
            return fn(
                ticker=tool_input["ticker"],
                transaction_date=_parse_date(tool_input["transaction_date"]),
                published_at=_parse_date(tool_input["published_at"]),
                reporting_lag_days=tool_input.get("reporting_lag_days", 0),
            )

        elif tool_name == "get_index_context_for_freshness":
            fn = _get_utility(
                "get_index_context_for_freshness", "get_index_context_for_freshness"
            )
            return fn(
                index_membership=tool_input["index_membership"],
                market_cap_gbp=tool_input.get("market_cap_gbp"),
            )

        else:
            logger.warning("layer3: unknown tool '%s'", tool_name)
            return {"error": f"Unknown tool: {tool_name}"}

    return executor


# ---------------------------------------------------------------------------
# Date parsing helper
# ---------------------------------------------------------------------------

def _parse_date(value) -> Optional[date]:
    """Convert string 'YYYY-MM-DD', datetime, or date object to date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
    return None


# ---------------------------------------------------------------------------
# Input builders
# ---------------------------------------------------------------------------

def build_layer1_inputs(transaction: dict, company_profile: dict) -> dict:
    """Build inputs dict for Layer 1 agent."""
    return {
        "ticker": transaction.get("ticker", ""),
        "company_name": transaction.get("company_name", ""),
        "index_membership": company_profile.get("index_membership", ""),
        "market_cap_gbp": company_profile.get("market_cap_gbp"),
    }


def build_layer2_inputs(transaction: dict) -> dict:
    """Build inputs dict for Layer 2 agent."""
    return {
        "ticker": transaction.get("ticker", ""),
        "company_name": transaction.get("company_name", ""),
        "director_name": transaction.get("director_name", ""),
        "director_role": transaction.get("director_role", ""),
        "transaction_category": transaction.get("transaction_category", ""),
        "transaction_date_actual": str(transaction.get("transaction_date_actual", "")),
        "reporting_lag_days": transaction.get("reporting_lag_days", 0),
        "shares_transacted": transaction.get("shares_transacted"),
        "price_per_share_gbp": transaction.get("price_per_share_gbp"),
        "total_consideration_gbp": transaction.get("total_consideration_gbp"),
        "previous_holding": transaction.get("previous_holding"),
        "resulting_holding": transaction.get("resulting_holding"),
        "resulting_holding_pct": transaction.get("resulting_holding_pct"),
        "rns_article_id": transaction.get("rns_article_id", ""),
    }


def build_layer3_inputs(
    transaction: dict,
    announcement: dict,
    company_profile: dict,
    price_cache: Optional[dict] = None,
) -> dict:
    """Build inputs dict for Layer 3 agent."""
    return {
        "ticker": transaction.get("ticker", ""),
        "company_name": transaction.get("company_name", ""),
        "index_membership": company_profile.get("index_membership", ""),
        "market_cap_gbp": company_profile.get("market_cap_gbp"),
        "transaction_date_actual": str(transaction.get("transaction_date_actual", "")),
        "published_at": str(transaction.get("transaction_date_reported", "")),
        "reporting_lag_days": transaction.get("reporting_lag_days", 0),
        "headline": announcement.get("headline", ""),
        "summary": announcement.get("summary", ""),
        "key_topics": announcement.get("key_topics") or [],
        "rns_article_id": announcement.get("rns_article_id", ""),
        "price_data_cache": price_cache,
    }


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_layer1_prompt(inputs: dict) -> str:
    company_name = inputs.get("company_name", "")
    ticker = inputs.get("ticker", "")
    index_membership = inputs.get("index_membership", "")
    market_cap_gbp = inputs.get("market_cap_gbp", "Unknown")

    return f"""\
You are a quantitative research assistant assessing whether a \
company is a suitable trading platform for a retail investor \
operating in LSE small-cap and AIM markets.

Your task is to evaluate platform risk — the risk that even if \
a positive price movement occurs, the investor cannot execute \
trades efficiently due to illiquidity, high volatility, or \
wide bid/ask spread.

You are one of three independent investigation agents. You do \
not have access to the director buying signal that triggered \
this investigation, nor to the findings of the other agents. \
Assess the company as a platform only.

COMPANY:
Name: {company_name}
Ticker: {ticker}
Index: {index_membership}
Market Cap (may be stale): {market_cap_gbp}

AVAILABLE TOOLS:
- get_company_profile: retrieve company profile from Firestore
- get_price_history: retrieve price history across time windows
- get_volatility_metrics: retrieve volatility and liquidity data
- get_index_snapshot: retrieve current index benchmark data
- get_relative_performance: calculate stock vs index performance

INVESTIGATION INSTRUCTIONS:

Step 1 — Retrieve company profile
Call get_company_profile. Note if market cap is stale.

Step 2 — Retrieve price history
Call get_price_history with windows ["1w", "1m", "3m", "1y"].
If data_quality_flag is "unavailable", note this and proceed \
with what is available. Do not abort the investigation.

Step 3 — Retrieve volatility and liquidity metrics
Call get_volatility_metrics.
Note liquidity_flag and any data quality issues.
Note: bid_ask_spread may be null — this is a known limitation, \
flag it but do not treat it as a blocking data gap.

Step 4 — Retrieve index benchmark
Call get_index_snapshot using index_membership and market_cap_gbp \
to select the appropriate benchmark index.

Step 5 — Calculate relative performance
Call get_relative_performance using outputs from Steps 2 and 4.
If index history is unavailable, use position_in_52wk_range \
as the primary relative context measure.

Step 6 — Assess platform risk
Based on the data retrieved, assess:

LIQUIDITY: Can the investor execute a £1,000-£4,000 retail \
position without material market impact or execution delay?
Use liquidity_flag, avg_daily_volume, and market_cap as evidence.
AIM stocks with very_low liquidity flag are a meaningful risk.

VOLATILITY: Is price movement within a range that allows \
meaningful entry and exit planning?
Use volatility_30d as primary evidence.
High volatility is not automatically disqualifying — context matters.

PRICE MOMENTUM: What direction has the stock been moving?
Use price changes across the four time windows.
Distinguish between: general upward trend, recent decline from \
higher levels (potential reversion opportunity), recent sharp rise \
(may indicate signal already priced in — note for synthesis), \
and erratic/directionless movement.

RELATIVE PERFORMANCE: Is the stock moving with or against \
its market benchmark?
Use relative_performance data if available.
Use position_in_52wk_range as supporting context.
A stock near its 52-week low warrants different interpretation \
than one near its 52-week high.

Step 7 — Assign platform risk rating
Assign ONE of:
- low: liquid, moderate volatility, tradeable without concern
- medium: some liquidity or volatility concern, manageable \
  for a retail position with care
- high: meaningful liquidity or volatility risk — a strong \
  signal would be needed to justify entry
- very_high: illiquid or extremely volatile — execution risk \
  is material regardless of signal strength

IMPORTANT: Assign the rating based on evidence. State what \
evidence drove the rating. Do not hedge by always choosing "medium".

Step 8 — Produce structured output
Respond ONLY with the following JSON structure.
No preamble. No explanation outside the JSON.

{{
  "platform_risk_rating": "",
  "market_cap_gbp": 0.0,
  "market_cap_stale": false,
  "index_membership": "",

  "liquidity": {{
    "flag": "",
    "avg_daily_volume_30d": 0,
    "assessment": "",
    "evidence": ""
  }},

  "volatility": {{
    "volatility_30d": 0.0,
    "assessment": "",
    "evidence": ""
  }},

  "price_momentum": {{
    "current_price": 0.0,
    "change_1w_pct": 0.0,
    "change_1m_pct": 0.0,
    "change_3m_pct": 0.0,
    "change_1y_pct": 0.0,
    "assessment": "",
    "evidence": ""
  }},

  "relative_performance": {{
    "index_name": "",
    "position_in_52wk_range_pct": 0.0,
    "vs_index_available": false,
    "vs_index_assessment": "",
    "evidence": ""
  }},

  "data_quality": {{
    "overall": "",
    "price_data_available": true,
    "volatility_data_available": true,
    "bid_ask_spread_available": false,
    "index_history_available": false,
    "flags": []
  }},

  "inference_notes": "",
  "platform_risk_justification": "",
  "limitations": ""
}}

FIELD GUIDANCE:

assessment fields: short factual description of what the data shows.
  Do not interpret — describe.
  Example: "Average daily volume of 45,000 shares. Liquidity flag: low."

evidence fields: state which specific data points drove the assessment.
  Example: "volatility_30d = 0.42 (annualised). 30d avg volume = 45,000 shares."

inference_notes: state explicitly what was inferred rather than directly retrieved.

platform_risk_justification: 2-3 sentences explaining the rating.
  Reference specific evidence. Be direct.

limitations: list what could not be assessed due to data unavailability.\
"""


def _build_layer2_prompt(inputs: dict) -> str:
    company_name = inputs.get("company_name", "")
    ticker = inputs.get("ticker", "")
    director_name = inputs.get("director_name", "")
    director_role = inputs.get("director_role", "")
    transaction_category = inputs.get("transaction_category", "")
    transaction_date_actual = inputs.get("transaction_date_actual", "")
    reporting_lag_days = inputs.get("reporting_lag_days", "")
    shares_transacted = inputs.get("shares_transacted", "")
    price_per_share_gbp = inputs.get("price_per_share_gbp", "")
    total_consideration_gbp = inputs.get("total_consideration_gbp", "")
    previous_holding = inputs.get("previous_holding", "")
    resulting_holding = inputs.get("resulting_holding", "")
    resulting_holding_pct = inputs.get("resulting_holding_pct", "")
    rns_article_id = inputs.get("rns_article_id", "")

    return f"""\
You are a financial research assistant analysing a director or \
PDMR share transaction to assess its quality as an investment signal.

Your task is to evaluate the transaction itself and the director's \
credibility as a signal source. You will use tools to retrieve \
historical transaction data and, conditionally, director background \
information.

You are one of three independent investigation agents. You do not \
have access to platform risk data or signal freshness data from \
the other agents. Assess the director signal independently.

TRANSACTION BEING ASSESSED:
Company: {company_name}
Ticker: {ticker}
Director: {director_name}
Role: {director_role}
Transaction type: {transaction_category}
Transaction date: {transaction_date_actual}
Reporting lag: {reporting_lag_days} days
Shares transacted: {shares_transacted}
Price per share (GBP): {price_per_share_gbp}
Total consideration (GBP): {total_consideration_gbp}
Previous holding: {previous_holding} shares
Resulting holding: {resulting_holding} shares
Resulting holding (%): {resulting_holding_pct}%

AVAILABLE TOOLS:
- get_director_transaction_history: retrieve this director's \
  prior transactions at this company
- get_company_insider_activity: retrieve all recent insider \
  transactions at this company
- get_company_ch_filings: retrieve Companies House filings \
  for this company
- get_director_companies_house_profile: retrieve director's \
  CH profile and appointments (conditional — see below)

INVESTIGATION INSTRUCTIONS:

Step 1 — Characterise the transaction
Before calling any tools, assess from the data provided:

TRANSACTION NATURE: Classify as one of:
- first_entry: previous_holding is zero
- position_increase: resulting_holding > previous_holding
- position_decrease: resulting_holding < previous_holding AND resulting_holding > 0
- complete_exit: resulting_holding is zero or near-zero

POSITION CHANGE: Calculate % change from previous_holding.
If previous_holding is zero, state "new position". Show calculation.

COMMITMENT SIGNIFICANCE:
- "material": total_consideration_gbp > £20,000 AND/OR resulting_holding_pct > 0.5%
- "moderate": total_consideration_gbp £5,000-£20,000
- "token": total_consideration_gbp < £5,000
Apply judgement in context — a £5,000 purchase at a £5m company may be material.

Step 2 — Retrieve director transaction history
Call get_director_transaction_history with:
  ticker={ticker}, director_name={director_name}, exclude_id={rns_article_id}

Note data_maturity and collection_age_days.
If data_maturity = "empty" AND collection_age_days < 30:
  This is a pipeline maturity constraint, not a signal about the director. State this.

Step 3 — Retrieve company insider activity
Call get_company_insider_activity with:
  ticker={ticker}, days_lookback=90, exclude_id={rns_article_id}

Note cluster_detected and net_sentiment.
A cluster (2+ directors buying within 30 days) is a meaningful signal amplifier.

Step 4 — Retrieve Companies House company filings
Call get_company_ch_filings with:
  ticker={ticker}, days_lookback=365

Note board stability and any recent director changes.

Step 5 — CONDITIONAL: Credibility research
Evaluate this condition:

IF ANY of these are true:
  - resulting_holding_pct < 0.5%
  - previous_holding == 0
  - transaction_history.total_count == 0

THEN call get_director_companies_house_profile with:
  director_name={director_name}, company_name={company_name}, ticker={ticker}

  credibility_research_triggered = true
  If disqualified = true: flag this prominently — hard negative signal.
  If name_match_confidence = "low": do not assert findings from this match.

ELSE:
  Skip this tool call. credibility_research_triggered = false.
  Note: "Existing material holding provides transaction context."

Step 6 — Assess signal strength
Score 1-10 starting from baseline 5:

Positive: +2 first_entry with material consideration; +2 position_increase > 20%;
  +1 cluster_detected; +1 net_sentiment = "buying"; +1 established buying history;
  +1 long tenure (if credibility research triggered)

Negative: -2 position_decrease or complete_exit; -1 token consideration;
  -1 reporting_lag_days > 30; -1 disqualified; -1 board instability concurrent

Step 7 — Produce structured output
Respond ONLY with the following JSON structure.
No preamble. No explanation outside the JSON.

{{
  "transaction_assessment": {{
    "transaction_nature": "",
    "position_change_pct": 0.0,
    "position_change_note": "",
    "commitment_significance": "",
    "commitment_justification": "",
    "holding_significance": ""
  }},

  "director_history": {{
    "total_prior_transactions": 0,
    "open_market_prior_transactions": 0,
    "holding_trajectory": "",
    "pattern_summary": "",
    "data_maturity": "",
    "collection_age_days": 0
  }},

  "insider_momentum": {{
    "net_sentiment": "",
    "buy_count_90d": 0,
    "sell_count_90d": 0,
    "cluster_detected": false,
    "cluster_summary": "",
    "corroboration_strength": ""
  }},

  "board_context": {{
    "board_stability": "",
    "recent_appointments": 0,
    "recent_resignations": 0,
    "context_note": ""
  }},

  "credibility_research": {{
    "triggered": false,
    "trigger_reason": "",
    "disqualified": false,
    "tenure_days": null,
    "total_current_appointments": null,
    "name_match_confidence": null,
    "credibility_summary": null
  }},

  "signal_strength": 0,
  "signal_direction": "",
  "signal_strength_justification": "",

  "data_quality": {{
    "overall": "",
    "transaction_history_maturity": "",
    "credibility_data_found": null,
    "flags": []
  }},

  "inference_notes": "",
  "limitations": ""
}}\
"""


def _build_layer3_prompt(inputs: dict) -> str:
    company_name = inputs.get("company_name", "")
    ticker = inputs.get("ticker", "")
    index_membership = inputs.get("index_membership", "")
    market_cap_gbp = inputs.get("market_cap_gbp", "Unknown")
    headline = inputs.get("headline", "")
    summary = inputs.get("summary", "")
    key_topics = inputs.get("key_topics", [])
    transaction_date_actual = inputs.get("transaction_date_actual", "")
    published_at = inputs.get("published_at", "")
    reporting_lag_days = inputs.get("reporting_lag_days", 0)
    rns_article_id = inputs.get("rns_article_id", "")
    price_data_cache = inputs.get("price_data_cache")

    cache_note = (
        "Pre-fetched price data is available — use the cached result rather than "
        "calling get_price_movement_context (which would make a redundant network call)."
        if price_data_cache
        else "No pre-fetched price data available — call get_price_movement_context."
    )

    return f"""\
You are a financial research assistant assessing whether a \
director share purchase signal has already been priced in by the market.

Your task is to evaluate signal freshness — the degree to which \
the information in this announcement was already anticipated by \
the market, and whether there remains actionable value.

This is a reasoning task. You must maintain a strict boundary \
between FACTS (what the data directly shows) and INFERENCE \
(your interpretation of what the data means). Label your reasoning clearly.

You are one of three independent investigation agents. You do \
not have access to platform risk data or director signal data \
from the other agents. Assess signal freshness independently.

ANNOUNCEMENT BEING ASSESSED:
Company: {company_name}
Ticker: {ticker}
Index: {index_membership}
Market Cap: {market_cap_gbp}
Headline: {headline}
Summary: {summary}
Key topics: {key_topics}
Transaction date: {transaction_date_actual}
Announcement date: {published_at}
Reporting lag: {reporting_lag_days} days

PRICE DATA NOTE: {cache_note}

AVAILABLE TOOLS:
- get_company_news_history: retrieve prior news summaries for this company
- get_price_movement_context: retrieve recent price movement around key dates
- get_index_context_for_freshness: retrieve market benchmark context

INVESTIGATION INSTRUCTIONS:

Step 1 — Retrieve prior news history
Call get_company_news_history with:
  ticker={ticker}, days_lookback=90, exclude_id={rns_article_id},
  current_topics={key_topics}

Note data_maturity, collection_age_days, topics_seen, topic_match, sentiment_trend.
If data_maturity = "empty": state collection age and proceed to price assessment.

Step 2 — Retrieve price movement context
{cache_note}
If calling the tool, use:
  ticker={ticker}, transaction_date={transaction_date_actual},
  published_at={published_at}, reporting_lag_days={reporting_lag_days}

Note movement_during_lag (meaningful if lag > 5 days).
Note volume_anomaly as observation ONLY — do NOT assert information leakage.
Note position_in_52wk_range.

Step 3 — Retrieve index context
Call get_index_context_for_freshness with:
  index_membership={index_membership}, market_cap_gbp={market_cap_gbp}

Use to normalise price movement. If stock rose 5% but index rose 6%, \
the stock underperformed despite rising — this matters for freshness.

Step 4 — Assess signal freshness
CRITICAL: Separate your assessment into two clearly labelled sections:

FACTUAL EVIDENCE: State only what the data directly shows. No interpretation.
Examples: "Price rose 8.3% between transaction date and publication date."
         "Average volume in the 2 weeks before publication was 1.8x the 30-day average."

INFERENCE: Clearly label this section as inference. Begin with "INFERENCE:".
Reason about what the factual evidence means for signal freshness.
Be explicit about uncertainty. Do not overstate confidence.

Assign freshness_assessment as one of:
- "likely_fresh": movement in line with market, no prior topic coverage, no volume anomaly
- "possibly_priced_in": some indicators of anticipation but not conclusive
- "likely_priced_in": significant movement before publication, or clear prior topic coverage
- "insufficient_data": insufficient data — state what is missing

Step 5 — Produce structured output
Respond ONLY with the following JSON structure.
No preamble. No explanation outside the JSON.

{{
  "freshness_assessment": {{
    "overall": "",
    "confidence": ""
  }},

  "price_evidence": {{
    "price_at_transaction_date": null,
    "price_at_publication": null,
    "price_now": null,
    "movement_since_transaction_pct": null,
    "movement_since_publication_pct": null,
    "movement_during_lag_pct": null,
    "movement_during_lag_note": "",
    "volume_anomaly_detected": false,
    "volume_anomaly_note": "",
    "position_in_52wk_range_pct": null,
    "factual_summary": ""
  }},

  "news_evidence": {{
    "articles_found": 0,
    "data_maturity": "",
    "collection_age_days": 0,
    "prior_topic_coverage": false,
    "topics_seen": [],
    "sentiment_trend": "",
    "relevant_prior_articles": [],
    "factual_summary": ""
  }},

  "index_context": {{
    "index_name": "",
    "index_value": null,
    "index_historical_available": false,
    "normalisation_note": ""
  }},

  "inference_assessment": "",

  "data_quality": {{
    "overall": "",
    "price_data_available": true,
    "news_history_available": true,
    "index_history_available": false,
    "flags": []
  }},

  "inference_notes": "",
  "limitations": ""
}}

FIELD GUIDANCE:

factual_summary fields: facts only, no interpretation.
inference_assessment: begins with "INFERENCE:". Explicit about uncertainty.
volume_anomaly_note: state factually — do NOT assert leakage or insider trading.
limitations: be specific about what could not be assessed.\
"""


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------

def _parse_json_response(text: str) -> dict:
    """Strip markdown fences and parse JSON from final agent response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    return json.loads(text.strip())


# ---------------------------------------------------------------------------
# Tool-use loop
# ---------------------------------------------------------------------------

def run_layer_agent(
    layer: int,
    inputs: dict,
    tools: list,
    model: str,
    client,
    executor,
    max_iterations: int = 20,
) -> tuple:
    """
    Run a single layer agent using the Anthropic tool-use pattern.

    Parameters
    ----------
    layer : int
        Layer number (1, 2, or 3) — used for logging only.
    inputs : dict
        Layer-specific inputs passed to the prompt builder.
    tools : list
        Anthropic tool definitions for this layer.
    model : str
        Anthropic model identifier.
    client : anthropic.Anthropic
        Anthropic API client.
    executor : callable
        Tool executor: (tool_name: str, tool_input: dict) -> dict.
    max_iterations : int
        Maximum number of LLM round-trips before aborting.

    Returns
    -------
    (output, token_usage) : tuple[dict, dict]
        output — parsed JSON output from the agent.
                 On failure, contains an "_error" key.
        token_usage — {"input": int, "output": int, "model": str}.
    """
    prompt_builders = {
        1: _build_layer1_prompt,
        2: _build_layer2_prompt,
        3: _build_layer3_prompt,
    }
    prompt = prompt_builders[layer](inputs)
    messages = [{"role": "user", "content": prompt}]

    total_input = 0
    total_output = 0

    for iteration in range(max_iterations):
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            tools=tools,
            messages=messages,
        )
        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if tool_use_blocks:
            tool_results = []
            for block in tool_use_blocks:
                logger.info("layer%d [iter %d]: calling tool '%s'", layer, iteration, block.name)
                try:
                    result = executor(block.name, block.input)
                except Exception as exc:
                    logger.error(
                        "layer%d: tool '%s' raised: %s", layer, block.name, exc
                    )
                    result = {"error": str(exc), "tool": block.name}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                })
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        else:
            # Final response — extract and parse JSON
            text_blocks = [b for b in response.content if b.type == "text"]
            if not text_blocks:
                logger.error("layer%d: final response has no text block", layer)
                return (
                    {"_error": "no text block in final response"},
                    {"input": total_input, "output": total_output, "model": model},
                )
            try:
                output = _parse_json_response(text_blocks[0].text)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error("layer%d: JSON parse failed: %s", layer, exc)
                output = {
                    "_error": f"JSON parse failed: {exc}",
                    "_raw_text": text_blocks[0].text[:500],
                }
            logger.info(
                "layer%d: complete — %d input tokens, %d output tokens",
                layer, total_input, total_output,
            )
            return output, {"input": total_input, "output": total_output, "model": model}

    logger.error(
        "layer%d: exceeded max_iterations=%d without final response", layer, max_iterations
    )
    return (
        {"_error": f"exceeded max_iterations ({max_iterations})"},
        {"input": total_input, "output": total_output, "model": model},
    )


# ---------------------------------------------------------------------------
# Pre-fetch helpers
# ---------------------------------------------------------------------------

def should_trigger_credibility_research(
    transaction: dict,
    config: Optional[dict] = None,
) -> bool:
    """
    Determine whether Layer 2 should use the credibility research model (Sonnet).

    Returns True if resulting_holding_pct < threshold OR previous_holding == 0.
    The threshold defaults to 0.5% from ORCHESTRATOR_CONFIG.
    """
    cfg = config or ORCHESTRATOR_CONFIG
    threshold = cfg.get("credibility_holding_pct_threshold", 0.5)

    previous_holding = transaction.get("previous_holding")
    if previous_holding is not None and previous_holding == 0:
        return True

    holding_pct = transaction.get("resulting_holding_pct")
    if holding_pct is not None and holding_pct < threshold:
        return True

    return False


def prefetch_price_data(
    ticker: str,
    transaction_date,
    published_at,
    reporting_lag_days: int = 0,
) -> dict:
    """
    Pre-fetch all price data needed by Layer 1 and Layer 3.

    Called once per investigation event. Both layers receive the same
    cached data — this halves yfinance calls and respects rate limits.
    The layers remain analytically independent: they reason differently
    about the same factual data.

    Returns
    -------
    dict with keys:
        "price_history"  — output of get_price_history, or {} on failure
        "price_movement" — output of get_price_movement_context, or {} on failure
    """
    result: dict = {"price_history": {}, "price_movement": {}}

    try:
        fn = _get_utility("get_price_history", "get_price_history")
        result["price_history"] = fn(ticker=ticker, windows=["1w", "1m", "3m", "1y"])
        logger.info("prefetch: price_history OK for %s", ticker)
    except Exception as exc:
        logger.error("prefetch: get_price_history failed for %s: %s", ticker, exc)

    try:
        fn = _get_utility("get_price_movement_context", "get_price_movement_context")
        result["price_movement"] = fn(
            ticker=ticker,
            transaction_date=_parse_date(transaction_date),
            published_at=_parse_date(published_at),
            reporting_lag_days=reporting_lag_days,
        )
        logger.info("prefetch: price_movement OK for %s", ticker)
    except Exception as exc:
        logger.error("prefetch: get_price_movement_context failed for %s: %s", ticker, exc)

    return result


# ---------------------------------------------------------------------------
# Sequential orchestrator — Stage 3
# ---------------------------------------------------------------------------

def run_agentic_investigation_sequential(
    rns_article_id: str,
    announcement: dict,
    transaction: dict,
    company_profile: dict,
    client,
    db=None,
    config: Optional[dict] = None,
) -> dict:
    """
    Run three layer agents sequentially for one open-market PDMR transaction.

    Used during Stage 3 for debugging — equivalent output to the parallel
    runner (Stage 4). Layers are analytically independent; execution order
    does not affect their findings.

    Parameters
    ----------
    rns_article_id : str
        The signals Firestore document_id (deduplication key).
    announcement : dict
        Full announcement dict. Should include headline, summary, key_topics.
        Caller may add these from classification output before calling.
    transaction : dict
        Parsed pdmr_transaction dict (open-market transaction only).
        Must include ticker, company_name, director_name, transaction_category,
        transaction_date_actual, transaction_date_reported, reporting_lag_days,
        resulting_holding_pct, previous_holding.
    company_profile : dict
        Company profile from get_company_profile() or universe_companies.
        Must include index_membership. Pass {} if unavailable.
    client : anthropic.Anthropic
        Anthropic API client.
    db : Firestore client, optional
    config : dict, optional

    Returns
    -------
    dict with keys "layer1", "layer2", "layer3".
        Each value: {"output": dict | None, "tokens": dict}
        output is None if the layer raised an unexpected exception.
        output contains "_error" key if the agent failed gracefully.
    """
    cfg = config or ORCHESTRATOR_CONFIG
    started_at = datetime.now(tz=timezone.utc)

    update_agentic_status(rns_article_id, "running", db=db)

    # Pre-fetch shared price data (one yfinance call pair, shared by L1 and L3)
    price_cache = prefetch_price_data(
        ticker=transaction.get("ticker", ""),
        transaction_date=transaction.get("transaction_date_actual"),
        published_at=transaction.get("transaction_date_reported"),
        reporting_lag_days=transaction.get("reporting_lag_days", 0),
    )

    layer_results: dict = {}

    # -------------------------------------------------------------------
    # Layer 1 — Platform Assessment
    # -------------------------------------------------------------------
    try:
        l1_inputs = build_layer1_inputs(transaction, company_profile)
        l1_executor = _make_layer1_executor(price_cache)
        l1_model = cfg.get("layer1_model", "claude-haiku-4-5-20251001")
        logger.info("layer1: starting — %s", transaction.get("ticker"))
        l1_output, l1_tokens = run_layer_agent(
            layer=1,
            inputs=l1_inputs,
            tools=LAYER1_TOOLS,
            model=l1_model,
            client=client,
            executor=l1_executor,
        )
    except Exception as exc:
        logger.error("layer1: unexpected failure: %s", exc)
        l1_output = None
        l1_tokens = {"input": 0, "output": 0, "model": cfg.get("layer1_model", "")}
    layer_results["layer1"] = {"output": l1_output, "tokens": l1_tokens}

    # -------------------------------------------------------------------
    # Layer 2 — Director Purchase Analysis
    # -------------------------------------------------------------------
    try:
        l2_inputs = build_layer2_inputs(transaction)
        l2_inputs["rns_article_id"] = rns_article_id
        l2_executor = _make_layer2_executor()
        use_credibility = should_trigger_credibility_research(transaction, cfg)
        l2_model = (
            cfg.get("layer2_model_credibility", "claude-sonnet-4-6")
            if use_credibility
            else cfg.get("layer2_model_standard", "claude-haiku-4-5-20251001")
        )
        logger.info(
            "layer2: starting — %s (model=%s credibility=%s)",
            transaction.get("ticker"), l2_model, use_credibility,
        )
        l2_output, l2_tokens = run_layer_agent(
            layer=2,
            inputs=l2_inputs,
            tools=LAYER2_TOOLS,
            model=l2_model,
            client=client,
            executor=l2_executor,
        )
    except Exception as exc:
        logger.error("layer2: unexpected failure: %s", exc)
        l2_output = None
        l2_tokens = {"input": 0, "output": 0, "model": cfg.get("layer2_model_standard", "")}
    layer_results["layer2"] = {"output": l2_output, "tokens": l2_tokens}

    # -------------------------------------------------------------------
    # Layer 3 — Signal Freshness
    # -------------------------------------------------------------------
    try:
        ann = {**announcement, "rns_article_id": rns_article_id}
        l3_inputs = build_layer3_inputs(transaction, ann, company_profile, price_cache)
        l3_executor = _make_layer3_executor(price_cache)
        l3_model = cfg.get("layer3_model", "claude-sonnet-4-6")
        logger.info("layer3: starting — %s", transaction.get("ticker"))
        l3_output, l3_tokens = run_layer_agent(
            layer=3,
            inputs=l3_inputs,
            tools=LAYER3_TOOLS,
            model=l3_model,
            client=client,
            executor=l3_executor,
        )
    except Exception as exc:
        logger.error("layer3: unexpected failure: %s", exc)
        l3_output = None
        l3_tokens = {"input": 0, "output": 0, "model": cfg.get("layer3_model", "")}
    layer_results["layer3"] = {"output": l3_output, "tokens": l3_tokens}

    # -------------------------------------------------------------------
    # Persist all layer outputs to Firestore
    # -------------------------------------------------------------------
    persist_layer_outputs(
        rns_article_id=rns_article_id,
        layer1_output=l1_output,
        layer2_output=l2_output,
        layer3_output=l3_output,
        db=db,
    )

    elapsed = (datetime.now(tz=timezone.utc) - started_at).total_seconds()
    logger.info(
        "agentic_sequential: complete in %.1fs — l1=%s l2=%s l3=%s",
        elapsed,
        "ok" if l1_output and "_error" not in l1_output else "failed",
        "ok" if l2_output and "_error" not in l2_output else "failed",
        "ok" if l3_output and "_error" not in l3_output else "failed",
    )

    return layer_results
