# Pure UI helper functions — stdlib only, no Streamlit or Firestore dependencies.

from datetime import datetime


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


_SIGNAL_STATE_STYLE = {
    "watching":           ("WATCHING",   "badge-state-watching"),
    "monitor":            ("MONITOR",    "badge-state-monitor"),
    "signal_active":      ("SIGNAL",     "badge-state-active"),
    "signal_reinforced":  ("CONFIRMED",  "badge-state-reinforced"),
    "signal_mixed":       ("MIXED",      "badge-state-mixed"),
    "signal_negative":    ("NEGATIVE",   "badge-state-negative"),
}

_POSITION_STATE_STYLE = {
    "acted":    ("ACTED",    "badge-pos-acted"),
    "deferred": ("DEFERRED", "badge-pos-deferred"),
    "declined": ("DECLINED", "badge-pos-declined"),
    "closed":   ("CLOSED",   "badge-pos-closed"),
}


def signal_state_badge(state):
    """Return HTML badge for a signal_state value."""
    label, cls = _SIGNAL_STATE_STYLE.get(state or "watching", ("WATCHING", "badge-state-watching"))
    return f'<span class="badge {cls}">{label}</span>'


def position_state_badge(state):
    """Return HTML badge for a position_state value, or empty string if unset."""
    if not state:
        return ""
    label, cls = _POSITION_STATE_STYLE.get(state, (state.upper(), "badge-no"))
    return f'<span class="badge {cls}">{label}</span>'
