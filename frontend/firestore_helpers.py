# Firestore reads, writes, and cached data helpers for the LSE Research Terminal.

import logging

import streamlit as st
from google.cloud import firestore

logger = logging.getLogger(__name__)

from constants import (
    _CONFIG_COLLECTION,
    _LSEG_FILTERS_DOC,
    _DEFAULT_EXCLUDED_TYPES,
    _DEFAULT_COMPANY_KEYWORDS,
    _SUBCOL_SIGNAL_HISTORY,
    _SUBCOL_POSITION_HISTORY,
    _COL_SIGNALS_UNIFIED,
    _COL_SIGNALS,
    _COL_DISCOVERY_RESULTS,
    _COL_UNIVERSE,
    _COL_PENDING_JOBS,
    _COL_ANNOUNCEMENTS,
    _COL_SIGNAL_PERF,
    SignalState,
    PositionState,
)


# ── Firestore client ───────────────────────────────────────────────────────────

@st.cache_resource
def get_db():
    """
    Create a Firestore client.

    On Streamlit Community Cloud: reads GCP credentials from
    st.secrets["gcp_service_account"] (set in the Streamlit dashboard).
    Locally: falls through to GOOGLE_APPLICATION_CREDENTIALS env var.
    """
    try:
        if "gcp_service_account" in st.secrets:
            from google.oauth2 import service_account
            sa_info = dict(st.secrets["gcp_service_account"])
            creds = service_account.Credentials.from_service_account_info(
                sa_info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            return firestore.Client(credentials=creds, project=sa_info.get("project_id"))
    except Exception:
        pass
    return firestore.Client()


# ── Signal / discovery results (reads from signals_unified, lens_id=regulatory_catalyst) ──

_CATALYST_LENS_ID = "regulatory_catalyst"


def get_signal_results(db, limit=500):
    """
    Fetch non-dismissed regulatory catalyst signals from signals_unified, newest first.

    Requires a composite index on (lens_id, dismissed, stored_at).
    Falls back gracefully when index not yet built — app.py calls
    get_signal_results_all() on the resulting exception.
    """
    docs = (
        db.collection(_COL_SIGNALS_UNIFIED)
        .where("lens_id", "==", _CATALYST_LENS_ID)
        .where("dismissed", "==", False)
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, doc.to_dict()) for doc in docs]


def get_signal_results_all(db, limit=500):
    """
    Fallback — fetch all regulatory catalyst signals and filter/sort in Python.

    Uses only single-field equality filters (no composite index required).
    """
    docs = (
        db.collection(_COL_SIGNALS_UNIFIED)
        .where("lens_id", "==", _CATALYST_LENS_ID)
        .limit(limit)
        .stream()
    )
    results = [
        (d.id, d.to_dict()) for d in docs
        if not d.to_dict().get("dismissed", False)
    ]
    results.sort(key=lambda x: str(x[1].get("stored_at", "")), reverse=True)
    return results


def get_all_signals_for_ticker(db, ticker: str) -> list:
    """
    Return all non-dismissed regulatory catalyst signals for a specific ticker, newest first.

    Used as a top-up pass to guarantee acted/deferred positions remain visible
    regardless of the main fetch limit.
    """
    docs = (
        db.collection(_COL_SIGNALS_UNIFIED)
        .where("lens_id", "==", _CATALYST_LENS_ID)
        .where("ticker", "==", ticker)
        .stream()
    )
    results = [
        (d.id, d.to_dict()) for d in docs
        if not d.to_dict().get("dismissed", False)
    ]
    results.sort(key=lambda x: str(x[1].get("stored_at", "")), reverse=True)
    return results


def get_discovery_results(db, limit=100):
    docs = (
        db.collection(_COL_DISCOVERY_RESULTS)
        .where("dismissed", "==", False)
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, doc.to_dict()) for doc in docs]


def get_discovery_results_all(db, limit=100):
    docs = (
        db.collection(_COL_DISCOVERY_RESULTS)
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, d) for doc in docs if not (d := doc.to_dict()).get("dismissed", False)]


