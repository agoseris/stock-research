"""
Orchestrator configuration constants.

Model pricing (GBP) should be updated if Anthropic changes pricing.
Model IDs should be updated when newer versions become available.
"""

ORCHESTRATOR_CONFIG = {
    # --- Models ---
    # Classification accuracy is critical — always use Sonnet
    "classification_model": "claude-sonnet-4-6",
    # TR-1 simple lens and investor research — Gemini Flash (free tier)
    "tr1_simple_lens_model": "gemini-2.0-flash",
    "tr1_investor_research_model": "gemini-2.0-flash",
    # Simple lens is one-shot from pre-extracted data — Haiku is sufficient
    "simple_lens_model": "claude-haiku-4-5-20251001",
    # Layer 1: platform assessment — moderate reasoning, Haiku appropriate
    "layer1_model": "claude-haiku-4-5-20251001",
    # Layer 2: director analysis — Haiku unless credibility research triggered
    "layer2_model_standard": "claude-haiku-4-5-20251001",
    # Layer 2 credibility branch: Sonnet for director track record reasoning
    "layer2_model_credibility": "claude-sonnet-4-6",
    # Layer 3: signal freshness — reasoning-heavy, requires Sonnet
    "layer3_model": "claude-sonnet-4-6",
    # Synthesis: cross-layer reasoning, financial decision context — Sonnet
    "synthesis_model": "claude-sonnet-4-6",

    # --- Timeouts ---
    "layer_timeout_seconds": 300,       # 5 minutes per layer (parallel)
    "tool_call_timeout_seconds": 30,    # per individual tool call

    # --- Rate limiting ---
    "yfinance_delay_seconds": 2,        # between yfinance fetches
    "ch_api_delay_seconds": 1,          # between Companies House API calls

    # --- Thresholds ---
    # Credibility research triggered if resulting_holding_pct < this
    "credibility_holding_pct_threshold": 0.5,
    "cluster_detection_window_days": 30,
    "insider_activity_lookback_days": 90,

    # --- Pricing in GBP (update if Anthropic changes pricing) ---
    "haiku_input_cost_per_1m_tokens": 0.63,
    "haiku_output_cost_per_1m_tokens": 1.26,
    "sonnet_input_cost_per_1m_tokens": 2.36,
    "sonnet_output_cost_per_1m_tokens": 11.81,
}

# Article-type TTL in days for the announcements collection base record
ANNOUNCEMENT_TTL_DAYS = {
    "pdmr_transaction": None,      # no expiry on base record
    "tr1_crossing": None,          # no expiry — retained alongside pdmr
    "regulatory_catalyst": 90,     # 3 months
    "substantive_news": 90,        # 3 months
    "administrative": 14,          # 2 weeks
    "unclassified": None,          # no expiry — age out naturally
}

# TTL in days for child collections
PDMR_TRANSACTION_TTL_DAYS = 730        # 24 months
COMPANY_NEWS_SUMMARY_TTL_DAYS = 90     # 3 months
SIGNAL_TTL_DAYS = 730                  # 24 months
