# Pure UI helper functions — stdlib only, no Streamlit or Firestore dependencies.

from datetime import datetime, timezone

from constants import SignalState, PositionState, Rec, LegacyRec


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


_ACTION_TOOLTIPS = {
    Rec.ACT:     "The LLM has assessed this announcement as a potential investment catalyst worth investigating.",
    Rec.MONITOR: "Weak signal — worth tracking but not yet at the threshold for action.",
    Rec.IGNORE:  "The LLM found no meaningful catalyst signal in this announcement.",
}


def recommended_action_badge_unified(recommendation: str):
    """
    Badge HTML and CSS card class from a unified recommendation value.

    Parameters
    ----------
    recommendation : str
        One of: 'act', 'monitor', 'ignore' (Rec constants).
        Also accepts legacy values 'yes' / 'investigate' (normalised to 'act').

    Returns
    -------
    (badge_html, card_css_class) tuple.
    """
    r = (recommendation or "").lower()
    # Normalise legacy vocabulary so callers need not pre-convert.
    if r == LegacyRec.YES or LegacyRec.INVESTIGATE in r:
        r = Rec.ACT
    if r == Rec.ACT:
        tip = _ACTION_TOOLTIPS[Rec.ACT]
        return f'<span class="badge badge-yes" title="{tip}">⬆ Action</span>', "action-yes"
    if r == Rec.MONITOR:
        tip = _ACTION_TOOLTIPS[Rec.MONITOR]
        return f'<span class="badge badge-monitor" title="{tip}">◉ Monitor</span>', "action-monitor"
    tip = _ACTION_TOOLTIPS[Rec.IGNORE]
    return f'<span class="badge badge-no" title="{tip}">— No action</span>', "action-no"


def recommend_add_badge(assessment_text):
    val = get_field(assessment_text, "RECOMMEND_ADD").lower()
    if val == LegacyRec.YES:
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


_SIGNAL_STATE_STYLE = {
    SignalState.WATCHING:          ("WATCHING",   "badge-state-watching"),
    SignalState.MONITOR:           ("MONITOR",    "badge-state-monitor"),
    SignalState.SIGNAL_ACTIVE:     ("SIGNAL",     "badge-state-active"),
    SignalState.SIGNAL_REINFORCED: ("CONFIRMED",  "badge-state-reinforced"),
    SignalState.SIGNAL_MIXED:      ("MIXED",      "badge-state-mixed"),
    SignalState.SIGNAL_NEGATIVE:   ("NEGATIVE",   "badge-state-negative"),
}

_STATE_TOOLTIPS = {
    SignalState.WATCHING:          "No signals detected yet. Company is monitored.",
    SignalState.MONITOR:           "Weak or moderate signals detected. Below the threshold for action.",
    SignalState.SIGNAL_ACTIVE:     "Strong signal active. Review and decide.",
    SignalState.SIGNAL_REINFORCED: "A second strong signal has reinforced the first.",
    SignalState.SIGNAL_MIXED:      "A counter-signal arrived while a positive signal was active. Review carefully.",
    SignalState.SIGNAL_NEGATIVE:   "Strong counter-signal. Positive signals are suppressed until this decays.",
}

_POSITION_STATE_STYLE = {
    PositionState.ACTED:    ("ACTED",    "badge-pos-acted"),
    PositionState.DEFERRED: ("DEFERRED", "badge-pos-deferred"),
    PositionState.DECLINED: ("DECLINED", "badge-pos-declined"),
    PositionState.CLOSED:   ("CLOSED",   "badge-pos-closed"),
}


def signal_state_badge(state, age=""):
    """Return HTML badge for a signal_state value, with optional time-in-state suffix."""
    label, cls = _SIGNAL_STATE_STYLE.get(state or SignalState.WATCHING, ("WATCHING", "badge-state-watching"))
    tooltip = _STATE_TOOLTIPS.get(state or SignalState.WATCHING, "")
    age_str = f" — {age}" if age else ""
    return f'<span class="badge {cls}" title="{tooltip}">{label}{age_str}</span>'


def position_state_badge(state):
    """Return HTML badge for a position_state value, or empty string if unset."""
    if not state:
        return ""
    label, cls = _POSITION_STATE_STYLE.get(state, (state.upper(), "badge-no"))
    return f'<span class="badge {cls}">{label}</span>'


def format_signal_age(since_val) -> str:
    """Return a human-readable age string (e.g. '3d', '12h', '45m') from a signal_state_since value."""
    if not since_val:
        return ""
    try:
        s = str(since_val).replace("Z", "+00:00")
        since = datetime.fromisoformat(s)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - since
        days = delta.days
        hours = delta.seconds // 3600
        if days >= 1:
            return f"{days}d"
        if hours >= 1:
            return f"{hours}h"
        return f"{max(delta.seconds // 60, 1)}m"
    except Exception:
        return ""


def format_market_cap(gbp) -> str:
    """Format a raw GBP market cap value (e.g. 45_300_000) as '£45m' or '£1.2bn'."""
    if gbp is None:
        return "—"
    try:
        m = float(gbp)
        if m >= 1_000_000_000:
            return f"£{m / 1_000_000_000:.1f}bn"
        if m >= 1_000_000:
            return f"£{m / 1_000_000:.0f}m"
        return f"£{m:,.0f}"
    except Exception:
        return "—"


def format_price_info(price_pence, price_change) -> str:
    """Return an HTML snippet showing price and directional change."""
    if price_pence is None:
        return '<span class="card-price-null">—</span>'
    try:
        price_str = f"{float(price_pence):.0f}p"
        if price_change:
            chg = str(price_change).strip()
            # Parse numeric value to determine direction reliably.
            # LSEG does not always include a '+' prefix on positive values.
            try:
                val = float(chg.replace(",", "").rstrip("%"))
            except ValueError:
                val = None
            if val is not None:
                if val > 0:
                    return f'{price_str} <span class="card-price-up">▲ {chg}</span>'
                if val < 0:
                    return f'{price_str} <span class="card-price-down">▼ {chg}</span>'
                return f'{price_str} <span class="card-price-neutral">↔ 0%</span>'
            # Fallback for unparseable strings — use string prefix heuristic
            if chg.startswith("+"):
                return f'{price_str} <span class="card-price-up">▲ {chg}</span>'
            if chg.startswith("-"):
                return f'{price_str} <span class="card-price-down">▼ {chg}</span>'
            return f'{price_str} <span class="card-price-neutral">{chg}</span>'
        return f'<span class="card-price">{price_str}</span>'
    except Exception:
        return '<span class="card-price-null">—</span>'
