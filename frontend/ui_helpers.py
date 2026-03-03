# Pure UI helper functions — stdlib only, no Streamlit or Firestore dependencies.

from datetime import datetime, timezone


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
    "yes":     "The LLM has assessed this announcement as a potential investment catalyst worth investigating.",
    "monitor": "Weak signal — worth tracking but not yet at the threshold for action.",
    "no":      "The LLM found no meaningful catalyst signal in this announcement.",
}


def recommended_action_badge(analysis_text):
    val = get_field(analysis_text, "RECOMMENDED_ACTION").lower()
    if val == "yes":
        tip = _ACTION_TOOLTIPS["yes"]
        return f'<span class="badge badge-yes" title="{tip}">⬆ Action</span>', "action-yes"
    elif val == "monitor":
        tip = _ACTION_TOOLTIPS["monitor"]
        return f'<span class="badge badge-monitor" title="{tip}">◉ Monitor</span>', "action-monitor"
    else:
        tip = _ACTION_TOOLTIPS["no"]
        return f'<span class="badge badge-no" title="{tip}">— No action</span>', "action-no"


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


_SIGNAL_STATE_STYLE = {
    "watching":           ("WATCHING",   "badge-state-watching"),
    "monitor":            ("MONITOR",    "badge-state-monitor"),
    "signal_active":      ("SIGNAL",     "badge-state-active"),
    "signal_reinforced":  ("CONFIRMED",  "badge-state-reinforced"),
    "signal_mixed":       ("MIXED",      "badge-state-mixed"),
    "signal_negative":    ("NEGATIVE",   "badge-state-negative"),
}

_STATE_TOOLTIPS = {
    "watching":          "No signals detected yet. Company is monitored.",
    "monitor":           "Weak or moderate signals detected. Below the threshold for action.",
    "signal_active":     "Strong signal active. Review and decide.",
    "signal_reinforced": "A second strong signal has reinforced the first.",
    "signal_mixed":      "A counter-signal arrived while a positive signal was active. Review carefully.",
    "signal_negative":   "Strong counter-signal. Positive signals are suppressed until this decays.",
}

_POSITION_STATE_STYLE = {
    "acted":    ("ACTED",    "badge-pos-acted"),
    "deferred": ("DEFERRED", "badge-pos-deferred"),
    "declined": ("DECLINED", "badge-pos-declined"),
    "closed":   ("CLOSED",   "badge-pos-closed"),
}


def signal_state_badge(state, age=""):
    """Return HTML badge for a signal_state value, with optional time-in-state suffix."""
    label, cls = _SIGNAL_STATE_STYLE.get(state or "watching", ("WATCHING", "badge-state-watching"))
    tooltip = _STATE_TOOLTIPS.get(state or "watching", "")
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
            if chg.startswith("+"):
                return f'{price_str} <span class="card-price-up">▲ {chg}</span>'
            if chg.startswith("-"):
                return f'{price_str} <span class="card-price-down">▼ {chg}</span>'
            return f'{price_str} <span class="card-price-neutral">{chg}</span>'
        return f'<span class="card-price">{price_str}</span>'
    except Exception:
        return '<span class="card-price-null">—</span>'
