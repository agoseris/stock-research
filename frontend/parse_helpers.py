# Pure parsing functions — stdlib + openpyxl only.
# No Streamlit or Firestore dependencies.
# Note: _parse_lseg_excel duplicates logic in backend/lseg_excel_provider.py.
# frontend/ must not import from backend/ (Streamlit Community Cloud constraint).
# Both copies must be kept in sync.

import csv
import io
from datetime import date, datetime, time, timezone

import openpyxl


def _parse_lseg_excel(file_bytes, universe_tickers, excluded_types,
                      company_keywords, not_of_interest_tickers):
    """
    Parse LSEG Excel export bytes and apply pre-filters.

    Returns a dict with keys: passed, discovery, suppressed, skipped_source, total_rows.
    Each row is a plain dict (not a dataclass) suitable for Streamlit display.

    Filtering order:
      1. Source filter     — only RNS rows proceed
      2. Universe filter   — non-universe rows route to discovery
      2.5. Name filter     — company_keywords matched against company name; matched rows suppressed
      2.8. Muted filter    — not_of_interest tickers suppressed
      3. Type filter       — excluded_types (loaded from Firestore app_config) suppressed
    """

    def _parse_dt(date_val, time_val):
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

    def _parse_price(val):
        if val is None or val == "-" or val == "":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active

    passed, discovery, suppressed = [], [], []
    skipped_source = 0
    total_rows = 0

    for row in ws.iter_rows(min_row=1):
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

        if not col1_val:
            continue

        # Filter 1: Source
        if source_val.upper() != "RNS":
            skipped_source += 1
            continue

        # Parse Col 1: "[Company] - [Ticker] - [Announcement Type]"
        parts = str(col1_val).replace("\xa0", " ").split(" - ", maxsplit=2)
        company = parts[0].strip() if parts else ""
        ticker = parts[1].strip() if len(parts) > 1 else ""
        ann_type = parts[2].strip() if len(parts) > 2 else ""

        hyperlink = cells[0].hyperlink
        source_url = hyperlink.target if hyperlink and hyperlink.target else ""

        published_at = _parse_dt(date_val, time_val)
        price_pence = _parse_price(price_val)
        price_change_pct = str(price_change_val).strip() if price_change_val not in (None, "-", "") else None

        row_dict = {
            "ticker": ticker,
            "company_name": company,
            "announcement_type": ann_type,
            "source": source_val,
            "published_at": published_at,
            "price_pence": price_pence,
            "price_change_pct": price_change_pct,
            "source_url": source_url,
            "in_universe": ticker in universe_tickers,
        }

        # Filter 2: Universe
        if not row_dict["in_universe"]:
            discovery.append(row_dict)
            continue

        # Filter 2.5: Trust / fund company name
        trust_match = next(
            (kw for kw in company_keywords if kw in company.lower()),
            None,
        )
        if trust_match:
            suppressed.append((row_dict, f"Company name suggests investment trust/fund: '{trust_match}'"))
            continue

        # Filter 2.8: Muted ticker
        if ticker.upper() in not_of_interest_tickers:
            suppressed.append((row_dict, f"Ticker muted: '{ticker}'"))
            continue

        # Filter 3: Announcement type
        ann_lower = ann_type.lower()
        excluded_match = next(
            (ex for ex in excluded_types if ex.lower() in ann_lower),
            None,
        )
        if excluded_match:
            suppressed.append((row_dict, f"Announcement type excluded: '{excluded_match}'"))
            continue

        passed.append(row_dict)

    return {
        "passed": passed,
        "discovery": discovery,
        "suppressed": suppressed,
        "skipped_source": skipped_source,
        "total_rows": total_rows,
    }


def _parse_universe_csv(file_bytes: bytes) -> list:
    """
    Parse a universe CSV file and return a list of company dicts.

    Expected columns: Exchange, Code, Name, Market Cap
    Exchange: "AIM" → "AIM", anything else → "LSE_MAIN"
    Market Cap: in millions GBP; strip commas and multiply by 1,000,000.
    Rows missing ticker or name are skipped.
    """
    content = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    companies = []
    for row in reader:
        ticker = (row.get("Code") or "").strip()
        name = (row.get("Name") or "").strip()
        if not ticker or not name:
            continue
        exchange_raw = (row.get("Exchange") or "").strip()
        listing_exchange = "AIM" if exchange_raw.upper() == "AIM" else "LSE_MAIN"
        tier = 2 if listing_exchange == "AIM" else 1
        mcap_raw = (row.get("Market Cap") or "").strip().replace(",", "")
        try:
            market_cap_gbp = float(mcap_raw) * 1_000_000 if mcap_raw else None
        except ValueError:
            market_cap_gbp = None
        companies.append({
            "ticker": ticker,
            "company_name": name,
            "market_cap_gbp": market_cap_gbp,
            "listing_exchange": listing_exchange,
            "tier": tier,
        })
    return companies


def _compute_universe_delta(parsed_rows: list, existing_companies: list) -> dict:
    """
    Compute delta between a parsed CSV and the current Firestore universe.

    Returns:
      new    — rows in parsed not in existing
      update — rows in both parsed and existing
      absent — existing companies whose ticker is not in parsed
    """
    existing_by_ticker = {
        c.get("ticker_lse", "").upper(): c
        for c in existing_companies
        if c.get("ticker_lse")
    }
    parsed_tickers = {r["ticker"].upper() for r in parsed_rows}
    new_companies = [r for r in parsed_rows if r["ticker"].upper() not in existing_by_ticker]
    update_companies = [r for r in parsed_rows if r["ticker"].upper() in existing_by_ticker]
    absent_companies = [
        c for c in existing_companies
        if c.get("ticker_lse", "").upper() not in parsed_tickers
    ]
    return {"new": new_companies, "update": update_companies, "absent": absent_companies}
