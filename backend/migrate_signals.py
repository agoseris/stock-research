"""
migrate_signals.py

Phase 2 migration script: read all documents from `signal_results` and
`signals`, transform each to the unified schema, and write to a new
`signals_unified` collection.

Does NOT modify or delete either source collection. Safe to re-run:
documents are written with set(merge=False), so re-running replaces
the unified doc with a fresh transformation.

Usage:
    cd backend && python migrate_signals.py           # full migration
    cd backend && python migrate_signals.py --dry-run  # log transforms, no writes
    cd backend && python migrate_signals.py --limit 20 # process first 20 docs

Output:
    Per-document log lines (source → unified ID, fields mapped, warnings)
    Final summary: total / success / failed / skipped
"""

import argparse
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# Credentials fallback: .env path → frontend/gcp-credentials.json
_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds_path or not os.path.exists(_creds_path):
    _frontend_creds = os.path.join(_PROJECT_ROOT, "frontend", "gcp-credentials.json")
    if os.path.exists(_frontend_creds):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _frontend_creds


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIGNAL_TTL_DAYS = 90  # 90-day TTL for unified documents
UNIFIED_COLLECTION = "signals_unified"


# ---------------------------------------------------------------------------
# Fingerprint (matches FirestoreProvider._fingerprint)
# ---------------------------------------------------------------------------

