from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

try:
    import pandas as pd
    DataFrame = pd.DataFrame
except ImportError:  # pragma: no cover — pandas always present on VM
    DataFrame = object


@dataclass
class Announcement:
    ticker: str
    company_name: str
    headline: str
    body: str
    published_at: datetime
    source_url: str
    source_name: str
    # Confidence that the Companies House data in this announcement relates
    # to the correct corporate entity. Set by CompaniesHouseProvider when
    # the CH number was matched by name search rather than exact lookup.
    # None = source is not Companies House (field not applicable).
    # 1.0 = exact name match. Lower values indicate fuzzy match.
    companies_house_confidence: Optional[float] = None


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


# ---------------------------------------------------------------------------
# Dataclasses for the universe pipeline (Abstractions 6 and 7)
# ---------------------------------------------------------------------------

@dataclass
class UniverseCompany:
    """
    A single company in the monitored universe.

    Populated by the universe pipeline from FTSE Russell PDF constituent
    lists, enriched with market data from MarketDataProviderBase, and
    persisted via UniverseStorageProviderBase.

    Fields marked Optional may be None when yfinance returns incomplete
    data — these are flagged DATA_QUALITY by the pipeline.
    """
    ticker_lse: str                        # Native LSE ticker, e.g. SDY
    ticker_yahoo: str                      # Yahoo Finance format, e.g. SDY.L
    company_name: str                      # Legal name from FTSE Russell PDF
    tier: int                              # 1 = FTSE Small Cap; 2 = Growth Trajectory
    listing_exchange: str                  # LSE_MAIN or AIM
    entity_type: str                       # OPERATING, REIT, SPAC, ROYALTY, NO_REVENUE, DATA_QUALITY
    liquidity_flag: str                    # LIQUID, MODERATE, ILLIQUID
    universe_added_date: date              # Date first included in the universe
    last_refreshed: datetime               # Timestamp of the pipeline run that wrote this record

    isin: Optional[str] = None
    market_cap_gbp: Optional[float] = None
    revenue_ttm: Optional[float] = None
    revenue_growth_yoy: Optional[float] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    adtv_gbp: Optional[float] = None
    last_price: Optional[float] = None
    last_price_date: Optional[date] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    # Companies House registration number and name-match confidence.
    # Populated during universe import by searching the CH API by name.
    # Confidence 1.0 = exact match; lower = fuzzy; None = no match found.
    companies_house_number: Optional[str] = None
    companies_house_confidence: Optional[float] = None


@dataclass
class RefreshLog:
    """
    A record of a single universe pipeline refresh run.

    Written to universe_refresh_log by FirestoreUniverseProvider
    after each completed (or failed) run. Supports health metrics
    and the DATA_QUALITY upgrade trigger defined in the brief.
    """
    run_id: str                  # Unique identifier for this run (ISO timestamp)
    run_timestamp: datetime      # UTC timestamp when the run started
    tier1_count: int             # Number of companies admitted to Tier 1
    tier2_count: int             # Number of companies admitted to Tier 2
    data_quality_count: int      # Number of records flagged DATA_QUALITY
    lower_bound_gbp: float       # Dynamic lower bound computed from smallest Tier 1 market cap
    success: bool                # True if both tiers completed and universe was written
    errors: List[str] = field(default_factory=list)  # Error messages if success is False


# ---------------------------------------------------------------------------
# Abstraction 6 — Market Data Provider
# ---------------------------------------------------------------------------

class MarketDataProviderBase(ABC):
    """
    Abstract base for all market data providers.

    The PoC implementation is YFinanceProvider (market_data_yfinance.py).
    Swap to EODHD or any other source by implementing this interface —
    the pipeline and universe storage layers are unaffected.

    Rate limiting and retry logic are the responsibility of the concrete
    implementation, not the pipeline that calls it.
    """

    @abstractmethod
    def get_info(self, ticker_yahoo: str) -> dict:
        """
        Return a dict of market data fields for the given ticker.

        The dict should include (where available):
          marketCap, totalRevenue, revenueGrowth, sector, industry,
          averageVolume, currentPrice, fiftyTwoWeekHigh, fiftyTwoWeekLow,
          trailingPE, trailingEps, isin.

        Returns an empty dict if the ticker is not found or data is
        unavailable. Never raises — errors are absorbed by the implementation.
        """
        pass

    @abstractmethod
    def get_price_history(self, ticker_yahoo: str, period: str) -> DataFrame:
        """
        Return OHLCV price history for the given ticker and period.

        period follows yfinance conventions: '1y', '2y', '5y', 'max', etc.

        Returns an empty DataFrame if data is unavailable.
        Never raises — errors are absorbed by the implementation.
        """
        pass


# ---------------------------------------------------------------------------
# Abstraction 7 — Universe Storage Provider
# ---------------------------------------------------------------------------

class UniverseStorageProviderBase(ABC):
    """
    Abstract base for universe storage backends.

    Manages two conceptually distinct datasets:
      1. The universe itself — a list of UniverseCompany records, one per
         company, overwritten on each quarterly refresh.
      2. The refresh log — an append-only record of each pipeline run.

    Distinct from StorageProviderBase, which is oriented around announcement
    deduplication and signal/discovery results. The universe is a slowly-
    changing reference dataset and warrants its own abstraction.

    The PoC implementation is FirestoreUniverseProvider
    (storage_firestore_universe.py).

    All write methods are non-blocking best-effort — a storage failure
    must never crash the pipeline. Implementations must absorb exceptions
    and log them to stdout.
    """

    @abstractmethod
    def save_universe(self, companies: List[UniverseCompany]) -> None:
        """
        Persist the full universe, overwriting any previous snapshot.

        Called once per successful pipeline run after both tiers are
        built. Must not be called with partial data — the caller is
        responsible for ensuring completeness before calling this method.
        """
        pass

    @abstractmethod
    def get_universe(self, tier: Optional[int] = None) -> List[UniverseCompany]:
        """
        Retrieve the current universe.

        If tier is provided, return only companies belonging to that tier.
        If tier is None, return all companies.

        Returns an empty list if the universe has not yet been populated
        or if storage is unavailable.
        """
        pass

    @abstractmethod
    def get_company(self, ticker_lse: str) -> Optional[UniverseCompany]:
        """
        Retrieve a single company by its LSE ticker.

        Returns None if the company is not in the universe or if storage
        is unavailable.
        """
        pass

    @abstractmethod
    def save_refresh_log(self, log: RefreshLog) -> None:
        """
        Append a refresh log entry.

        Called after every pipeline run — both successful and failed runs
        should be logged so that health metrics can be tracked over time.
        """
        pass

    @abstractmethod
    def get_last_refresh_log(self) -> Optional[RefreshLog]:
        """
        Retrieve the most recent refresh log entry.

        Returns None if no runs have been logged yet or if storage is
        unavailable.
        """
        pass
