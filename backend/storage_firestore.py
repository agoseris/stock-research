"""
storage_firestore.py

Implements StorageProviderBase using Google Cloud Firestore.

Collections:
  announcements    — deduplication store, keyed by headline fingerprint
  signals_unified  — all lens results in the unified schema (Phase 5+)
  discovery_results — lightweight assessments for non-universe companies

All writes are non-blocking best-effort — a Firestore failure never
crashes the pipeline. Errors are logged to stdout.
"""

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from dotenv import load_dotenv, find_dotenv
from google.cloud import firestore

from abstractions import Announcement, StorageProviderBase

load_dotenv(find_dotenv())


# ---------------------------------------------------------------------------
# Regulatory catalyst transform helpers
# ---------------------------------------------------------------------------

def extract_catalyst_field(blob: str, field: str) -> str:
    """Extract a labeled field from an LLM analysis blob (FIELD: value)."""
    for line in (blob or "").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(field.upper() + ":"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _parse_relevance_score(relevance_raw: str) -> Optional[int]:
    """Parse RELEVANCE field value e.g. '8/10' to an integer, or None."""
    if not relevance_raw:
        return None
    m = re.search(r'(\d+)\s*/\s*10', relevance_raw)
    if m:
        return int(m.group(1))
    m = re.search(r'^\s*(\d+)\s*$', relevance_raw)
    if m:
        v = int(m.group(1))
        return v if 0 <= v <= 10 else None
    return None


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


def classify_catalyst_strength(action: str, relevance_raw: str) -> str:
    """Derive unified signal_strength from RELEVANCE score; fall back to action."""
    score = _parse_relevance_score(relevance_raw)
    if score is not None:
        return _int_strength_to_unified(score)
    a = (action or "").lower()
    if a == "yes":
        return "moderate"
    if a == "monitor":
        return "weak"
    return "noise"


def map_catalyst_recommendation(action: str) -> str:
    """Map RECOMMENDED_ACTION (Yes/No/Monitor) to unified recommendation."""
    a = (action or "").strip().lower()
    if a == "yes":
        return "act"
    if a == "monitor":
        return "monitor"
    return "ignore"


class FirestoreProvider(StorageProviderBase):
    """
    Firestore implementation of StorageProviderBase.

    Credentials are resolved automatically from the
    GOOGLE_APPLICATION_CREDENTIALS environment variable.
    """

    # Firestore collection names
    ANNOUNCEMENTS_COLLECTION = "announcements"
    SIGNALS_UNIFIED_COLLECTION = "signals_unified"
    DISCOVERY_RESULTS_COLLECTION = "discovery_results"

    SIGNAL_TTL_DAYS = 90

    def __init__(self):
        self.db = firestore.Client()
        print("Firestore client initialised.")

    # --- Internal helpers ---

    def _fingerprint(self, text: str) -> str:
        """
        Returns a stable SHA-256 fingerprint of normalised text.
        Used as the Firestore document ID for deduplication.
        Normalisation: lowercase, strip punctuation and extra whitespace.
        """
        normalised = "".join(
            c for c in text.lower() if c.isalnum() or c.isspace()
        ).split()
        normalised = " ".join(normalised)
        return hashlib.sha256(normalised.encode()).hexdigest()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # --- Deduplication ---

    def save_announcement(self, announcement: Announcement) -> bool:
        """
        Persist an announcement to the deduplication store.
        Document ID is a fingerprint of the source_url (if present) or the headline.
        """
        try:
            key_text = announcement.source_url if announcement.source_url else announcement.headline
            fp = self._fingerprint(key_text)
            doc_ref = self.db.collection(self.ANNOUNCEMENTS_COLLECTION).document(fp)
            doc_ref.set({
                "ticker": announcement.ticker,
                "company_name": announcement.company_name,
                "headline": announcement.headline,
                "source_url": announcement.source_url,
                "source_name": announcement.source_name,
                "published_at": str(announcement.published_at),
                "stored_at": self._now(),
            })
            return True
        except Exception as e:
            print(f"  [Firestore] save_announcement failed: {e}")
            return False

    def announcement_exists(self, source_url: str, headline: str = "") -> bool:
        """
        Check deduplication store by source_url (or headline if URL is empty).
        Uses direct document ID lookup — fast, no index required.
        Returns True if this announcement has already been processed.
        """
        try:
            key_text = source_url if source_url else headline
            fp = self._fingerprint(key_text)
            doc = self.db.collection(self.ANNOUNCEMENTS_COLLECTION).document(fp).get()
            return doc.exists
        except Exception as e:
            print(f"  [Firestore] announcement_exists failed: {e}")
            return False

    def headline_exists(self, headline: str) -> bool:
        """
        Check deduplication store by headline fingerprint.
        Faster than source_url lookup — direct document ID fetch.
        Use this as the primary deduplication check in the pipeline.
        """
        try:
            fp = self._fingerprint(headline)
            doc = self.db.collection(self.ANNOUNCEMENTS_COLLECTION).document(fp).get()
            return doc.exists
        except Exception as e:
            print(f"  [Firestore] headline_exists failed: {e}")
            return False

    def get_existing_source(self, source_url: str, headline: str = "") -> Optional[str]:
        """
        Check the deduplication store using the same key as save_announcement
        (source_url if present, else headline). Returns the source_name of the
        original record if found, or None if this announcement is new.
        Use this in preference to headline_exists() — it uses the correct key
        and provides the origin source for observability logging.
        """
        try:
            key_text = source_url if source_url else headline
            fp = self._fingerprint(key_text)
            doc = self.db.collection(self.ANNOUNCEMENTS_COLLECTION).document(fp).get()
            if doc.exists:
                return doc.to_dict().get("source_name", "unknown")
            return None
        except Exception as e:
            print(f"  [Firestore] get_existing_source failed: {e}")
            return None

    # --- Signal results ---

    def _build_unified_catalyst_doc(self, result: dict, now: datetime) -> Tuple[str, dict]:
        """
        Transform a raw regulatory catalyst result dict (with llm_analysis blob)
        into a unified schema document for signals_unified.

        Returns (unified_id, unified_doc).
        """
        analysis = result.get("llm_analysis", "")

        action_raw          = extract_catalyst_field(analysis, "RECOMMENDED_ACTION")
        relevance_raw       = extract_catalyst_field(analysis, "RELEVANCE")
        confidence_raw      = extract_catalyst_field(analysis, "CONFIDENCE_SIGNAL")
        summary_raw         = extract_catalyst_field(analysis, "SUMMARY")
        signal_type_raw     = extract_catalyst_field(analysis, "SIGNAL_TYPE") or "regulatory_catalyst"
        source_rel_raw      = extract_catalyst_field(analysis, "SOURCE_RELIABILITY")
        outcome_raw         = extract_catalyst_field(analysis, "OUTCOME_PROBABILITY")
        market_mispricing   = extract_catalyst_field(analysis, "MARKET_MISPRICING")

        recommendation  = map_catalyst_recommendation(action_raw)
        signal_strength = classify_catalyst_strength(action_raw, relevance_raw)

        key_text   = result.get("source_url") or result.get("headline", "")
        unified_id = self._fingerprint(key_text) if key_text else hashlib.sha256(
            str(now.timestamp()).encode()
        ).hexdigest()

        unified_doc = {
            "ticker":           result.get("ticker", ""),
            "company_name":     result.get("company_name", ""),
            "lens_id":          "regulatory_catalyst",
            "signal_type":      signal_type_raw,
            "stored_at":        now.isoformat(),
            "published_at":     str(result.get("published_at", "")),
            "source_url":       result.get("source_url", ""),
            "headline":         result.get("headline", ""),
            "signal_strength":  signal_strength,
            "recommendation":   recommendation,
            "summary":          summary_raw,
            "rationale":        outcome_raw,
            "limitations":      "",
            "confidence_signal": confidence_raw,
            "dismissed":        False,
            "dismissed_at":     None,
            "agentic_status":   "not_applicable",
            "expires_at":       now + timedelta(days=self.SIGNAL_TTL_DAYS),
            "price_pence":      result.get("price_pence"),
            "price_change":     result.get("price_change"),
            "market_cap_gbp":   result.get("market_cap_gbp"),
            # Signal quality fields — populated by proposal_agent.py after save
            "signal_maturity":           "first_signal",
            "reinforcement_refs":        [],
            "disqualification_refs":     [],
            "notifier_category":         None,
            "active_lens_count_at_fire": 0,
            "lens_data": {
                "analysed_at":       result.get("analysed_at", now.isoformat()),
                "lens":              result.get("lens", "regulatory_catalyst"),
                "queue":             result.get("queue"),
                "source":            result.get("source"),
                "relevance_score":   relevance_raw,
                "source_reliability": source_rel_raw,
                "market_mispricing": market_mispricing,
            },
        }
        return unified_id, unified_doc

    def save_signal_result(self, result: dict) -> bool:
        """
        Persist a regulatory catalyst signal result to signals_unified.
        Parses the llm_analysis blob and writes a normalised unified document.
        Document ID is a fingerprint of source_url (or headline), ensuring
        idempotent writes — re-processing the same announcement overwrites
        the existing document rather than creating a duplicate.
        """
        try:
            now = datetime.now(timezone.utc)
            unified_id, unified_doc = self._build_unified_catalyst_doc(result, now)
            self.db.collection(self.SIGNALS_UNIFIED_COLLECTION).document(unified_id).set(unified_doc)
            return True
        except Exception as e:
            print(f"  [Firestore] save_signal_result failed: {e}")
            return False

    def get_signal_results(self, limit: int = 50) -> List[dict]:
        """
        Retrieve recent regulatory catalyst signal results from signals_unified.
        """
        try:
            docs = (
                self.db.collection(self.SIGNALS_UNIFIED_COLLECTION)
                .where("lens_id", "==", "regulatory_catalyst")
                .order_by("stored_at", direction=firestore.Query.DESCENDING)
                .limit(limit)
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"  [Firestore] get_signal_results failed: {e}")
            return []

    # --- Discovery results ---

    def save_discovery_result(self, result: dict) -> bool:
        """
        Persist a discovery queue result.
        Uses auto-generated Firestore document ID.
        """
        try:
            payload = dict(result)
            payload["stored_at"] = self._now()
            self.db.collection(self.DISCOVERY_RESULTS_COLLECTION).add(payload)
            return True
        except Exception as e:
            print(f"  [Firestore] save_discovery_result failed: {e}")
            return False

    def get_discovery_results(self, limit: int = 50) -> List[dict]:
        """
        Retrieve recent discovery results, most recent first.
        """
        try:
            docs = (
                self.db.collection(self.DISCOVERY_RESULTS_COLLECTION)
                .order_by("stored_at", direction=firestore.Query.DESCENDING)
                .limit(limit)
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"  [Firestore] get_discovery_results failed: {e}")
            return []


if __name__ == "__main__":
    """
    Connectivity test — run this on the VM to confirm Firestore is
    correctly configured before wiring it into the pipeline.

    Expected output:
      Firestore client initialised.
      Writing test document...
      Reading test document...
      Read back: {'test': True, 'message': 'Firestore connectivity confirmed', ...}
      Deleting test document...
      Firestore connectivity test PASSED.
    """
    print("Running Firestore connectivity test...\n")

    storage = FirestoreProvider()

    # Write a test document
    print("Writing test document...")
    test_ref = storage.db.collection("_connectivity_test").document("test")
    test_ref.set({
        "test": True,
        "message": "Firestore connectivity confirmed",
        "written_at": storage._now(),
    })

    # Read it back
    print("Reading test document...")
    doc = test_ref.get()
    if doc.exists:
        print(f"Read back: {doc.to_dict()}")
    else:
        print("ERROR: could not read test document back.")
        exit(1)

    # Clean up
    print("Deleting test document...")
    test_ref.delete()

    print("\nFirestore connectivity test PASSED.")
