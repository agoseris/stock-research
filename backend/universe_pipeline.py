"""
universe_pipeline.py

Dynamic universe management pipeline — quarterly refresh.

Builds the LSE small-cap monitored universe in two tiers:

  Tier 1 — FTSE Small Cap index constituents (SMX), after investment trust
            exclusions. Approximately 250-270 operating companies.

  Tier 2 — LSE Main Market and AIM companies with market cap between the
            Tier 1 lower bound and £1B, not already in Tier 1, after
            investment trust exclusions. Approximately 100-200 companies.

Data sources:
  A. FTSE Russell constituent PDFs (SMX, ASX, AXX) — definitive universe
  B. MarketDataProviderBase / YFinanceProvider — market cap, revenue, liquidity

Abstraction integrity: this module never imports or instantiates a concrete
provider. All three providers (market_data, universe_storage, notifier) are
injected via __init__.

Failure handling: on any exception during pipeline execution, the previous
Firestore snapshot is retained (save_universe is never called with partial
data), and an alert is sent via the notification provider.

Run cadence: quarterly, aligned with FTSE Russell index rebalancing
(March, June, September, December).
"""

import io
import os
import time
from datetime import date, datetime, timezone
from typing import List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv

from abstractions import (
    MarketDataProviderBase,
    NotificationProviderBase,
    RefreshLog,
    UniverseCompany,
    UniverseStorageProviderBase,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# FTSE Russell factsheet download URLs
_FTSE_BASE_URL = (
    "https://research.ftserussell.com/analytics/factsheets/Home/"
    "DownloadConstituentsWeights/?indexdetails={code}"
)

FTSE_SMX_URL = _FTSE_BASE_URL.format(code="SMX")   # FTSE Small Cap — Tier 1
FTSE_ASX_URL = _FTSE_BASE_URL.format(code="ASX")   # FTSE All-Share — Tier 2 candidates
FTSE_AXX_URL = _FTSE_BASE_URL.format(code="AXX")   # FTSE AIM All-Share — Tier 2 candidates

# Market cap ceiling for Tier 2 (£1 billion)
TIER2_UPPER_BOUND_GBP = 1_000_000_000

# Liquidity thresholds (ADTV in GBP) — from brief Section 8.2
ADTV_LIQUID_THRESHOLD = 500_000
ADTV_MODERATE_THRESHOLD = 100_000

# Investment trust name keywords — from brief Section 7
TRUST_KEYWORDS = [
    "trust", "fund", "income & growth", "investment company",
    "investment trust", "capital & income", "equity income",
    "growth & income", "managed income", "global income",
]

# yfinance sector / industry strings that indicate financial vehicles
FINANCIAL_VEHICLE_SECTORS = {
    "asset management", "financial services", "closed-end fund",
    "investment trusts", "fund", "etf",
}

# Companies listed less than this many days are eligible for SPAC classification
SPAC_MAX_AGE_DAYS = 730  # 2 years

# HTTP request timeout (seconds)
HTTP_TIMEOUT = 30

# PDF download minimum size sanity check (bytes)
PDF_MIN_SIZE_BYTES = 10_000


# ---------------------------------------------------------------------------
# PDF download and extraction helpers
# ---------------------------------------------------------------------------

def _download_pdf(url: str, label: str) -> bytes:
    """
    Download a FTSE Russell constituent PDF and return raw bytes.

    Raises RuntimeError if the download fails or the response is not a PDF.
    """
    print(f"  Downloading {label}...")
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to download {label}: {e}") from e

    content = resp.content
    if len(content) < PDF_MIN_SIZE_BYTES:
        raise RuntimeError(
            f"{label} download too small ({len(content)} bytes) — "
            "likely not a valid PDF."
        )

    # Validate PDF magic bytes
    if not content.startswith(b"%PDF"):
        raise RuntimeError(
            f"{label} response is not a PDF "
            f"(content-type: {resp.headers.get('content-type', 'unknown')})."
        )

    print(f"  {label}: {len(content):,} bytes downloaded.")
    return content


def _extract_tickers_from_pdf(pdf_bytes: bytes, label: str) -> List[Tuple[str, str]]:
    """
    Extract (ticker, company_name) pairs from a FTSE Russell constituent PDF.

    FTSE Russell factsheets list constituents in a table. The ticker symbol
    appears in a column labelled 'Constituent' or similar; company name in
    an adjacent column. This function extracts all rows that look like
    (SHORT_TICKER, Company Name) pairs.

    Returns a list of (ticker_lse, company_name) tuples, uppercased ticker.

    Raises RuntimeError if pdfplumber is not available or extraction fails
    catastrophically (yields < 10 rows, which indicates a format change).
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError(
            "pdfplumber is required for PDF extraction. "
            "Install with: pip install pdfplumber"
        ) from e

    rows = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if table:
                    rows.extend(table)
    except Exception as e:
        raise RuntimeError(f"pdfplumber extraction failed for {label}: {e}") from e

    if len(rows) < 10:
        raise RuntimeError(
            f"{label}: only {len(rows)} rows extracted — "
            "PDF format may have changed. Manual inspection required."
        )

    results = []
    for row in rows:
        if not row or len(row) < 2:
            continue
        # Ticker is typically the first non-empty cell that is a short
        # uppercase alphabetical string (1-5 chars, no spaces).
        # Company name is the next non-empty cell.
        ticker_candidate = (row[0] or "").strip()
        name_candidate = (row[1] or "").strip()

        if not ticker_candidate or not name_candidate:
            continue
        # Filter: ticker should be 1-6 uppercase letters/digits, no spaces
        if (
            1 <= len(ticker_candidate) <= 6
            and ticker_candidate.replace(".", "").isalnum()
            and ticker_candidate[0].isalpha()
            and not ticker_candidate[0].isdigit()
        ):
            results.append((ticker_candidate.upper(), name_candidate))

    print(f"  {label}: {len(results)} tickers extracted.")
    return results


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def _is_investment_trust_by_sector(info: dict) -> bool:
    """True if yfinance sector/industry indicates a financial vehicle."""
    sector = (info.get("sector") or "").lower()
    industry = (info.get("industry") or "").lower()
    for kw in FINANCIAL_VEHICLE_SECTORS:
        if kw in sector or kw in industry:
            return True
    return False


def _is_investment_trust_by_name(company_name: str) -> bool:
    """True if the company name contains a trust/fund keyword."""
    name_lower = company_name.lower()
    return any(kw in name_lower for kw in TRUST_KEYWORDS)


def _has_zero_revenue(info: dict) -> bool:
    """True if revenue is null or zero."""
    rev = info.get("totalRevenue")
    return rev is None or rev == 0


def _compute_liquidity_flag(avg_volume: Optional[float], price: Optional[float]) -> str:
    """
    Derive the LIQUID / MODERATE / ILLIQUID flag from ADTV.

    ADTV = averageVolume × currentPrice.
    Thresholds from brief Section 8.2.
    Returns 'ILLIQUID' if data is unavailable (conservative default).
    """
    if avg_volume is None or price is None or avg_volume == 0 or price == 0:
        return "ILLIQUID"
    adtv = avg_volume * price
    if adtv > ADTV_LIQUID_THRESHOLD:
        return "LIQUID"
    if adtv > ADTV_MODERATE_THRESHOLD:
        return "MODERATE"
    return "ILLIQUID"


def _compute_adtv(avg_volume: Optional[float], price: Optional[float]) -> Optional[float]:
    """Return ADTV in GBP, or None if data is unavailable."""
    if avg_volume is None or price is None:
        return None
    return avg_volume * price


def _is_reit(info: dict, company_name: str) -> bool:
    """Heuristic: REIT detection from industry field or name."""
    industry = (info.get("industry") or "").lower()
    name = company_name.lower()
    return "reit" in industry or "real estate investment trust" in industry or "reit" in name


def _is_royalty(info: dict, company_name: str) -> bool:
    """Heuristic: royalty/streaming company detection."""
    industry = (info.get("industry") or "").lower()
    name = company_name.lower()
    return "royalt" in industry or "royalt" in name or "streaming" in name


# ---------------------------------------------------------------------------
# Main pipeline class
# ---------------------------------------------------------------------------

class UniversePipeline:
    """
    Quarterly universe refresh pipeline.

    Builds Tier 1 and Tier 2 from FTSE Russell constituent PDFs, enriched
    with market data via MarketDataProviderBase. On success, writes the
    combined universe to Firestore via UniverseStorageProviderBase.

    On any failure, the previous Firestore snapshot is retained (write is
    never called), and an alert is sent via NotificationProviderBase.

    All three providers are injected — this class never imports or
    instantiates a concrete provider.
    """

    def __init__(
        self,
        market_data: MarketDataProviderBase,
        universe_storage: UniverseStorageProviderBase,
        notifier: NotificationProviderBase,
    ):
        self.market_data = market_data
        self.universe_storage = universe_storage
        self.notifier = notifier

    # --- Tier 1 ---

    def _build_tier1(self, run_timestamp: datetime) -> Tuple[List[UniverseCompany], float]:
        """
        Build Tier 1 from the FTSE Small Cap (SMX) constituent list.

        Returns (tier1_companies, lower_bound_gbp).
        lower_bound_gbp is the market cap of the smallest Tier 1 member —
        used as the dynamic lower bound for Tier 2 construction.

        Raises RuntimeError on unrecoverable failures (PDF download error,
        extraction failure). These propagate to run() which handles them.
        """
        print("\n--- TIER 1: FTSE Small Cap (SMX) ---")
        pdf_bytes = _download_pdf(FTSE_SMX_URL, "SMX")
        pairs = _extract_tickers_from_pdf(pdf_bytes, "SMX")

        tier1: List[UniverseCompany] = []
        data_quality_tickers: List[str] = []
        excluded_counts = {"trust_sector": 0, "trust_name": 0, "no_revenue": 0}

        for ticker_lse, company_name in pairs:
            ticker_yahoo = ticker_lse + ".L"
            info = self.market_data.get_info(ticker_yahoo)

            if not info:
                # yfinance returned nothing — flag as DATA_QUALITY
                data_quality_tickers.append(ticker_lse)
                print(f"  DATA_QUALITY (no data): {ticker_lse}")
                continue

            # --- Three-filter investment trust exclusion (applied in order) ---

            if _is_investment_trust_by_sector(info):
                excluded_counts["trust_sector"] += 1
                continue

            if _is_investment_trust_by_name(company_name):
                excluded_counts["trust_name"] += 1
                continue

            if _has_zero_revenue(info):
                # Log as DATA_QUALITY only if other fields are present
                # (suggests genuine data, just missing revenue)
                if info.get("marketCap"):
                    data_quality_tickers.append(ticker_lse)
                    print(f"  DATA_QUALITY (revenue null, market cap present): {ticker_lse}")
                else:
                    excluded_counts["no_revenue"] += 1
                continue

            # --- Passed all exclusions — OPERATING ---
            avg_vol = info.get("averageVolume")
            price = info.get("currentPrice")
            adtv = _compute_adtv(avg_vol, price)
            liquidity = _compute_liquidity_flag(avg_vol, price)
            market_cap = info.get("marketCap")

            # Derive last_price_date from a history call (best-effort)
            last_price_date = None
            try:
                hist = self.market_data.get_price_history(ticker_yahoo, period="5d")
                if not hist.empty:
                    last_price_date = hist.index[-1].date()
            except Exception:
                pass

            company = UniverseCompany(
                ticker_lse=ticker_lse,
                ticker_yahoo=ticker_yahoo,
                company_name=company_name,
                tier=1,
                listing_exchange="LSE_MAIN",
                entity_type="OPERATING",
                liquidity_flag=liquidity,
                universe_added_date=run_timestamp.date(),
                last_refreshed=run_timestamp,
                isin=info.get("isin"),
                market_cap_gbp=market_cap,
                revenue_ttm=info.get("totalRevenue"),
                revenue_growth_yoy=info.get("revenueGrowth"),
                sector=info.get("sector"),
                industry=info.get("industry"),
                adtv_gbp=adtv,
                last_price=price,
                last_price_date=last_price_date,
                fifty_two_week_high=info.get("fiftyTwoWeekHigh"),
                fifty_two_week_low=info.get("fiftyTwoWeekLow"),
            )
            tier1.append(company)

        print(f"\nTier 1 summary:")
        print(f"  Admitted (OPERATING): {len(tier1)}")
        print(f"  Excluded (trust by sector): {excluded_counts['trust_sector']}")
        print(f"  Excluded (trust by name):   {excluded_counts['trust_name']}")
        print(f"  Excluded (no revenue):      {excluded_counts['no_revenue']}")
        print(f"  Flagged DATA_QUALITY:       {len(data_quality_tickers)}")

        if not tier1:
            raise RuntimeError("Tier 1 is empty after filtering — aborting pipeline.")

        # Dynamic lower bound: smallest market cap in Tier 1
        market_caps = [
            c.market_cap_gbp for c in tier1 if c.market_cap_gbp is not None
        ]
        if not market_caps:
            raise RuntimeError(
                "No market cap data available for any Tier 1 company — "
                "cannot compute Tier 2 lower bound."
            )
        lower_bound = min(market_caps)
        print(f"\nDynamic Tier 2 lower bound: £{lower_bound:,.0f}")

        return tier1, lower_bound

    # --- Tier 2 ---

    def _build_tier2(
        self,
        tier1_tickers: Set[str],
        lower_bound_gbp: float,
        run_timestamp: datetime,
    ) -> List[UniverseCompany]:
        """
        Build Tier 2 from FTSE All-Share (ASX) and AIM All-Share (AXX) lists.

        Applies the market cap band filter (lower_bound_gbp to £1B),
        removes Tier 1 tickers, and applies investment trust exclusion.
        REITs, SPACs, and royalty companies are retained and flagged.

        Raises RuntimeError on unrecoverable failures.
        """
        print("\n--- TIER 2: FTSE All-Share (ASX) + AIM All-Share (AXX) ---")

        asx_bytes = _download_pdf(FTSE_ASX_URL, "ASX")
        asx_pairs = _extract_tickers_from_pdf(asx_bytes, "ASX")

        axx_bytes = _download_pdf(FTSE_AXX_URL, "AXX")
        axx_pairs = _extract_tickers_from_pdf(axx_bytes, "AXX")

        # Build (ticker → (name, exchange)) map; ASX tickers are LSE_MAIN,
        # AXX tickers are AIM. Merge and deduplicate; ASX takes precedence
        # for exchange label if a ticker appears in both (edge case).
        candidate_map: dict = {}
        for ticker, name in axx_pairs:
            candidate_map[ticker] = (name, "AIM")
        for ticker, name in asx_pairs:
            candidate_map[ticker] = (name, "LSE_MAIN")  # overwrite AIM if present

        # Remove Tier 1 members
        before_t1_removal = len(candidate_map)
        for t in tier1_tickers:
            candidate_map.pop(t, None)
        print(f"  Candidates after Tier 1 removal: {len(candidate_map)} "
              f"(removed {before_t1_removal - len(candidate_map)})")

        tier2: List[UniverseCompany] = []
        data_quality_count = 0
        excluded_counts = {
            "no_market_cap": 0, "too_small": 0, "too_large": 0,
            "trust": 0, "no_revenue": 0,
        }
        flagged_counts = {"REIT": 0, "SPAC": 0, "ROYALTY": 0}

        for ticker_lse, (company_name, exchange) in candidate_map.items():
            ticker_yahoo = ticker_lse + ".L"
            info = self.market_data.get_info(ticker_yahoo)

            if not info or info.get("marketCap") is None:
                excluded_counts["no_market_cap"] += 1
                data_quality_count += 1
                continue

            market_cap = info["marketCap"]

            # Market cap band filter
            if market_cap < lower_bound_gbp:
                excluded_counts["too_small"] += 1
                continue
            if market_cap > TIER2_UPPER_BOUND_GBP:
                excluded_counts["too_large"] += 1
                continue

            # Three-filter investment trust exclusion
            if (
                _is_investment_trust_by_sector(info)
                or _is_investment_trust_by_name(company_name)
                or _has_zero_revenue(info)
            ):
                # Distinguish genuine investment trusts from no-revenue cases
                if _has_zero_revenue(info) and not _is_investment_trust_by_sector(info) and not _is_investment_trust_by_name(company_name):
                    excluded_counts["no_revenue"] += 1
                else:
                    excluded_counts["trust"] += 1
                continue

            # Determine entity type
            avg_vol = info.get("averageVolume")
            price = info.get("currentPrice")
            adtv = _compute_adtv(avg_vol, price)
            liquidity = _compute_liquidity_flag(avg_vol, price)

            if _is_reit(info, company_name):
                entity_type = "REIT"
                flagged_counts["REIT"] += 1
            elif _is_royalty(info, company_name):
                entity_type = "ROYALTY"
                flagged_counts["ROYALTY"] += 1
            else:
                entity_type = "OPERATING"

            # Derive last_price_date (best-effort)
            last_price_date = None
            try:
                hist = self.market_data.get_price_history(ticker_yahoo, period="5d")
                if not hist.empty:
                    last_price_date = hist.index[-1].date()
            except Exception:
                pass

            company = UniverseCompany(
                ticker_lse=ticker_lse,
                ticker_yahoo=ticker_yahoo,
                company_name=company_name,
                tier=2,
                listing_exchange=exchange,
                entity_type=entity_type,
                liquidity_flag=liquidity,
                universe_added_date=run_timestamp.date(),
                last_refreshed=run_timestamp,
                isin=info.get("isin"),
                market_cap_gbp=market_cap,
                revenue_ttm=info.get("totalRevenue"),
                revenue_growth_yoy=info.get("revenueGrowth"),
                sector=info.get("sector"),
                industry=info.get("industry"),
                adtv_gbp=adtv,
                last_price=price,
                last_price_date=last_price_date,
                fifty_two_week_high=info.get("fiftyTwoWeekHigh"),
                fifty_two_week_low=info.get("fiftyTwoWeekLow"),
            )
            tier2.append(company)

        print(f"\nTier 2 summary:")
        print(f"  Admitted: {len(tier2)}")
        print(f"    OPERATING: {sum(1 for c in tier2 if c.entity_type == 'OPERATING')}")
        for label, count in flagged_counts.items():
            if count:
                print(f"    {label} (flagged, retained): {count}")
        print(f"  Excluded (no market cap / DATA_QUALITY): {excluded_counts['no_market_cap']}")
        print(f"  Excluded (too small):  {excluded_counts['too_small']}")
        print(f"  Excluded (too large):  {excluded_counts['too_large']}")
        print(f"  Excluded (trust):      {excluded_counts['trust']}")
        print(f"  Excluded (no revenue): {excluded_counts['no_revenue']}")

        return tier2

    # --- Main entry point ---

    def run(self) -> bool:
        """
        Execute a full quarterly universe refresh.

        Returns True if both tiers were built successfully and the universe
        was written to Firestore. Returns False on any failure.

        On failure: the previous Firestore snapshot is retained (save_universe
        is not called), and an alert is sent via the notification provider.
        """
        run_timestamp = datetime.now(timezone.utc)
        run_id = run_timestamp.strftime("%Y%m%dT%H%M%SZ")
        errors: List[str] = []

        print(f"\n{'='*60}")
        print(f"Universe pipeline run started: {run_timestamp.isoformat()}")
        print(f"Run ID: {run_id}")
        print(f"{'='*60}")

        try:
            # Tier 1
            tier1, lower_bound_gbp = self._build_tier1(run_timestamp)
            tier1_tickers: Set[str] = {c.ticker_lse for c in tier1}

            # Tier 2
            tier2 = self._build_tier2(tier1_tickers, lower_bound_gbp, run_timestamp)

            # Combined universe
            all_companies = tier1 + tier2
            data_quality_count = sum(
                1 for c in all_companies if c.entity_type == "DATA_QUALITY"
            )

            print(f"\n{'='*60}")
            print(f"Pipeline complete. Total universe: {len(all_companies)} companies")
            print(f"  Tier 1: {len(tier1)}")
            print(f"  Tier 2: {len(tier2)}")
            print(f"  DATA_QUALITY: {data_quality_count}")
            print(f"{'='*60}\n")

            # Write universe to Firestore (only on full success)
            print("Writing universe to Firestore...")
            self.universe_storage.save_universe(all_companies)

            # Write refresh log
            log = RefreshLog(
                run_id=run_id,
                run_timestamp=run_timestamp,
                tier1_count=len(tier1),
                tier2_count=len(tier2),
                data_quality_count=data_quality_count,
                lower_bound_gbp=lower_bound_gbp,
                success=True,
                errors=[],
            )
            self.universe_storage.save_refresh_log(log)
            print("Refresh log written.")

            # Alert on DATA_QUALITY threshold (>10% of universe)
            dq_pct = data_quality_count / len(all_companies) if all_companies else 0
            if dq_pct > 0.10:
                self.notifier.send(
                    title="Universe Pipeline — DATA_QUALITY Warning",
                    body=(
                        f"Run {run_id}: {data_quality_count}/{len(all_companies)} records "
                        f"({dq_pct:.1%}) flagged DATA_QUALITY. "
                        "Consider upgrading to EODHD if this persists over two runs."
                    ),
                    priority="normal",
                )
            else:
                self.notifier.send(
                    title="Universe Pipeline — Refresh Complete",
                    body=(
                        f"Run {run_id}: {len(tier1)} Tier 1 + {len(tier2)} Tier 2 = "
                        f"{len(all_companies)} companies. "
                        f"Lower bound: £{lower_bound_gbp:,.0f}. "
                        f"DATA_QUALITY: {data_quality_count}."
                    ),
                    priority="normal",
                )

            return True

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            errors.append(error_msg)
            print(f"\nPIPELINE FAILED: {error_msg}")

            # Write failure log (without overwriting universe)
            try:
                log = RefreshLog(
                    run_id=run_id,
                    run_timestamp=run_timestamp,
                    tier1_count=0,
                    tier2_count=0,
                    data_quality_count=0,
                    lower_bound_gbp=0.0,
                    success=False,
                    errors=errors,
                )
                self.universe_storage.save_refresh_log(log)
            except Exception as log_err:
                print(f"  Could not write failure log: {log_err}")

            # Alert via notification provider
            try:
                self.notifier.send(
                    title="Universe Pipeline — FAILED",
                    body=(
                        f"Run {run_id} failed. Previous universe snapshot retained.\n"
                        f"Error: {error_msg}"
                    ),
                    priority="high",
                )
            except Exception as notif_err:
                print(f"  Could not send failure alert: {notif_err}")

            return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """
    Run a full quarterly universe refresh.

    Concrete providers are instantiated here and injected into the pipeline.
    The pipeline itself never imports a concrete provider.

    Usage:
        cd backend && python universe_pipeline.py
    """
    from market_data_yfinance import YFinanceProvider
    from storage_firestore_universe import FirestoreUniverseProvider
    from telegram_notifier import TelegramNotifier

    market_data = YFinanceProvider()
    universe_storage = FirestoreUniverseProvider()
    notifier = TelegramNotifier()

    pipeline = UniversePipeline(
        market_data=market_data,
        universe_storage=universe_storage,
        notifier=notifier,
    )

    success = pipeline.run()

    if success:
        print("Universe pipeline completed successfully.")
    else:
        print("Universe pipeline FAILED. Check logs above.")
        exit(1)
