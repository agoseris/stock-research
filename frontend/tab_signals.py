# Signals tab renderer — extracted from app.py to reduce token consumption.

import streamlit as st
from datetime import datetime, timedelta, timezone

from firestore_helpers import (
    get_signal_history_for_ticker,
    set_position_state,
    delete_signal_result,
)
from ui_helpers import (
    parse_analysis,
    get_field,
    recommended_action_badge,
    signal_state_badge,
    position_state_badge,
    format_signal_age,
    format_market_cap,
    format_price_info,
    format_timestamp,
)


def render_signals_tab(db, signals, company_map) -> None:
    # ── Filter bar ──────────────────────────────────────────────────────────────
    fc1, fc2, fc3 = st.columns([5, 2, 2])
    with fc1:
        pos_filter = st.radio(
            "Position",
            ["All", "Unreviewed", "Acted", "Deferred", "Declined", "Closed"],
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
        hide_no_action = st.checkbox(
            "Hide 'No action' signals", value=True, key="sig_hide_no_action"
        )

    # ── Compute time cutoff ─────────────────────────────────────────────────────
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
        if pos_filter == "Closed"   and pos != "closed":
            return False
        # Declined and Closed are hidden from all views except their explicit filter
        if pos == "declined" and pos_filter != "Declined":
            return False
        if pos == "closed" and pos_filter != "Closed":
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

    # ── Classify into groups ────────────────────────────────────────────────────
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
        # ── Card renderer (defined once, called per group) ───────────────────────
        def _render_signal_card(doc_id, result, company):
            analysis    = result.get("llm_analysis", "")
            ticker      = result.get("ticker") or "—"
            co_name     = result.get("company_name") or ""
            headline    = result.get("headline") or "—"
            source      = result.get("source") or "—"
            source_url  = result.get("source_url", "")
            analysed_at = result.get("analysed_at") or ""
            summary     = get_field(analysis, "SUMMARY")

            sig_state = company.get("signal_state") or "watching"
            pos_state = (company.get("position_state") or "").strip()
            sig_age   = format_signal_age(company.get("signal_state_since"))
            is_urgent = pos_state == "acted" and sig_state in ("signal_negative", "signal_mixed")

            badge_html, card_class = recommended_action_badge(analysis)
            classes     = f"signal-card-compact {card_class}" + (" urgent" if is_urgent else "")
            state_badge = signal_state_badge(sig_state, sig_age)
            pos_badge   = position_state_badge(pos_state)

            mkt       = result.get("market_cap_gbp") or company.get("market_cap_gbp")
            mkt_str   = format_market_cap(mkt)
            mkt_label = f"Market Cap {mkt_str}" if mkt_str != "—" else "—"
            price_html = format_price_info(result.get("price_pence"), result.get("price_change"))

            urgency_html = (
                '<div class="urgency-banner">⚠ COUNTER-SIGNAL — REVIEW POSITION</div>'
                if is_urgent else ""
            )

            # Headline links to source article when a URL is available
            headline_html = (
                f'<a href="{source_url}" target="_blank" '
                f'style="color:inherit;text-decoration:none;'
                f'border-bottom:1px solid rgba(100,150,200,0.25);">{headline}</a>'
                if source_url else headline
            )

            card_html = (
                f'<div class="{classes}">'
                f'{urgency_html}'
                f'<div class="card-row-top">'
                f'<span><span class="card-ticker">{ticker}</span>'
                f'<span class="card-company">{co_name}</span></span>'
                f'<span class="card-market">{mkt_label} &nbsp;·&nbsp; {price_html}</span>'
                f'</div>'
                f'<div class="card-headline">{headline_html}</div>'
                f'<div class="card-meta">{format_timestamp(analysed_at)} &nbsp;·&nbsp; {source}</div>'
                f'<div class="card-badges">{badge_html}{state_badge}{pos_badge}</div>'
                f'</div>'
            )
            st.markdown(card_html, unsafe_allow_html=True)

            col_exp, col_act, col_defer, col_decline, col_close, col_dismiss, col_hist = st.columns(
                [4, 1, 1, 1, 1, 1, 1]
            )
            with col_exp:
                with st.expander("Analysis detail"):
                    if summary:
                        st.markdown(
                            f'<div style="font-size:0.85rem;color:#a0c0d8;font-family:IBM Plex Sans,'
                            f'sans-serif;line-height:1.6;margin-bottom:0.8rem;">{summary}</div>',
                            unsafe_allow_html=True,
                        )
                    # Exclude fields already surfaced at card level
                    _top_level = {"SUMMARY", "RECOMMENDED_ACTION"}
                    parsed = [(k, v) for k, v in parse_analysis(analysis)
                              if k.upper() not in _top_level]
                    formatted = "\n".join(f"{k}: {v}" if k else v for k, v in parsed)
                    st.markdown(f'<div class="analysis-block">{formatted}</div>', unsafe_allow_html=True)

            # ── Position state buttons — only valid transitions shown per state ──
            # None / Closed : Act · Defer · Decline · Dismiss  (Closed = back to neutral)
            # Acted         : Close
            # Deferred      : Act · Decline · Dismiss
            # Declined      : Dismiss

            with col_act:
                if pos_state in ("", None, "closed", "deferred"):
                    help_txt = ("Proceed — record that you have taken a position."
                                if pos_state == "deferred"
                                else "Record that you have taken a position. Counter-signals will be surfaced as urgent.")
                    if st.button("Act", key=f"act_{doc_id}", help=help_txt):
                        set_position_state(db, ticker, "acted")
                        st.rerun()

            with col_defer:
                if pos_state in ("", None, "closed"):
                    if st.button("Defer", key=f"defer_{doc_id}",
                                 help="Interested but not acting now. Useful for pipeline companies or illiquid situations."):
                        set_position_state(db, ticker, "deferred")
                        st.rerun()

            with col_decline:
                if pos_state in ("", None, "closed", "deferred"):
                    if st.button("Decline", key=f"decline_{doc_id}",
                                 help="Pass on this opportunity. Signal state resets — the company remains monitored for future signals."):
                        set_position_state(db, ticker, "declined")
                        st.rerun()

            with col_close:
                if pos_state == "acted":
                    if st.button("Close", key=f"close_{doc_id}",
                                 help="Record that you have exited this position. Signal state resets to Watching so the company can be re-evaluated on future signals."):
                        set_position_state(db, ticker, "closed")
                        st.rerun()

            with col_dismiss:
                if pos_state in ("", None, "closed", "deferred", "declined"):
                    if st.button("Dismiss", key=f"dismiss_{doc_id}",
                                 help="Permanently delete this signal result. Use for noise — the company remains monitored."):
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
                    hist_rows = []
                    for h in history:
                        ts       = format_timestamp(h.get("timestamp", ""))
                        prev     = h.get("previous_state", "—")
                        nxt      = h.get("new_state", "—")
                        strength = h.get("signal_strength", "")
                        lens     = h.get("lens", "")
                        s_html   = f" &nbsp;·&nbsp; {strength}" if strength else ""
                        l_html   = f" &nbsp;·&nbsp; {lens}" if lens else ""
                        hist_rows.append(
                            f'<div class="history-row">'
                            f'<span class="hist-ts">{ts}</span>'
                            f' &nbsp;·&nbsp; <span class="hist-states">{prev} → {nxt}</span>'
                            f'{s_html}{l_html}'
                            f'</div>'
                        )
                    st.markdown("".join(hist_rows), unsafe_allow_html=True)

        # ── Render groups ────────────────────────────────────────────────────────
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
