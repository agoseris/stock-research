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

VERSION = "2.47"

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

from constants import EMOJI_MUTE
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
    get_all_universe_companies,
    get_universe_stats,
    mark_not_of_interest,
    submit_universe_admit_job,
    submit_universe_bulk_import_job,
    get_pending_jobs,
    cleanup_universe_orphans,
    get_all_signals_for_ticker,
    get_director_signals,
)
from parse_helpers import _parse_universe_csv, _compute_universe_delta
from ui_helpers import parse_analysis, get_field, recommend_add_badge, format_timestamp
from tab_signals import render_signals_tab
from tab_ingest import render_ingest_tab

# ── Header ───────────────────────────────────────────────────────────────────────

db = get_db()

st.markdown(f'<div class="terminal-header">LSE Small-Cap Research System · Regulatory Catalyst Lens · v{VERSION}</div>', unsafe_allow_html=True)
st.markdown('<div class="page-title">Research Terminal</div>', unsafe_allow_html=True)
st.markdown('<div class="page-subtitle">Signals surfaced by autonomous pipeline · Dismiss to archive · Universe expands only through explicit decision</div>', unsafe_allow_html=True)

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
    if c.get("position_state") in ("acted", "deferred")
}
_loaded_tickers = {(r.get("ticker") or "").upper() for _, r in signals}
for _t in sorted(_priority_tickers - _loaded_tickers):
    _fresh = get_all_signals_for_ticker(db, _t)
    if _fresh:
        signals.append(_fresh[0])

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

# ── Stat bar ─────────────────────────────────────────────────────────────────────

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

# ── Tabs ─────────────────────────────────────────────────────────────────────────

tab_signals, tab_discovery, tab_universe, tab_ingest, tab_config = st.tabs([
    f"Signals  [{len(signals)}]",
    f"Discovery  [{len(discoveries)}]",
    "Universe",
    "Ingest",
    "Config",
])

# ── Signals tab ──────────────────────────────────────────────────────────────────

with tab_signals:
    render_signals_tab(db, signals, company_map, director_signals=director_signals)

# ── Discovery tab ────────────────────────────────────────────────────────────────

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
            company    = get_field(assessment, "COMPANY")
            reason     = get_field(assessment, "REASON")
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

# ── Universe tab ─────────────────────────────────────────────────────────────────

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
    query           = col_search.text_input("Search ticker or company name", placeholder="e.g. GMR or Phoenix", key="universe_search")
    exchange_filter = col_exchange.selectbox("Exchange", ["All", "AIM", "LSE Main"], key="universe_exchange")
    show_muted      = col_show.checkbox("Show muted", value=False, key="universe_show_muted")

    # Page size — reset page on any filter change
    page_size = st.selectbox("Rows per page", [25, 50, 100], key="universe_page_size")

    # Remove orphaned documents (no ticker_lse field) then load companies
    cleanup_universe_orphans(db)
    try:
        all_companies = get_all_universe_companies(db)
    except Exception as e:
        all_companies = []
        st.caption(f"Could not load universe: {e}")

    filtered = [c for c in all_companies if c.get("ticker_lse")]
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
    total_pages    = max(1, (total_filtered + page_size - 1) // page_size)
    current_page   = st.session_state.get("universe_page", 0)
    current_page   = min(current_page, total_pages - 1)

    start         = current_page * page_size
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
            ticker   = c.get("ticker_lse", "")
            is_muted = c.get("not_of_interest", False)
            mcap     = c.get("market_cap_gbp")
            mcap_str = f"{mcap / 1_000_000:.0f}" if mcap else "—"
            conf     = c.get("companies_house_confidence")
            conf_str = "Exact" if conf and conf >= 1.0 else (f"{conf:.2f}" if conf else "—")
            added    = c.get("universe_added_date", "")
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
        ma_ticker   = ma_col1.text_input("Ticker (LSE)", placeholder="e.g. ACME", key="manual_add_ticker")
        ma_exchange = ma_col2.selectbox("Exchange", ["AIM", "LSE Main"], key="manual_add_exchange")
        ma_name  = st.text_input("Company Name", placeholder="e.g. Acme Industries PLC", key="manual_add_name")
        ma_mcap  = st.number_input("Market Cap (£M, optional)", min_value=0.0, value=0.0, key="manual_add_mcap")
        if st.button("Add to Universe", key="manual_add_submit"):
            if ma_ticker.strip() and ma_name.strip():
                exch_code = "AIM" if ma_exchange == "AIM" else "LSE_MAIN"
                mcap_gbp  = ma_mcap * 1_000_000 if ma_mcap > 0 else None
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
                    _parsed  = _parse_universe_csv(ui_file.getvalue())
                    _existing = get_all_universe_companies(db)
                    _delta   = _compute_universe_delta(_parsed, _existing)
                    st.session_state["universe_import_cache_key"]       = _cache_key
                    st.session_state["universe_import_delta"]           = _delta
                    st.session_state["universe_import_absent_decisions"] = {}
                    st.session_state["universe_import_submitted"]       = False
                except Exception as _e:
                    st.error(f"Could not parse CSV: {_e}")

        _delta     = st.session_state.get("universe_import_delta")
        _submitted = st.session_state.get("universe_import_submitted", False)

        if _delta is None:
            st.caption("Upload a CSV with columns: Exchange, Code, Name, Market Cap (values in £M)")

        elif _submitted:
            st.success(
                "✓ Submitted — job_runner processing on VM. "
                "New companies require CH lookup (~0.6s each)."
            )
            try:
                _all_jobs    = get_pending_jobs(db, limit=10)
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
            _n_new    = len(_delta["new"])
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
                    _aticker  = _ac.get("ticker_lse", "")
                    _aname    = _ac.get("company_name", "")
                    _aexch    = _ac.get("listing_exchange", "")
                    _amcap    = _ac.get("market_cap_gbp")
                    _amcap_str = f"{_amcap / 1_000_000:.0f}" if _amcap else "—"
                    _amuted   = _ac.get("not_of_interest", False)
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
                _mute_tickers   = [t for t, d in _decisions.items() if d == "mute"]
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

# ── Ingest tab ───────────────────────────────────────────────────────────────────

with tab_ingest:
    render_ingest_tab(db)

# ── Config tab ───────────────────────────────────────────────────────────────────

with tab_config:

    # ── Announcement Type Exclusions ────────────────────────────────────────────

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

    # ── Company Name Keywords ───────────────────────────────────────────────────

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
