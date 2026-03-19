# Ingest tab renderer — extracted from app.py to reduce token consumption.

import streamlit as st
from datetime import datetime, timedelta, timezone

try:
    from lseg_scraper import (
        fetch_announcement_body as _fetch_lseg_body,
        fetch_announcement_index as _fetch_lseg_index,
    )
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

from constants import _OUTCOME_ORDER, _OUTCOME_STYLE
from firestore_helpers import (
    get_exclusion_list,
    get_company_keywords,
    get_not_of_interest_tickers,
    get_universe_tickers,
    get_pending_jobs,
    _get_processed_source_urls,
    submit_job,
    mark_not_of_interest,
)
from parse_helpers import _parse_lseg_excel, _filter_announcement_rows


def render_ingest_tab(db) -> None:

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
                    excluded_types          = get_exclusion_list(db)
                    company_keywords        = get_company_keywords(db)
                    not_of_interest_tickers = get_not_of_interest_tickers(db)
                    universe_tickers        = get_universe_tickers(db)
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
        excluded_types          = get_exclusion_list(db)
        company_keywords        = get_company_keywords(db)
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
            st.session_state["ingest_dismissed"]        = set()
            st.session_state["ingest_session_muted"]    = set()
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
        c2.metric("Skipped (no source)", result["skipped_source"])
        c3.metric("Passed filters", len(result["passed"]))
        c4.metric("Non-universe (skipped)", len(result["discovery"]))
        c5.metric("Suppressed", len(result["suppressed"]))

        st.markdown("---")

        # ── Build unified row list ──────────────────────────────────────────────
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

        # ── Sort and filter controls ────────────────────────────────────────────

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

        # ── Column header row ───────────────────────────────────────────────────

        if rows:
            hdr = st.columns([1.2, 1, 2.5, 2, 1, 0.8, 1, 1, 0.6, 0.6, 0.6, 1.8])
            for col, lbl in zip(hdr, [
                "Outcome", "Ticker", "Company", "Type", "Date", "Time",
                "Price(p)", "Chg%", "URL", "Hide", "Mute", "Action",
            ]):
                col.caption(f"**{lbl}**")
            st.markdown(
                '<hr style="margin:0.2rem 0 0.5rem 0;border-color:#1a2535;"/>',
                unsafe_allow_html=True,
            )

        # ── Render rows ─────────────────────────────────────────────────────────

        for i, row in enumerate(rows):
            row_uid  = row.get("source_url") or row["_row_id"]
            is_muted = row["outcome"] == "muted"

            (c_badge, c_ticker, c_company, c_type, c_date, c_time,
             c_price, c_chg, c_url, c_dismiss, c_mute, c_action) = st.columns(
                [1.2, 1, 2.5, 2, 1, 0.8, 1, 1, 0.6, 0.6, 0.6, 1.8]
            )

            # Outcome badge
            badge_label, color = _OUTCOME_STYLE.get(row["outcome"], ("?", "#888"))
            c_badge.markdown(
                f'<span style="color:{color};font-size:0.65rem;'
                f'font-family:\'IBM Plex Mono\',monospace;'
                f'{"opacity:0.5;" if is_muted else ""}">● {badge_label}</span>',
                unsafe_allow_html=True,
            )

            # Data cells — use caption for muted rows (lighter rendering)
            pub      = row.get("published_at")
            date_str = pub.strftime("%d %b") if pub else "—"
            time_str = pub.strftime("%H:%M") if pub else "—"
            price_str = str(row.get("price_pence") or "—")
            chg_str   = str(row.get("price_change_pct") or "—")

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
                c_url.markdown(
                    f'<a href="{src_url}" target="_blank" style="font-size:0.75rem;">↗</a>',
                    unsafe_allow_html=True,
                )
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

            # Action column — ⚡ auto-fetch (one-click) and 👨‍💻 manual sub-form
            subform_key   = "ingest_subform_open"
            fetch_err_key = f"fetch_err_{i}"

            if row["outcome"] == "passed":
                if src_url and src_url in processed_urls:
                    c_action.caption("✓ Analysed")
                elif row_uid in st.session_state["ingest_session_submitted"]:
                    c_action.caption("⏳ Submitted")
                else:
                    row_key = f"body_{i}"
                    btn_auto, btn_manual = c_action.columns(2)

                    # ⚡ Auto button — one-click fetch + submit (Playwright only)
                    if _PLAYWRIGHT_AVAILABLE and src_url:
                        if btn_auto.button("⚡", key=f"fetch_{i}",
                                           help="Auto-fetch announcement body and submit for analysis"):
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

                    # 👨‍💻 Manual button — opens paste sub-form
                    if btn_manual.button("👨‍💻", key=f"open_body_{i}",
                                         help="Paste announcement text manually"):
                        if st.session_state.get(subform_key) == row_key:
                            st.session_state.pop(subform_key, None)
                        else:
                            st.session_state[subform_key] = row_key
                        st.rerun()

                    # Manual sub-form (no auto-fetch — use ⚡ button for that)
                    if st.session_state.get(subform_key) == row_key:
                        with st.container():
                            form_hdr, form_close = st.columns([9, 1])
                            if src_url:
                                form_hdr.markdown(f"[Open announcement on LSEG ↗]({src_url})")
                            if form_close.button("✕", key=f"cancel_{i}", help="Close"):
                                st.session_state.pop(subform_key, None)
                                st.rerun()
                            body = st.text_area(
                                "Announcement body",
                                key=row_key,
                                height=150,
                                placeholder="Paste announcement body text here",
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

    # ── Step 2: Job Status ──────────────────────────────────────────────────────

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
            "pending":    "#f7a84a",
            "processing": "#7eb8f7",
            "complete":   "#4af7a0",
            "failed":     "#c0392b",
        }

        def _fmt_ts(value) -> str:
            """Format a timestamp for display regardless of type.

            Firestore SERVER_TIMESTAMP fields arrive as timezone-aware datetimes;
            job_runner sets claimed_at / processed_at as ISO 8601 strings.
            Returns a compact local-time string: 'DD Mon HH:MM'.
            """
            if value is None:
                return ""
            try:
                if isinstance(value, str):
                    dt = datetime.fromisoformat(value)
                elif hasattr(value, "tzinfo"):          # datetime (Firestore)
                    dt = value
                else:
                    return str(value)
                local_dt = dt.astimezone()              # system local timezone
                return local_dt.strftime("%-d %b %H:%M")
            except Exception:
                return str(value)

        for job_id, job in jobs:
            status      = job.get("status", "—")
            ticker      = job.get("ticker", "—")
            job_type    = job.get("job_type", "lseg_ingest")
            headline    = job.get("headline") or f"[{job_type}] {job.get('company_name', '—')}"
            colour      = status_colours.get(status, "#3d5166")
            error       = job.get("error")
            submitted   = _fmt_ts(job.get("submitted_at"))
            processed   = _fmt_ts(job.get("processed_at"))

            # Build the optional timestamp annotation shown after the headline.
            if submitted and processed and status in ("complete", "failed"):
                ts_html = (
                    f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.62rem;'
                    f'color:#3d5166;margin-left:0.75rem;">'
                    f'ingested {submitted} · done {processed}</span>'
                )
            elif submitted:
                ts_html = (
                    f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.62rem;'
                    f'color:#3d5166;margin-left:0.75rem;">'
                    f'ingested {submitted}</span>'
                )
            else:
                ts_html = ""

            st.markdown(
                f'<div style="padding:0.5rem 0;border-bottom:1px solid #1a2535;">'
                f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.65rem;'
                f'color:{colour};text-transform:uppercase;margin-right:1rem;">{status}</span>'
                f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.7rem;'
                f'color:#7eb8f7;">[{ticker}]</span>'
                f'<span style="font-size:0.8rem;margin-left:0.5rem;color:#8aabcc;">{headline}</span>'
                f'{ts_html}'
                f'{f"<br><span style=\'font-size:0.7rem;color:#c0392b;margin-left:1rem;\'>{error}</span>" if error else ""}'
                f'</div>',
                unsafe_allow_html=True,
            )