def dismiss_document(db, collection, doc_id):
    """Soft-dismiss for discovery_results (sets dismissed=True). Retained for Discovery tab."""
    db.collection(collection).document(doc_id).update({
        "dismissed": True,
        "dismissed_at": firestore.SERVER_TIMESTAMP,
    })


def delete_signal_result(db, doc_id: str) -> None:
    """Hard-delete a signals_unified regulatory catalyst document. Irreversible."""
    db.collection(_COL_SIGNALS_UNIFIED).document(doc_id).delete()


# ── Director lens signals (signals collection) ─────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def get_director_signals(_db, limit: int = 200) -> list:
    """
    Fetch director/TR-1 signals from the signals collection, newest first.
    Returns list of (doc_id, data_dict) tuples.

    Enriched fields from signals_unified (same doc_ids) are merged onto each
    dict under _unified_* keys so the UI can display proposal-agent values
    (recommendation, signal_maturity, etc.) without touching the primary write path.
    """
    try:
        docs = (
            _db.collection(_COL_SIGNALS)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        results = [(doc.id, doc.to_dict()) for doc in docs]
    except Exception as e:
        logger.warning("get_director_signals failed — returning empty list. Error: %s", e)
        return []

    if not results:
        return results

    # Batch-read the corresponding signals_unified docs (same doc_ids) and
    # merge enriched fields so the card renderer can use proposal-agent values.
    try:
        refs = [_db.collection(_COL_SIGNALS_UNIFIED).document(doc_id) for doc_id, _ in results]
        unified_snaps = _db.get_all(refs)
        unified_map = {snap.id: snap.to_dict() for snap in unified_snaps if snap.exists}
        merged = []
        for doc_id, data in results:
            u = unified_map.get(doc_id) or {}
            if u:
                data = {
                    **data,
                    "_unified_recommendation":    u.get("recommendation"),
                    "_unified_signal_maturity":   u.get("signal_maturity"),
                    "_unified_signal_strength":   u.get("signal_strength"),
                    "_unified_disq_refs":         u.get("disqualification_refs") or [],
                    "_unified_active_lens_count": u.get("active_lens_count_at_fire", 0),
                }
            merged.append((doc_id, data))
        return merged
    except Exception as e:
        logger.warning("get_director_signals unified merge failed: %s", e)
        return results


def delete_director_signal(db, doc_id: str) -> None:
    """Hard-delete a director lens signals document. Irreversible."""
    db.collection(_COL_SIGNALS).document(doc_id).delete()
    try:
        get_director_signals.clear()
    except Exception as e:
        logger.warning("Could not clear get_director_signals cache: %s", e)


# ── Config store ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def get_exclusion_list(_db):
    ref = _db.collection(_CONFIG_COLLECTION).document(_LSEG_FILTERS_DOC)
    doc = ref.get()
    if doc.exists:
        data = doc.to_dict()
        if "excluded_announcement_types" in data:
            return data["excluded_announcement_types"]
    ref.set({"excluded_announcement_types": _DEFAULT_EXCLUDED_TYPES}, merge=True)
    return list(_DEFAULT_EXCLUDED_TYPES)


def save_exclusion_list(db, excluded_types):
    db.collection(_CONFIG_COLLECTION).document(_LSEG_FILTERS_DOC).set(
        {"excluded_announcement_types": excluded_types}, merge=True
    )
    get_exclusion_list.clear()


@st.cache_data(ttl=60)
def get_company_keywords(_db):
    ref = _db.collection(_CONFIG_COLLECTION).document(_LSEG_FILTERS_DOC)
    doc = ref.get()
    if doc.exists:
        data = doc.to_dict()
        if "excluded_company_keywords" in data:
            return data["excluded_company_keywords"]
    ref.set({"excluded_company_keywords": _DEFAULT_COMPANY_KEYWORDS}, merge=True)
    return list(_DEFAULT_COMPANY_KEYWORDS)


def save_company_keywords(db, keywords):
    db.collection(_CONFIG_COLLECTION).document(_LSEG_FILTERS_DOC).set(
        {"excluded_company_keywords": keywords}, merge=True
    )
    get_company_keywords.clear()


# ── Universe helpers ───────────────────────────────────────────────────────────

def cleanup_universe_orphans(db) -> int:
    """
    Delete universe_companies documents that have no ticker_lse field.

    These are orphaned documents created by partial writes (e.g. a merge-set
    that only set market_cap_gbp on a ticker that had no existing document).
    Returns the number of documents deleted and clears related caches.
    """
    deleted = 0
    try:
        for doc in db.collection(_COL_UNIVERSE).stream():
            if not doc.to_dict().get("ticker_lse"):
                doc.reference.delete()
                deleted += 1
        if deleted:
            get_all_universe_companies.clear()
            get_universe_tickers.clear()
            get_not_of_interest_tickers.clear()
            get_universe_stats.clear()
    except Exception as e:
        logger.warning("cleanup_universe_orphans failed after deleting %d doc(s). Error: %s", deleted, e)
    return deleted


@st.cache_data(ttl=300)
def get_universe_tickers(_db):
    """Fetch the set of all LSE tickers in the Firestore universe. Cached for 5 minutes."""
    docs = _db.collection(_COL_UNIVERSE).select([]).stream()
    return {doc.id for doc in docs}


@st.cache_data(ttl=300)
def get_not_of_interest_tickers(_db) -> set:
    """Return set of tickers where not_of_interest == True. Cached for 5 minutes."""
    docs = (
        _db.collection(_COL_UNIVERSE)
        .where("not_of_interest", "==", True)
        .select([])
        .stream()
    )
    return {doc.id for doc in docs}


@st.cache_data(ttl=300)
def get_all_universe_companies(_db) -> list:
    """Load all universe_companies documents as a list of dicts. Cached for 5 minutes."""
    docs = _db.collection(_COL_UNIVERSE).stream()
    return [doc.to_dict() for doc in docs]


@st.cache_data(ttl=300)
def get_universe_stats(_db) -> dict:
    """Return summary counts for the Universe tab stats bar. Cached for 5 minutes."""
    companies = get_all_universe_companies(_db)
    total = len(companies)
    aim = sum(1 for c in companies if c.get("listing_exchange") == "AIM")
    ftse = sum(1 for c in companies if c.get("listing_exchange") == "LSE_MAIN")
    muted = sum(1 for c in companies if c.get("not_of_interest", False))
    return {"total": total, "aim": aim, "ftse": ftse, "muted": muted}


def mark_not_of_interest(db, ticker: str, value: bool):
    """Set the not_of_interest flag on a universe company and clear related caches."""
    db.collection(_COL_UNIVERSE).document(ticker.upper()).set(
        {"not_of_interest": value}, merge=True
    )
    get_not_of_interest_tickers.clear()
    get_all_universe_companies.clear()
    get_universe_stats.clear()


def submit_universe_admit_job(db, ticker, company_name, market_cap_gbp,
                               listing_exchange, not_of_interest=False,
                               source_discovery_id=None):
    """Write a universe_admit job to the pending_jobs collection."""
    job = {
        "job_type": "universe_admit",
        "status": "pending",
        "submitted_at": firestore.SERVER_TIMESTAMP,
        "ticker": ticker.strip().upper(),
        "company_name": company_name,
        "market_cap_gbp": market_cap_gbp,
        "listing_exchange": listing_exchange,
        "not_of_interest": not_of_interest,
        "source_discovery_id": source_discovery_id,
    }
    db.collection(_COL_PENDING_JOBS).add(job)


def submit_universe_bulk_import_job(db, new_companies, update_companies,
                                     remove_tickers, mute_tickers):
    """Write a universe_bulk_import job to the pending_jobs collection."""
    job = {
        "job_type": "universe_bulk_import",
        "status": "pending",
        "submitted_at": firestore.SERVER_TIMESTAMP,
        "new_companies": new_companies,
        "update_companies": update_companies,
        "remove_tickers": remove_tickers,
        "mute_tickers": mute_tickers,
    }
    db.collection(_COL_PENDING_JOBS).add(job)


# ── Job helpers ────────────────────────────────────────────────────────────────

def get_pending_jobs(db, limit=20):
    """Return recent pending_jobs documents, newest first."""
    docs = (
        db.collection(_COL_PENDING_JOBS)
        .order_by("submitted_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, doc.to_dict()) for doc in docs]


@st.cache_data(ttl=60)
def _get_processed_source_urls(_db) -> set:
    """Return set of source_url values already in the announcements dedup store."""
    try:
        docs = _db.collection(_COL_ANNOUNCEMENTS).select(["source_url"]).stream()
        return {d.to_dict().get("source_url", "") for d in docs if d.to_dict().get("source_url")}
    except Exception as e:
        logger.warning("_get_processed_source_urls failed — dedup check will be skipped. Error: %s", e)
        return set()


@st.cache_data(ttl=120)
def get_signal_history_for_ticker(_db, ticker: str, limit: int = 10) -> list:
    """Fetch the signal_history subcollection for a ticker, newest first. Cached 2 min."""
    try:
        docs = (
            _db.collection(_COL_UNIVERSE)
            .document(ticker.upper())
            .collection(_SUBCOL_SIGNAL_HISTORY)
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception as e:
        logger.warning("get_signal_history_for_ticker(%s) failed — returning empty history. Error: %s", ticker, e)
        return []


def set_position_state(db, ticker: str, state: str) -> None:
    """
    Write position_state to a universe_companies doc and append an entry to the
    position_history subcollection for a complete, append-only audit trail.

    Closing or declining a position also resets signal_state to 'watching' so
    the company can be evaluated fresh on future signals (per two-axis model spec).
    """
    doc_ref = db.collection(_COL_UNIVERSE).document(ticker.upper())

    # Read current state before overwriting so the history entry captures the transition.
    try:
        existing = doc_ref.get().to_dict() or {}
        previous_state = existing.get("position_state") or None
    except Exception as e:
        logger.warning("set_position_state: could not read current state for %s: %s", ticker, e)
        previous_state = None

    update = {
        "position_state": state,
        "position_state_since": firestore.SERVER_TIMESTAMP,
    }
    if state in (PositionState.CLOSED, PositionState.DECLINED):
        update["signal_state"] = SignalState.WATCHING
        update["signal_state_since"] = firestore.SERVER_TIMESTAMP

    doc_ref.set(update, merge=True)

    # Append-only history entry — never modified after creation.
    doc_ref.collection(_SUBCOL_POSITION_HISTORY).add({
        "timestamp":      firestore.SERVER_TIMESTAMP,
        "previous_state": previous_state,
        "new_state":      state,
        "trigger":        "ui",
    })

    get_all_universe_companies.clear()
    get_signal_history_for_ticker.clear()


@st.cache_data(ttl=120)
def get_position_history_for_ticker(_db, ticker: str, limit: int = 20) -> list:
    """Fetch the position_history subcollection for a ticker, newest first. Cached 2 min."""
    try:
        docs = (
            _db.collection(_COL_UNIVERSE)
            .document(ticker.upper())
            .collection(_SUBCOL_POSITION_HISTORY)
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception as e:
        logger.warning("get_position_history_for_ticker(%s) failed — returning empty history. Error: %s", ticker, e)
        return []


def submit_job(db, row_dict, body):
    """Write an lseg_ingest job document to the pending_jobs collection."""
    job = {
        "job_type": "lseg_ingest",
        "status": "pending",
        "submitted_at": firestore.SERVER_TIMESTAMP,
        "processed_at": None,
        "ticker": row_dict["ticker"],
        "company_name": row_dict["company_name"],
        "headline": f"{row_dict['company_name']} — {row_dict['announcement_type']}",
        "body": body,
        "source_url": row_dict["source_url"],
        "published_at": row_dict["published_at"],
        "price": row_dict["price_pence"],
        "price_change": row_dict["price_change_pct"],
        "error": None,
    }
    db.collection(_COL_PENDING_JOBS).add(job)


# ── Signal performance ─────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def get_signal_performance(_db) -> list[dict]:
    """
    Fetch all signal_performance documents.
    Returns list of data dicts (doc_id merged in as _id).
    """
    try:
        docs = _db.collection(_COL_SIGNAL_PERF).stream()
        return [{**doc.to_dict(), "_id": doc.id} for doc in docs]
    except Exception as e:
        logger.warning("get_signal_performance failed — Performance tab will be empty. Error: %s", e)
        return []
