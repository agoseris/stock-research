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

VERSION = "2.4"



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


# ── Config store ───────────────────────────────────────────────────────────────

_CONFIG_COLLECTION = "app_config"
_LSEG_FILTERS_DOC = "lseg_filters"

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
    ref = _db.collection(_CONFIG_COLLECTION).document(_LSEG_FILTERS_DOC)
    doc = ref.get()
    if doc.exists:
        data = doc.to_dict()
        if "excluded_announcement_types" in data:
            return data["excluded_announcement_types"]
    ref.set({"excluded_announcement_types": _DEFAULT_EXCLUDED_TYPES}, merge=True)
    return list(_DEFAULT_EXCLUDED_TYPES)


def save_exclusion_list(db, excluded_types):
    db.collection(_CONFIG_COLLECTION).document(_LSEG_FILTERS_DOC).set(
        {"excluded_announcement_types": excluded_types}, merge=True
    )
    get_exclusion_list.clear()


_DEFAULT_COMPANY_KEYWORDS = ["trust", "trst", "income", "growth", "grwth", "fund"]


@st.cache_data(ttl=60)
def get_company_keywords(_db):
    ref = _db.collection(_CONFIG_COLLECTION).document(_LSEG_FILTERS_DOC)
    doc = ref.get()
    if doc.exists:
        data = doc.to_dict()
        if "excluded_company_keywords" in data:
            return data["excluded_company_keywords"]
    ref.set({"excluded_company_keywords": _DEFAULT_COMPANY_KEYWORDS}, merge=True)
    return list(_DEFAULT_COMPANY_KEYWORDS)


def save_company_keywords(db, keywords):
    db.collection(_CONFIG_COLLECTION).document(_LSEG_FILTERS_DOC).set(
        {"excluded_company_keywords": keywords}, merge=True
    )
    get_company_keywords.clear()


# ── Universe helpers ───────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_universe_tickers(_db):
    """Fetch the set of all LSE tickers in the Firestore universe. Cached for 5 minutes."""
    docs = _db.collection("universe_companies").select([]).stream()
    return {doc.id for doc in docs}


@st.cache_data(ttl=300)
def get_not_of_interest_tickers(_db) -> set:
    """Return set of tickers where not_of_interest == True. Cached for 5 minutes."""
    docs = (
        _db.collection("universe_companies")
        .where("not_of_interest", "==", True)
        .select([])
        .stream()
    )
    return {doc.id for doc in docs}


@st.cache_data(ttl=300)
def get_all_universe_companies(_db) -> list:
    """Load all universe_companies documents as a list of dicts. Cached for 5 minutes."""
    docs = _db.collection("universe_companies").stream()
    return [doc.to_dict() for doc in docs]


@st.cache_data(ttl=300)
def get_universe_stats(_db) -> dict:
    """Return summary counts for the Universe tab stats bar. Cached for 5 minutes."""
    companies = get_all_universe_companies(_db)
    total = len(companies)
    aim = sum(1 for c in companies if c.get("listing_exchange") == "AIM")
    ftse = sum(1 for c in companies if c.get("listing_exchange") == "LSE_MAIN")
    ch_matched = sum(1 for c in companies if c.get("companies_house_number"))
    muted = sum(1 for c in companies if c.get("not_of_interest", False))
    return {"total": total, "aim": aim, "ftse": ftse, "ch_matched": ch_matched, "muted": muted}


def mark_not_of_interest(db, ticker: str, value: bool):
    """Set the not_of_interest flag on a universe company and clear related caches."""
    db.collection("universe_companies").document(ticker.upper()).set(
        {"not_of_interest": value}, merge=True
    )
    get_not_of_interest_tickers.clear()
    get_all_universe_companies.clear()
    get_universe_stats.clear()


def submit_universe_admit_job(db, ticker, company_name, market_cap_gbp,
                               listing_exchange, not_of_interest=False,
                               source_discovery_id=None):
    """Write a universe_admit job to the pending_jobs collection."""
    job = {
        "job_type": "universe_admit",
        "status": "pending",
        "submitted_at": firestore.SERVER_TIMESTAMP,
        "ticker": ticker.strip().upper(),
        "company_name": company_name,
        "market_cap_gbp": market_cap_gbp,
        "listing_exchange": listing_exchange,
        "not_of_interest": not_of_interest,
        "source_discovery_id": source_discovery_id,
    }
    db.collection("pending_jobs").add(job)


