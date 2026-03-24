"""
tab_performance.py

Performance tab — tracks price movement before, at, and after each signal
to measure lens accuracy and identify conditions being missed.

Data source: signal_performance Firestore collection, maintained by
scripts/update_signal_performance.py (daily cron).

Layout
------
1. Filter bar         — lens type, minimum data window
2. Summary metrics    — total tracked, % positive at 1m, % beating index, awaiting data
3. Lens accuracy      — per-lens table: count / positive% / beat-index% at 1m and 3m
4. Price position     — where the signal sits in the price move (pre vs post)
5. Signal detail      — sortable DataFrame with all snapshot columns
"""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from firestore_helpers import get_signal_performance
from constants import LegacyRec, Rec

# ---------------------------------------------------------------------------
# Snapshot keys in display order
# ---------------------------------------------------------------------------

_PRE_WINDOWS  = ["pre_90d", "pre_30d", "pre_1w"]
_POST_WINDOWS = ["post_1d", "post_2d", "post_3d", "post_4d", "post_5d",
                 "post_6d", "post_7d", "post_2w", "post_1m", "post_3m",
                 "post_6m", "post_12m"]

_WINDOW_LABELS = {
    "pre_90d":  "−90d",
    "pre_30d":  "−30d",
    "pre_1w":   "−1w",
    "baseline": "Signal",
    "post_1d":  "+1d",
    "post_2d":  "+2d",
    "post_3d":  "+3d",
    "post_4d":  "+4d",
    "post_5d":  "+5d",
    "post_6d":  "+6d",
    "post_7d":  "+7d",
    "post_2w":  "+2w",
    "post_1m":  "+1m",
    "post_3m":  "+3m",
    "post_6m":  "+6m",
    "post_12m": "+12m",
}

_LENS_LABELS = {
    "director_buying":      "Director Buying",
    "director_disposal":    "Director Disposal",
    "tr1_crossing":         "TR-1 Accumulation",
    "regulatory_catalyst":  "Regulatory Catalyst",
    "unknown":              "Unknown",
}

_LENS_ORDER = [
    "director_buying", "tr1_crossing", "regulatory_catalyst",
    "director_disposal", "unknown",
]

