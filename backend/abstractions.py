from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class Announcement:
    ticker: str
    company_name: str
    headline: str
    body: str
    published_at: datetime
    source_url: str
    source_name: str


@dataclass
class PriceData:
    ticker: str
    price: float
    currency: str
    as_of: datetime


class DataProviderBase(ABC):
    """Abstract base for all market data providers.
    Swap implementations without touching the rest of the system."""

    @abstractmethod
    def get_prices(self, tickers: List[str]) -> List[PriceData]:
        pass


class AnnouncementProviderBase(ABC):
    """Abstract base for all announcement/news feed providers."""

    @abstractmethod
    def get_recent_announcements(self, max_results: int = 50) -> List[Announcement]:
        pass


class LLMProviderBase(ABC):
    """Abstract base for all LLM providers."""

    @abstractmethod
    def analyse(self, prompt: str) -> str:
        pass


class NotificationProviderBase(ABC):
    """Abstract base for all notification channels."""

    @abstractmethod
    def send(self, title: str, body: str, priority: str = "normal") -> bool:
        pass


class StorageProviderBase(ABC):
    """
    Abstract base for all storage backends.

    Covers three responsibilities:
      1. Deduplication — has this announcement been seen before?
      2. Signal results — store and retrieve full LLM analysis for universe companies
      3. Discovery results — store and retrieve lightweight assessments for non-universe companies
    """

    # --- Deduplication ---

    @abstractmethod
    def save_announcement(self, announcement: Announcement) -> bool:
        """
        Persist an announcement and mark it as seen.
        Used for fingerprint-based deduplication across pipeline runs.
        Returns True on success.
        """
        pass

    @abstractmethod
    def announcement_exists(self, source_url: str) -> bool:
        """
        Returns True if an announcement with this source_url has already
        been processed. Used to prevent duplicate LLM analysis calls.
        """
        pass

    # --- Signal results (universe companies, full LLM analysis) ---

    @abstractmethod
    def save_signal_result(self, result: dict) -> bool:
        """
        Persist a processed signal queue result.
        Called by the pipeline after LLM analysis of a universe company announcement.
        Returns True on success.
        """
        pass

    @abstractmethod
    def get_signal_results(self, limit: int = 50) -> List[dict]:
        """
        Retrieve recent signal results, most recent first.
        Used by the Streamlit interface to display flagged opportunities.
        """
        pass

    # --- Discovery results (non-universe companies, lightweight assessment) ---

    @abstractmethod
    def save_discovery_result(self, result: dict) -> bool:
        """
        Persist a processed discovery queue result.
        Called by the pipeline after lightweight LLM assessment of a
        non-universe company announcement.
        Returns True on success.
        """
        pass

    @abstractmethod
    def get_discovery_results(self, limit: int = 50) -> List[dict]:
        """
        Retrieve recent discovery results, most recent first.
        Used by the Streamlit interface to present universe admission decisions.
        """
        pass


class StrategyLensBase(ABC):
    """Abstract base for all investment strategy lenses."""

    @abstractmethod
    def pre_filter(self, announcement: Announcement) -> bool:
        """Returns True if this announcement warrants LLM analysis."""
        pass

    @abstractmethod
    def build_prompt(self, announcement: Announcement) -> str:
        """Returns a structured prompt for the LLM analysis layer."""
        pass