# ── Job helpers ────────────────────────────────────────────────────────────────

def get_pending_jobs(db, limit=20):
    """Return recent pending_jobs documents, newest first."""
    docs = (
        db.collection("pending_jobs")
        .order_by("submitted_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, doc.to_dict()) for doc in docs]


@st.cache_data(ttl=60)
def _get_processed_source_urls(_db) -> set:
    """Return set of source_url values already in the announcements dedup store."""
    try:
        docs = _db.collection("announcements").select(["source_url"]).stream()
        return {d.to_dict().get("source_url", "") for d in docs if d.to_dict().get("source_url")}
    except Exception:
        return set()


def submit_job(db, row_dict, body):
    """Write an lseg_ingest job document to the pending_jobs collection."""
    job = {
        "job_type": "lseg_ingest",
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


def _parse_lseg_excel(file_bytes, universe_tickers, excluded_types,
                      company_keywords, not_of_interest_tickers):
    """
    Parse LSEG Excel export bytes and apply pre-filters.

    Returns a dict with keys: passed, discovery, suppressed, skipped_source, total_rows.
    Each row is a plain dict (not a dataclass) suitable for Streamlit display.

    Filtering order:
      1. Source filter     — only RNS rows proceed
      2. Universe filter   — non-universe rows route to discovery
      2.5. Name filter     — company_keywords matched against company name; matched rows suppressed
      2.8. Muted filter    — not_of_interest tickers suppressed
      3. Type filter       — excluded_types (loaded from Firestore app_config) suppressed
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

        # Filter 2.5: Trust / fund company name
        trust_match = next(
            (kw for kw in company_keywords if kw in company.lower()),
            None,
        )
        if trust_match:
            suppressed.append((row_dict, f"Company name suggests investment trust/fund: '{trust_match}'"))
            continue

        # Filter 2.8: Muted ticker
        if ticker.upper() in not_of_interest_tickers:
            suppressed.append((row_dict, f"Ticker muted: '{ticker}'"))
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

tab_signals, tab_discovery, tab_universe, tab_ingest, tab_config = st.tabs([
    f"Signals  [{len(signals)}]",
    f"Discovery  [{len(discoveries)}]",
    "Universe",
    "Ingest",
    "Config",
])

# ── Signals tab ────────────────────────────────────────────────────────────────

with tab_signals:
    if not signals:
        st.markdown('<div class="empty-state">NO SIGNALS · Pipeline has not surfaced any results yet</div>', unsafe_allow_html=True)
    else:
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
                        "Market Cap (£M)", min_value=0.0, max_value=1000.0,
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
- Market capitalisation ≤ £1B at time of inclusion
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
        import pandas as pd

        def _ch_conf_label(c):
            conf = c.get("companies_house_confidence")
            if conf is None:
                return "—"
            if conf >= 1.0:
                return "Exact"
            return f"{conf:.2f}"

        df_universe = pd.DataFrame([{
            "Ticker": c.get("ticker_lse", ""),
            "Company": c.get("company_name", ""),
            "Exchange": c.get("listing_exchange", ""),
            "Market Cap (£M)": (
                f"{c['market_cap_gbp'] / 1_000_000:.0f}"
                if c.get("market_cap_gbp") else "—"
            ),
            "CH Confidence": _ch_conf_label(c),
            "Muted": "●" if c.get("not_of_interest", False) else "",
            "Added": c.get("universe_added_date", "")[:10] if c.get("universe_added_date") else "—",
        } for c in page_companies])

        st.dataframe(df_universe, hide_index=True, width="stretch")

        # Mute / Unmute buttons below table — one per company on current page
        st.markdown(
            '<div class="terminal-header" style="margin-top:0.8rem;margin-bottom:0.5rem;">Actions</div>',
            unsafe_allow_html=True,
        )
        for idx, c in enumerate(page_companies):
            ticker = c.get("ticker_lse", "")
            is_muted = c.get("not_of_interest", False)
            label = "Unmute" if is_muted else "Mute"
            btn_cols = st.columns([2, 2, 6])
            btn_cols[0].caption(f"**{ticker}**")
            btn_cols[1].caption(c.get("company_name", "")[:40])
            if btn_cols[2].button(label, key=f"universe_mute_{ticker}_{idx}"):
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
        ma_mcap = st.number_input("Market Cap (£M, optional)", min_value=0.0, max_value=1000.0, value=0.0, key="manual_add_mcap")
        if st.button("Add to Universe", key="manual_add_submit"):
            if ma_ticker.strip() and ma_name.strip():
                exch_code = "AIM" if ma_exchange == "AIM" else "LSE_MAIN"
                mcap_gbp = ma_mcap * 1_000_000 if ma_mcap > 0 else None
                submit_universe_admit_job(db, ma_ticker, ma_name, mcap_gbp, exch_code)
                st.success("Submitted — CH lookup running on VM. Results appear in ~30 seconds.")
            else:
                st.warning("Ticker and Company Name are required.")

# ── Ingest tab ─────────────────────────────────────────────────────────────────

# Outcome ordering and styling for unified table
_OUTCOME_ORDER = {"passed": 0, "discovery": 1, "muted": 2, "suppressed": 3}
_OUTCOME_STYLE = {
    "passed":     ("PASSED",     "#27ae60"),
    "discovery":  ("DISCOVERY",  "#f39c12"),
    "muted":      ("MUTED",      "#7f8c8d"),
    "suppressed": ("SUPPRESSED", "#2980b9"),
}

with tab_ingest:

    # ── Step 1: Upload ─────────────────────────────────────────────────────────

    st.markdown(
        '<div class="terminal-header" style="margin-bottom:0.4rem;">Step 1 — Upload LSEG Export</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Export from LSEG: Market News → filter Source=RNS, Market=AIM/Small Cap → Export to Excel."
    )

    uploaded_file = st.file_uploader(
        "LSEG Excel export (.xlsx)",
        type=["xlsx"],
        label_visibility="collapsed",
    )

    if uploaded_file is not None:
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
            st.session_state.pop("ingest_subform_open", None)

    # Initialise session state keys used by the unified table
    st.session_state.setdefault("ingest_dismissed", set())
    st.session_state.setdefault("ingest_session_muted", set())

    if "ingest_result" in st.session_state:
        result = st.session_state["ingest_result"]

        # Filter summary metrics
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total rows", result["total_rows"])
        c2.metric("Skipped (non-RNS)", result["skipped_source"])
        c3.metric("Passed filters", len(result["passed"]))
        c4.metric("Discovery candidates", len(result["discovery"]))
        c5.metric("Suppressed", len(result["suppressed"]))

        st.markdown("---")

        # ── Build unified row list ─────────────────────────────────────────────
        # Assign stable _row_id before any sorting/filtering so dismiss keys
        # remain consistent across reruns.

        all_rows = []
        for idx, r in enumerate(result["passed"]):
            all_rows.append({**r, "outcome": "passed", "reason": "", "_row_id": f"p_{idx}"})
        for idx, r in enumerate(result["discovery"]):
            all_rows.append({**r, "outcome": "discovery", "reason": "", "_row_id": f"d_{idx}"})
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

        col_sort, col_filter = st.columns([2, 2])
        sort_by = col_sort.selectbox(
            "Sort by", ["Outcome", "Date", "Ticker", "Company"],
            label_visibility="collapsed",
            key="ingest_sort_by",
        )
        filter_by = col_filter.selectbox(
            "Show", ["All", "Passed", "Discovery", "Muted", "Suppressed"],
            label_visibility="collapsed",
            key="ingest_filter_by",
        )

        sort_fns = {
            "Outcome":  lambda r: (_OUTCOME_ORDER.get(r["outcome"], 9), str(r.get("published_at", ""))),
            "Date":     lambda r: str(r.get("published_at", "")),
            "Ticker":   lambda r: r.get("ticker", ""),
            "Company":  lambda r: r.get("company_name", ""),
        }
        rows = sorted(all_rows, key=sort_fns[sort_by])
        if filter_by != "All":
            rows = [r for r in rows if r["outcome"] == filter_by.lower()]

        # Fetch already-processed URLs for "✓ Analysed" indicator
        processed_urls = _get_processed_source_urls(db)

        # ── Column header row ──────────────────────────────────────────────────

        if rows:
            hdr = st.columns([1.2, 1, 2.5, 2, 1, 0.8, 1, 1, 0.6, 0.6, 0.6, 1.8])
            for col, label in zip(hdr, [
                "Outcome", "Ticker", "Company", "Type", "Date", "Time",
                "Price(p)", "Chg%", "URL", "Dismiss", "Mute", "Action",
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
                col.caption(val) if is_muted else col.markdown(
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
            if c_dismiss.button("✕", key=f"dismiss_{i}", help="Dismiss this row"):
                st.session_state["ingest_dismissed"].add(row_uid)
                st.rerun()

            # Mute button (non-muted rows only)
            if not is_muted:
                if c_mute.button("Mute", key=f"mute_{i}", help="Mute this ticker permanently"):
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
                else:
                    row_key = f"body_{i}"
                    if c_action.button("Submit ▾", key=f"open_body_{i}"):
                        if st.session_state.get(subform_key) == row_key:
                            st.session_state.pop(subform_key, None)
                        else:
                            st.session_state[subform_key] = row_key
                        st.rerun()

                    if st.session_state.get(subform_key) == row_key:
                        with st.container():
                            body = st.text_area(
                                "Paste announcement body",
                                key=row_key,
                                height=150,
                                placeholder="Copy and paste the full announcement text...",
                                label_visibility="collapsed",
                            )
                            col_submit, col_cancel = st.columns([2, 1])
                            if col_submit.button(
                                "Submit for analysis",
                                key=f"submit_{i}",
                                disabled=not (body or "").strip(),
                            ):
                                submit_job(db, row, body)
                                st.session_state.pop(subform_key, None)
                                _get_processed_source_urls.clear()
                                st.rerun()
                            if col_cancel.button("Cancel", key=f"cancel_{i}"):
                                st.session_state.pop(subform_key, None)
                                st.rerun()

            elif row["outcome"] == "discovery":
                row_key_admit = f"admit_{i}"
                col_a, col_b = c_action.columns(2)

                if col_a.button("+ Univ", key=f"admit_btn_{i}", help="Add to Universe"):
                    if st.session_state.get(subform_key) == row_key_admit:
                        st.session_state.pop(subform_key, None)
                    else:
                        st.session_state[subform_key] = row_key_admit
                    st.rerun()

                if col_b.button("→ Pass", key=f"promote_btn_{i}", help="Promote to Passed"):
                    # Move row from discovery to passed in session state
                    orig = row.copy()
                    orig.pop("outcome", None)
                    orig.pop("reason", None)
                    try:
                        st.session_state["ingest_result"]["discovery"].remove(orig)
                    except (ValueError, AttributeError):
                        pass
                    st.session_state["ingest_result"]["passed"].append(orig)
                    st.rerun()

                if st.session_state.get(subform_key) == row_key_admit:
                    with st.container():
                        st.caption("**Admit to Universe**")
                        a_ticker   = st.text_input("Ticker",       value=row.get("ticker", ""),       key=f"da_t_{i}")
                        a_name     = st.text_input("Company Name", value=row.get("company_name", ""), key=f"da_n_{i}")
                        a_exchange = st.selectbox("Exchange",      ["AIM", "LSE Main"],               key=f"da_e_{i}")
                        a_mcap     = st.number_input("Market Cap (£M, optional)", 0.0, 1000.0, 0.0,  key=f"da_m_{i}")
                        col_sub, col_can = st.columns([2, 1])
                        if col_sub.button("Submit admission", key=f"da_submit_{i}"):
                            exch_code = "AIM" if a_exchange == "AIM" else "LSE_MAIN"
                            mcap_gbp = a_mcap * 1_000_000 if a_mcap > 0 else None
                            submit_universe_admit_job(db, a_ticker, a_name, mcap_gbp, exch_code, not_of_interest=False)
                            st.session_state.pop(subform_key, None)
                            st.rerun()
                        if col_can.button("Cancel", key=f"da_cancel_{i}"):
                            st.session_state.pop(subform_key, None)
                            st.rerun()

            else:
                # Muted or suppressed — show reason on hover via help
                if row.get("reason"):
                    c_action.caption(f"ℹ {row['reason'][:30]}")

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
