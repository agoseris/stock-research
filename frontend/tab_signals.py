# Signals tab renderer — extracted from app.py to reduce token consumption.

import streamlit as st
from datetime import datetime, timedelta, timezone

from firestore_helpers import (
    get_signal_history_for_ticker,
    set_position_state,
    delete_signal_result,
    delete_director_signal,
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


# ---------------------------------------------------------------------------
# Director lens signal helpers
# ---------------------------------------------------------------------------

def _director_rec_class(rec: str) -> str:
    """Map agentic_recommendation to CSS card class suffix."""
    r = (rec or "").lower()
    if "investigate" in r:
        return "director-investigate"
    if "monitor" in r:
        return "director-monitor"
    if "ignore" in r:
        return "director-ignore"
    return "director-pending"


def _director_rec_badge(rec: str, agentic_status: str) -> str:
    """Return HTML badge for director recommendation or agentic status."""
    if agentic_status == "complete" and rec:
        r = rec.lower()
        if "investigate" in r:
            css = "badge badge-director-investigate"
            label = "Investigate Further"
        elif "monitor" in r:
            css = "badge badge-director-monitor"
            label = "Monitor"
        elif "ignore" in r:
            css = "badge badge-director-ignore"
            label = "Ignore"
        else:
            css = "badge badge-director-pending"
            label = rec
    elif agentic_status == "running":
        css = "badge badge-director-pending"
        label = "Agentic running…"
    elif agentic_status == "failed":
        css = "badge badge-director-ignore"
        label = "Agentic failed"
    elif agentic_status == "skipped":
        return ""  # simple lens decided investigation not warranted
    else:
        css = "badge badge-director-pending"
        label = "Agentic pending"
    return f'<span class="{css}">{label}</span>'


def _simple_rec_badge(simple_rec: str) -> str:
    """Return HTML badge for simple lens recommended action."""
    r = (simple_rec or "").lower()
    if "investigate" in r or r == "yes":
        css, label = "badge badge-yes", "Simple: Investigate"
    elif "monitor" in r:
        css, label = "badge badge-monitor", "Simple: Monitor"
    else:
        css, label = "badge badge-no", "Simple: Pass"
    return f'<span class="{css}">{label}</span>'


def _format_announcement_datetime(value) -> str:
    """Format announcement published_at to 'YYYY-MM-DD HH:MM', with time."""
    if not value:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)[:16]


