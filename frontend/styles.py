# All application CSS — injected once at startup via apply_styles().

import streamlit as st

_CSS = """
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
.signal-card-compact.director-investigate { border-left-color: #a064f7; }
.signal-card-compact.director-monitor { border-left-color: #7eb8f7; }
.signal-card-compact.director-ignore { border-left-color: #1a2535; }
.signal-card-compact.director-pending { border-left-color: #2a3040; }

/* Director lens badges */
.badge-director-investigate { background: #1a0a2a; color: #a064f7; border: 1px solid #a064f744; }
.badge-director-monitor { background: #0a1525; color: #7eb8f7; border: 1px solid #7eb8f744; }
.badge-director-ignore { background: #111820; color: #3d5166; border: 1px solid #1a253544; }
.badge-director-pending { background: #111820; color: #4a6080; border: 1px solid #1a253544; }
.badge-director-type { background: #150a2a; color: #c09af7; border: 1px solid #a064f744; }

/* TR-1 accumulation lens — card borders */
.signal-card-compact.tr1-investigate { border-left-color: #4af7c8; }
.signal-card-compact.tr1-monitor { border-left-color: #f7e14a; }
.signal-card-compact.tr1-ignore { border-left-color: #1a2535; }

/* TR-1 accumulation lens — badges */
.badge-tr1-investigate { background: #081f1a; color: #4af7c8; border: 1px solid #4af7c844; }
.badge-tr1-monitor { background: #1f1a04; color: #f7e14a; border: 1px solid #f7e14a44; }
.badge-tr1-ignore { background: #111820; color: #3d5166; border: 1px solid #1a253544; }
.badge-tr1-direction-up { background: #081a10; color: #4af798; border: 1px solid #4af79844; }
.badge-tr1-direction-down { background: #1a0808; color: #f76a6a; border: 1px solid #f76a6a44; }
.badge-tr1-direction-unknown { background: #111820; color: #4a6080; border: 1px solid #1a253544; }
.badge-tr1-notifier-type { background: #0a1520; color: #78b8e8; border: 1px solid #7eb8f744; }
.badge-tr1-conviction { background: #081f1a; color: #40d0b0; border: 1px solid #40d0b044; }
.badge-tr1-mechanical { background: #141008; color: #a08040; border: 1px solid #a0804044; }
.badge-tr1-unclear { background: #111820; color: #4a6080; border: 1px solid #1a253544; }

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

/* Sticky tab bar — multiple selectors to cover Streamlit version differences */
[data-testid="stTabBar"],
[data-baseweb="tab-list"],
.stTabs [role="tablist"],
div[class*="stTabBar"] {
    position: sticky !important;
    top: 0 !important;
    z-index: 999 !important;
    background-color: #0a0e14 !important;
    padding-top: 0.4rem;
    padding-bottom: 0.2rem;
    border-bottom: 1px solid #131e2e;
}

/* Performance tab */
.perf-section-heading {
    font-size: 0.7rem;
    font-family: 'IBM Plex Mono', monospace;
    color: #c8a84a;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin: 1.2rem 0 0.5rem;
}
.perf-metric-positive { color: #4af798; }
.perf-metric-negative { color: #f76a6a; }
.perf-metric-neutral  { color: #8aabcc; }
.perf-accuracy-row {
    display: flex;
    gap: 2rem;
    align-items: center;
    padding: 0.4rem 0;
    border-bottom: 1px solid #131e2e;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
}
.perf-lens-label {
    width: 180px;
    color: #5a7a9a;
    letter-spacing: 0.05em;
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
"""


def apply_styles() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
