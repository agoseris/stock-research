"""
app.py

Streamlit interface for the AI Stock Research Tool.
Reads signal and discovery results from Firestore.
Supports dismissing reviewed signals.

Run with:
    streamlit run app.py
"""

import io
import os
from datetime import date, datetime, time, timezone

import openpyxl
import streamlit as st
from google.cloud import firestore
from dotenv import load_dotenv

load_dotenv()

VERSION = "1.6"



# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="LSE Research Terminal",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
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
/* Keep sidebar toggle visible despite hidden header */
[data-testid="collapsedControl"] { visibility: visible !important; }
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
    """
    Create a Firestore client.

    On Streamlit Community Cloud: reads GCP credentials from
    st.secrets["gcp_service_account"] (set in the Streamlit dashboard).
    Locally: falls through to GOOGLE_APPLICATION_CREDENTIALS env var.
    """
    try:
        if "gcp_service_account" in st.secrets:
            from google.oauth2 import service_account
            sa_info = dict(st.secrets["gcp_service_account"])
            creds = service_account.Credentials.from_service_account_info(
                sa_info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            return firestore.Client(credentials=creds, project=sa_info.get("project_id"))
    except Exception:
        pass
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


# ── Universe and job helpers ───────────────────────────────────────────────────

# ── Config store ───────────────────────────────────────────────────────────────

_CONFIG_COLLECTION = "app_config"
_LSEG_FILTERS_DOC = "lseg_filters"

# Seed list used when the Firestore document does not yet exist.
_DEFAULT_EXCLUDED_TYPES = [
    "Holding(s) in Company",
    "Notice of AGM",
    "Notice of Results",
    "Annual Report",
    "Half-Year Report",
    "Interim Report",
    "Confirmation Statement",
    "Change of Registered Office",
    "Change of Nominated Adviser",
    "Change of Broker",
    "Total Voting Rights",
    "Blocklisting Interim Review",
    "Publication of Prospectus",
    "Result of AGM",
    "Director Declaration",
    "Conversion of B Shares",
]


@st.cache_data(ttl=60)
def get_exclusion_list(_db):
    """
    Load the announcement type exclusion list from Firestore app_config/lseg_filters.
    Seeds the document from _DEFAULT_EXCLUDED_TYPES on first call if it does not exist.
    Cached for 60 seconds so UI edits propagate quickly.
    """
    ref = _db.collection(_CONFIG_COLLECTION).document(_LSEG_FILTERS_DOC)
    doc = ref.get()
    if doc.exists:
        return doc.to_dict().get("excluded_announcement_types", [])
    # First run — seed Firestore from the default list
    ref.set({"excluded_announcement_types": _DEFAULT_EXCLUDED_TYPES})
    return list(_DEFAULT_EXCLUDED_TYPES)


def save_exclusion_list(db, excluded_types):
    """Persist the exclusion list to Firestore and invalidate the cache."""
    db.collection(_CONFIG_COLLECTION).document(_LSEG_FILTERS_DOC).set(
        {"excluded_announcement_types": excluded_types}
    )
    get_exclusion_list.clear()


@st.cache_data(ttl=300)
def get_universe_tickers(_db):
    """Fetch the set of all LSE tickers in the Firestore universe. Cached for 5 minutes."""
    docs = _db.collection("universe_companies").select([]).stream()
    return {doc.id for doc in docs}


def get_universe_company(db, ticker):
    """Return the universe_companies document for a ticker, or None."""
    doc = db.collection("universe_companies").document(ticker.upper()).get()
    return doc.to_dict() if doc.exists else None


def get_ticker_signal_count(db, ticker):
    """Return (count, most_recent_dict_or_None) for signals recorded against a ticker."""
    docs = list(
        db.collection("signal_results")
        .where("ticker", "==", ticker.upper())
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(50)
        .stream()
    )
    return len(docs), docs[0].to_dict() if docs else None


def get_pending_jobs(db, limit=20):
    """Return recent pending_jobs documents, newest first."""
    docs = (
        db.collection("pending_jobs")
        .order_by("submitted_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, doc.to_dict()) for doc in docs]


def submit_job(db, row_dict, body):
    """Write a job document to the pending_jobs collection."""
    job = {
        "status": "pending",
        "submitted_at": firestore.SERVER_TIMESTAMP,
        "processed_at": None,
        "ticker": row_dict["ticker"],
        "company_name": row_dict["company_name"],
        "headline": f"{row_dict['company_name']} — {row_dict['announcement_type']}",
        "body": body,
        "source_url": row_dict["source_url"],
        "published_at": row_dict["published_at"],
        "price": row_dict["price_pence"],
        "price_change": row_dict["price_change_pct"],
        "error": None,
    }
    db.collection("pending_jobs").add(job)


# ── Excel parsing ──────────────────────────────────────────────────────────────
# Note: this duplicates the logic in backend/lseg_excel_provider.py.
# frontend/app.py must not import from backend/ (Streamlit Community Cloud
# deployment constraint). Both copies must be kept in sync.

def _parse_lseg_excel(file_bytes, universe_tickers, excluded_types):
    """
    Parse LSEG Excel export bytes and apply pre-filters.

    Returns a dict with keys: passed, discovery, suppressed, skipped_source, total_rows.
    Each row is a plain dict (not a dataclass) suitable for Streamlit display.

    Filtering order:
      1. Source filter   — only RNS rows proceed
      2. Universe filter — non-universe rows route to discovery
      3. Type filter     — excluded_types (loaded from Firestore app_config) suppressed
    """

    def _parse_dt(date_val, time_val):
        if isinstance(date_val, str):
            try:
                d = datetime.strptime(date_val.strip(), "%d.%m.%y").date()
            except ValueError:
                d = datetime.now(timezone.utc).date()
        elif isinstance(date_val, datetime):
            d = date_val.date()
        elif isinstance(date_val, date):
            d = date_val
        else:
            d = datetime.now(timezone.utc).date()

        if isinstance(time_val, time):
            t = time_val
        elif isinstance(time_val, str):
            try:
                t = datetime.strptime(time_val.strip(), "%H:%M:%S").time()
            except ValueError:
                t = time(0, 0, 0)
        else:
            t = time(0, 0, 0)

        return datetime.combine(d, t, tzinfo=timezone.utc)

    def _parse_price(val):
        if val is None or val == "-" or val == "":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active

    passed, discovery, suppressed = [], [], []
    skipped_source = 0
    total_rows = 0

    for row in ws.iter_rows(min_row=1):
        if all(cell.value is None for cell in row):
            continue
        total_rows += 1
        cells = list(row)

        def cv(idx):
            return cells[idx].value if idx < len(cells) else None

        col1_val = cv(0)
        source_val = str(cv(1) or "").strip()
        date_val = cv(2)
        time_val = cv(3)
        price_val = cv(4)
        price_change_val = cv(5)

        if not col1_val:
            continue

        # Filter 1: Source
        if source_val.upper() != "RNS":
            skipped_source += 1
            continue

        # Parse Col 1: "[Company] - [Ticker] - [Announcement Type]"
        parts = str(col1_val).replace("\xa0", " ").split(" - ", maxsplit=2)
        company = parts[0].strip() if parts else ""
        ticker = parts[1].strip() if len(parts) > 1 else ""
        ann_type = parts[2].strip() if len(parts) > 2 else ""

        hyperlink = cells[0].hyperlink
        source_url = hyperlink.target if hyperlink and hyperlink.target else ""

        published_at = _parse_dt(date_val, time_val)
        price_pence = _parse_price(price_val)
        price_change_pct = str(price_change_val).strip() if price_change_val not in (None, "-", "") else None

        row_dict = {
            "ticker": ticker,
            "company_name": company,
            "announcement_type": ann_type,
            "source": source_val,
            "published_at": published_at,
            "price_pence": price_pence,
            "price_change_pct": price_change_pct,
            "source_url": source_url,
            "in_universe": ticker in universe_tickers,
        }

        # Filter 2: Universe
        if not row_dict["in_universe"]:
            discovery.append(row_dict)
            continue

        # Filter 3: Announcement type
        ann_lower = ann_type.lower()
        excluded_match = next(
            (ex for ex in excluded_types if ex.lower() in ann_lower),
            None,
        )
        if excluded_match:
            suppressed.append((row_dict, f"Announcement type excluded: '{excluded_match}'"))
            continue

        passed.append(row_dict)

    return {
        "passed": passed,
        "discovery": discovery,
        "suppressed": suppressed,
        "skipped_source": skipped_source,
        "total_rows": total_rows,
    }


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

tab_signals, tab_discovery, tab_ingest = st.tabs([
    f"Signals  [{len(signals)}]",
    f"Discovery Queue  [{len(discoveries)}]",
    "Ingest",
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

# ── Ingest tab ─────────────────────────────────────────────────────────────────

with tab_ingest:

    # ── Section 1: Upload and Pre-filter ──────────────────────────────────────

    st.markdown(
        '<div class="terminal-header" style="margin-bottom:0.8rem;">Step 1 — Upload LSEG Export</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Export from LSEG: Market News → filter Source=RNS, Market=AIM/Small Cap → Export to Excel. "
        "Upload the .xlsx file here."
    )

    uploaded_file = st.file_uploader(
        "LSEG Excel export (.xlsx)",
        type=["xlsx"],
        label_visibility="collapsed",
    )

    if uploaded_file is not None:
        # Only re-parse when a new file is uploaded
        if st.session_state.get("ingest_file_name") != uploaded_file.name:
            universe_tickers = get_universe_tickers(db)
            excluded_types = get_exclusion_list(db)
            file_bytes = uploaded_file.read()
            st.session_state["ingest_result"] = _parse_lseg_excel(file_bytes, universe_tickers, excluded_types)
            st.session_state["ingest_file_name"] = uploaded_file.name
            st.session_state.pop("ingest_submitted", None)

    if "ingest_result" in st.session_state:
        result = st.session_state["ingest_result"]
        passed = result["passed"]
        disc = result["discovery"]
        supp = result["suppressed"]
        # Summary
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total rows", result["total_rows"])
        c2.metric("Skipped (non-RNS)", result["skipped_source"])
        c3.metric("Passed filters", len(passed))
        c4.metric("Discovery candidates", len(disc))
        c5.metric("Suppressed (type)", len(supp))

        # Passed rows table
        if passed:
            import pandas as pd

            df = pd.DataFrame([{
                "Ticker": r["ticker"],
                "Company": r["company_name"],
                "Type": r["announcement_type"],
                "Time": r["published_at"].strftime("%H:%M") if r["published_at"] else "—",
                "Price (p)": r["price_pence"] if r["price_pence"] is not None else "—",
                "Change": r["price_change_pct"] if r["price_change_pct"] else "—",
                "URL": r["source_url"],
            } for r in passed])

            st.dataframe(
                df,
                column_config={
                    "URL": st.column_config.LinkColumn("URL", display_text="Open ↗"),
                },
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.markdown(
                '<div class="empty-state">NO ROWS PASSED FILTERS</div>',
                unsafe_allow_html=True,
            )

        # Suppressed rows (collapsed detail)
        if supp:
            with st.expander(f"Suppressed rows ({len(supp)})"):
                for row_dict, reason in supp:
                    st.caption(f"[{row_dict['ticker']}] {row_dict['announcement_type']} — {reason}")

        # Discovery candidates (collapsed detail)
        if disc:
            with st.expander(f"Discovery candidates ({len(disc)}) — not in universe"):
                for row_dict in disc:
                    url_part = f"  [{row_dict['source_url']}]({row_dict['source_url']})" if row_dict["source_url"] else ""
                    st.caption(f"[{row_dict['ticker']}] {row_dict['company_name']} — {row_dict['announcement_type']}{url_part}")

        # ── Section 2: Submit for Analysis ────────────────────────────────────

        if passed:
            st.markdown("---")
            st.markdown(
                '<div class="terminal-header" style="margin-bottom:0.8rem;">Step 2 — Submit for Analysis</div>',
                unsafe_allow_html=True,
            )
            st.caption(
                "Open the announcement on LSEG, read it, paste the full body text below, then submit. "
                "The VM will pick up the job and run LLM analysis. Results appear in the Signals tab."
            )

            if "ingest_submitted" not in st.session_state:
                st.session_state["ingest_submitted"] = set()

            for idx, row_dict in enumerate(passed):
                label = f"[{row_dict['ticker']}]  {row_dict['company_name']} — {row_dict['announcement_type']}"

                with st.expander(label):
                    if row_dict["source_url"]:
                        st.markdown(f"[Open on LSEG ↗]({row_dict['source_url']})")

                    if idx in st.session_state["ingest_submitted"]:
                        st.success(f"Submitted — [{row_dict['ticker']}] {row_dict['announcement_type']}")
                        continue

                    body_key = f"ingest_body_{idx}"
                    body = st.text_area(
                        "Paste announcement body",
                        key=body_key,
                        height=150,
                        placeholder="Copy and paste the full announcement text from LSEG here...",
                        label_visibility="collapsed",
                    )

                    if st.button(
                        "Submit for analysis",
                        key=f"ingest_submit_{idx}",
                        disabled=not (body or "").strip(),
                    ):
                        submit_job(db, row_dict, body)
                        st.session_state["ingest_submitted"].add(idx)
                        st.rerun()

    # ── Section 3: Job Status ──────────────────────────────────────────────────

    st.markdown("---")
    hdr_col, refresh_col = st.columns([10, 1])
    with hdr_col:
        st.markdown(
            '<div class="terminal-header" style="margin-bottom:0.8rem;">Step 3 — Job Status</div>',
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
            headline = job.get("headline", "—")
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

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:

    # ── Universe Lookup ────────────────────────────────────────────────────────

    st.markdown(
        '<div class="terminal-header" style="margin-bottom:0.6rem;">Universe Lookup</div>',
        unsafe_allow_html=True,
    )

    lookup_input = st.text_input(
        "Ticker",
        key="universe_lookup_input",
        placeholder="e.g. GMR",
        label_visibility="collapsed",
    )

    if st.button("Look up", key="universe_lookup_btn") and lookup_input.strip():
        ticker_q = lookup_input.strip().upper()
        company_data = get_universe_company(db, ticker_q)

        if company_data:
            st.success(f"**{ticker_q}** is in the universe")
            ch_conf = company_data.get("companies_house_confidence")
            ch_num = company_data.get("companies_house_number")
            if ch_conf is not None:
                st.caption(f"CH confidence: {ch_conf:.2f}  |  CH number: {ch_num or '—'}")
            try:
                sig_count, latest = get_ticker_signal_count(db, ticker_q)
                st.caption(f"Signals recorded: {sig_count}")
                if latest:
                    ts = format_timestamp(latest.get("analysed_at", ""))
                    headline = latest.get("headline", "")
                    st.caption(f"Most recent: {ts}")
                    st.caption(headline[:100] + "…" if len(headline) > 100 else headline)
            except Exception:
                st.caption("Signal count unavailable.")
        else:
            st.warning(f"**{ticker_q}** is not in the universe")

    st.markdown("---")

    # ── Filtration Rules ───────────────────────────────────────────────────────

    st.markdown(
        '<div class="terminal-header" style="margin-bottom:0.6rem;">Filtration Rules</div>',
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
