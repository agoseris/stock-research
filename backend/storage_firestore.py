"""
storage_firestore.py

Implements StorageProviderBase using Google Cloud Firestore.

Collections:
  announcements   — deduplication store, keyed by headline fingerprint
  signal_results  — full LLM analysis results for universe companies
  discovery_results — lightweight assessments for non-universe companies

All writes are non-blocking best-effort — a Firestore failure never
crashes the pipeline. Errors are logged to stdout.
"""

import hashlib
import os
from datetime import datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv, find_dotenv
from google.cloud import firestore

from abstractions import Announcement, StorageProviderBase

load_dotenv(find_dotenv())


class FirestoreProvider(StorageProviderBase):
    """
    Firestore implementation of StorageProviderBase.

    Credentials are resolved automatically from the
    GOOGLE_APPLICATION_CREDENTIALS environment variable.
    """

    # Firestore collection names
    ANNOUNCEMENTS_COLLECTION = "announcements"
    SIGNAL_RESULTS_COLLECTION = "signal_results"
    DISCOVERY_RESULTS_COLLECTION = "discovery_results"

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

    def save_signal_result(self, result: dict) -> bool:
        """
        Persist a signal queue result.
        Uses auto-generated Firestore document ID.
        Adds a stored_at timestamp for ordering in the UI.
        """
        try:
            payload = dict(result)
            payload["stored_at"] = self._now()
            self.db.collection(self.SIGNAL_RESULTS_COLLECTION).add(payload)
            return True
        except Exception as e:
            print(f"  [Firestore] save_signal_result failed: {e}")
            return False

    def get_signal_results(self, limit: int = 50) -> List[dict]:
        """
        Retrieve recent signal results, most recent first.
        """
        try:
            docs = (
                self.db.collection(self.SIGNAL_RESULTS_COLLECTION)
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
