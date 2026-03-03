"""
signal_state.py

Pure state transition engine for the two-axis signal/position state model.

No Firestore imports — this module is intentionally free of storage concerns
so it can be tested independently and reused by both the autonomous pipeline
(pipeline.py) and the interactive runner (job_runner.py).

Signal states (system-managed, ordered by conviction):
  watching        — default; no signals yet (None treated identically)
  monitor         — one or more weak/moderate signals; below threshold for action
  signal_active   — strong signal present; surfaced to investor
  signal_reinforced — confirming signal received on top of active signal
  signal_mixed    — counter-signal received while active/reinforced
  signal_negative — strong counter-signal; awaits decay before positive signals land

Position states (human-managed):
  acted / deferred / declined / closed
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from abstractions import UniverseCompany


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

SIGNAL_STATES = frozenset({
    "watching",
    "monitor",
    "signal_active",
    "signal_reinforced",
    "signal_mixed",
    "signal_negative",
})

SIGNAL_STRENGTHS = frozenset({"strong", "moderate", "weak", "noise"})


def _normalise(state: Optional[str]) -> str:
    """Treat None and any unknown value identically to 'watching'."""
    return state if state in SIGNAL_STATES else "watching"


# ---------------------------------------------------------------------------
# Transition table
# (current_state_normalised, effective_strength) → new_state or None
# effective_strength is one of: strong / moderate / weak / noise / negative
# 'negative' = is_negative_signal() returned True (overrides the strength value)
# ---------------------------------------------------------------------------

TRANSITION_TABLE: dict = {
    # ── From watching ──────────────────────────────────────────────────────
    ("watching", "strong"):    "signal_active",
    ("watching", "moderate"):  "monitor",
    ("watching", "weak"):      "monitor",
    ("watching", "noise"):     None,
    ("watching", "negative"):  None,

    # ── From monitor ───────────────────────────────────────────────────────
    ("monitor", "strong"):     "signal_active",
    ("monitor", "moderate"):   "monitor",   # no state change; last_signal_at refreshed by caller
    ("monitor", "weak"):       "monitor",
    ("monitor", "noise"):      None,
    ("monitor", "negative"):   "watching",  # any "no" verdict resets monitor

    # ── From signal_active ─────────────────────────────────────────────────
    ("signal_active", "strong"):    "signal_reinforced",
    ("signal_active", "moderate"):  "signal_active",
    ("signal_active", "weak"):      "signal_active",
    ("signal_active", "noise"):     None,
    ("signal_active", "negative"):  "signal_mixed",

    # ── From signal_reinforced ─────────────────────────────────────────────
    ("signal_reinforced", "strong"):    "signal_reinforced",
    ("signal_reinforced", "moderate"):  "signal_reinforced",
    ("signal_reinforced", "weak"):      "signal_reinforced",
    ("signal_reinforced", "noise"):     None,
    ("signal_reinforced", "negative"):  "signal_mixed",

    # ── From signal_mixed ──────────────────────────────────────────────────
    ("signal_mixed", "strong"):    "signal_reinforced",
    ("signal_mixed", "moderate"):  "signal_active",
    ("signal_mixed", "weak"):      "signal_active",
    ("signal_mixed", "noise"):     None,
    ("signal_mixed", "negative"):  "signal_negative",

    # ── From signal_negative ───────────────────────────────────────────────
    # Must decay back to watching before positive signals take effect
    ("signal_negative", "strong"):    None,
    ("signal_negative", "moderate"):  None,
    ("signal_negative", "weak"):      None,
    ("signal_negative", "noise"):     None,
    ("signal_negative", "negative"):  None,
}


# ---------------------------------------------------------------------------
# Decay rules
# state → (decay_config_key, decays_to_state)
# ---------------------------------------------------------------------------

DECAY_RULES: dict = {
    "monitor":          ("monitor_decay_days",               "watching"),
    "signal_active":    ("active_confirmation_window_days",  "monitor"),
    "signal_reinforced": ("reinforced_staleness_days",       "signal_active"),
    "signal_mixed":     ("mixed_resolution_days",            "signal_active"),
    "signal_negative":  ("negative_decay_days",              "watching"),
}


# ---------------------------------------------------------------------------
# LLM output parsers
# ---------------------------------------------------------------------------

def classify_signal_strength(llm_analysis: str) -> str:
    """
    Derive signal strength from the structured LLM output.

    Reads RECOMMENDED_ACTION and CONFIDENCE_SIGNAL fields.
    Returns one of: 'strong', 'moderate', 'weak', 'noise'.
    """
    action = ""
    confidence = ""
    for line in llm_analysis.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("RECOMMENDED_ACTION:"):
            action = stripped.split(":", 1)[1].strip().lower()
        elif stripped.upper().startswith("CONFIDENCE_SIGNAL:"):
            confidence = stripped.split(":", 1)[1].strip().lower()

    if action == "yes":
        if any(kw in confidence for kw in ("very high", "high", "strong")):
            return "strong"
        return "moderate"
    if action == "monitor":
        return "weak"
    # "no" or anything else → noise
    return "noise"


def is_negative_signal(llm_analysis: str) -> bool:
    """
    Returns True if the LLM output is a counter-signal.

    For Phase 2a/2b: any RECOMMENDED_ACTION: no verdict counts.
    Future lenses may extend this with explicit counter-signal keywords
    (director sells, distressed placing, etc.).
    """
    for line in llm_analysis.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("RECOMMENDED_ACTION:"):
            return stripped.split(":", 1)[1].strip().lower() == "no"
    return False


def extract_confidence(llm_analysis: str) -> str:
    """Extract the raw CONFIDENCE_SIGNAL value from LLM output."""
    for line in llm_analysis.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("CONFIDENCE_SIGNAL:"):
            return stripped.split(":", 1)[1].strip()
    return ""


# ---------------------------------------------------------------------------
# Core transition functions
# ---------------------------------------------------------------------------

def compute_signal_transition(
    current_state: Optional[str],
    signal_strength: str,
    is_negative: bool,
) -> Optional[str]:
    """
    Compute the new signal state given the current state and incoming signal.

    Args:
        current_state: current signal_state value (None treated as 'watching')
        signal_strength: one of 'strong', 'moderate', 'weak', 'noise'
        is_negative: True if the signal is a counter-signal (e.g. RECOMMENDED_ACTION: no)

    Returns:
        New state string, or None if no transition should occur.
        The caller should check: if return value == current (or None) → no change.
    """
    normalised = _normalise(current_state)
    # When is_negative=True, the directional character of the signal overrides
    # the strength — 'negative' is treated as its own strength in the table.
    effective = "negative" if is_negative else signal_strength
    new_state = TRANSITION_TABLE.get((normalised, effective))
    return new_state


def compute_decay_transitions(
    companies: List[UniverseCompany],
    config: dict,
    now: datetime,
) -> List[Tuple[str, str, str]]:
    """
    Identify companies whose signal_state has exceeded its confirmation window.

    Args:
        companies: full universe company list (UniverseCompany objects)
        config: signal config dict (e.g. from get_signal_config())
        now: reference time for window calculation

    Returns:
        List of (ticker_lse, current_state, new_state) for each company that
        should decay. Empty list if none have expired.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    result = []
    for company in companies:
        state = company.signal_state
        rule = DECAY_RULES.get(state)
        if rule is None:
            continue  # watching/None — no decay

        config_key, decays_to = rule
        days = config.get(config_key, 0)
        if days <= 0 or company.signal_state_since is None:
            continue

        since = company.signal_state_since
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

        if (now - since).days >= days:
            result.append((company.ticker_lse, state, decays_to))

    return result


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import timedelta

    print("signal_state.py self-test\n")
    failures = 0

    def check(label, got, expected):
        global failures
        ok = got == expected
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
            print(f"  [{status}] {label}: got {got!r}, expected {expected!r}")
        else:
            print(f"  [{status}] {label}")

    # ── classify_signal_strength ────────────────────────────────────────────
    print("classify_signal_strength:")
    check("yes + high confidence → strong",
          classify_signal_strength("RECOMMENDED_ACTION: Yes\nCONFIDENCE_SIGNAL: Director buying at high conviction"),
          "strong")
    check("yes + very high → strong",
          classify_signal_strength("RECOMMENDED_ACTION: Yes\nCONFIDENCE_SIGNAL: Very high — multiple insider buys"),
          "strong")
    check("yes + moderate → moderate",
          classify_signal_strength("RECOMMENDED_ACTION: Yes\nCONFIDENCE_SIGNAL: Moderate — one director buy"),
          "moderate")
    check("yes + no qualifier → moderate",
          classify_signal_strength("RECOMMENDED_ACTION: Yes\nCONFIDENCE_SIGNAL: None identified"),
          "moderate")
    check("monitor → weak",
          classify_signal_strength("RECOMMENDED_ACTION: Monitor\nCONFIDENCE_SIGNAL: Low"),
          "weak")
    check("no → noise",
          classify_signal_strength("RECOMMENDED_ACTION: No\nCONFIDENCE_SIGNAL: None identified"),
          "noise")
    check("empty → noise",
          classify_signal_strength(""),
          "noise")

    # ── is_negative_signal ──────────────────────────────────────────────────
    print("\nis_negative_signal:")
    check("no → True",  is_negative_signal("RECOMMENDED_ACTION: No"), True)
    check("yes → False", is_negative_signal("RECOMMENDED_ACTION: Yes"), False)
    check("monitor → False", is_negative_signal("RECOMMENDED_ACTION: Monitor"), False)

    # ── compute_signal_transition ────────────────────────────────────────────
    print("\ncompute_signal_transition:")
    T = compute_signal_transition
    check("None + strong → signal_active",       T(None, "strong", False), "signal_active")
    check("None + moderate → monitor",           T(None, "moderate", False), "monitor")
    check("None + weak → monitor",               T(None, "weak", False), "monitor")
    check("None + noise → None",                 T(None, "noise", False), None)
    check("watching + strong → signal_active",   T("watching", "strong", False), "signal_active")
    check("monitor + strong → signal_active",    T("monitor", "strong", False), "signal_active")
    check("monitor + negative → watching",       T("monitor", "noise", True), "watching")
    check("signal_active + strong → reinforced", T("signal_active", "strong", False), "signal_reinforced")
    check("signal_active + negative → mixed",    T("signal_active", "moderate", True), "signal_mixed")
    check("signal_reinforced + negative → mixed",T("signal_reinforced", "weak", True), "signal_mixed")
    check("signal_mixed + strong → reinforced",  T("signal_mixed", "strong", False), "signal_reinforced")
    check("signal_mixed + moderate → active",    T("signal_mixed", "moderate", False), "signal_active")
    check("signal_mixed + negative → negative",  T("signal_mixed", "strong", True), "signal_negative")
    check("signal_negative + strong → None",     T("signal_negative", "strong", False), None)
    check("signal_negative + negative → None",   T("signal_negative", "noise", True), None)
    check("unknown state treated as watching",   T("INVALID", "strong", False), "signal_active")

    # ── compute_decay_transitions ────────────────────────────────────────────
    print("\ncompute_decay_transitions:")
    now = datetime.now(timezone.utc)
    config = {
        "monitor_decay_days": 30,
        "active_confirmation_window_days": 90,
        "reinforced_staleness_days": 180,
        "mixed_resolution_days": 30,
        "negative_decay_days": 90,
    }

    def _make_company(ticker, state, since_days_ago):
        from abstractions import UniverseCompany
        from datetime import date
        c = UniverseCompany(
            ticker_lse=ticker,
            ticker_yahoo=f"{ticker}.L",
            company_name=f"{ticker} PLC",
            tier=1,
            listing_exchange="AIM",
            entity_type="OPERATING",
            liquidity_flag="LIQUID",
            universe_added_date=date.today(),
            last_refreshed=now,
        )
        c.signal_state = state
        c.signal_state_since = now - timedelta(days=since_days_ago)
        return c

    companies_test = [
        _make_company("A", "monitor", 31),        # expired
        _make_company("B", "monitor", 29),        # not yet expired
        _make_company("C", "signal_active", 91),  # expired
        _make_company("D", "signal_active", 89),  # not yet
        _make_company("E", "watching", 999),      # no decay rule
        _make_company("F", "signal_negative", 91),# expired
    ]

    decayed = compute_decay_transitions(companies_test, config, now)
    decayed_tickers = {t for t, _, _ in decayed}
    check("monitor expired decays", "A" in decayed_tickers, True)
    check("monitor not-expired stays", "B" not in decayed_tickers, True)
    check("signal_active expired decays", "C" in decayed_tickers, True)
    check("signal_active not-expired stays", "D" not in decayed_tickers, True)
    check("watching never decays", "E" not in decayed_tickers, True)
    check("signal_negative expired decays", "F" in decayed_tickers, True)

    # Check decay targets
    for ticker, old, new in decayed:
        if ticker == "A":
            check("monitor → watching", new, "watching")
        if ticker == "C":
            check("signal_active → monitor", new, "monitor")
        if ticker == "F":
            check("signal_negative → watching", new, "watching")

    print(f"\n{'All tests passed.' if failures == 0 else f'{failures} test(s) FAILED.'}")
