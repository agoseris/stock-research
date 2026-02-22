"""
app.py

Streamlit interface for the AI Stock Research Tool.
Reads signal and discovery results from Firestore.
Supports dismissing reviewed signals.

Run with:
    streamlit run app.py
"""

import os
import streamlit as st
from datetime import datetime
from google.cloud import firestore
from dotenv import load_dotenv

load_dotenv()

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
    color: #3d5166;
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
    color: #4a6080;
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
    color: #3d5166;
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
    color: #3d5166;
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

/* Signal cards */
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
    color: #4a6080;
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
    color: #2d3f52;
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

/* Analysis block */
.analysis-block {
    background: #080c12;
    border: 1px solid #131e2e;
    border-radius: 3px;
    padding: 0.9rem 1rem;
    margin-top: 0.6rem;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
    color: #5a7a9a;
    line-height: 1.8;
    white-space: pre-wrap;
}

/* Dividers within analysis */
.analysis-key { color: #3d5166; }
.analysis-val { color: #8aabcc; }

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
    border: 1px solid #1a2535;
    color: #3d5166;
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
    color: #3d5166 !important;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    background: transparent !important;
    border: none !important;
}
</style>
""", unsafe_allow_html=True)


# ── Firestore client ───────────────────────────────────────────────────────────

@st.cache_resource
def get_db():
    return firestore.Client()


def get_signal_results(db, limit=100):
    docs = (
        db.collection("signal_results")
        .where("dismissed", "==", False)
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, doc.to_dict()) for doc in docs]


def get_signal_results_all(db, limit=100):
    """Fallback — fetch all and filter in Python if index not yet built."""
    docs = (
        db.collection("signal_results")
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, d) for doc in docs if not (d := doc.to_dict()).get("dismissed", False)]


def get_discovery_results(db, limit=100):
    docs = (
        db.collection("discovery_results")
        .where("dismissed", "==", False)
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, doc.to_dict()) for doc in docs]


def get_discovery_results_all(db, limit=100):
    docs = (
        db.collection("discovery_results")
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, d) for doc in docs if not (d := doc.to_dict()).get("dismissed", False)]


def dismiss_document(db, collection, doc_id):
    db.collection(collection).document(doc_id).update({
        "dismissed": True,
        "dismissed_at": datetime.utcnow().isoformat(),
    })


# ── Helpers ────────────────────────────────────────────────────────────────────

def parse_analysis(text):
    """Parse LLM analysis text into a list of (key, value) tuples."""
    lines = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            lines.append((key.strip(), val.strip()))
        else:
            lines.append(("", line))
    return lines


def get_field(text, field):
    """Extract a specific field value from LLM analysis text."""
    for line in (text or "").splitlines():
        if line.strip().upper().startswith(field.upper()):
            return line.split(":", 1)[-1].strip()
    return ""


def recommended_action_badge(analysis_text):
    val = get_field(analysis_text, "RECOMMENDED_ACTION").lower()
    if val == "yes":
        return '<span class="badge badge-yes">⬆ Action</span>', "action-yes"
    elif val == "monitor":
        return '<span class="badge badge-monitor">◉ Monitor</span>', "action-monitor"
    else:
        return '<span class="badge badge-no">— No action</span>', "action-no"


def recommend_add_badge(assessment_text):
    val = get_field(assessment_text, "RECOMMEND_ADD").lower()
    if val == "yes":
        return '<span class="badge badge-yes">⬆ Add to universe</span>', "discovery"
    elif val == "maybe":
        return '<span class="badge badge-maybe">? Consider</span>', "discovery"
    else:
        return '<span class="badge badge-no">— Pass</span>', "action-no"


def format_timestamp(ts_str):
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y  %H:%M UTC")
    except Exception:
        return ts_str or "—"


# ── Header ─────────────────────────────────────────────────────────────────────

db = get_db()

st.markdown('<div class="terminal-header">LSE Small-Cap Research System · Regulatory Catalyst Lens</div>', unsafe_allow_html=True)
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

tab_signals, tab_discovery = st.tabs([
    f"Signals  [{len(signals)}]",
    f"Discovery Queue  [{len(discoveries)}]",
])

# ── Signals tab ────────────────────────────────────────────────────────────────

with tab_signals:
    if not signals:
        st.markdown('<div class="empty-state">NO SIGNALS · Pipeline has not surfaced any results yet</div>', unsafe_allow_html=True)
    else:
        # Sort: action-yes first, then monitor, then no
        def signal_sort_key(item):
            val = get_field(item[1].get("llm_analysis", ""), "RECOMMENDED_ACTION").lower()
            return {"yes": 0, "monitor": 1}.get(val, 2)

        for doc_id, result in sorted(signals, key=signal_sort_key):
            analysis = result.get("llm_analysis", "")
            badge_html, card_class = recommended_action_badge(analysis)
            summary = get_field(analysis, "SUMMARY")
            source_badge = f'<span class="badge badge-source">{result.get("source", "—")}</span>'

            st.markdown(f"""
<div class="signal-card {card_class}">
    <div>
        <span class="card-ticker">{result.get("ticker", "—")}</span>
        <span class="card-company">{result.get("company_name", "")}</span>
    </div>
    <div class="card-headline">{result.get("headline", "—")}</div>
    <div class="card-meta">{format_timestamp(result.get("analysed_at", ""))}</div>
    <div>{badge_html}{source_badge}</div>
    {f'<div style="margin-top:0.7rem;font-size:0.82rem;color:#7a9ab8;font-family:IBM Plex Sans,sans-serif;line-height:1.5;">{summary}</div>' if summary else ''}
</div>
""", unsafe_allow_html=True)

            col1, col2 = st.columns([8, 1])
            with col1:
                with st.expander("Full analysis"):
                    parsed = parse_analysis(analysis)
                    formatted = "\n".join(
                        f"{k}: {v}" if k else v
                        for k, v in parsed
                    )
                    st.markdown(f'<div class="analysis-block">{formatted}</div>', unsafe_allow_html=True)
            with col2:
                if st.button("Dismiss", key=f"dismiss_signal_{doc_id}"):
                    dismiss_document(db, "signal_results", doc_id)
                    st.rerun()

# ── Discovery tab ──────────────────────────────────────────────────────────────

with tab_discovery:
    if not discoveries:
        st.markdown('<div class="empty-state">NO DISCOVERY ITEMS · All items reviewed or pipeline has not run yet</div>', unsafe_allow_html=True)
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
            with col2:
                if st.button("Dismiss", key=f"dismiss_disc_{doc_id}"):
                    dismiss_document(db, "discovery_results", doc_id)
                    st.rerun()