def _fingerprint(text: str) -> str:
    """SHA-256 of normalised text — matches storage_firestore._fingerprint."""
    normalised = " ".join(
        "".join(c for c in text.lower() if c.isalnum() or c.isspace()).split()
    )
    return hashlib.sha256(normalised.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------

def _extract_blob_field(blob: str, field: str) -> str:
    """Extract a labeled field from an LLM analysis blob (FIELD: value)."""
    for line in (blob or "").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(field.upper() + ":"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _coerce_to_isostring(value) -> Optional[str]:
    """Convert Firestore Timestamp / datetime / str to ISO string, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _safe_expires_at(value, fallback_days: int = SIGNAL_TTL_DAYS):
    """Return expires_at value as-is if present; else compute from now."""
    if value is not None:
        return value
    return datetime.now(tz=timezone.utc) + timedelta(days=fallback_days)


# ---------------------------------------------------------------------------
# Signal strength / recommendation mapping
# ---------------------------------------------------------------------------

def _int_strength_to_unified(val) -> str:
    """Map integer 1–10 strength score to unified string scale."""
    if val is None:
        return "noise"
    try:
        v = int(val)
        if v >= 9:
            return "strong"
        if v >= 7:
            return "substantial"
        if v >= 5:
            return "moderate"
        if v >= 3:
            return "weak"
        return "noise"
    except (TypeError, ValueError):
        return "noise"


def _parse_relevance_score(relevance_raw: str) -> Optional[int]:
    """Parse RELEVANCE field value (e.g. '8/10', '7/10') to an integer, or None."""
    import re
    if not relevance_raw:
        return None
    m = re.search(r'(\d+)\s*/\s*10', relevance_raw)
    if m:
        return int(m.group(1))
    # Fallback: bare integer
    m = re.search(r'^\s*(\d+)\s*$', relevance_raw)
    if m:
        v = int(m.group(1))
        return v if 0 <= v <= 10 else None
    return None


def _classify_catalyst_strength(action: str, relevance_raw: str) -> str:
    """
    Derive unified signal_strength from regulatory catalyst fields.

    Primary: RELEVANCE score (0–10) mapped via _int_strength_to_unified.
    Fallback: RECOMMENDED_ACTION keyword when score cannot be parsed.
    """
    score = _parse_relevance_score(relevance_raw)
    if score is not None:
        return _int_strength_to_unified(score)
    # Fallback: derive from action alone
    a = (action or "").lower()
    if a == "yes":
        return "moderate"
    if a == "monitor":
        return "weak"
    return "noise"


def _map_catalyst_recommendation(action: str) -> str:
    """Map regulatory catalyst RECOMMENDED_ACTION to unified recommendation."""
    a = (action or "").strip().lower()
    if a == "yes":
        return "act"
    if a == "monitor":
        return "monitor"
    return "ignore"


def _map_director_recommendation(rec: str) -> str:
    """Map Director simple / synthesis recommendation to unified recommendation."""
    r = (rec or "").strip().lower()
    if r in ("investigate further", "investigate"):
        return "act"
    if r == "monitor":
        return "monitor"
    return "ignore"


def _map_tr1_recommendation(rec: str) -> str:
    """Map TR-1 simple lens recommendation to unified recommendation."""
    r = (rec or "").strip().lower()
    if r == "investigate":
        return "act"
    if r == "monitor":
        return "monitor"
    return "ignore"


def _normalise_tr1_strength(s) -> str:
    """Validate / normalise a TR-1 signal_strength string."""
    _VALID = {"strong", "moderate", "weak", "noise", "negative", "substantial"}
    v = (s or "").lower()
    return v if v in _VALID else "noise"


# ---------------------------------------------------------------------------
# Transformation: signal_results → unified
# ---------------------------------------------------------------------------

def _transform_signal_result(doc_id: str, data: dict, now: datetime) -> tuple[str, dict, list]:
    """
    Transform a signal_results document to unified schema.

    Returns:
        (unified_id, unified_doc, warnings)
    """
    warnings = []
    analysis = data.get("llm_analysis", "")

    action_raw = _extract_blob_field(analysis, "RECOMMENDED_ACTION")
    confidence_raw = _extract_blob_field(analysis, "CONFIDENCE_SIGNAL")
    summary_raw = _extract_blob_field(analysis, "SUMMARY")
    relevance_raw = _extract_blob_field(analysis, "RELEVANCE")        # e.g. "8/10"
    signal_type_raw = _extract_blob_field(analysis, "SIGNAL_TYPE") or "regulatory_catalyst"
    source_rel_raw = _extract_blob_field(analysis, "SOURCE_RELIABILITY")
    outcome_raw = _extract_blob_field(analysis, "OUTCOME_PROBABILITY")  # rationale source
    market_mispricing_raw = _extract_blob_field(analysis, "MARKET_MISPRICING")

    if not action_raw:
        warnings.append("RECOMMENDED_ACTION not found in llm_analysis blob")
    if not summary_raw:
        warnings.append("SUMMARY not found in llm_analysis blob")
    if not relevance_raw:
        warnings.append("RELEVANCE score not found — signal_strength will fall back to action-based")

    recommendation = _map_catalyst_recommendation(action_raw)
    signal_strength = _classify_catalyst_strength(action_raw, relevance_raw)

    # Document ID: fingerprint of source_url or headline
    key_text = data.get("source_url") or data.get("headline", "")
    if not key_text:
        warnings.append("No source_url or headline — using original doc_id as fallback")
        unified_id = doc_id
    else:
        unified_id = _fingerprint(key_text)

    stored_at_raw = data.get("stored_at")

    unified = {
        # Core identity
        "ticker": data.get("ticker", ""),
        "company_name": data.get("company_name", ""),
        "lens_id": "regulatory_catalyst",
        "signal_type": signal_type_raw,
        # Timestamps
        "stored_at": _coerce_to_isostring(stored_at_raw) or now.isoformat(),
        "published_at": _coerce_to_isostring(
            data.get("published_at") or data.get("analysed_at")
        ),
        # Source
        "source_url": data.get("source_url", ""),
        "headline": data.get("headline", ""),
        # Signal classification
        "signal_strength": signal_strength,
        "recommendation": recommendation,
        # Narrative fields
        "summary": summary_raw,
        "rationale": outcome_raw,    # OUTCOME_PROBABILITY: why the recommendation is made
        "limitations": "",           # not surfaced in this lens prompt format
        "confidence_signal": confidence_raw,
        # Lifecycle
        "dismissed": bool(data.get("dismissed", False)),
        "dismissed_at": _coerce_to_isostring(data.get("dismissed_at")),
        "agentic_status": "not_applicable",
        "expires_at": _safe_expires_at(data.get("expires_at")),
        # Market data
        "price_pence": data.get("price_pence"),
        "price_change": data.get("price_change"),
        "market_cap_gbp": data.get("market_cap_gbp"),
        # Lens-specific extension — all blob fields parsed; raw blob not retained
        "lens_data": {
            "analysed_at": _coerce_to_isostring(data.get("analysed_at")),
            "lens": data.get("lens", "regulatory_catalyst"),
            "queue": data.get("queue"),
            "source": data.get("source"),
            "relevance_score": relevance_raw,       # e.g. "8/10" — numeric relevance score
            "source_reliability": source_rel_raw,
            "market_mispricing": market_mispricing_raw,
        },
        # Migration provenance
        "_migration_source": "signal_results",
        "_migration_source_id": doc_id,
        "_migrated_at": now,
    }

    return unified_id, unified, warnings


# ---------------------------------------------------------------------------
# Transformation: signals (director_buying / director_disposal) → unified
# ---------------------------------------------------------------------------

def _transform_director_signal(doc_id: str, data: dict, now: datetime) -> tuple[str, dict, list]:
    """
    Transform a signals document (director_buying/disposal) to unified schema.

    Returns:
        (unified_id, unified_doc, warnings)
    """
    warnings = []

    # Signal strength: prefer agentic synthesis signal_strength if available;
    # otherwise derive from simple lens integer score
    agentic_full = data.get("agentic_synthesis_full") or {}
    layer_signal_strength_raw = None
    if isinstance(agentic_full, dict):
        director_layer = agentic_full.get("layer_summaries", {}).get("director_signal", {})
        layer_signal_strength_raw = director_layer.get("signal_strength")  # int in synthesis

    simple_strength_int = data.get("simple_signal_strength")  # int 1–10
    if layer_signal_strength_raw is not None:
        signal_strength = _int_strength_to_unified(layer_signal_strength_raw)
    elif simple_strength_int is not None:
        signal_strength = _int_strength_to_unified(simple_strength_int)
    else:
        warnings.append("No signal_strength found (neither simple nor agentic)")
        signal_strength = "noise"

    # Recommendation: prefer agentic over simple
    agentic_rec_raw = data.get("agentic_recommendation", "")
    simple_rec_raw = data.get("simple_recommended_action", "")
    agentic_status = data.get("agentic_status", "")

    if agentic_rec_raw and agentic_status == "complete":
        recommendation = _map_director_recommendation(agentic_rec_raw)
        rationale = data.get("agentic_justification", "")
        limitations = data.get("agentic_limitations", "") or data.get("simple_limitations", "")
    else:
        recommendation = _map_director_recommendation(simple_rec_raw)
        rationale = data.get("simple_summary", "")
        limitations = data.get("simple_limitations", "")

    if not simple_rec_raw and not agentic_rec_raw:
        warnings.append("No recommendation found (neither simple nor agentic)")

    # All simple_* and agentic_* fields go into lens_data
    lens_data_keys = [k for k in data if k.startswith("simple_") or k.startswith("agentic_")]
    lens_data = {k: _coerce_to_isostring(data[k]) if isinstance(data[k], datetime) else data[k]
                 for k in lens_data_keys}
    lens_data["investor_action"] = data.get("investor_action")

    published_at = data.get("published_at") or data.get("announcement_published_at")

    unified = {
        "ticker": data.get("ticker", ""),
        "company_name": data.get("company_name", ""),
        "lens_id": "director_purchasing",
        "signal_type": data.get("signal_type", "director_buying"),
        "stored_at": _coerce_to_isostring(data.get("created_at")) or now.isoformat(),
        "published_at": _coerce_to_isostring(published_at),
        "source_url": data.get("source_url", ""),
        "headline": data.get("headline", ""),
        "signal_strength": signal_strength,
        "recommendation": recommendation,
        "summary": data.get("simple_summary", ""),
        "rationale": rationale,
        "limitations": limitations,
        "confidence_signal": "",  # not a direct output of this lens
        "dismissed": bool(data.get("dismissed", False)),
        "dismissed_at": _coerce_to_isostring(data.get("dismissed_at")),
        "agentic_status": agentic_status or "pending",
        "expires_at": _safe_expires_at(data.get("expires_at")),
        "price_pence": data.get("price_pence"),
        "price_change": data.get("price_change"),
        "market_cap_gbp": data.get("market_cap_gbp"),
        "lens_data": lens_data,
        "_migration_source": "signals",
        "_migration_source_id": doc_id,
        "_migrated_at": now,
    }

    return doc_id, unified, warnings  # doc_id is already a SHA-256 fingerprint


# ---------------------------------------------------------------------------
# Transformation: signals (tr1_crossing) → unified
# ---------------------------------------------------------------------------

def _transform_tr1_signal(doc_id: str, data: dict, now: datetime) -> tuple[str, dict, list]:
    """
    Transform a signals document (tr1_crossing) to unified schema.

    Returns:
        (unified_id, unified_doc, warnings)
    """
    warnings = []
    simple_result = data.get("simple_lens_result") or {}
    investor_research = data.get("investor_research") or {}

    signal_strength = _normalise_tr1_strength(simple_result.get("signal_strength"))
    recommendation = _map_tr1_recommendation(simple_result.get("recommendation", ""))
    rationale = investor_research.get("key_reasoning", "") or simple_result.get("rationale", "")
    limitations = simple_result.get("limitations", "")
    confidence_signal = investor_research.get("confidence", "")

    if not simple_result:
        warnings.append("simple_lens_result is empty or missing")

    # Lens-specific extension: tr1 extraction fields + simple + investor research
    tr1_fields = [
        "notifier_name", "issuer_name", "direction", "old_holding_pct",
        "new_holding_pct", "threshold_crossed", "nature_of_holding", "crossing_date",
    ]
    lens_data = {k: data.get(k) for k in tr1_fields}
    lens_data["simple_lens_result"] = simple_result
    lens_data["investor_research"] = investor_research
    lens_data["investor_research_completed_at"] = _coerce_to_isostring(
        data.get("investor_research_completed_at")
    )
    lens_data["investor_research_token_usage"] = data.get("investor_research_token_usage")
    lens_data["agentic_completed_at"] = _coerce_to_isostring(data.get("agentic_completed_at"))
    lens_data["investor_action"] = data.get("investor_action")

    published_at = data.get("published_at") or data.get("announcement_published_at")

    unified = {
        "ticker": data.get("ticker", ""),
        "company_name": data.get("company_name", ""),
        "lens_id": "tr1_accumulation",
        "signal_type": "tr1_crossing",
        "stored_at": _coerce_to_isostring(data.get("created_at")) or now.isoformat(),
        "published_at": _coerce_to_isostring(published_at),
        "source_url": data.get("source_url", ""),
        "headline": data.get("headline", ""),
        "signal_strength": signal_strength,
        "recommendation": recommendation,
        "summary": simple_result.get("rationale", ""),
        "rationale": rationale,
        "limitations": limitations,
        "confidence_signal": confidence_signal,
        "dismissed": bool(data.get("dismissed", False)),
        "dismissed_at": _coerce_to_isostring(data.get("dismissed_at")),
        "agentic_status": data.get("agentic_status", "pending"),
        "expires_at": _safe_expires_at(data.get("expires_at")),
        "price_pence": data.get("price_pence"),
        "price_change": data.get("price_change"),
        "market_cap_gbp": data.get("market_cap_gbp"),
        "lens_data": lens_data,
        "_migration_source": "signals",
        "_migration_source_id": doc_id,
        "_migrated_at": now,
    }

    return doc_id, unified, warnings


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _transform_signals_doc(doc_id: str, data: dict, now: datetime) -> tuple[str, dict, list]:
    """Dispatch signals document to the correct transformer by signal_type."""
    signal_type = data.get("signal_type", "")
    if signal_type in ("director_buying", "director_disposal"):
        return _transform_director_signal(doc_id, data, now)
    if signal_type == "tr1_crossing":
        return _transform_tr1_signal(doc_id, data, now)
    # Unknown type: transform as director best-effort, flag the warning
    warnings = [f"Unknown signal_type={signal_type!r} — transforming as director_purchasing best-effort"]
    uid, doc, w = _transform_director_signal(doc_id, data, now)
    doc["lens_id"] = f"unknown:{signal_type}"
    return uid, doc, warnings + w


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

def migrate(dry_run: bool = False, limit: Optional[int] = None) -> dict:
    """
    Run the full migration.

    Returns a summary dict with counts.
    """
    from google.cloud import firestore  # imported here so dry-run can still work without creds
    db = firestore.Client()

    now = datetime.now(tz=timezone.utc)
    summary = {
        "total": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "collision_warnings": 0,
    }

    # Track unified IDs written this run to detect collisions
    written_ids: set[str] = set()

    def _write(unified_id: str, unified_doc: dict) -> bool:
        if dry_run:
            return True
        try:
            db.collection(UNIFIED_COLLECTION).document(unified_id).set(unified_doc)
            return True
        except Exception as e:
            print(f"    ERROR writing to {UNIFIED_COLLECTION}/{unified_id[:16]}…: {e}")
            return False

    # ── signal_results ──────────────────────────────────────────────────────
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Migrating signal_results → {UNIFIED_COLLECTION}…")

    sr_docs = list(db.collection("signal_results").stream())
    if limit is not None:
        sr_docs = sr_docs[:limit]

    for doc in sr_docs:
        summary["total"] += 1
        doc_id = doc.id
        data = doc.to_dict()

        try:
            unified_id, unified_doc, warnings = _transform_signal_result(doc_id, data, now)
        except Exception as e:
            print(f"  [FAIL] signal_results/{doc_id[:16]}…: transform error: {e}")
            summary["failed"] += 1
            continue

        # Collision check
        if unified_id in written_ids:
            print(f"  [SKIP] signal_results/{doc_id[:16]}…: collision on unified_id {unified_id[:16]}…"
                  f" (duplicate source_url/headline)")
            summary["skipped"] += 1
            summary["collision_warnings"] += 1
            continue

        ticker = unified_doc.get("ticker", "?")
        action = unified_doc.get("recommendation", "?")
        strength = unified_doc.get("signal_strength", "?")
        prefix = "[DRY] " if dry_run else ""
        warn_str = f"  ⚠ {'; '.join(warnings)}" if warnings else ""

        if _write(unified_id, unified_doc):
            written_ids.add(unified_id)
            summary["success"] += 1
            print(f"  {prefix}[OK ] signal_results/{doc_id[:16]}… → {unified_id[:16]}… "
                  f"ticker={ticker} rec={action} strength={strength}{warn_str}")
        else:
            summary["failed"] += 1

    # ── signals ─────────────────────────────────────────────────────────────
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Migrating signals → {UNIFIED_COLLECTION}…")

    # Compute remaining limit for signals collection
    signals_limit = None
    if limit is not None:
        signals_limit = max(0, limit - len(sr_docs))

    sig_docs = list(db.collection("signals").stream())
    if signals_limit is not None:
        sig_docs = sig_docs[:signals_limit]

    for doc in sig_docs:
        summary["total"] += 1
        doc_id = doc.id
        data = doc.to_dict()

        try:
            unified_id, unified_doc, warnings = _transform_signals_doc(doc_id, data, now)
        except Exception as e:
            print(f"  [FAIL] signals/{doc_id[:16]}…: transform error: {e}")
            summary["failed"] += 1
            continue

        if unified_id in written_ids:
            print(f"  [SKIP] signals/{doc_id[:16]}…: collision on unified_id {unified_id[:16]}…")
            summary["skipped"] += 1
            summary["collision_warnings"] += 1
            continue

        ticker = unified_doc.get("ticker", "?")
        lens = unified_doc.get("lens_id", "?")
        action = unified_doc.get("recommendation", "?")
        strength = unified_doc.get("signal_strength", "?")
        agentic = unified_doc.get("agentic_status", "?")
        prefix = "[DRY] " if dry_run else ""
        warn_str = f"  ⚠ {'; '.join(warnings)}" if warnings else ""

        if _write(unified_id, unified_doc):
            written_ids.add(unified_id)
            summary["success"] += 1
            print(f"  {prefix}[OK ] signals/{doc_id[:16]}… → {unified_id[:16]}… "
                  f"ticker={ticker} lens={lens} rec={action} strength={strength} "
                  f"agentic={agentic}{warn_str}")
        else:
            summary["failed"] += 1

    return summary


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate signal_results and signals to signals_unified."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log all transforms without writing to Firestore.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most N documents total (split across both source collections).",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("=" * 60)
        print("DRY RUN — no documents will be written to Firestore.")
        print("=" * 60)

    summary = migrate(dry_run=args.dry_run, limit=args.limit)

    print("\n" + "=" * 60)
    print("Migration complete.")
    print(f"  Total processed : {summary['total']}")
    print(f"  Success         : {summary['success']}")
    print(f"  Failed          : {summary['failed']}")
    print(f"  Skipped (dup)   : {summary['skipped']}")
    if summary["collision_warnings"]:
        print(f"  ⚠ Collisions    : {summary['collision_warnings']} (review above)")
    print("=" * 60)

    if summary["failed"] > 0:
        sys.exit(1)
