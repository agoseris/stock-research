"""
app.py

Streamlit interface for the AI Stock Research Tool.
Reads signal and discovery results from Firestore.
Supports dismissing reviewed signals.

Run with:
    streamlit run app.py
"""

import streamlit as st
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

VERSION = "2.63"

# ── Page config ─────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="LSE Research Terminal",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Styling ──────────────────────────────────────────────────────────────────────

from styles import apply_styles
apply_styles()

# ── Module imports ───────────────────────────────────────────────────────────────

from constants import PositionState, LegacyRec
from firestore_helpers import (
    get_db,
    get_signal_results,
    get_signal_results_all,
    get_discovery_results,
    get_discovery_results_all,
    get_all_universe_companies,
    get_all_signals_for_ticker,
    get_director_signals,
)
from ui_helpers import get_field
from tab_signals import render_signals_tab
from tab_ingest import render_ingest_tab
from tab_performance import render_performance_tab
from tab_discovery import render_discovery_tab
from tab_universe import render_universe_tab
from tab_config import render_config_tab

# ── Header ───────────────────────────────────────────────────────────────────────

db = get_db()

st.markdown(f'<div class="terminal-header">LSE Small-Cap Research System · Director Buying · TR-1 Accumulation · Regulatory Catalyst · v{VERSION}</div>', unsafe_allow_html=True)
st.markdown('<div class="page-title">Catalyst Monitor</div>', unsafe_allow_html=True)
st.markdown('<div class="page-subtitle">Three lenses · Informed actors surfaced by autonomous pipeline · Universe expands only through explicit decision</div>', unsafe_allow_html=True)

# ── Load data ────────────────────────────────────────────────────────────────────

try:
    signals = get_signal_results(db)
except Exception:
    signals = get_signal_results_all(db)

director_signals = get_director_signals(db)

try:
    discoveries = get_discovery_results(db)
except Exception:
    discoveries = get_discovery_results_all(db)

# Build a ticker → company dict for signal/position state lookups in the Signals tab.
# Uses the cached bulk fetch — no extra Firestore reads.
company_map = {
    c["ticker_lse"].upper(): c
    for c in get_all_universe_companies(db)
    if c.get("ticker_lse")
}

# Top-up pass: guarantee acted/deferred signals are always visible regardless of
# the main fetch limit. Identifies any acted/deferred tickers absent from the
# result set and fetches their most recent signal directly.
_priority_tickers = {
    t for t, c in company_map.items()
    if c.get("position_state") in (PositionState.ACTED, PositionState.DEFERRED)
}
_loaded_tickers = {(r.get("ticker") or "").upper() for _, r in signals}
for _t in sorted(_priority_tickers - _loaded_tickers):
    _fresh = get_all_signals_for_ticker(db, _t)
    if _fresh:
        signals.append(_fresh[0])

# Count actionable items — across all lenses
action_count = sum(
    1 for _, r in signals
    if get_field(r.get("llm_analysis", ""), "RECOMMENDED_ACTION").lower() == LegacyRec.YES
)
monitor_count = sum(
    1 for _, r in signals
    if get_field(r.get("llm_analysis", ""), "RECOMMENDED_ACTION").lower() == LegacyRec.MONITOR
)

# Director / TR-1 signals: recommendation from simple_lens_result or simple_recommended_action
for _, r in (director_signals or []):
    _simple_rec = (
        (r.get("simple_lens_result") or {}).get("recommendation")  # TR-1
        or r.get("simple_recommended_action")                        # director buying
        or ""
    ).lower()
    if _simple_rec.startswith(LegacyRec.INVESTIGATE):
        action_count += 1
    elif _simple_rec.startswith(LegacyRec.MONITOR):
        monitor_count += 1

discovery_count = sum(
    1 for _, r in discoveries
    if get_field(r.get("discovery_assessment", ""), "RECOMMEND_ADD").lower() in (LegacyRec.YES, "maybe")
)

# ── Stat bar ─────────────────────────────────────────────────────────────────────

_dir_signal_count = len(director_signals or [])
st.markdown(f"""
<div class="stat-bar">
    <div class="stat-item">
        <span class="stat-label">Investigate</span>
        <span class="stat-value alert">{action_count}</span>
    </div>
    <div class="stat-item">
        <span class="stat-label">Monitor</span>
        <span class="stat-value">{monitor_count}</span>
    </div>
    <div class="stat-item">
        <span class="stat-label">Catalyst Signals</span>
        <span class="stat-value">{len(signals)}</span>
    </div>
    <div class="stat-item">
        <span class="stat-label">Director / TR-1</span>
        <span class="stat-value">{_dir_signal_count}</span>
    </div>
    <div class="stat-item">
        <span class="stat-label">Discovery Queue</span>
        <span class="stat-value">{len(discoveries)}</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Navigation (tabs) ────────────────────────────────────────────────────────────

_NAV_OPTIONS = [
    f"Signals  [{len(signals) + len(director_signals or [])}]",
    f"Discovery  [{len(discoveries)}]",
    "Universe",
    "Ingest",
    "Performance",
    "Config",
]

(tab_signals, tab_discovery, tab_universe,
 tab_ingest, tab_performance, tab_config) = st.tabs(_NAV_OPTIONS)

# ── Signals ──────────────────────────────────────────────────────────────────────

with tab_signals:
    render_signals_tab(db, signals, company_map, director_signals=director_signals)

# ── Discovery tab ────────────────────────────────────────────────────────────────

with tab_discovery:
    render_discovery_tab(db, discoveries)

# ── Universe tab ─────────────────────────────────────────────────────────────────

with tab_universe:
    render_universe_tab(db)

# ── Ingest tab ───────────────────────────────────────────────────────────────────

with tab_ingest:
    render_ingest_tab(db)

# ── Performance tab ──────────────────────────────────────────────────────────────

with tab_performance:
    render_performance_tab(db, company_map)

# ── Config tab ───────────────────────────────────────────────────────────────────

with tab_config:
    render_config_tab(db)
