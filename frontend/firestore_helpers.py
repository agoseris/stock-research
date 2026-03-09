# Firestore reads, writes, and cached data helpers for the LSE Research Terminal.

from datetime import datetime, timezone

import streamlit as st
from google.cloud import firestore

from constants import (
    _CONFIG_COLLECTION,
    _LSEG_FILTERS_DOC,
    _DEFAULT_EXCLUDED_TYPES,
    _DEFAULT_COMPANY_KEYWORDS,
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


# ── Signal / discovery results ─────────────────────────────────────────────────

def get_signal_results(db, limit=500):
    docs = (
        db.collection("signal_results")
        .where("dismissed", "==", False)
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, doc.to_dict()) for doc in docs]


def get_signal_results_all(db, limit=500):
    """Fallback — fetch all and filter in Python if index not yet built."""
    docs = (
        db.collection("signal_results")
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, d) for doc in docs if not (d := doc.to_dict()).get("dismissed", False)]


def get_all_signals_for_ticker(db, ticker: str) -> list:
    """
    Return all non-dismissed signal_results for a specific ticker, newest first.

    Used as a top-up pass to guarantee acted/deferred positions remain visible
    regardless of the main fetch limit. Single-field equality filter — no composite
    index required.
    """
    docs = db.collection("signal_results").where("ticker", "==", ticker).stream()
    results = [
        (d.id, d.to_dict()) for d in docs
        if not d.to_dict().get("dismissed", False)
    ]
    results.sort(key=lambda x: str(x[1].get("stored_at", "")), reverse=True)
    return results


def get_discovery_results(db, limit=100):
    docs = (
        db.collection("discovery_results")
        .where("dismissed", "==", False)
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, doc.to_dict()) for doc in docs]


def get_discovery_results_all(db, limit=100):
    docs = (
        db.collection("discovery_results")
        .order_by("stored_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, d) for doc in docs if not (d := doc.to_dict()).get("dismissed", False)]


def dismiss_document(db, collection, doc_id):
    """Soft-dismiss for discovery_results (sets dismissed=True). Retained for Discovery tab."""
    db.collection(collection).document(doc_id).update({
        "dismissed": True,
        "dismissed_at": datetime.utcnow().isoformat(),
    })


def delete_signal_result(db, doc_id: str) -> None:
    """Hard-delete a signal_results document. Irreversible."""
    db.collection("signal_results").document(doc_id).delete()


# ── Director lens signals (signals collection) ─────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def get_director_signals(_db, limit: int = 200) -> list:
    """
    Fetch director lens signals from the signals collection, newest first.
    Returns list of (doc_id, data_dict) tuples.
    """
    try:
        docs = (
            _db.collection("signals")
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [(doc.id, doc.to_dict()) for doc in docs]
    except Exception:
        return []


def delete_director_signal(db, doc_id: str) -> None:
    """Hard-delete a director lens signals document. Irreversible."""
    db.collection("signals").document(doc_id).delete()
    try:
        get_director_signals.clear()
    except Exception:
        pass


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
        for doc in db.collection("universe_companies").stream():
            if not doc.to_dict().get("ticker_lse"):
                doc.reference.delete()
                deleted += 1
        if deleted:
            get_all_universe_companies.clear()
            get_universe_tickers.clear()
            get_not_of_interest_tickers.clear()
            get_universe_stats.clear()
    except Exception:
        pass
    return deleted


@st.cache_data(ttl=300)
def get_universe_tickers(_db):
    """Fetch the set of all LSE tickers in the Firestore universe. Cached for 5 minutes."""
    docs = _db.collection("universe_companies").select([]).stream()
    return {doc.id for doc in docs}


@st.cache_data(ttl=300)
def get_not_of_interest_tickers(_db) -> set:
    """Return set of tickers where not_of_interest == True. Cached for 5 minutes."""
    docs = (
        _db.collection("universe_companies")
        .where("not_of_interest", "==", True)
        .select([])
        .stream()
    )
    return {doc.id for doc in docs}


@st.cache_data(ttl=300)
def get_all_universe_companies(_db) -> list:
    """Load all universe_companies documents as a list of dicts. Cached for 5 minutes."""
    docs = _db.collection("universe_companies").stream()
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
    db.collection("universe_companies").document(ticker.upper()).set(
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
    db.collection("pending_jobs").add(job)


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
    db.collection("pending_jobs").add(job)


# ── Job helpers ────────────────────────────────────────────────────────────────

def get_pending_jobs(db, limit=20):
    """Return recent pending_jobs documents, newest first."""
    docs = (
        db.collection("pending_jobs")
        .order_by("submitted_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [(doc.id, doc.to_dict()) for doc in docs]


@st.cache_data(ttl=60)
def _get_processed_source_urls(_db) -> set:
    """Return set of source_url values already in the announcements dedup store."""
    try:
        docs = _db.collection("announcements").select(["source_url"]).stream()
        return {d.to_dict().get("source_url", "") for d in docs if d.to_dict().get("source_url")}
    except Exception:
        return set()


@st.cache_data(ttl=120)
def get_signal_history_for_ticker(_db, ticker: str, limit: int = 10) -> list:
    """Fetch the signal_history subcollection for a ticker, newest first. Cached 2 min."""
    try:
        docs = (
            _db.collection("universe_companies")
            .document(ticker.upper())
            .collection("signal_history")
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception:
        return []


def set_position_state(db, ticker: str, state: str) -> None:
    """
    Write position_state and position_state_since to a universe_companies doc.

    Closing or declining a position also resets signal_state to 'watching' so
    the company can be evaluated fresh on future signals (per two-axis model spec).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    update = {
        "position_state": state,
        "position_state_since": now_iso,
    }
    if state in ("closed", "declined"):
        update["signal_state"] = "watching"
        update["signal_state_since"] = now_iso
    db.collection("universe_companies").document(ticker.upper()).set(
        update, merge=True,
    )
    get_all_universe_companies.clear()
    get_signal_history_for_ticker.clear()


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
    db.collection("pending_jobs").add(job)
