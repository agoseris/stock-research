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
from dotenv import load_dotenv

load_dotenv()

VERSION = "2.36"

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="LSE Research Terminal",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Styling ────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
    background-color: #0a0e14;
    color: #c5cdd9;
}

/* Hide Streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding: 2rem 2.5rem 2rem 2.5rem; max-width: 1400px; }

/* Terminal header */
.terminal-header {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem;
    color: #5a7a9a;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    margin-bottom: 0.25rem;
}

.page-title {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.6rem;
    font-weight: 600;
    color: #e8f0fa;
    letter-spacing: -0.02em;
    margin-bottom: 0;
    line-height: 1.2;
}

.page-subtitle {
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 0.85rem;
    color: #7a9ab8;
    margin-top: 0.3rem;
    margin-bottom: 2rem;
    font-weight: 300;
}

/* Stat bar */
.stat-bar {
    display: flex;
    gap: 2rem;
    padding: 0.9rem 1.2rem;
    background: #0f1520;
    border: 1px solid #1a2535;
    border-radius: 4px;
    margin-bottom: 2rem;
}
.stat-item { display: flex; flex-direction: column; gap: 0.15rem; }
.stat-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.6rem;
    color: #5a7a9a;
    text-transform: uppercase;
    letter-spacing: 0.12em;
}
.stat-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.1rem;
    font-weight: 600;
    color: #7eb8f7;
}
.stat-value.alert { color: #f7a84a; }
.stat-value.positive { color: #4af7a0; }

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    background: transparent;
    border-bottom: 1px solid #1a2535;
    margin-bottom: 1.5rem;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #5a7a9a;
    background: transparent;
    border: none;
    padding: 0.6rem 1.2rem;
    border-bottom: 2px solid transparent;
}
.stTabs [aria-selected="true"] {
    color: #7eb8f7 !important;
    border-bottom: 2px solid #7eb8f7 !important;
    background: transparent !important;
}

/* Signal cards — full size (Discovery tab) */
.signal-card {
    background: #0f1520;
    border: 1px solid #1a2535;
    border-left: 3px solid #1a2535;
    border-radius: 4px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1rem;
    transition: border-color 0.2s;
}
.signal-card.action-yes { border-left-color: #f7a84a; }
.signal-card.action-monitor { border-left-color: #7eb8f7; }
.signal-card.action-no { border-left-color: #1a2535; }
.signal-card.discovery { border-left-color: #4af7a0; }

/* Signal cards — compact (Signals tab) */
.signal-card-compact {
    background: #0f1520;
    border: 1px solid #1a2535;
    border-left: 3px solid #1a2535;
    border-radius: 4px;
    padding: 0.75rem 1.1rem;
    margin-bottom: 0.4rem;
}
.signal-card-compact.action-yes { border-left-color: #f7a84a; }
.signal-card-compact.action-monitor { border-left-color: #7eb8f7; }
.signal-card-compact.action-no { border-left-color: #1a2535; }
.signal-card-compact.urgent { border-left-color: #e55353 !important; background: #140c0c; }

/* Compact card layout */
.card-row-top {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.2rem;
}
.card-market {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.68rem;
    color: #8aabcc;
    white-space: nowrap;
}
.card-price-up   { color: #4af7a0; }
.card-price-down { color: #e55353; }
.card-price-neutral { color: #8aabcc; }
.card-price-null { color: #4a6080; }
.card-price      { color: #8aabcc; }
.card-badges     { margin-top: 0.45rem; }

.card-ticker {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem;
    font-weight: 600;
    color: #7eb8f7;
    letter-spacing: 0.1em;
}
.card-company {
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 0.78rem;
    color: #8aabcc;
    margin-left: 0.6rem;
}
.card-headline {
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 0.95rem;
    color: #c5cdd9;
    font-weight: 500;
    margin: 0.4rem 0 0.2rem 0;
    line-height: 1.4;
}
.card-meta {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.62rem;
    color: #5a7a9a;
    margin-bottom: 0.8rem;
}

/* Badge */
.badge {
    display: inline-block;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.6rem;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    padding: 0.2rem 0.5rem;
    border-radius: 2px;
    margin-right: 0.4rem;
}
.badge-yes { background: #2a1f0a; color: #f7a84a; border: 1px solid #f7a84a44; }
.badge-monitor { background: #0a1525; color: #7eb8f7; border: 1px solid #7eb8f744; }
.badge-no { background: #111820; color: #3d5166; border: 1px solid #1a253544; }
.badge-maybe { background: #0a1f14; color: #4af7a0; border: 1px solid #4af7a044; }
.badge-source { background: #111820; color: #3d5166; border: 1px solid #1a253544; }

/* Signal state badges */
.badge-state-watching   { background: #0e1a26; color: #3d5166; border: 1px solid #1a253544; }
.badge-state-monitor    { background: #0a1525; color: #7eb8f7; border: 1px solid #7eb8f744; }
.badge-state-active     { background: #2a1f0a; color: #f7a84a; border: 1px solid #f7a84a44; }
.badge-state-reinforced { background: #2a280a; color: #ffca28; border: 1px solid #ffca2844; }
.badge-state-mixed      { background: #2a230a; color: #ffd740; border: 1px solid #ffd74044; }
.badge-state-negative   { background: #2a0a0a; color: #e55353; border: 1px solid #e5535344; }

/* Position state badges */
.badge-pos-acted    { background: #0a2a1a; color: #4af7a0; border: 1px solid #4af7a044; }
.badge-pos-deferred { background: #2a2a0a; color: #f7e14a; border: 1px solid #f7e14a44; }
.badge-pos-declined { background: #0e1a26; color: #4a6080; border: 1px solid #1a253544; }
.badge-pos-closed   { background: #0e1a26; color: #3d5166; border: 1px solid #1a253544; }

/* Urgency card variant */
.signal-card.urgent,
.signal-card-compact.urgent { border-left-color: #e55353 !important; background: #140c0c; }
.urgency-banner {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.62rem;
    font-weight: 600;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: #e55353;
    margin-bottom: 0.6rem;
}

/* Signal history row */
.history-row {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.64rem;
    color: #7a9ab8;
    padding: 0.25rem 0;
    border-bottom: 1px solid #0f1a26;
    line-height: 1.6;
}
.hist-ts { color: #5a7a9a; }
.hist-states { color: #a0c0d8; }

/* Analysis block */
.analysis-block {
    background: #080c12;
    border: 1px solid #131e2e;
    border-radius: 3px;
    padding: 0.9rem 1rem;
    margin-top: 0.6rem;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
    color: #8aabcc;
    line-height: 1.8;
    white-space: pre-wrap;
}

/* Dividers within analysis */
.analysis-key { color: #5a7a9a; }
.analysis-val { color: #a0c0d8; }

/* Empty state */
.empty-state {
    text-align: center;
    padding: 4rem 2rem;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem;
    color: #2d3f52;
    letter-spacing: 0.1em;
}

/* Dismiss button */
.stButton button {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.62rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    background: transparent;
    border: 1px solid #2a3f55;
    color: #5a7a9a;
    padding: 0.25rem 0.7rem;
    border-radius: 2px;
    transition: all 0.15s;
}
.stButton button:hover {
    border-color: #c0392b44;
    color: #c0392b;
    background: #1a080844;
}

/* Expander */
.streamlit-expanderHeader {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.68rem;
    color: #5a7a9a !important;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    background: transparent !important;
    border: none !important;
}
</style>
""", unsafe_allow_html=True)

from constants import EMOJI_MUTE, _OUTCOME_ORDER, _OUTCOME_STYLE
from firestore_helpers import (
    get_db,
    get_signal_results,
    get_signal_results_all,
    get_discovery_results,
    get_discovery_results_all,
    dismiss_document,
    get_exclusion_list,
    save_exclusion_list,
    get_company_keywords,
    save_company_keywords,
    get_universe_tickers,
    get_not_of_interest_tickers,
    get_all_universe_companies,
    get_universe_stats,
    mark_not_of_interest,
    submit_universe_admit_job,
    submit_universe_bulk_import_job,
    get_pending_jobs,
    _get_processed_source_urls,
    submit_job,
    get_signal_history_for_ticker,
    set_position_state,
    delete_signal_result,
)
from parse_helpers import (
    _parse_lseg_excel, _parse_universe_csv, _compute_universe_delta,
    _filter_announcement_rows,
)
from ui_helpers import (
    parse_analysis,
    get_field,
    recommended_action_badge,
    recommend_add_badge,
    format_timestamp,
    signal_state_badge,
    position_state_badge,
    format_signal_age,
    format_market_cap,
    format_price_info,
)

try:
    from lseg_scraper import (
        fetch_announcement_body as _fetch_lseg_body,
        fetch_announcement_index as _fetch_lseg_index,
    )
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

# ── Header ─────────────────────────────────────────────────────────────────────

db = get_db()

st.markdown(f'<div class="terminal-header">LSE Small-Cap Research System · Regulatory Catalyst Lens · v{VERSION}</div>', unsafe_allow_html=True)
st.markdown('<div class="page-title">Research Terminal</div>', unsafe_allow_html=True)
st.markdown('<div class="page-subtitle">Signals surfaced by autonomous pipeline · Dismiss to archive · Universe expands only through explicit decision</div>', unsafe_allow_html=True)

# ── Load data ──────────────────────────────────────────────────────────────────

try:
    signals = get_signal_results(db)
except Exception:
    signals = get_signal_results_all(db)

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

# Count actionable items
action_count = sum(
    1 for _, r in signals
    if get_field(r.get("llm_analysis", ""), "RECOMMENDED_ACTION").lower() == "yes"
)
monitor_count = sum(
    1 for _, r in signals
    if get_field(r.get("llm_analysis", ""), "RECOMMENDED_ACTION").lower() == "monitor"
)
discovery_count = sum(
    1 for _, r in discoveries
    if get_field(r.get("discovery_assessment", ""), "RECOMMEND_ADD").lower() in ("yes", "maybe")
)

# ── Stat bar ───────────────────────────────────────────────────────────────────

st.markdown(f"""
<div class="stat-bar">
    <div class="stat-item">
        <span class="stat-label">Action Required</span>
        <span class="stat-value alert">{action_count}</span>
    </div>
    <div class="stat-item">
        <span class="stat-label">Monitor</span>
        <span class="stat-value">{monitor_count}</span>
    </div>
    <div class="stat-item">
        <span class="stat-label">Total Signals</span>
        <span class="stat-value">{len(signals)}</span>
    </div>
    <div class="stat-item">
        <span class="stat-label">Universe Candidates</span>
        <span class="stat-value positive">{discovery_count}</span>
    </div>
    <div class="stat-item">
        <span class="stat-label">Discovery Queue</span>
        <span class="stat-value">{len(discoveries)}</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Tabs ───────────────────────────────────────────────────────────────────────

tab_signals, tab_discovery, tab_universe, tab_ingest, tab_config = st.tabs([
    f"Signals  [{len(signals)}]",
    f"Discovery  [{len(discoveries)}]",
    "Universe",
    "Ingest",
    "Config",
])

# ── Signals tab ────────────────────────────────────────────────────────────────

with tab_signals:
    # ── Filter bar
    fc1, fc2, fc3 = st.columns([5, 2, 2])
    with fc1:
        pos_filter = st.radio(
            "Position",
            ["All", "Unreviewed", "Acted", "Deferred", "Declined"],
            horizontal=True,
            key="sig_pos_filter",
        )
    with fc2:
        since_filter = st.selectbox(
            "Since",
            ["All time", "Today", "This week", "This month"],
            key="sig_since_filter",
        )
    with fc3:
        hide_no_action = st.checkbox("Hide 'No action' signals", value=True, key="sig_hide_no_action")

    # ── Compute time cutoff
    _since_map = {
        "Today":      datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0),
        "This week":  datetime.now(timezone.utc) - timedelta(days=7),
        "This month": datetime.now(timezone.utc) - timedelta(days=30),
    }
    since_cutoff = _since_map.get(since_filter)

    def _passes_filters(result, company):
        pos = (company.get("position_state") or "").strip()
        if pos_filter == "Unreviewed" and pos:
            return False
        if pos_filter == "Acted"    and pos != "acted":
            return False
        if pos_filter == "Deferred" and pos != "deferred":
            return False
        if pos_filter == "Declined" and pos != "declined":
            return False
        if since_cutoff:
            try:
                dt = datetime.fromisoformat(result.get("analysed_at", "").replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < since_cutoff:
                    return False
            except Exception:
                pass
        return True

    # ── Classify into groups
    grp_urgent, grp_action, grp_monitor, grp_no_action = [], [], [], []
    for doc_id, result in signals:
        company = company_map.get((result.get("ticker") or "").upper(), {})
        if not _passes_filters(result, company):
            continue
        action_val = get_field(result.get("llm_analysis", ""), "RECOMMENDED_ACTION").lower()
        if action_val not in ("yes", "monitor") and hide_no_action:
            continue
        pos = (company.get("position_state") or "").strip()
        sig = company.get("signal_state") or "watching"
        item = (doc_id, result, company)
        if pos == "acted" and sig in ("signal_negative", "signal_mixed"):
            grp_urgent.append(item)
        elif action_val == "yes":
            grp_action.append(item)
        elif action_val == "monitor":
            grp_monitor.append(item)
        else:
            grp_no_action.append(item)

    total_shown = len(grp_urgent) + len(grp_action) + len(grp_monitor) + len(grp_no_action)

    if total_shown == 0:
        st.markdown(
            '<div class="empty-state">NO SIGNALS MATCH CURRENT FILTERS</div>',
            unsafe_allow_html=True,
        )
    else:
        # ── Card renderer (defined once, called per group)
        def _render_signal_card(doc_id, result, company):
            analysis    = result.get("llm_analysis", "")
            ticker      = result.get("ticker") or "—"
            co_name     = result.get("company_name") or ""
            headline    = result.get("headline") or "—"
            source      = result.get("source") or "—"
            analysed_at = result.get("analysed_at") or ""
            summary     = get_field(analysis, "SUMMARY")

            sig_state = company.get("signal_state") or "watching"
            pos_state = (company.get("position_state") or "").strip()
            sig_age   = format_signal_age(company.get("signal_state_since"))
            is_urgent = pos_state == "acted" and sig_state in ("signal_negative", "signal_mixed")

            badge_html, card_class = recommended_action_badge(analysis)
            classes    = f"signal-card-compact {card_class}" + (" urgent" if is_urgent else "")
            state_badge = signal_state_badge(sig_state, sig_age)
            pos_badge   = position_state_badge(pos_state)
            source_badge = f'<span class="badge badge-source">{source}</span>'

            mkt = result.get("market_cap_gbp") or company.get("market_cap_gbp")
            mkt_str   = format_market_cap(mkt)
            price_html = format_price_info(result.get("price_pence"), result.get("price_change"))

            urgency_html = (
                '<div class="urgency-banner">⚠ COUNTER-SIGNAL — REVIEW POSITION</div>'
                if is_urgent else ""
            )

            card_html = (
                f'<div class="{classes}">'
                f'{urgency_html}'
                f'<div class="card-row-top">'
                f'<span><span class="card-ticker">{ticker}</span>'
                f'<span class="card-company">{co_name}</span></span>'
                f'<span class="card-market">{mkt_str} &nbsp;·&nbsp; {price_html}</span>'
                f'</div>'
                f'<div class="card-headline">{headline}</div>'
                f'<div class="card-meta">{format_timestamp(analysed_at)} &nbsp;·&nbsp; {source}</div>'
                f'<div class="card-badges">{badge_html}{state_badge}{pos_badge}</div>'
                f'</div>'
            )
            st.markdown(card_html, unsafe_allow_html=True)

            col_exp, col_act, col_defer, col_decline, col_dismiss, col_hist = st.columns(
                [4, 1, 1, 1, 1, 1]
            )
            with col_exp:
                with st.expander("Summary + Full analysis"):
                    if summary:
                        st.markdown(
                            f'<div style="font-size:0.85rem;color:#a0c0d8;font-family:IBM Plex Sans,'
                            f'sans-serif;line-height:1.6;margin-bottom:0.8rem;">{summary}</div>',
                            unsafe_allow_html=True,
                        )
                    parsed = parse_analysis(analysis)
                    formatted = "\n".join(f"{k}: {v}" if k else v for k, v in parsed)
                    st.markdown(f'<div class="analysis-block">{formatted}</div>', unsafe_allow_html=True)

            with col_act:
                lbl = "✓ Acted" if pos_state == "acted" else "Act"
                if st.button(lbl, key=f"act_{doc_id}",
                             help="Record that you have taken a position. Counter-signals on this company will be surfaced as urgent."):
                    set_position_state(db, ticker, "acted")
                    st.rerun()

            with col_defer:
                lbl = "⏸ Deferred" if pos_state == "deferred" else "Defer"
                if st.button(lbl, key=f"defer_{doc_id}",
                             help="Interested but not acting now. Useful for pipeline companies or illiquid situations."):
                    set_position_state(db, ticker, "deferred")
                    st.rerun()

            with col_decline:
                lbl = "✗ Declined" if pos_state == "declined" else "Decline"
                if st.button(lbl, key=f"decline_{doc_id}",
                             help="Record that you have decided against this opportunity. The company remains monitored and new signals will still appear."):
                    set_position_state(db, ticker, "declined")
                    st.rerun()

            with col_dismiss:
                if st.button("Dismiss", key=f"dismiss_{doc_id}",
                             help="Permanently delete this result. Use for noise — announcements that should not have surfaced. The company remains monitored."):
                    delete_signal_result(db, doc_id)
                    st.rerun()

            with col_hist:
                hist_key = f"show_hist_{doc_id}"
                if hist_key not in st.session_state:
                    st.session_state[hist_key] = False
                lbl = "Hist ▲" if st.session_state[hist_key] else "Hist"
                if st.button(lbl, key=f"hist_{doc_id}",
                             help="Show the full signal state transition history for this company."):
                    st.session_state[hist_key] = not st.session_state[hist_key]
                    st.rerun()

            if st.session_state.get(f"show_hist_{doc_id}"):
                history = get_signal_history_for_ticker(db, ticker)
                if not history:
                    st.caption("No signal history recorded yet for this company.")
                else:
                    rows = []
                    for h in history:
                        ts       = format_timestamp(h.get("timestamp", ""))
                        prev     = h.get("previous_state", "—")
                        nxt      = h.get("new_state", "—")
                        strength = h.get("signal_strength", "")
                        lens     = h.get("lens", "")
                        s_html   = f" &nbsp;·&nbsp; {strength}" if strength else ""
                        l_html   = f" &nbsp;·&nbsp; {lens}" if lens else ""
                        rows.append(
                            f'<div class="history-row">'
                            f'<span class="hist-ts">{ts}</span>'
                            f' &nbsp;·&nbsp; <span class="hist-states">{prev} → {nxt}</span>'
                            f'{s_html}{l_html}'
                            f'</div>'
                        )
                    st.markdown("".join(rows), unsafe_allow_html=True)

        # ── Render groups
        if grp_urgent:
            with st.expander(f"🚨  Urgent — {len(grp_urgent)}", expanded=True):
                for item in grp_urgent:
                    _render_signal_card(*item)

        if grp_action:
            with st.expander(f"⬆  Action Required — {len(grp_action)}", expanded=True):
                for item in grp_action:
                    _render_signal_card(*item)

        if grp_monitor:
            with st.expander(f"◉  Monitor — {len(grp_monitor)}", expanded=True):
                for item in grp_monitor:
                    _render_signal_card(*item)

        if not hide_no_action and grp_no_action:
            with st.expander(f"—  No action — {len(grp_no_action)}", expanded=False):
                for item in grp_no_action:
                    _render_signal_card(*item)

# ── Discovery tab ──────────────────────────────────────────────────────────────

with tab_discovery:
    if not discoveries:
        st.markdown('<div class="empty-state">NO DISCOVERY ITEMS</div>', unsafe_allow_html=True)
        st.caption(
            "Discovery results are created when the job runner processes a company that is "
            "not in the monitored universe. To generate one: upload an LSEG export in the "
            "Ingest tab, find a row with outcome DISCOVERY, click → Pass to move it to "
            "Passed, then submit it for analysis. The job runner routes non-universe "
            "submissions here automatically."
        )
    else:
        def discovery_sort_key(item):
            val = get_field(item[1].get("discovery_assessment", ""), "RECOMMEND_ADD").lower()
            return {"yes": 0, "maybe": 1}.get(val, 2)

        for doc_id, result in sorted(discoveries, key=discovery_sort_key):
            assessment = result.get("discovery_assessment", "")
            badge_html, card_class = recommend_add_badge(assessment)
            company = get_field(assessment, "COMPANY")
            reason = get_field(assessment, "REASON")
            thesis_fit = get_field(assessment, "THESIS_FIT")
            source_badge = f'<span class="badge badge-source">{result.get("source", "—")}</span>'
            thesis_badge = f'<span class="badge badge-source">Thesis fit {thesis_fit}</span>' if thesis_fit else ""

            st.markdown(f"""
<div class="signal-card {card_class}">
    <div>
        <span class="card-ticker">{company or result.get("company_name", "—")}</span>
    </div>
    <div class="card-headline">{result.get("headline", "—")}</div>
    <div class="card-meta">{format_timestamp(result.get("assessed_at", ""))}</div>
    <div>{badge_html}{source_badge}{thesis_badge}</div>
    {f'<div style="margin-top:0.7rem;font-size:0.82rem;color:#7a9ab8;font-family:IBM Plex Sans,sans-serif;line-height:1.5;">{reason}</div>' if reason else ''}
</div>
""", unsafe_allow_html=True)

            col1, col2 = st.columns([8, 1])
            with col1:
                with st.expander("Full assessment"):
                    parsed = parse_analysis(assessment)
                    formatted = "\n".join(
                        f"{k}: {v}" if k else v
                        for k, v in parsed
                    )
                    st.markdown(f'<div class="analysis-block">{formatted}</div>', unsafe_allow_html=True)

                with st.expander("Admit to Universe"):
                    admit_ticker = st.text_input(
                        "Ticker", value=result.get("ticker", ""),
                        key=f"disc_admit_ticker_{doc_id}"
                    )
                    admit_name = st.text_input(
                        "Company Name", value=result.get("company_name", ""),
                        key=f"disc_admit_name_{doc_id}"
                    )
                    admit_exchange = st.selectbox(
                        "Exchange", ["AIM", "LSE Main"],
                        key=f"disc_admit_exchange_{doc_id}"
                    )
                    admit_mcap = st.number_input(
                        "Market Cap (£M)", min_value=0.0,
                        value=0.0, key=f"disc_admit_mcap_{doc_id}"
                    )
                    if st.button("Submit for admission", key=f"disc_admit_submit_{doc_id}"):
                        exch_code = "AIM" if admit_exchange == "AIM" else "LSE_MAIN"
                        mcap_gbp = admit_mcap * 1_000_000 if admit_mcap > 0 else None
                        submit_universe_admit_job(
                            db, admit_ticker, admit_name, mcap_gbp, exch_code,
                            not_of_interest=False, source_discovery_id=doc_id
                        )
                        st.success("Admission job submitted — CH lookup running on VM.")

            with col2:
                if st.button("Dismiss", key=f"dismiss_disc_{doc_id}"):
                    dismiss_document(db, "discovery_results", doc_id)
                    st.rerun()

# ── Universe tab ───────────────────────────────────────────────────────────────

with tab_universe:
    st.markdown("""
**Monitored Universe** — LSE-listed small-cap companies actively monitored for investment signals.

**Inclusion criteria:**
- Listed on AIM or FTSE Main Market
- Active operating company (trusts, funds, SPACs excluded)

**Companies House matching:**
- Confidence 1.00 = exact name match · 0.85–0.99 = fuzzy match (acceptable) · No match = offshore-incorporated or unmatched
""")

    # Stats bar
    try:
        stats = get_universe_stats(db)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total", stats["total"])
        c2.metric("AIM", stats["aim"])
        c3.metric("FTSE Main", stats["ftse"])
        c4.metric("CH Matched", stats["ch_matched"])
        c5.metric("Muted", stats["muted"])
    except Exception as e:
        st.caption(f"Could not load universe stats: {e}")

    st.markdown("---")

    # Search controls
    col_search, col_exchange, col_show = st.columns([3, 1, 1])
    query = col_search.text_input("Search ticker or company name", placeholder="e.g. GMR or Phoenix", key="universe_search")
    exchange_filter = col_exchange.selectbox("Exchange", ["All", "AIM", "LSE Main"], key="universe_exchange")
    show_muted = col_show.checkbox("Show muted", value=False, key="universe_show_muted")

    # Page size — reset page on any filter change
    page_size = st.selectbox("Rows per page", [25, 50, 100], key="universe_page_size")

    # Load and filter companies
    try:
        all_companies = get_all_universe_companies(db)
    except Exception as e:
        all_companies = []
        st.caption(f"Could not load universe: {e}")

    filtered = all_companies
    if not show_muted:
        filtered = [c for c in filtered if not c.get("not_of_interest", False)]
    if exchange_filter == "AIM":
        filtered = [c for c in filtered if c.get("listing_exchange") == "AIM"]
    elif exchange_filter == "LSE Main":
        filtered = [c for c in filtered if c.get("listing_exchange") == "LSE_MAIN"]
    if query.strip():
        q = query.strip().lower()
        filtered = [
            c for c in filtered
            if q in c.get("ticker_lse", "").lower()
            or q in c.get("company_name", "").lower()
        ]

    # Pagination state — reset to page 0 on filter/size change
    filter_key = (query, exchange_filter, show_muted, page_size)
    if st.session_state.get("universe_filter_key") != filter_key:
        st.session_state["universe_filter_key"] = filter_key
        st.session_state["universe_page"] = 0

    total_filtered = len(filtered)
    total_pages = max(1, (total_filtered + page_size - 1) // page_size)
    current_page = st.session_state.get("universe_page", 0)
    current_page = min(current_page, total_pages - 1)

    start = current_page * page_size
    page_companies = filtered[start: start + page_size]

    st.caption(f"{total_filtered} companies · page {current_page + 1} of {total_pages}")

    # Pagination controls (top)
    pg_prev, pg_next = st.columns([1, 1])
    if pg_prev.button("← Prev", disabled=(current_page == 0), key="universe_prev"):
        st.session_state["universe_page"] = current_page - 1
        st.rerun()
    if pg_next.button("Next →", disabled=(current_page >= total_pages - 1), key="universe_next"):
        st.session_state["universe_page"] = current_page + 1
        st.rerun()

    if page_companies:
        # Column headers
        hdr = st.columns([1.2, 4, 1.2, 1.5, 1.5, 1, 1.5, 1.5])
        for col, lbl in zip(hdr, ["Ticker", "Company", "Exchange", "Mkt Cap (£M)", "CH Conf", "Muted", "Added", "Action"]):
            col.caption(f"**{lbl}**")
        st.markdown('<hr style="margin:0.2rem 0 0.5rem 0;border-color:#1a2535;"/>', unsafe_allow_html=True)

        for idx, c in enumerate(page_companies):
            ticker = c.get("ticker_lse", "")
            is_muted = c.get("not_of_interest", False)
            mcap = c.get("market_cap_gbp")
            mcap_str = f"{mcap / 1_000_000:.0f}" if mcap else "—"
            conf = c.get("companies_house_confidence")
            conf_str = "Exact" if conf and conf >= 1.0 else (f"{conf:.2f}" if conf else "—")
            added = c.get("universe_added_date", "")
            added_str = str(added)[:10] if added else "—"

            (c_tick, c_comp, c_exch, c_mcap_col, c_ch, c_mut, c_added, c_action) = st.columns(
                [1.2, 4, 1.2, 1.5, 1.5, 1, 1.5, 1.5]
            )
            c_tick.markdown(
                f'<span style="font-size:0.8rem;font-family:\'IBM Plex Mono\',monospace;'
                f'{"color:#7f8c8d;" if is_muted else ""}">{ticker}</span>',
                unsafe_allow_html=True,
            )
            c_comp.caption(c.get("company_name", "—")[:42])
            c_exch.caption(c.get("listing_exchange", "—"))
            c_mcap_col.caption(mcap_str)
            c_ch.caption(conf_str)
            c_mut.caption("●" if is_muted else "")
            c_added.caption(added_str)

            mute_help = "Unmute from signal pipeline" if is_muted else "Mute from signal pipeline"
            if c_action.button(EMOJI_MUTE, key=f"universe_mute_{ticker}_{idx}", help=mute_help):
                mark_not_of_interest(db, ticker, not is_muted)
                st.rerun()
    else:
        st.markdown('<div class="empty-state">NO COMPANIES MATCH FILTER</div>', unsafe_allow_html=True)

    st.markdown("---")

    # Manual add form
    with st.expander("Add company manually"):
        ma_col1, ma_col2 = st.columns([2, 1])
        ma_ticker = ma_col1.text_input("Ticker (LSE)", placeholder="e.g. ACME", key="manual_add_ticker")
        ma_exchange = ma_col2.selectbox("Exchange", ["AIM", "LSE Main"], key="manual_add_exchange")
        ma_name = st.text_input("Company Name", placeholder="e.g. Acme Industries PLC", key="manual_add_name")
        ma_mcap = st.number_input("Market Cap (£M, optional)", min_value=0.0, value=0.0, key="manual_add_mcap")
        if st.button("Add to Universe", key="manual_add_submit"):
            if ma_ticker.strip() and ma_name.strip():
                exch_code = "AIM" if ma_exchange == "AIM" else "LSE_MAIN"
                mcap_gbp = ma_mcap * 1_000_000 if ma_mcap > 0 else None
                submit_universe_admit_job(db, ma_ticker, ma_name, mcap_gbp, exch_code)
                st.success("Submitted — CH lookup running on VM. Results appear in ~30 seconds.")
            else:
                st.warning("Ticker and Company Name are required.")

    # File import section
    with st.expander("📂 Import from file"):
        # Use a counter in the key so Cancel can reset the file uploader widget
        _file_key = st.session_state.get("universe_import_file_key", 0)
        ui_file = st.file_uploader(
            "Universe CSV", type=["csv"],
            key=f"universe_import_file_{_file_key}",
            help="CSV with columns: Exchange, Code, Name, Market Cap (values in £M)",
        )

        # Parse on new file upload (cache by filename)
        if ui_file is not None:
            _cache_key = (ui_file.name,)
            if st.session_state.get("universe_import_cache_key") != _cache_key:
                try:
                    _parsed = _parse_universe_csv(ui_file.getvalue())
                    _existing = get_all_universe_companies(db)
                    _delta = _compute_universe_delta(_parsed, _existing)
                    st.session_state["universe_import_cache_key"] = _cache_key
                    st.session_state["universe_import_delta"] = _delta
                    st.session_state["universe_import_absent_decisions"] = {}
                    st.session_state["universe_import_submitted"] = False
                except Exception as _e:
                    st.error(f"Could not parse CSV: {_e}")

        _delta = st.session_state.get("universe_import_delta")
        _submitted = st.session_state.get("universe_import_submitted", False)

        if _delta is None:
            st.caption("Upload a CSV with columns: Exchange, Code, Name, Market Cap (values in £M)")

        elif _submitted:
            st.success(
                "✓ Submitted — job_runner processing on VM. "
                "New companies require CH lookup (~0.6s each)."
            )
            try:
                _all_jobs = get_pending_jobs(db, limit=10)
                _import_jobs = [(jid, j) for jid, j in _all_jobs
                                if j.get("job_type") == "universe_bulk_import"]
                if _import_jobs:
                    _jid, _jdata = _import_jobs[0]
                    st.caption(
                        f"Most recent bulk import job: {_jid[:8]}… — "
                        f"status: {_jdata.get('status', '?')}"
                    )
            except Exception:
                pass

        else:
            _n_new = len(_delta["new"])
            _n_update = len(_delta["update"])
            _n_absent = len(_delta["absent"])
            st.markdown(f"**{_n_new} new · {_n_update} updates · {_n_absent} not in file**")

            _decisions = st.session_state.get("universe_import_absent_decisions", {})

            if _n_absent > 0:
                st.caption(
                    "Companies in Firestore not present in the uploaded file. "
                    "Default action: leave unchanged."
                )

                # Column headers
                _ah = st.columns([1.2, 4, 1.2, 1.5, 1, 1, 1])
                for _col, _lbl in zip(_ah, ["Ticker", "Company", "Exchange", "Mkt Cap (£M)", "Muted", "", ""]):
                    _col.caption(f"**{_lbl}**")

                for _ac in _delta["absent"]:
                    _aticker = _ac.get("ticker_lse", "")
                    _aname = _ac.get("company_name", "")
                    _aexch = _ac.get("listing_exchange", "")
                    _amcap = _ac.get("market_cap_gbp")
                    _amcap_str = f"{_amcap / 1_000_000:.0f}" if _amcap else "—"
                    _amuted = _ac.get("not_of_interest", False)
                    _decision = _decisions.get(_aticker)

                    _ra, _rb, _rc, _rd, _re, _rf1, _rf2 = st.columns([1.2, 4, 1.2, 1.5, 1, 1, 1])
                    _tick_style = "color:#7f8c8d;" if _amuted else ""
                    _ra.markdown(
                        f'<span style="font-size:0.8rem;font-family:IBM Plex Mono,monospace;'
                        f'{_tick_style}">{_aticker}</span>',
                        unsafe_allow_html=True,
                    )
                    _rb.caption(_aname[:42])
                    _rc.caption(_aexch)
                    _rd.caption(_amcap_str)
                    _re.caption("●" if _amuted else "")

                    if _decision == "mute":
                        _rf1.markdown("**WILL MUTE**")
                        if _rf2.button("undo", key=f"uimport_undo_{_aticker}"):
                            _decisions.pop(_aticker, None)
                            st.session_state["universe_import_absent_decisions"] = dict(_decisions)
                            st.rerun()
                    elif _decision == "remove":
                        _rf1.markdown("**WILL REMOVE**")
                        if _rf2.button("undo", key=f"uimport_undo_{_aticker}"):
                            _decisions.pop(_aticker, None)
                            st.session_state["universe_import_absent_decisions"] = dict(_decisions)
                            st.rerun()
                    else:
                        if _rf1.button(EMOJI_MUTE, key=f"uimport_mute_{_aticker}", help="Mute from signal pipeline"):
                            _decisions[_aticker] = "mute"
                            st.session_state["universe_import_absent_decisions"] = dict(_decisions)
                            st.rerun()
                        if _rf2.button("🗑️", key=f"uimport_remove_{_aticker}", help="Remove from universe"):
                            _decisions[_aticker] = "remove"
                            st.session_state["universe_import_absent_decisions"] = dict(_decisions)
                            st.rerun()

            st.markdown("")
            _btn_cancel, _btn_commit = st.columns([1, 1])

            if _btn_cancel.button("Cancel", key="uimport_cancel"):
                for _k in ["universe_import_cache_key", "universe_import_delta",
                           "universe_import_absent_decisions", "universe_import_submitted"]:
                    st.session_state.pop(_k, None)
                st.session_state["universe_import_file_key"] = _file_key + 1
                st.rerun()

            if _btn_commit.button("Commit import", key="uimport_commit", type="primary"):
                _remove_tickers = [t for t, d in _decisions.items() if d == "remove"]
                _mute_tickers = [t for t, d in _decisions.items() if d == "mute"]
                try:
                    submit_universe_bulk_import_job(
                        db,
                        new_companies=_delta["new"],
                        update_companies=_delta["update"],
                        remove_tickers=_remove_tickers,
                        mute_tickers=_mute_tickers,
                    )
                    st.session_state["universe_import_submitted"] = True
                    get_all_universe_companies.clear()
                    get_universe_stats.clear()
                    get_universe_tickers.clear()
                    st.rerun()
                except Exception as _e:
                    st.error(f"Failed to submit: {_e}")

# ── Ingest tab ─────────────────────────────────────────────────────────────────

with tab_ingest:

    # ── Step 1: Data source ─────────────────────────────────────────────────────

    st.markdown(
        '<div class="terminal-header" style="margin-bottom:0.4rem;">Step 1 — Data Source</div>',
        unsafe_allow_html=True,
    )

    if _PLAYWRIGHT_AVAILABLE:
        if st.button("🔄 Fetch from LSEG", key="btn_fetch_lseg",
                     help="Scrape today's announcements directly from LSEG News Explorer (MCX + AXX + SMX)"):
            with st.spinner("Fetching today's announcements from LSEG…"):
                try:
                    get_exclusion_list.clear()
                    get_company_keywords.clear()
                    excluded_types        = get_exclusion_list(db)
                    company_keywords      = get_company_keywords(db)
                    not_of_interest_tickers = get_not_of_interest_tickers(db)
                    universe_tickers      = get_universe_tickers(db)
                    raw_rows = _fetch_lseg_index()
                    if not raw_rows:
                        st.warning(
                            "Fetched 0 rows from LSEG. "
                            "The market may be closed, or the page took too long to load. "
                            "Check logs/streamlit.log for details."
                        )
                    else:
                        st.session_state["ingest_result"] = _filter_announcement_rows(
                            raw_rows, universe_tickers, excluded_types,
                            company_keywords, not_of_interest_tickers,
                        )
                        st.session_state["ingest_cache_key"]        = None
                        st.session_state["ingest_dismissed"]        = set()
                        st.session_state["ingest_session_muted"]    = set()
                        st.session_state["ingest_session_submitted"] = set()
                        st.session_state.pop("ingest_subform_open", None)
                        st.rerun()
                except Exception as _fe:
                    st.error(f"Fetch failed: {_fe}")
        st.caption("— or upload an LSEG Excel export —")
    else:
        st.caption(
            "Export from LSEG: Market News → filter Source=RNS, Market=AIM/Small Cap → Export to Excel."
        )

    uploaded_file = st.file_uploader(
        "LSEG Excel export (.xlsx)",
        type=["xlsx"],
        label_visibility="collapsed",
    )

    if uploaded_file is not None:
        # Always flush config caches before computing the parse cache key so that
        # edits made directly in Firestore (bypassing the Config tab) are picked up.
        get_exclusion_list.clear()
        get_company_keywords.clear()
        excluded_types = get_exclusion_list(db)
        company_keywords = get_company_keywords(db)
        not_of_interest_tickers = get_not_of_interest_tickers(db)
        ingest_cache_key = (
            uploaded_file.name,
            tuple(excluded_types),
            tuple(company_keywords),
            tuple(sorted(not_of_interest_tickers)),
        )
        if st.session_state.get("ingest_cache_key") != ingest_cache_key:
            universe_tickers = get_universe_tickers(db)
            file_bytes = uploaded_file.read()
            st.session_state["ingest_result"] = _parse_lseg_excel(
                file_bytes, universe_tickers, excluded_types,
                company_keywords, not_of_interest_tickers
            )
            st.session_state["ingest_cache_key"] = ingest_cache_key
            # Clear per-file session state on new upload
            st.session_state["ingest_dismissed"] = set()
            st.session_state["ingest_session_muted"] = set()
            st.session_state["ingest_session_submitted"] = set()
            st.session_state.pop("ingest_subform_open", None)

    # Initialise session state keys used by the unified table
    st.session_state.setdefault("ingest_dismissed", set())
    st.session_state.setdefault("ingest_session_muted", set())
    st.session_state.setdefault("ingest_session_submitted", set())

    if "ingest_result" in st.session_state:
        result = st.session_state["ingest_result"]

        # Filter summary metrics
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total rows", result["total_rows"])
        c2.metric("Skipped (non-RNS)", result["skipped_source"])
        c3.metric("Passed filters", len(result["passed"]))
        c4.metric("Non-universe (skipped)", len(result["discovery"]))
        c5.metric("Suppressed", len(result["suppressed"]))

        st.markdown("---")

        # ── Build unified row list ─────────────────────────────────────────────
        # Assign stable _row_id before any sorting/filtering so dismiss keys
        # remain consistent across reruns.

        all_rows = []
        for idx, r in enumerate(result["passed"]):
            all_rows.append({**r, "outcome": "passed", "reason": "", "_row_id": f"p_{idx}"})
        for idx, (r, reason) in enumerate(result["suppressed"]):
            outcome = "muted" if reason.lower().startswith("ticker muted") else "suppressed"
            all_rows.append({**r, "outcome": outcome, "reason": reason, "_row_id": f"s_{idx}"})

        # Apply session-level mutes (ticker muted this session without re-parse)
        session_muted = st.session_state["ingest_session_muted"]
        for row in all_rows:
            if row["ticker"].upper() in session_muted and row["outcome"] == "passed":
                row["outcome"] = "muted"

        # Remove dismissed rows
        dismissed = st.session_state["ingest_dismissed"]
        all_rows = [
            r for r in all_rows
            if (r.get("source_url") or r["_row_id"]) not in dismissed
        ]

        # ── Sort and filter controls ───────────────────────────────────────────

        col_sort, col_filter, col_since = st.columns([2, 2, 2])
        sort_by = col_sort.selectbox(
            "Sort by", ["Outcome", "Date", "Ticker", "Company"],
            label_visibility="collapsed",
            key="ingest_sort_by",
        )
        filter_by = col_filter.selectbox(
            "Show", ["Passed", "All", "Muted", "Suppressed"],
            label_visibility="collapsed",
            key="ingest_filter_by",
        )
        _SINCE_OPTIONS = {"All today": None, "Last 4h": 4, "Last 2h": 2, "Last 1h": 1, "Last 30m": 0.5}
        since_label = col_since.selectbox(
            "Since", list(_SINCE_OPTIONS.keys()),
            label_visibility="collapsed",
            key="ingest_since",
        )
        hide_analysed = st.checkbox(
            "Hide already analysed", value=True, key="ingest_hide_analysed"
        )

        # Fetch already-processed URLs for "✓ Analysed" indicator and hide-analysed filter
        processed_urls = _get_processed_source_urls(db)

        sort_fns = {
            "Outcome":  lambda r: (_OUTCOME_ORDER.get(r["outcome"], 9), str(r.get("published_at", ""))),
            "Date":     lambda r: str(r.get("published_at", "")),
            "Ticker":   lambda r: r.get("ticker", ""),
            "Company":  lambda r: r.get("company_name", ""),
        }
        rows = sorted(all_rows, key=sort_fns[sort_by])
        if filter_by != "All":
            rows = [r for r in rows if r["outcome"] == filter_by.lower()]
        since_hours = _SINCE_OPTIONS[since_label]
        if since_hours is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
            rows = [r for r in rows if r.get("published_at", datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
        if hide_analysed:
            rows = [
                r for r in rows
                if r.get("source_url") not in processed_urls or r["outcome"] != "passed"
            ]

        # ── Column header row ──────────────────────────────────────────────────

        if rows:
            hdr = st.columns([1.2, 1, 2.5, 2, 1, 0.8, 1, 1, 0.6, 0.6, 0.6, 1.8])
            for col, label in zip(hdr, [
                "Outcome", "Ticker", "Company", "Type", "Date", "Time",
                "Price(p)", "Chg%", "URL", "Hide", "Mute", "Action",
            ]):
                col.caption(f"**{label}**")
            st.markdown(
                '<hr style="margin:0.2rem 0 0.5rem 0;border-color:#1a2535;"/>',
                unsafe_allow_html=True,
            )

        # ── Render rows ────────────────────────────────────────────────────────

        for i, row in enumerate(rows):
            row_uid = row.get("source_url") or row["_row_id"]
            is_muted = row["outcome"] == "muted"
            txt = st.caption if is_muted else st.text  # lighter style for muted

            (c_badge, c_ticker, c_company, c_type, c_date, c_time,
             c_price, c_chg, c_url, c_dismiss, c_mute, c_action) = st.columns(
                [1.2, 1, 2.5, 2, 1, 0.8, 1, 1, 0.6, 0.6, 0.6, 1.8]
            )

            # Outcome badge
            label, color = _OUTCOME_STYLE.get(row["outcome"], ("?", "#888"))
            c_badge.markdown(
                f'<span style="color:{color};font-size:0.65rem;'
                f'font-family:\'IBM Plex Mono\',monospace;'
                f'{"opacity:0.5;" if is_muted else ""}">● {label}</span>',
                unsafe_allow_html=True,
            )

            # Data cells — use caption for muted rows (lighter rendering)
            pub = row.get("published_at")
            date_str = pub.strftime("%d %b") if pub else "—"
            time_str = pub.strftime("%H:%M") if pub else "—"
            price_str = str(row.get("price_pence") or "—")
            chg_str = str(row.get("price_change_pct") or "—")

            for col, val in [
                (c_ticker,  row.get("ticker", "—")),
                (c_company, row.get("company_name", "—")[:28]),
                (c_type,    row.get("announcement_type", "—")[:24]),
                (c_date,    date_str),
                (c_time,    time_str),
                (c_price,   price_str),
                (c_chg,     chg_str),
            ]:
                if is_muted:
                    col.caption(val)
                else:
                    col.markdown(
                        f'<span style="font-size:0.8rem;">{val}</span>',
                        unsafe_allow_html=True,
                    )

            # URL link
            src_url = row.get("source_url", "")
            if src_url:
                c_url.markdown(f'<a href="{src_url}" target="_blank" style="font-size:0.75rem;">↗</a>', unsafe_allow_html=True)
            else:
                c_url.caption("—")

            # Dismiss button (all rows)
            if c_dismiss.button("👎", key=f"dismiss_{i}", help="Dismiss this row"):
                st.session_state["ingest_dismissed"].add(row_uid)
                st.rerun()

            # Mute button (non-muted rows only)
            if not is_muted:
                if c_mute.button("🔇", key=f"mute_{i}", help="Mute this ticker permanently"):
                    mark_not_of_interest(db, row["ticker"], True)
                    st.session_state["ingest_session_muted"].add(row["ticker"].upper())
                    st.rerun()
            else:
                c_mute.caption("—")

            # Action column
            subform_key = "ingest_subform_open"

            if row["outcome"] == "passed":
                if src_url and src_url in processed_urls:
                    c_action.caption("✓ Analysed")
                elif row_uid in st.session_state["ingest_session_submitted"]:
                    c_action.caption("⏳ Submitted")
                else:
                    row_key = f"body_{i}"
                    if c_action.button("Analyse ▾", key=f"open_body_{i}"):
                        if st.session_state.get(subform_key) == row_key:
                            st.session_state.pop(subform_key, None)
                        else:
                            st.session_state[subform_key] = row_key
                        st.rerun()

                    if st.session_state.get(subform_key) == row_key:
                        with st.container():
                            form_hdr, form_close = st.columns([9, 1])
                            if src_url:
                                form_hdr.markdown(f"[Open announcement on LSEG ↗]({src_url})")
                            if form_close.button("✕", key=f"cancel_{i}", help="Close"):
                                st.session_state.pop(subform_key, None)
                                st.rerun()
                            fetch_err_key = f"fetch_err_{i}"
                            if _PLAYWRIGHT_AVAILABLE and src_url:
                                if st.button("🔍 Auto-fetch & submit", key=f"fetch_{i}"):
                                    with st.spinner("Fetching and submitting…"):
                                        try:
                                            fetched = _fetch_lseg_body(src_url)
                                            st.session_state.pop(fetch_err_key, None)
                                            submit_job(db, row, fetched)
                                            st.session_state["ingest_session_submitted"].add(row_uid)
                                            st.session_state.pop(subform_key, None)
                                            _get_processed_source_urls.clear()
                                            st.rerun()
                                        except Exception as exc:
                                            st.session_state[fetch_err_key] = str(exc)
                                if fetch_err_key in st.session_state:
                                    st.error(f"Auto-fetch failed: {st.session_state[fetch_err_key]}")
                            body = st.text_area(
                                "Announcement body",
                                key=row_key,
                                height=150,
                                placeholder="Paste body text, or use Auto-fetch above",
                                label_visibility="collapsed",
                            )
                            if st.button(
                                "Submit for analysis",
                                key=f"submit_{i}",
                                disabled=not (body or "").strip(),
                            ):
                                submit_job(db, row, body)
                                st.session_state["ingest_session_submitted"].add(row_uid)
                                st.session_state.pop(subform_key, None)
                                _get_processed_source_urls.clear()
                                st.rerun()

            else:
                # Muted or suppressed — show reason on hover via help
                if row.get("reason"):
                    c_action.caption(f"ℹ {row['reason']}")

        if not rows:
            st.markdown(
                '<div class="empty-state">NO ROWS MATCH CURRENT FILTER</div>',
                unsafe_allow_html=True,
            )

    # ── Step 2: Job Status ─────────────────────────────────────────────────────

    st.markdown("---")
    hdr_col, refresh_col = st.columns([10, 1])
    with hdr_col:
        st.markdown(
            '<div class="terminal-header" style="margin-bottom:0.8rem;">Step 2 — Job Status</div>',
            unsafe_allow_html=True,
        )
    with refresh_col:
        if st.button("↺ Refresh", key="ingest_refresh_jobs"):
            st.rerun()

    try:
        jobs = get_pending_jobs(db)
    except Exception as e:
        jobs = []
        st.caption(f"Could not load jobs: {e}")

    if not jobs:
        st.caption("No jobs found in pending_jobs collection.")
    else:
        status_colours = {
            "pending": "#f7a84a",
            "processing": "#7eb8f7",
            "complete": "#4af7a0",
            "failed": "#c0392b",
        }
        for job_id, job in jobs:
            status = job.get("status", "—")
            ticker = job.get("ticker", "—")
            job_type = job.get("job_type", "lseg_ingest")
            headline = job.get("headline") or f"[{job_type}] {job.get('company_name', '—')}"
            colour = status_colours.get(status, "#3d5166")
            error = job.get("error")

            st.markdown(
                f'<div style="padding:0.5rem 0;border-bottom:1px solid #1a2535;">'
                f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.65rem;'
                f'color:{colour};text-transform:uppercase;margin-right:1rem;">{status}</span>'
                f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.7rem;'
                f'color:#7eb8f7;">[{ticker}]</span>'
                f'<span style="font-size:0.8rem;margin-left:0.5rem;color:#8aabcc;">{headline}</span>'
                f'{f"<br><span style=\'font-size:0.7rem;color:#c0392b;margin-left:1rem;\'>{error}</span>" if error else ""}'
                f'</div>',
                unsafe_allow_html=True,
            )

# ── Config tab ─────────────────────────────────────────────────────────────────

with tab_config:

    # ── Announcement Type Exclusions ───────────────────────────────────────────

    st.markdown(
        '<div class="terminal-header" style="margin-bottom:0.6rem;">Announcement Type Exclusions</div>',
        unsafe_allow_html=True,
    )
    st.caption("Announcement types suppressed before LLM analysis. Match is case-insensitive substring.")

    current_excluded = get_exclusion_list(db)

    for entry in current_excluded:
        col_label, col_btn = st.columns([5, 1])
        col_label.caption(entry)
        if col_btn.button("×", key=f"remove_excl_{entry}"):
            updated = [e for e in current_excluded if e != entry]
            save_exclusion_list(db, updated)
            st.rerun()

    st.markdown("")
    new_type = st.text_input(
        "Add type",
        key="new_excl_type",
        placeholder="e.g. Result of EGM",
        label_visibility="collapsed",
    )
    if st.button("Add", key="add_excl_type_btn") and new_type.strip():
        entry = new_type.strip()
        if entry not in current_excluded:
            save_exclusion_list(db, current_excluded + [entry])
        st.rerun()

    st.markdown("---")

    # ── Company Name Keywords ──────────────────────────────────────────────────

    st.markdown(
        '<div class="terminal-header" style="margin-bottom:0.6rem;">Company Name Keywords</div>',
        unsafe_allow_html=True,
    )
    st.caption("Company names containing these substrings are suppressed (investment trusts / funds). Case-insensitive.")

    current_keywords = get_company_keywords(db)

    for kw in current_keywords:
        col_label, col_btn = st.columns([5, 1])
        col_label.caption(kw)
        if col_btn.button("×", key=f"remove_kw_{kw}"):
            updated = [k for k in current_keywords if k != kw]
            save_company_keywords(db, updated)
            st.rerun()

    st.markdown("")
    new_kw = st.text_input(
        "Add keyword",
        key="new_company_kw",
        placeholder="e.g. reit",
        label_visibility="collapsed",
    )
    if st.button("Add", key="add_company_kw_btn") and new_kw.strip():
        kw = new_kw.strip().lower()
        if kw not in current_keywords:
            save_company_keywords(db, current_keywords + [kw])
        st.rerun()
