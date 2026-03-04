from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
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
    # Confidence that the Companies House data in this announcement relates
    # to the correct corporate entity. Set by CompaniesHouseProvider when
    # the CH number was matched by name search rather than exact lookup.
    # None = source is not Companies House (field not applicable).
    # 1.0 = exact name match. Lower values indicate fuzzy match.
    companies_house_confidence: Optional[float] = None


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

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this lens, e.g. 'regulatory_catalyst'."""
        pass

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

    Populated by the universe pipeline from FTSE Russell constituent CSVs
    and persisted via UniverseStorageProviderBase.

    Fields marked Optional may be None when data is unavailable.
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
    not_of_interest: bool = False

    # Signal state (system-managed)
    signal_state: Optional[str] = None           # watching/monitor/signal_active/signal_reinforced/signal_mixed/signal_negative
    signal_state_since: Optional[datetime] = None
    last_signal_at: Optional[datetime] = None    # most recent signal of any kind — for confirmation windows

    # Position state (human-managed)
    position_state: Optional[str] = None         # acted/deferred/declined/closed (None = no position)
    position_state_since: Optional[datetime] = None


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
# Abstraction 6 — Universe Storage Provider
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

    The implementation is FirestoreUniverseProvider
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

    @abstractmethod
    def save_company(self, company: UniverseCompany) -> None:
        """Upsert a single company without affecting other universe documents."""
        pass

    # --- Signal / position state ---

    @abstractmethod
    def update_signal_state(
        self,
        ticker_lse: str,
        new_state: str,
        timestamp: "datetime",
        update_since: bool = True,
    ) -> None:
        """
        Merge-update signal_state and last_signal_at on a company doc.
        If update_since is True (default), also updates signal_state_since.
        Use update_since=False when touching last_signal_at without a state change.
        """
        pass

    @abstractmethod
    def update_position_state(
        self, ticker_lse: str, new_state: str, timestamp: "datetime"
    ) -> None:
        """Merge-update position_state and position_state_since on a company doc."""
        pass

    @abstractmethod
    def record_signal_transition(self, ticker_lse: str, transition: dict) -> None:
        """
        Append a signal state transition record to
        universe_companies/{ticker}/signal_history (auto-ID document).
        """
        pass

    @abstractmethod
    def record_position_transition(self, ticker_lse: str, transition: dict) -> None:
        """
        Append a position state change record to
        universe_companies/{ticker}/position_history (auto-ID document).
        """
        pass

    @abstractmethod
    def get_signal_history(
        self, ticker_lse: str, limit: int = 50
    ) -> List[dict]:
        """Return signal history for a company, most recent first."""
        pass

    @abstractmethod
    def get_position_history(
        self, ticker_lse: str, limit: int = 50
    ) -> List[dict]:
        """Return position history for a company, most recent first."""
        pass

    @abstractmethod
    def get_decayed_companies(self, decay_config: dict) -> List[UniverseCompany]:
        """
        Return companies whose signal_state has been in its current state
        longer than the configured decay window.
        """
        pass

    @abstractmethod
    def get_signal_config(self) -> dict:
        """
        Return signal state configuration from app_config/signal_config.
        Seeds default values in Firestore on first call.
        """
        pass
