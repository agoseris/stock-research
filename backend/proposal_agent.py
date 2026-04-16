"""
proposal_agent.py

Post-classification enrichment layer. Called immediately after a signal is
saved to signals_unified and before the Telegram notification is dispatched.

Responsibilities (spec §3.1, §3.2, §3.5):
1. Cross-lens disqualification (§3.2) — query the last 30 days of signals
   for the same ticker; if any match a disqualification keyword in their
   summary or signal_type, downgrade act → monitor and record the refs.
2. Signal maturity classification (§3.1) — first_signal vs reinforced, based
   on whether a prior qualifying signal exists for this ticker in the window.
3. Active lens count snapshot (§3.5) — number of distinct lenses with
   non-ignore signals in the window at time of firing.

No LLM calls — fully deterministic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_SIGNALS_UNIFIED = "signals_unified"
_LOOKBACK_DAYS = 30

# ---------------------------------------------------------------------------
# Disqualification keywords (spec §3.2)
# ---------------------------------------------------------------------------

DISQUALIFICATION_KEYWORDS: frozenset[str] = frozenset([
    "suspension",
    "suspended",
    "investigation",
    "delay in results",
    "delayed results",
    "going concern",
    "material uncertainty",
    "placing",
    "equity raise",
    "open offer",
    "rescue financing",
    "covenant breach",
    "restatement",
    "auditor resign",
    "qualified opinion",
    "board investigation",
    "profit warning",
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_datetime(val: Any) -> datetime | None:
    """Convert a Firestore Timestamp, datetime, or ISO string to UTC-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        s = str(val).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _recent_ticker_signals(db, ticker: str, lookback_days: int = _LOOKBACK_DAYS) -> list[dict]:
    """
    Return all non-dismissed signals_unified docs for this ticker in the
    lookback window, keyed with _doc_id.

    Date filtering is done in Python to handle the mixed string/timestamp
    stored_at schema (regulatory catalyst stores ISO strings; TR-1 and
    director store Firestore timestamps).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    try:
        docs = (
            db.collection(_SIGNALS_UNIFIED)
            .where("ticker", "==", ticker)
            .where("dismissed", "==", False)
            .stream()
        )
        results = []
        for doc in docs:
            data = doc.to_dict() or {}
            stored_at = _to_datetime(data.get("stored_at"))
            if stored_at and stored_at >= cutoff:
                data["_doc_id"] = doc.id
                results.append(data)
        return results
    except Exception as e:
        msg = f"  [proposal_agent] query failed for ticker={ticker}: {e}"
        print(msg)
        logger.warning(msg)
        return []


# ---------------------------------------------------------------------------
# Core checks
# ---------------------------------------------------------------------------

def check_disqualification(
    recent_signals: list[dict],
    current_signal_id: str,
) -> list[dict]:
    """
    Scan prior signals for disqualification keywords in summary or signal_type.

    Returns a list of hit dicts — one per matching prior signal:
        {signal_id, keyword_matched, stored_at, summary_snippet}

    The current signal is excluded from the check.
    """
    hits = []
    for data in recent_signals:
        doc_id = data.get("_doc_id", "")
        if doc_id == current_signal_id:
            continue
        text = " ".join([
            (data.get("summary") or "").lower(),
            (data.get("signal_type") or "").lower(),
        ])
        for keyword in DISQUALIFICATION_KEYWORDS:
            if keyword in text:
                summary = data.get("summary") or ""
                hits.append({
                    "signal_id":      doc_id,
                    "keyword_matched": keyword,
                    "stored_at":      str(data.get("stored_at", "")),
                    "summary_snippet": summary[:120],
                })
                break  # one hit per prior signal is sufficient
    return hits


def compute_signal_maturity(
    recent_signals: list[dict],
    current_signal_id: str,
) -> tuple[str, list[str]]:
    """
    Classify signal maturity as 'reinforced' or 'first_signal'.

    A signal is reinforced if at least one prior non-dismissed signal for the
    same ticker exists at act or monitor level within the lookback window.
    Noise/ignore signals do not count (spec §3.1).

    Returns ("reinforced", [prior_signal_ids]) or ("first_signal", []).
    """
    qualifying_prior = [
        data["_doc_id"]
        for data in recent_signals
        if data.get("_doc_id") != current_signal_id
        and (data.get("recommendation") or "").lower() in ("act", "monitor")
    ]
    if qualifying_prior:
        return "reinforced", qualifying_prior
    return "first_signal", []


def count_active_lenses(recent_signals: list[dict], current_signal_id: str) -> int:
    """
    Count distinct lens_id values with non-ignore signals in the lookback window.
    Includes the current signal if it is among the recent signals.
    """
    lens_ids = {
        data["lens_id"]
        for data in recent_signals
        if (data.get("recommendation") or "").lower() in ("act", "monitor")
        and data.get("lens_id")
    }
    return len(lens_ids)


# ---------------------------------------------------------------------------
# TR-1 compounding check (spec §3.4)
# ---------------------------------------------------------------------------

_QUALIFYING_CATEGORIES: frozenset[str] = frozenset(["active_manager", "named_individual"])


def check_tr1_compounding(
    recent_signals: list[dict],
    current_signal_id: str,
    current_notifier_name: str | None,
    current_notifier_category: str | None,
) -> bool:
    """
    Return True if TR-1 repeat-signal compounding conditions are met (spec §3.4).

    Conditions (all must hold):
    1. Current notifier is active_manager or named_individual.
    2. At least one prior tr1_accumulation signal exists for the same ticker
       in the lookback window.
    3. That prior signal's notifier_category is also active_manager or
       named_individual.
    4. The prior notifier name is distinct from the current notifier name
       (case-insensitive).

    The "same active manager crossing a higher threshold" variant is not
    implemented here — that requires threshold comparison data not reliably
    available at enrichment time.
    """
    if current_notifier_category not in _QUALIFYING_CATEGORIES:
        return False

    current_name = (current_notifier_name or "").lower().strip()

    for data in recent_signals:
        if data.get("_doc_id") == current_signal_id:
            continue
        if data.get("lens_id") != "tr1_accumulation":
            continue
        if data.get("notifier_category") not in _QUALIFYING_CATEGORIES:
            continue
        prior_name = ((data.get("lens_data") or {}).get("notifier_name") or "").lower().strip()
        if prior_name and prior_name != current_name:
            return True

    return False


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def enrich_signal(
    db,
    signal_id: str,
    ticker: str,
    recommendation: str,
    lookback_days: int = _LOOKBACK_DAYS,
    lens_id: str | None = None,
    notifier_name: str | None = None,
    notifier_category: str | None = None,
) -> dict:
    """
    Enrich a just-saved signal with maturity, disqualification, and lens count.

    Queries recent signals for the same ticker, applies disqualification
    (act → monitor if a keyword match is found), computes maturity, and
    writes the enriched fields back to signals_unified via merge.

    Optional TR-1 compounding (spec §3.4): if lens_id is "tr1_accumulation"
    and compounding conditions are met, signal_maturity is set to "reinforced"
    and signal_strength is upgraded to "strong".

    Returns a dict of enriched fields safe to merge into the notification
    payload:
        {
            "recommendation":            str,   # possibly downgraded from "act"
            "signal_maturity":           str,   # "first_signal" | "reinforced"
            "reinforcement_refs":        list,
            "disqualification_refs":     list,
            "active_lens_count_at_fire": int,
            # signal_strength included only when TR-1 compounding upgrades it
        }
    """
    recent = _recent_ticker_signals(db, ticker, lookback_days)

    disq_hits    = check_disqualification(recent, signal_id)
    maturity, reinforcement_refs = compute_signal_maturity(recent, signal_id)
    active_count = count_active_lenses(recent, signal_id)

    disqualification_refs = [h["signal_id"] for h in disq_hits]
    final_recommendation  = recommendation
    compounding_upgrade   = False

    if disq_hits and recommendation == "act":
        final_recommendation = "monitor"
        kw     = disq_hits[0]["keyword_matched"]
        ref_id = disq_hits[0]["signal_id"][:16]
        logger.info(
            "proposal_agent: [%s] downgraded act→monitor — prior signal %s matched '%s'",
            ticker, ref_id, kw,
        )

    # A disqualified signal is not positively reinforced
    if disq_hits:
        maturity = "first_signal"
        reinforcement_refs = []
    elif lens_id == "tr1_accumulation" and not disq_hits:
        # TR-1 compounding: two distinct qualifying notifiers → force reinforced + strong
        if check_tr1_compounding(recent, signal_id, notifier_name, notifier_category):
            maturity = "reinforced"
            compounding_upgrade = True
            logger.info(
                "proposal_agent: [%s] TR-1 compounding — upgrading to reinforced/strong",
                ticker,
            )

    firestore_fields: dict = {
        "recommendation":            final_recommendation,
        "signal_maturity":           maturity,
        "reinforcement_refs":        reinforcement_refs,
        "disqualification_refs":     disqualification_refs,
        "active_lens_count_at_fire": active_count,
    }
    if compounding_upgrade:
        firestore_fields["signal_strength"] = "strong"

    try:
        db.collection(_SIGNALS_UNIFIED).document(signal_id).set(firestore_fields, merge=True)
    except Exception as e:
        logger.warning(
            "proposal_agent: Firestore write failed for signal %s: %s", signal_id[:16], e
        )

    # disqualification_detail is passed to the notification payload only — not persisted
    return {
        **firestore_fields,
        "disqualification_detail": disq_hits[0] if disq_hits else None,
    }
