"""
lseg_excel_provider.py

AnnouncementProviderBase implementation for LSEG Excel exports.

LSEG's web interface (Market News, filtered to RNS, AIM/Small Cap) can export
a list of recent announcements to Excel. This provider parses that file, applies
pre-filters, and returns structured results ready for human review and LLM analysis.

This is a human-in-the-loop ingestion path:
  Stage 2: Upload Excel → parse and pre-filter (this provider)
  Stage 3: Human reviews filtered list and selects rows for analysis
  Stage 4: Human pastes announcement body → job submitted to Firestore pending_jobs

The provider is read-only with respect to Firestore — it performs universe lookup
via the injected UniverseStorageProviderBase but does not write to any collection.

Usage:
    provider = LSEGExcelProvider("path/to/export.xlsx", universe_storage=firestore_provider)
    result = provider.parse_excel()
    # result.passed     — in-universe rows that cleared the type filter
    # result.discovery  — non-universe rows (route to discovery queue)
    # result.suppressed — in-universe rows filtered by announcement type (logged)
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import List, Optional, Set, Tuple

import openpyxl

from abstractions import Announcement, AnnouncementProviderBase, UniverseStorageProviderBase


# ---------------------------------------------------------------------------
# Configurable exclusion list
# Extend here without touching core logic. Match is substring (case-insensitive).
# ---------------------------------------------------------------------------

EXCLUDED_ANNOUNCEMENT_TYPES = [
    "Holding(s) in Company",
    "TR-1",
    "Transaction in Own Shares",
    "Notice of AGM",
    "Notice of Results",
    "Annual Report",
    "Half-Year Report",
    "Interim Report",
    "Confirmation Statement",
    "Change of Registered Office",
    "Change of Nominated Adviser",
    "Change of Broker",
    "Total Voting Rights",
    "Blocklisting Interim Review",
    "Publication of Prospectus",
    "Result of AGM",
    "Director Declaration",
    "Conversion of B Shares",
]


# ---------------------------------------------------------------------------
# Intermediate row representation
# ---------------------------------------------------------------------------

@dataclass
class LSEGRow:
    """
    A single parsed row from the LSEG Excel export.

    Represents the announcement at the point of import — before the human has
    clicked through to LSEG and pasted the announcement body. Body text is added
    at Stage 4 when the user submits the row for LLM analysis.
    """
    ticker: str
    company_name: str
    announcement_type: str
    source: str
    published_at: datetime
    price_pence: Optional[float]
    price_change_pct: Optional[str]
    source_url: str
    in_universe: bool

    def to_announcement(self, body: str = "") -> Announcement:
        """
        Construct an Announcement from this row.

        Call with the pasted body text at Stage 4. At Stage 3 (display only),
        body defaults to empty string — do not submit bodyless announcements
        for LLM analysis.
        """
        return Announcement(
            ticker=self.ticker,
            company_name=self.company_name,
            headline=f"{self.company_name} — {self.announcement_type}",
            body=body,
            published_at=self.published_at,
            source_url=self.source_url,
            source_name="LSEG RNS",
        )


@dataclass
class ParseResult:
    """
    Output of LSEGExcelProvider.parse_excel().

    Carries the three routing categories plus summary counts.
    """
    passed: List[LSEGRow]                  # In universe, cleared type filter — ready for analysis
    discovery: List[LSEGRow]               # Not in universe — route to discovery queue
    suppressed: List[Tuple[LSEGRow, str]]  # In universe, blocked by type filter — (row, reason)
    skipped_source: int                    # Non-RNS rows discarded at source filter
    total_rows: int                        # Non-empty rows found in the file


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class LSEGExcelProvider(AnnouncementProviderBase):
    """
    Parses LSEG Excel news exports and applies pre-filters.

    Excel file structure (no header row):
      Col 1: "[Company Name] - [Ticker] - [Announcement Type]"  (hyperlink on cell)
      Col 2: Source string ("RNS", "GNW", "PRN", "BZN", ...)
      Col 3: Date  (DD.MM.YY string, e.g. "23.02.26")
      Col 4: Time  (datetime.time object, e.g. 16:06:03)
      Col 5: Price in pence (numeric, or "-" if absent)
      Col 6: Price change % (string e.g. "1.59%", or "-" if absent)

    Filtering order (each stage only operates on rows that cleared the previous):
      1. Source filter   — only RNS rows proceed; others discarded (counted)
      2. Universe filter — non-universe rows route to discovery (not discarded)
      3. Type filter     — excluded announcement types suppressed and logged

    Inject universe_storage for universe lookup. Never instantiate
    UniverseStorageProviderBase internally.
    """

    SOURCE_NAME = "LSEG RNS"

    def __init__(
        self,
        file_path: str,
        universe_storage: Optional[UniverseStorageProviderBase] = None,
    ):
        self._file_path = file_path
        self._universe_storage = universe_storage
        self._universe_tickers: Optional[Set[str]] = None  # lazy-loaded on first parse

    # ------------------------------------------------------------------
    # Universe ticker cache
    # ------------------------------------------------------------------

    def _load_universe_tickers(self) -> Set[str]:
        """Load and cache universe tickers from injected storage. Returns empty set if unavailable."""
        if self._universe_tickers is not None:
            return self._universe_tickers
        if self._universe_storage is None:
            print("LSEGExcelProvider: no universe_storage injected — universe filter inactive.")
            self._universe_tickers = set()
            return self._universe_tickers
        try:
            companies = self._universe_storage.get_universe()
            self._universe_tickers = {c.ticker_lse for c in companies}
            print(f"LSEGExcelProvider: {len(self._universe_tickers)} tickers loaded from universe.")
        except Exception as e:
            print(f"LSEGExcelProvider: universe load failed ({e}) — universe filter inactive.")
            self._universe_tickers = set()
        return self._universe_tickers

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_col1(value: str) -> Tuple[str, str, str]:
        """
        Parse the combined Col 1 string into (company_name, ticker, announcement_type).

        Format: "[Company Name] - [Ticker] - [Announcement Type]"
        Announcement type may itself contain " - " (e.g. "Director/PDMR Shareholding - Grant").
        Split on the first two " - " separators only.
        """
        parts = value.replace("\xa0", " ").split(" - ", maxsplit=2)
        if len(parts) < 3:
            company = parts[0].strip() if parts else ""
            ticker = parts[1].strip() if len(parts) > 1 else ""
            ann_type = ""
        else:
            company = parts[0].strip()
            ticker = parts[1].strip()
            ann_type = parts[2].strip()
        return company, ticker, ann_type

    @staticmethod
    def _parse_datetime(date_val, time_val) -> datetime:
        """
        Combine the LSEG date value and time value into a UTC-aware datetime.

        date_val: string "DD.MM.YY", or a datetime/date object (openpyxl may return either)
        time_val: datetime.time object, or string "HH:MM:SS"
        """
        # --- Date ---
        if isinstance(date_val, str):
            try:
                d = datetime.strptime(date_val.strip(), "%d.%m.%y").date()
            except ValueError:
                d = datetime.now(timezone.utc).date()
        elif isinstance(date_val, datetime):
            d = date_val.date()
        elif isinstance(date_val, date):
            d = date_val
        else:
            d = datetime.now(timezone.utc).date()

        # --- Time ---
        if isinstance(time_val, time):
            t = time_val
        elif isinstance(time_val, str):
            try:
                t = datetime.strptime(time_val.strip(), "%H:%M:%S").time()
            except ValueError:
                t = time(0, 0, 0)
        else:
            t = time(0, 0, 0)

        return datetime.combine(d, t, tzinfo=timezone.utc)

    @staticmethod
    def _parse_price(val) -> Optional[float]:
        """Return price in pence as float, or None if absent or non-numeric."""
        if val is None or val == "-" or val == "":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_price_change(val) -> Optional[str]:
        """Return price change string, or None if absent."""
        if val is None or val == "-" or val == "":
            return None
        return str(val).strip()

    @staticmethod
    def _matches_exclusion(ann_type: str) -> Optional[str]:
        """
        Return the matching exclusion string if ann_type is on the exclusion list,
        or None if it passes. Matching is case-insensitive substring.
        """
        ann_lower = ann_type.lower()
        for excluded in EXCLUDED_ANNOUNCEMENT_TYPES:
            if excluded.lower() in ann_lower:
                return excluded
        return None

    # ------------------------------------------------------------------
    # Core parse
    # ------------------------------------------------------------------

    def parse_excel(self) -> ParseResult:
        """
        Parse the Excel file and apply all pre-filters.

        Returns a ParseResult with passed, discovery, and suppressed rows.
        Prints a per-row disposition and a summary to stdout.
        """
        universe_tickers = self._load_universe_tickers()

        wb = openpyxl.load_workbook(self._file_path, data_only=True)
        ws = wb.active

        passed: List[LSEGRow] = []
        discovery: List[LSEGRow] = []
        suppressed: List[Tuple[LSEGRow, str]] = []
        skipped_source = 0
        total_rows = 0

        for row in ws.iter_rows(min_row=1):
            # Skip entirely empty rows
            if all(cell.value is None for cell in row):
                continue
            total_rows += 1

            cells = list(row)

            def cv(idx):
                return cells[idx].value if idx < len(cells) else None

            col1_val = cv(0)
            source_val = str(cv(1) or "").strip()
            date_val = cv(2)
            time_val = cv(3)
            price_val = cv(4)
            price_change_val = cv(5)

            # Skip rows with no Col 1 value
            if not col1_val:
                continue

            # --- Filter 1: Source ---
            if source_val.upper() != "RNS":
                skipped_source += 1
                print(f"  [SKIP source={source_val}] {col1_val}")
                continue

            # Parse Col 1
            company_name, ticker, ann_type = self._parse_col1(str(col1_val))

            # Extract hyperlink URL from Col 1 cell
            hyperlink = cells[0].hyperlink
            source_url = hyperlink.target if hyperlink and hyperlink.target else ""

            # Parse date and time
            published_at = self._parse_datetime(date_val, time_val)

            row_obj = LSEGRow(
                ticker=ticker,
                company_name=company_name,
                announcement_type=ann_type,
                source=source_val,
                published_at=published_at,
                price_pence=self._parse_price(price_val),
                price_change_pct=self._parse_price_change(price_change_val),
                source_url=source_url,
                in_universe=ticker in universe_tickers,
            )

            # --- Filter 2: Universe ---
            if not row_obj.in_universe:
                discovery.append(row_obj)
                print(f"  [DISCOVERY] [{ticker}] {company_name} — {ann_type}")
                continue

            # --- Filter 3: Announcement type ---
            excluded_match = self._matches_exclusion(ann_type)
            if excluded_match:
                reason = f"Announcement type excluded: '{excluded_match}'"
                suppressed.append((row_obj, reason))
                print(f"  [SUPPRESSED {reason}] [{ticker}] {ann_type}")
                continue

            # Passed all filters
            passed.append(row_obj)
            print(f"  [PASSED] [{ticker}] {company_name} — {ann_type}")

        print(
            f"\nParse complete: {total_rows} rows | "
            f"{skipped_source} skipped (non-RNS) | "
            f"{len(discovery)} discovery | "
            f"{len(suppressed)} suppressed (type filter) | "
            f"{len(passed)} passed"
        )

        return ParseResult(
            passed=passed,
            discovery=discovery,
            suppressed=suppressed,
            skipped_source=skipped_source,
            total_rows=total_rows,
        )

    # ------------------------------------------------------------------
    # AnnouncementProviderBase implementation
    # ------------------------------------------------------------------

    def get_recent_announcements(self, max_results: int = 50) -> List[Announcement]:
        """
        Return passed announcements as Announcement objects (body empty).

        This satisfies the AnnouncementProviderBase contract. For the full
        interactive workflow — including discovery routing, suppression logging,
        and human body-paste — use parse_excel() directly and call
        LSEGRow.to_announcement(body=...) at Stage 4.
        """
        result = self.parse_excel()
        return [row.to_announcement() for row in result.passed[:max_results]]


# ---------------------------------------------------------------------------
# Manual test / development entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv

    load_dotenv()

    file_path = sys.argv[1] if len(sys.argv) > 1 else "../docs/LSEG_news_capture.xlsx"

    try:
        from storage_firestore_universe import FirestoreUniverseProvider
        universe_storage = FirestoreUniverseProvider()
    except Exception as e:
        print(f"Firestore unavailable ({e}) — running without universe filter.\n")
        universe_storage = None

    provider = LSEGExcelProvider(file_path, universe_storage=universe_storage)

    print(f"Parsing: {file_path}\n")
    result = provider.parse_excel()

    print(f"\n=== Passed ({len(result.passed)}) ===")
    for row in result.passed:
        price = f"  {row.price_pence}p" if row.price_pence else ""
        change = f"  {row.price_change_pct}" if row.price_change_pct else ""
        print(f"  [{row.ticker}] {row.company_name} — {row.announcement_type}")
        print(f"    {row.published_at.strftime('%H:%M')}  {price}{change}")
        if row.source_url:
            print(f"    {row.source_url}")

    print(f"\n=== Discovery ({len(result.discovery)}) ===")
    for row in result.discovery:
        print(f"  [{row.ticker}] {row.company_name} — {row.announcement_type}")
        if row.source_url:
            print(f"    {row.source_url}")

    print(f"\n=== Suppressed ({len(result.suppressed)}) ===")
    for row, reason in result.suppressed:
        print(f"  [{row.ticker}] {row.announcement_type}")
        print(f"    Reason: {reason}")