# Normalise raw stored recommendation values to canonical display labels.
# The signal_performance collection is populated from two different sources
# with different recommendation vocabularies; this map collapses them into
# a single display label. LegacyRec values (director/TR-1) are normalised
# to the same labels as Rec values (unified schema).
_REC_DISPLAY = {
    LegacyRec.INVESTIGATE: "Act",
    LegacyRec.YES:         "Act",
    LegacyRec.MONITOR:     "Monitor",
    Rec.ACT:               "Act",
    Rec.MONITOR:           "Monitor",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap_pct(doc: dict, key: str, field: str = "pct_vs_baseline") -> float | None:
    snap = (doc.get("snapshots") or {}).get(key) or {}
    val = snap.get(field)
    return float(val) if val is not None else None


def _snap_price(doc: dict, key: str) -> float | None:
    snap = (doc.get("snapshots") or {}).get(key) or {}
    val = snap.get("price_p")
    return float(val) if val is not None else None


def _fmt_pct(val: float | None, show_sign: bool = True) -> str:
    if val is None:
        return "—"
    sign = "+" if val >= 0 and show_sign else ""
    return f"{sign}{val:.1f}%"


def _pct_color(val: float | None) -> str:
    if val is None:
        return "perf-metric-neutral"
    return "perf-metric-positive" if val >= 0 else "perf-metric-negative"


def _colored(val: float | None) -> str:
    cls = _pct_color(val)
    return f'<span class="{cls}">{_fmt_pct(val)}</span>'


def _signal_date_str(doc: dict) -> str:
    raw = doc.get("signal_date") or ""
    try:
        return str(raw)[:10]
    except Exception:
        return "—"


def _lens_label(doc: dict) -> str:
    return _LENS_LABELS.get(doc.get("lens_type") or "unknown", "Unknown")


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

def _compute_summary(docs: list[dict]) -> dict:
    total = len(docs)
    has_1m, pos_1m, beat_1m = 0, 0, 0
    has_3m, pos_3m, beat_3m = 0, 0, 0
    awaiting = 0

    for doc in docs:
        snaps = doc.get("snapshots") or {}

        p1m = _snap_pct(doc, "post_1m")
        v1m = _snap_pct(doc, "post_1m", "pct_vs_index")
        if p1m is not None:
            has_1m += 1
            if p1m >= 0:
                pos_1m += 1
            if v1m is not None and v1m >= 0:
                beat_1m += 1

        p3m = _snap_pct(doc, "post_3m")
        v3m = _snap_pct(doc, "post_3m", "pct_vs_index")
        if p3m is not None:
            has_3m += 1
            if p3m >= 0:
                pos_3m += 1
            if v3m is not None and v3m >= 0:
                beat_3m += 1

        # "awaiting" = no post_1w data yet
        if _snap_pct(doc, "post_7d") is None:
            awaiting += 1

    return {
        "total":    total,
        "has_1m":   has_1m,
        "pos_1m":   pos_1m,
        "beat_1m":  beat_1m,
        "has_3m":   has_3m,
        "pos_3m":   pos_3m,
        "beat_3m":  beat_3m,
        "awaiting": awaiting,
    }


# ---------------------------------------------------------------------------
# Lens accuracy section
# ---------------------------------------------------------------------------

def _render_accuracy_table(docs: list[dict]) -> None:
    st.markdown(
        '<div class="perf-section-heading">Lens Accuracy</div>',
        unsafe_allow_html=True,
    )

    by_lens: dict[str, list] = {}
    for doc in docs:
        lt = doc.get("lens_type") or "unknown"
        by_lens.setdefault(lt, []).append(doc)

    rows = []
    for lt in _LENS_ORDER:
        group = by_lens.get(lt)
        if not group:
            continue

        n = len(group)
        with_1m  = [d for d in group if _snap_pct(d, "post_1m") is not None]
        with_3m  = [d for d in group if _snap_pct(d, "post_3m") is not None]

        def pct_pos(subset, key):
            if not subset:
                return None
            pos = sum(1 for d in subset if (_snap_pct(d, key) or 0) >= 0)
            return pos / len(subset) * 100

        def pct_beat(subset, key):
            if not subset:
                return None
            beat = sum(1 for d in subset
                       if (_snap_pct(d, key, "pct_vs_index") or -999) >= 0)
            return beat / len(subset) * 100

        pos_1m  = pct_pos(with_1m, "post_1m")
        beat_1m = pct_beat(with_1m, "post_1m")
        pos_3m  = pct_pos(with_3m, "post_3m")
        beat_3m = pct_beat(with_3m, "post_3m")

        def _acc(pct, n_sub, n_total):
            if pct is None or n_sub == 0:
                return f'<span class="perf-metric-neutral">— ({n_sub}/{n_total})</span>'
            cls = "perf-metric-positive" if pct >= 50 else "perf-metric-negative"
            return f'<span class="{cls}">{pct:.0f}%</span> <span class="perf-metric-neutral">({n_sub}/{n_total})</span>'

        rows.append(
            f'<div class="perf-accuracy-row">'
            f'<span class="perf-lens-label">{_LENS_LABELS.get(lt, lt)}</span>'
            f'<span style="width:50px;color:#8aabcc">{n} sig</span>'
            f'<span style="width:140px">+1m positive: {_acc(pos_1m, len(with_1m), n)}</span>'
            f'<span style="width:150px">+1m vs index: {_acc(beat_1m, len(with_1m), n)}</span>'
            f'<span style="width:140px">+3m positive: {_acc(pos_3m, len(with_3m), n)}</span>'
            f'<span style="width:150px">+3m vs index: {_acc(beat_3m, len(with_3m), n)}</span>'
            f'</div>'
        )

    st.markdown("\n".join(rows), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Price position chart (where is the signal in the move?)
# ---------------------------------------------------------------------------

def _render_price_position(docs: list[dict], company_map: dict) -> None:
    """
    Show the distribution of pre-signal vs post-signal price moves to indicate
    whether signals tend to be early, timely, or late relative to price action.
    Signals are binned by: pre_1w return (was price already moving?) and
    post_1w return (did it continue moving?).
    """
    st.markdown(
        '<div class="perf-section-heading">Signal Position in Price Move</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "All % values are relative to the signal-date price.  "
        "Pre-signal positive = stock was higher then (fell into the signal).  "
        "Post-signal positive = stock rose after signal.  "
        "A timely signal shows flat/negative pre with positive post."
    )

    rows = []
    for doc in docs:
        pre_30  = _snap_pct(doc, "pre_30d")
        pre_1w  = _snap_pct(doc, "pre_1w")
        post_1w = _snap_pct(doc, "post_7d")
        post_1m = _snap_pct(doc, "post_1m")

        if pre_30 is None and pre_1w is None and post_1w is None:
            continue

        ticker = doc.get("ticker") or "—"
        rows.append({
            "Ticker":    ticker,
            "Sector":    company_map.get(ticker.upper(), {}).get("sector") or "—",
            "Lens":      _lens_label(doc),
            "Date":      _signal_date_str(doc),
            "−30d→0":   pre_30,
            "−1w→0":    pre_1w,
            "0→+1w":    post_1w,
            "0→+1m":    post_1m,
        })

    if not rows:
        st.markdown('<div class="perf-metric-neutral" style="font-size:0.75rem;padding:1rem 0">No signals with pre-signal data yet.</div>', unsafe_allow_html=True)
        return

    df = pd.DataFrame(rows)
    pct_cols = ["−30d→0", "−1w→0", "0→+1w", "0→+1m"]

    def _style_pct(val):
        if pd.isna(val):
            return "color: #3a4f66"
        return "color: #4af798" if val >= 0 else "color: #f76a6a"

    styled = (
        df.style
        .format({c: lambda v: _fmt_pct(v) for c in pct_cols}, na_rep="—")
        .applymap(_style_pct, subset=pct_cols)
        .set_properties(**{"font-family": "IBM Plex Mono, monospace", "font-size": "0.72rem"})
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Signal detail table
# ---------------------------------------------------------------------------

def _render_detail_table(docs: list[dict], lens_filter: str, rec_filter: str, company_map: dict) -> None:
    st.markdown(
        '<div class="perf-section-heading">Signal Detail</div>',
        unsafe_allow_html=True,
    )

    rows = []
    for doc in docs:
        lt = doc.get("lens_type") or "unknown"
        if lens_filter != "All" and _LENS_LABELS.get(lt, lt) != lens_filter:
            continue
        if rec_filter != "All":
            doc_rec = _REC_DISPLAY.get((doc.get("recommendation") or "").split()[0].lower())
            if doc_rec != rec_filter:
                continue

        ticker = doc.get("ticker") or "—"
        rows.append({
            "Ticker":    ticker,
            "Company":   (doc.get("company_name") or "")[:28],
            "Sector":    company_map.get(ticker.upper(), {}).get("sector") or "—",
            "Lens":      _lens_label(doc),
            "Date":      _signal_date_str(doc),
            "Rec":       _REC_DISPLAY.get((doc.get("recommendation") or "").split()[0].lower()) or (doc.get("recommendation") or "—").title(),
            "Price (p)": _snap_price(doc, "baseline"),
            "−30d":      _snap_pct(doc, "pre_30d"),
            "−1w":       _snap_pct(doc, "pre_1w"),
            "+1d":       _snap_pct(doc, "post_1d"),
            "+2d":       _snap_pct(doc, "post_2d"),
            "+3d":       _snap_pct(doc, "post_3d"),
            "+1w":       _snap_pct(doc, "post_7d"),
            "+2w":       _snap_pct(doc, "post_2w"),
            "+1m":       _snap_pct(doc, "post_1m"),
            "+1m idx":   _snap_pct(doc, "post_1m", "pct_vs_index"),
            "+3m":       _snap_pct(doc, "post_3m"),
            "+3m idx":   _snap_pct(doc, "post_3m", "pct_vs_index"),
            "+6m":       _snap_pct(doc, "post_6m"),
        })

    if not rows:
        st.markdown('<div class="empty-state">NO SIGNALS MATCH FILTERS</div>', unsafe_allow_html=True)
        return

    df = pd.DataFrame(rows).sort_values("Date", ascending=False)
    pct_cols = ["−30d", "−1w", "+1d", "+2d", "+3d", "+1w", "+2w",
                "+1m", "+1m idx", "+3m", "+3m idx", "+6m"]

    def _style_pct(val):
        if pd.isna(val):
            return "color: #3a4f66"
        return "color: #4af798" if val >= 0 else "color: #f76a6a"

    fmt = {c: lambda v: _fmt_pct(v) for c in pct_cols}
    fmt["Price (p)"] = lambda v: f"{v:.1f}p" if v is not None else "—"

    styled = (
        df.style
        .format(fmt, na_rep="—")
        .applymap(_style_pct, subset=pct_cols)
        .set_properties(**{"font-family": "IBM Plex Mono, monospace", "font-size": "0.70rem"})
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)
    st.caption(
        f"{len(rows)} signals shown.  "
        f"Pre-signal % (−30d, −1w): positive = stock was higher then, i.e. fell into the signal.  "
        f"Post-signal % (+1d…+12m): positive = stock rose after signal.  "
        f"'idx' columns = outperformance vs benchmark index."
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render_performance_tab(db, company_map: dict) -> None:
    perf_docs = get_signal_performance(db)

    if not perf_docs:
        st.markdown('<div class="empty-state">NO PERFORMANCE DATA YET<br><br>Run scripts/update_signal_performance.py to populate.</div>', unsafe_allow_html=True)
        return

    # --- Filter bar ---
    col_l, col_r, col_spacer = st.columns([2, 2, 8])
    with col_l:
        lens_options = ["All"] + [
            _LENS_LABELS[lt] for lt in _LENS_ORDER
            if any((d.get("lens_type") or "unknown") == lt for d in perf_docs)
        ]
        lens_filter = st.selectbox("Lens", lens_options, key="perf_lens_filter", label_visibility="collapsed")
    with col_r:
        # Normalise raw recommendation values to canonical display labels before
        # building options — collapses "investigate"/"yes" → "Act", "monitor" → "Monitor".
        all_recs = sorted({
            _REC_DISPLAY.get((d.get("recommendation") or "").split()[0].lower(), "")
            for d in perf_docs
            if (d.get("recommendation") or "").strip()
        } - {""})
        rec_options = ["All"] + all_recs
        rec_filter = st.selectbox("Recommendation", rec_options, key="perf_rec_filter", label_visibility="collapsed")

    # Apply lens + rec filters to summary, accuracy, and position sections
    filtered = perf_docs
    if lens_filter != "All":
        filtered = [d for d in filtered if _lens_label(d) == lens_filter]
    if rec_filter != "All":
        filtered = [
            d for d in filtered
            if _REC_DISPLAY.get((d.get("recommendation") or "").split()[0].lower()) == rec_filter
        ]

    # --- Summary metrics ---
    s = _compute_summary(filtered)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Tracked", s["total"])
    c2.metric(
        "+1m positive",
        f"{s['pos_1m']/s['has_1m']*100:.0f}%" if s["has_1m"] else "—",
        f"{s['has_1m']} signals",
    )
    c3.metric(
        "+1m beat index",
        f"{s['beat_1m']/s['has_1m']*100:.0f}%" if s["has_1m"] else "—",
        f"{s['has_1m']} signals",
    )
    c4.metric(
        "+3m positive",
        f"{s['pos_3m']/s['has_3m']*100:.0f}%" if s["has_3m"] else "—",
        f"{s['has_3m']} signals",
    )
    c5.metric("Awaiting +1w", s["awaiting"])

    st.markdown("---")

    # --- Lens accuracy ---
    _render_accuracy_table(filtered)

    st.markdown("---")

    # --- Price position ---
    _render_price_position(filtered, company_map)

    st.markdown("---")

    # --- Signal detail ---
    _render_detail_table(perf_docs, lens_filter, rec_filter, company_map)