def _render_director_signal_card(doc_id: str, signal: dict, company: dict, db) -> None:
    """Render a single director lens signal card."""
    ticker      = signal.get("ticker") or "—"
    co_name     = signal.get("company_name") or ""
    signal_type = signal.get("signal_type") or "director_buying"
    headline    = signal.get("headline") or ""
    source_url  = signal.get("source_url") or ""

    # Prefer announcement published datetime (has time); fall back to transaction date
    pub_display = _format_announcement_datetime(signal.get("announcement_published_at"))
    if not pub_display:
        raw = signal.get("published_at") or ""
        pub_display = raw.strftime("%Y-%m-%d") if hasattr(raw, "strftime") else str(raw)[:10]

    agentic_status = signal.get("agentic_status") or "pending"
    agentic_rec    = signal.get("agentic_recommendation") or ""
    simple_rec     = signal.get("simple_recommended_action") or ""

    sig_state = company.get("signal_state") or "watching"
    pos_state = (company.get("position_state") or "").strip()
    sig_age   = format_signal_age(company.get("signal_state_since"))

    card_class  = f"signal-card-compact {_director_rec_class(agentic_rec)}"
    state_badge = signal_state_badge(sig_state, sig_age)
    pos_badge   = position_state_badge(pos_state)
    type_label  = "Director Buying" if signal_type == "director_buying" else "Director Disposal"

    mkt       = company.get("market_cap_gbp")
    mkt_str   = format_market_cap(mkt)
    mkt_label = f"Market Cap {mkt_str}" if mkt_str != "—" else "—"
    price_html = format_price_info(signal.get("price_pence"), signal.get("price_change"))
    market_str = f"{mkt_label} &nbsp;·&nbsp; {price_html}" if price_html != "—" else mkt_label

    # Director name from synthesis briefing if available
    synthesis = signal.get("agentic_synthesis_full") or {}
    briefing  = synthesis.get("briefing") or {}
    director  = briefing.get("director") or ""

    agentic_badge = _director_rec_badge(agentic_rec, agentic_status)
    simple_badge  = _simple_rec_badge(simple_rec)
    type_badge    = f'<span class="badge badge-director-type">{type_label}</span>'
    director_html = f'<span style="color:#8aabcc;font-size:0.78rem"> · {director}</span>' if director else ""

    headline_html = (
        f'<a href="{source_url}" target="_blank" '
        f'style="color:inherit;text-decoration:none;'
        f'border-bottom:1px solid rgba(100,150,200,0.25);">{headline}</a>'
        if source_url and headline else headline
    )

    card_html = (
        f'<div class="{card_class}">'
        f'<div class="card-row-top">'
        f'<span><span class="card-ticker">{ticker}</span>'
        f'<span class="card-company">{co_name}</span>{director_html}</span>'
        f'<span class="card-market">{market_str}</span>'
        f'</div>'
        + (f'<div class="card-headline">{headline_html}</div>' if headline else "")
        + f'<div class="card-meta">{pub_display}</div>'
        f'<div class="card-badges">{type_badge}{simple_badge}{agentic_badge}{state_badge}{pos_badge}</div>'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

    # ── Action buttons ───────────────────────────────────────────────────────
    col_act, col_defer, col_decline, col_close, col_dismiss, _ = st.columns([1, 1, 1, 1, 1, 3])

    with col_act:
        if pos_state in ("", None, "closed", "deferred"):
            if st.button("Act", key=f"dir_act_{doc_id}",
                         help="Record that you have taken a position."):
                set_position_state(db, ticker, "acted")
                st.rerun()

    with col_defer:
        if pos_state in ("", None, "closed"):
            if st.button("Defer", key=f"dir_defer_{doc_id}",
                         help="Interested but not acting now."):
                set_position_state(db, ticker, "deferred")
                st.rerun()

    with col_decline:
        if pos_state in ("", None, "closed", "deferred"):
            if st.button("Decline", key=f"dir_decline_{doc_id}",
                         help="Pass. Signal resets; company stays monitored."):
                set_position_state(db, ticker, "declined")
                st.rerun()

    with col_close:
        if pos_state == "acted":
            if st.button("Close", key=f"dir_close_{doc_id}",
                         help="Exit position. Signal state resets to Watching."):
                set_position_state(db, ticker, "closed")
                st.rerun()

    with col_dismiss:
        if pos_state in ("", None, "closed", "deferred", "declined"):
            if st.button("Dismiss", key=f"dir_dismiss_{doc_id}",
                         help="Permanently delete this director signal."):
                delete_director_signal(db, doc_id)
                st.rerun()

    # ── Simple Lens — full width ─────────────────────────────────────────────
    with st.expander("Simple Lens"):
        summary     = signal.get("simple_summary") or ""
        strength    = signal.get("simple_signal_strength")
        direction   = signal.get("simple_signal_direction") or ""
        limitations = signal.get("simple_limitations") or ""
        if summary:
            st.markdown(f'<div style="font-size:0.82rem;color:#a0c0d8;line-height:1.55;">{summary}</div>', unsafe_allow_html=True)
        if strength is not None or direction:
            st.markdown(
                f'<div style="font-size:0.78rem;color:#6a8ca8;margin-top:0.4rem;">'
                f'Strength: {strength} &nbsp;·&nbsp; Direction: {direction}</div>',
                unsafe_allow_html=True,
            )
        if limitations:
            st.markdown(
                f'<div style="font-size:0.75rem;color:#4a6080;margin-top:0.5rem;border-top:1px solid #1a2535;padding-top:0.4rem;">'
                f'<strong style="color:#6a8ca8;">Limitations:</strong><br>{limitations}</div>',
                unsafe_allow_html=True,
            )

    # ── Agentic Briefing — full width ────────────────────────────────────────
    if agentic_status == "complete" and synthesis:
        with st.expander("Agentic Briefing"):
            layer_sums = synthesis.get("layer_summaries") or {}
            cross      = synthesis.get("cross_layer_assessment") or {}
            dq         = synthesis.get("data_quality_summary") or {}
            vs_simple  = synthesis.get("vs_simple_lens") or {}
            token_usage = signal.get("agentic_token_usage") or {}

            justification = synthesis.get("recommendation_justification") or ""
            if justification:
                st.markdown(
                    f'<div style="font-size:0.82rem;color:#c09af7;line-height:1.55;margin-bottom:0.6rem;">'
                    f'<strong>Recommendation:</strong> {agentic_rec}<br><br>{justification}</div>',
                    unsafe_allow_html=True,
                )

            fact_sum = synthesis.get("fact_summary") or ""
            inf_sum  = synthesis.get("inference_summary") or ""
            if fact_sum:
                st.markdown(
                    f'<div style="font-size:0.78rem;color:#8aabcc;line-height:1.5;margin-top:0.5rem;">'
                    f'<strong style="color:#a0c0d8;">Facts:</strong><br>{fact_sum}</div>',
                    unsafe_allow_html=True,
                )
            if inf_sum:
                st.markdown(
                    f'<div style="font-size:0.78rem;color:#8aabcc;line-height:1.5;margin-top:0.5rem;">'
                    f'<strong style="color:#f7e14a;">Inference:</strong><br>{inf_sum}</div>',
                    unsafe_allow_html=True,
                )

            thin_flags = dq.get("thin_data_flags") or []
            if thin_flags:
                flags_html = "".join(f"<li>{f}</li>" for f in thin_flags)
                st.markdown(
                    f'<div style="font-size:0.75rem;color:#f7a84a;margin-top:0.5rem;">'
                    f'<strong>Thin data:</strong><ul style="margin:0.2rem 0 0 1rem;padding:0;">{flags_html}</ul></div>',
                    unsafe_allow_html=True,
                )

            coherence = cross.get("coherence") or ""
            if coherence:
                tensions   = cross.get("tensions") or ""
                amplifiers = cross.get("amplifiers") or ""
                st.markdown(
                    f'<div style="font-size:0.75rem;color:#6a8ca8;margin-top:0.5rem;border-top:1px solid #1a2535;padding-top:0.4rem;">'
                    f'<strong style="color:#8aabcc;">Coherence:</strong> {coherence}'
                    + (f'<br><strong>Amplifiers:</strong> {amplifiers}' if amplifiers else "")
                    + (f'<br><strong>Tensions:</strong> {tensions}' if tensions else "")
                    + f'</div>',
                    unsafe_allow_html=True,
                )

            agentic_adds    = vs_simple.get("agentic_adds") or ""
            agentic_changes = vs_simple.get("agentic_changes") or ""
            if agentic_adds or agentic_changes:
                st.markdown(
                    f'<div style="font-size:0.75rem;color:#6a8ca8;margin-top:0.5rem;border-top:1px solid #1a2535;padding-top:0.4rem;">'
                    f'<strong style="color:#8aabcc;">vs Simple Lens:</strong>'
                    + (f'<br><strong>Adds:</strong> {agentic_adds}' if agentic_adds else "")
                    + (f'<br><strong>Changes:</strong> {agentic_changes}' if agentic_changes else "")
                    + f'</div>',
                    unsafe_allow_html=True,
                )

            limitations = signal.get("agentic_limitations") or synthesis.get("limitations") or ""
            if limitations:
                st.markdown(
                    f'<div style="font-size:0.75rem;color:#4a6080;margin-top:0.5rem;border-top:1px solid #1a2535;padding-top:0.4rem;">'
                    f'<strong style="color:#6a8ca8;">Limitations:</strong><br>'
                    f'<pre style="font-size:0.72rem;color:#4a6080;background:none;border:none;margin:0;white-space:pre-wrap;">{limitations}</pre></div>',
                    unsafe_allow_html=True,
                )

            if token_usage:
                total_in  = token_usage.get("total_input", 0)
                total_out = token_usage.get("total_output", 0)
                cost      = token_usage.get("estimated_cost_gbp", 0)
                l1i = token_usage.get("layer1_input", 0)
                l2i = token_usage.get("layer2_input", 0)
                l3i = token_usage.get("layer3_input", 0)
                si  = token_usage.get("synthesis_input", 0)
                st.markdown(
                    f'<div style="font-size:0.72rem;color:#3d5166;margin-top:0.5rem;border-top:1px solid #1a2535;padding-top:0.4rem;">'
                    f'Tokens — L1: {l1i} · L2: {l2i} · L3: {l3i} · Synth: {si}'
                    f'<br>Total: {total_in}in / {total_out}out'
                    + (f' · Est. cost: £{cost:.4f}' if cost else "")
                    + f'</div>',
                    unsafe_allow_html=True,
                )
    elif agentic_status == "running":
        st.caption("Agentic analysis running…")
    elif agentic_status == "failed":
        st.caption("Agentic analysis failed.")
    elif agentic_status not in ("skipped",):
        st.caption("Agentic analysis pending.")


# ---------------------------------------------------------------------------
# TR-1 lens signal helpers
# ---------------------------------------------------------------------------

def _tr1_card_class(recommendation: str) -> str:
    r = (recommendation or "").lower()
    if r == "investigate":
        return "tr1-investigate"
    if r == "monitor":
        return "tr1-monitor"
    return "tr1-ignore"


def _tr1_rec_badge(recommendation: str) -> str:
    r = (recommendation or "").lower()
    if r == "investigate":
        return '<span class="badge badge-tr1-investigate">Investigate</span>'
    if r == "monitor":
        return '<span class="badge badge-tr1-monitor">Monitor</span>'
    return '<span class="badge badge-tr1-ignore">Ignore</span>'


def _tr1_direction_badge(direction: str, threshold: float | None) -> str:
    thr_str = f" · {threshold:.0f}% threshold" if threshold else ""
    if direction == "upward":
        return f'<span class="badge badge-tr1-direction-up">▲ UPWARD CROSSING{thr_str}</span>'
    if direction == "downward":
        return f'<span class="badge badge-tr1-direction-down">▼ DOWNWARD CROSSING{thr_str}</span>'
    return '<span class="badge badge-tr1-direction-unknown">? DIRECTION UNKNOWN</span>'


def _tr1_notifier_type_badge(notifier_type: str | None) -> str:
    if not notifier_type or notifier_type == "unknown":
        return ""
    label = notifier_type.replace("_", " ").title()
    return f'<span class="badge badge-tr1-notifier-type">{label}</span>'


def _tr1_conviction_badge(conviction: str | None) -> str:
    if not conviction:
        return ""
    if conviction == "conviction_likely":
        return '<span class="badge badge-tr1-conviction">Conviction Likely</span>'
    if conviction == "mechanical_likely":
        return '<span class="badge badge-tr1-mechanical">Mechanical</span>'
    return '<span class="badge badge-tr1-unclear">Unclear</span>'


def _fmt_pct(val) -> str:
    """Format a holding percentage float as e.g. '4.85%' or '—'."""
    if val is None:
        return "—"
    try:
        return f"{float(val):.2f}%"
    except (TypeError, ValueError):
        return str(val)


def _render_tr1_signal_card(doc_id: str, signal: dict, company: dict, db) -> None:
    """Render a single TR-1 accumulation signal card."""
    ticker     = signal.get("ticker") or "—"
    co_name    = signal.get("company_name") or ""
    headline   = signal.get("headline") or ""
    source_url = signal.get("source_url") or ""

    pub_display = _format_announcement_datetime(
        signal.get("announcement_published_at") or signal.get("published_at")
    )

    notifier_name  = signal.get("notifier_name") or "Unknown notifier"
    direction      = signal.get("direction") or "unknown"
    old_pct        = signal.get("old_holding_pct")
    new_pct        = signal.get("new_holding_pct")
    threshold      = signal.get("threshold_crossed")

    simple         = signal.get("simple_lens_result") or {}
    recommendation = simple.get("recommendation") or "Ignore"
    signal_str     = (simple.get("signal_strength") or "").replace("_", " ").title()

    agentic_status = signal.get("agentic_status") or "pending"
    inv_research   = signal.get("investor_research") or {}
    notifier_type  = inv_research.get("notifier_type") if agentic_status == "complete" else None
    conviction     = inv_research.get("conviction_assessment") if agentic_status == "complete" else None

    sig_state = company.get("signal_state") or "watching"
    pos_state = (company.get("position_state") or "").strip()
    sig_age   = format_signal_age(company.get("signal_state_since"))

    card_class  = f"signal-card-compact {_tr1_card_class(recommendation)}"
    state_badge = signal_state_badge(sig_state, sig_age)
    pos_badge   = position_state_badge(pos_state)

    mkt        = company.get("market_cap_gbp")
    mkt_str    = format_market_cap(mkt)
    mkt_label  = f"Market Cap {mkt_str}" if mkt_str != "—" else "—"
    price_html = format_price_info(signal.get("price_pence"), signal.get("price_change"))
    market_str = f"{mkt_label} &nbsp;·&nbsp; {price_html}" if price_html != "—" else mkt_label

    direction_badge  = _tr1_direction_badge(direction, threshold)
    rec_badge        = _tr1_rec_badge(recommendation)
    notifier_t_badge = _tr1_notifier_type_badge(notifier_type)
    conviction_badge = _tr1_conviction_badge(conviction)

    str_badge = (
        f'<span class="badge badge-tr1-investigate">{signal_str}</span>'
        if signal_str else ""
    )

    holding_str = f"{_fmt_pct(old_pct)} → {_fmt_pct(new_pct)}"

    headline_html = (
        f'<a href="{source_url}" target="_blank" '
        f'style="color:inherit;text-decoration:none;'
        f'border-bottom:1px solid rgba(74,247,200,0.2);">{headline}</a>'
        if source_url and headline else headline
    )

    notifier_html = (
        f'<span style="color:#78b8e8;font-size:0.78rem;"> · {notifier_name}</span>'
    )

    card_html = (
        f'<div class="{card_class}">'
        f'<div class="card-row-top">'
        f'<span><span class="card-ticker">{ticker}</span>'
        f'<span class="card-company">{co_name}</span>{notifier_html}</span>'
        f'<span class="card-market">{market_str}</span>'
        f'</div>'
        + (f'<div class="card-headline">{headline_html}</div>' if headline else "")
        + f'<div style="font-size:0.78rem;color:#5a7a8a;margin:0.15rem 0 0.3rem;">'
        f'Holdings: {holding_str}</div>'
        f'<div class="card-meta">{pub_display}</div>'
        f'<div class="card-badges">'
        f'{direction_badge}{rec_badge}{str_badge}'
        f'{notifier_t_badge}{conviction_badge}'
        f'{state_badge}{pos_badge}'
        f'</div>'
        f'</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

    # ── Action buttons ────────────────────────────────────────────────────────
    col_act, col_defer, col_decline, col_close, col_dismiss, _ = st.columns([1, 1, 1, 1, 1, 3])

    with col_act:
        if pos_state in ("", None, "closed", "deferred"):
            if st.button("Act", key=f"tr1_act_{doc_id}",
                         help="Record that you have taken a position."):
                set_position_state(db, ticker, "acted")
                st.rerun()

    with col_defer:
        if pos_state in ("", None, "closed"):
            if st.button("Defer", key=f"tr1_defer_{doc_id}",
                         help="Interested but not acting now."):
                set_position_state(db, ticker, "deferred")
                st.rerun()

    with col_decline:
        if pos_state in ("", None, "closed", "deferred"):
            if st.button("Decline", key=f"tr1_decline_{doc_id}",
                         help="Pass. Signal resets; company stays monitored."):
                set_position_state(db, ticker, "declined")
                st.rerun()

    with col_close:
        if pos_state == "acted":
            if st.button("Close", key=f"tr1_close_{doc_id}",
                         help="Exit position. Signal state resets to Watching."):
                set_position_state(db, ticker, "closed")
                st.rerun()

    with col_dismiss:
        if pos_state in ("", None, "closed", "deferred", "declined"):
            if st.button("Dismiss", key=f"tr1_dismiss_{doc_id}",
                         help="Permanently delete this TR-1 signal."):
                delete_director_signal(db, doc_id)
                st.rerun()

    # ── Simple Lens expander ──────────────────────────────────────────────────
    with st.expander("Simple Lens"):
        rationale   = simple.get("rationale") or ""
        limitations = simple.get("limitations") or ""
        if rationale:
            st.markdown(
                f'<div style="font-size:0.82rem;color:#a0c0d8;line-height:1.55;">{rationale}</div>',
                unsafe_allow_html=True,
            )
        if limitations:
            st.markdown(
                f'<div style="font-size:0.75rem;color:#4a6080;margin-top:0.5rem;'
                f'border-top:1px solid #1a2535;padding-top:0.4rem;">'
                f'<strong style="color:#6a8ca8;">Limitations:</strong><br>{limitations}</div>',
                unsafe_allow_html=True,
            )

    # ── Investor Research expander ────────────────────────────────────────────
    if agentic_status == "complete" and inv_research and not inv_research.get("_error"):
        with st.expander("Investor Research"):
            track_record = inv_research.get("track_record_summary") or ""
            confidence   = inv_research.get("confidence") or ""
            inv_limits   = inv_research.get("limitations") or ""
            citations    = inv_research.get("search_citations") or []

            type_str = (notifier_type or "").replace("_", " ").title()
            conv_str = (conviction or "").replace("_", " ").title()

            st.markdown(
                f'<div style="font-size:0.82rem;color:#a0c0d8;line-height:1.55;">'
                f'<strong>Type:</strong> {type_str} &nbsp;·&nbsp; '
                f'<strong>Conviction:</strong> {conv_str} &nbsp;·&nbsp; '
                f'<strong>Confidence:</strong> {confidence.title() if confidence else "—"}</div>',
                unsafe_allow_html=True,
            )
            if track_record:
                st.markdown(
                    f'<div style="font-size:0.80rem;color:#8aabcc;line-height:1.5;margin-top:0.4rem;">'
                    f'{track_record}</div>',
                    unsafe_allow_html=True,
                )
            if citations:
                links = " &nbsp;·&nbsp; ".join(
                    f'<a href="{u}" target="_blank" style="color:#4a8ca8;font-size:0.72rem;">'
                    f'{u[:60]}{"…" if len(u) > 60 else ""}</a>'
                    for u in citations[:5]
                )
                st.markdown(
                    f'<div style="margin-top:0.4rem;">{links}</div>',
                    unsafe_allow_html=True,
                )
            if inv_limits:
                st.markdown(
                    f'<div style="font-size:0.75rem;color:#4a6080;margin-top:0.5rem;'
                    f'border-top:1px solid #1a2535;padding-top:0.4rem;">'
                    f'<strong style="color:#6a8ca8;">Limitations:</strong><br>{inv_limits}</div>',
                    unsafe_allow_html=True,
                )
    elif agentic_status == "pending":
        st.caption("Investor research pending — run orchestrator.")
    elif agentic_status == "failed":
        err = inv_research.get("_error") or ""
        st.caption(f"Investor research failed.{' ' + err if err else ''}")
    # agentic_status == "skipped": no expander — simple lens decided it wasn't needed


# ---------------------------------------------------------------------------
# Collapse-resistant section toggle (st.expander has no `key` support in this
# Streamlit build, so we manage open/closed state in session_state manually).
# ---------------------------------------------------------------------------

def _toggle_section(state_key: str, label: str, items: list, render_fn) -> None:
    """Render a collapsible section whose state survives st.rerun() calls.

    Unlike st.expander(expanded=False), this keeps the section open after a
    button click inside it triggers st.rerun().
    """
    if state_key not in st.session_state:
        st.session_state[state_key] = False

    is_open = st.session_state[state_key]
    arrow = "▼" if is_open else "▶"
    if st.button(f"{arrow}  {label}", key=f"{state_key}_toggle"):
        st.session_state[state_key] = not is_open

    if st.session_state[state_key]:
        for item in items:
            render_fn(item)


def _render_tr1_signals_section(
    db, tr1_signals: list, company_map: dict,
    since_cutoff, pos_filter: str
) -> None:
    """Render the TR-1 Accumulation section below the Director Buying section."""
    if not tr1_signals:
        return

    # Apply filters
    visible = []
    for doc_id, signal in tr1_signals:
        ticker  = (signal.get("ticker") or "").upper()
        company = company_map.get(ticker, {})
        pos     = (company.get("position_state") or "").strip()

        if pos_filter == "Unreviewed" and pos:
            continue
        if pos_filter == "Acted"    and pos != "acted":
            continue
        if pos_filter == "Deferred" and pos != "deferred":
            continue
        if pos_filter == "Declined" and pos != "declined":
            continue
        if pos_filter == "Closed"   and pos != "closed":
            continue
        if pos == "declined" and pos_filter != "Declined":
            continue
        if pos == "closed" and pos_filter != "Closed":
            continue
        if since_cutoff:
            try:
                raw = signal.get("created_at")
                if hasattr(raw, "timestamp"):
                    dt = raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw
                else:
                    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < since_cutoff:
                    continue
            except Exception:
                pass
        visible.append((doc_id, signal, company))

    if not visible:
        return

    # Group by recommendation
    grp_investigate, grp_monitor, grp_ignore = [], [], []
    for item in visible:
        _, signal, _ = item
        simple = signal.get("simple_lens_result") or {}
        rec = (simple.get("recommendation") or "Ignore").lower()
        if rec == "investigate":
            grp_investigate.append(item)
        elif rec == "monitor":
            grp_monitor.append(item)
        else:
            grp_ignore.append(item)

    st.markdown(
        '<div style="font-size:0.7rem;font-family:IBM Plex Mono,monospace;color:#40c0a0;'
        'letter-spacing:0.1em;text-transform:uppercase;margin:1rem 0 0.4rem;">TR-1 Accumulation Lens</div>',
        unsafe_allow_html=True,
    )

    if grp_investigate:
        with st.expander(f"▲  Investigate — {len(grp_investigate)}", expanded=True):
            for item in grp_investigate:
                _render_tr1_signal_card(item[0], item[1], item[2], db)

    if grp_monitor:
        with st.expander(f"◉  Monitor — {len(grp_monitor)}", expanded=True):
            for item in grp_monitor:
                _render_tr1_signal_card(item[0], item[1], item[2], db)

    if grp_ignore:
        _toggle_section(
            "tr1_ignore_open",
            f"—  Ignored / Negative — {len(grp_ignore)}",
            grp_ignore,
            lambda item: _render_tr1_signal_card(item[0], item[1], item[2], db),
        )

    st.markdown('<hr style="border-color:#1a2535;margin:1rem 0;">', unsafe_allow_html=True)


def _render_director_signals_section(
    db, director_signals: list, company_map: dict,
    since_cutoff, pos_filter: str
) -> None:
    """Render the Director Lens Signals section at the top of the signals tab."""
    if not director_signals:
        return

    # Apply filters
    visible = []
    for doc_id, signal in director_signals:
        ticker  = (signal.get("ticker") or "").upper()
        company = company_map.get(ticker, {})
        pos     = (company.get("position_state") or "").strip()

        if pos_filter == "Unreviewed" and pos:
            continue
        if pos_filter == "Acted"    and pos != "acted":
            continue
        if pos_filter == "Deferred" and pos != "deferred":
            continue
        if pos_filter == "Declined" and pos != "declined":
            continue
        if pos_filter == "Closed"   and pos != "closed":
            continue
        if pos == "declined" and pos_filter != "Declined":
            continue
        if pos == "closed" and pos_filter != "Closed":
            continue
        if since_cutoff:
            try:
                raw = signal.get("created_at")
                if hasattr(raw, "timestamp"):
                    dt = raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw
                else:
                    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < since_cutoff:
                    continue
            except Exception:
                pass
        visible.append((doc_id, signal, company))

    if not visible:
        return

    # Group by recommendation priority
    grp_investigate, grp_monitor, grp_other = [], [], []
    for item in visible:
        _, signal, _ = item
        rec    = (signal.get("agentic_recommendation") or "").lower()
        status = signal.get("agentic_status") or "pending"
        if "investigate" in rec:
            grp_investigate.append(item)
        elif "monitor" in rec:
            grp_monitor.append(item)
        else:
            grp_other.append(item)

    st.markdown(
        '<div style="font-size:0.7rem;font-family:IBM Plex Mono,monospace;color:#a064f7;'
        'letter-spacing:0.1em;text-transform:uppercase;margin:1rem 0 0.4rem;">Director Buying Lens</div>',
        unsafe_allow_html=True,
    )

    if grp_investigate:
        with st.expander(f"⬆  Investigate Further — {len(grp_investigate)}", expanded=True):
            for item in grp_investigate:
                _render_director_signal_card(item[0], item[1], item[2], db)

    if grp_monitor:
        with st.expander(f"◉  Monitor — {len(grp_monitor)}", expanded=True):
            for item in grp_monitor:
                _render_director_signal_card(item[0], item[1], item[2], db)

    if grp_other:
        _toggle_section(
            "dir_other_open",
            f"—  Pending / Ignore — {len(grp_other)}",
            grp_other,
            lambda item: _render_director_signal_card(item[0], item[1], item[2], db),
        )

    st.markdown('<hr style="border-color:#1a2535;margin:1rem 0;">', unsafe_allow_html=True)


def render_signals_tab(db, signals, company_map, director_signals=None) -> None:
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

    # ── Split signals collection by signal_type ──────────────────────────────────
    _director_signal_types = frozenset(["director_buying", "director_disposal", ""])
    _dir_signals = []
    _tr1_signals = []
    for doc_id, sig in (director_signals or []):
        sig_type = sig.get("signal_type") or ""
        if sig_type == "tr1_crossing":
            _tr1_signals.append((doc_id, sig))
        elif sig_type in _director_signal_types:
            _dir_signals.append((doc_id, sig))

    # ── Director lens signals section ────────────────────────────────────────────
    if _dir_signals:
        _render_director_signals_section(db, _dir_signals, company_map, since_cutoff, pos_filter)

    # ── TR-1 Accumulation lens section ───────────────────────────────────────────
    if _tr1_signals:
        _render_tr1_signals_section(db, _tr1_signals, company_map, since_cutoff, pos_filter)

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

    if total_shown == 0 and not director_signals:
        st.markdown(
            '<div class="empty-state">NO SIGNALS MATCH CURRENT FILTERS</div>',
            unsafe_allow_html=True,
        )
    elif total_shown > 0:
        st.markdown(
            '<div style="font-size:0.7rem;font-family:IBM Plex Mono,monospace;color:#f7a84a;'
            'letter-spacing:0.1em;text-transform:uppercase;margin:1rem 0 0.4rem;">'
            'Regulatory Catalyst Lens</div>',
            unsafe_allow_html=True,
        )
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
            _toggle_section(
                "reg_no_action_open",
                f"—  No action — {len(grp_no_action)}",
                grp_no_action,
                lambda item: _render_signal_card(*item),
            )
