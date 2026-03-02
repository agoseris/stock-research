"""
lseg_scraper.py

Playwright-based scraper for LSEG RNS announcements.

Provides two public functions:
  fetch_announcement_body(url)  — RNS body text from an announcement detail page
  fetch_announcement_index()    — all rows from today's LSEG News Explorer index

Maintains a persistent browser cookie store so that the private investor
challenge gate survives between calls. Cookie file: lseg_cookies.json
in the same directory as this module (gitignored, never committed).

Raises RuntimeError on failure (page not found, body empty, etc.).
"""

from datetime import datetime, time, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

# ── Configuration ───────────────────────────────────────────────────────────────

_COOKIES_PATH = Path(__file__).parent / "lseg_cookies.json"

# LSEG challenge gate: body carries this class when the gate is active
_CHALLENGE_BODY_CLASS = "block-scroll"

# Text content of the private investor button on the challenge page
_PRIVATE_INVESTOR_TEXT = "a private investor"

# Shadow host element containing the RNS body text
_BODY_HOST_SELECTOR = 'div[itemprop="articleBody"]'

# Class of the content div inside the shadow root
_BODY_CONTENT_CLASS = "news-body-content"

# LSEG News Explorer — bare URL, no filter params (for initial load test)
_INDEX_BASE_URL = (
    "https://www.londonstockexchange.com/news"
    "?tab=news-explorer&indices="
)

# Individual index codes
_INDEX_MCX = "MCX"   # FTSE 250
_INDEX_AXX = "AXX"   # FTSE AIM All-Share
_INDEX_SMX = "SMX"   # FTSE Small Cap

# Bare test URL — no filters, no period; confirms tr.slide-panel selector works
_INDEX_TEST_URL = "https://www.londonstockexchange.com/news?tab=news-explorer"

# Angular ng-select pagination dropdown on the index page
_PAGINATION_SELECTOR = "#dropdownSize"
_PAGINATION_500_TEXT  = "Show 500 news"

# CSS selector for index result rows
_ROW_SELECTOR = "tr.slide-panel"

# "Apply filters" button on the News Explorer index page
_APPLY_FILTERS_SELECTOR = "span.apply-button"

_GOTO_TIMEOUT      = 30_000   # ms — page navigation
_ELEMENT_TIMEOUT   = 15_000   # ms — waiting for elements
_CHALLENGE_TIMEOUT = 10_000   # ms — challenge gate interaction
_PAGINATION_TIMEOUT = 20_000  # ms — pagination expansion + reload

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


# ── Public API ──────────────────────────────────────────────────────────────────

def fetch_announcement_body(url: str) -> str:
    """
    Fetch the RNS body text from an LSEG announcement page.

    Handles the private investor challenge gate automatically:
    - On first run (no cookie): clicks 'a private investor', saves cookie.
    - On subsequent runs: cookie is loaded and gate is skipped.
    - If the cookie expires and the gate reappears, it is handled automatically.

    The body text lives in a declarative Shadow DOM inside
    div[itemprop="articleBody"] and is extracted via JavaScript evaluation.

    Returns the stripped body text.
    Raises RuntimeError with a descriptive message on any failure.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        storage_state = str(_COOKIES_PATH) if _COOKIES_PATH.exists() else None
        context = browser.new_context(
            storage_state=storage_state,
            user_agent=_USER_AGENT,
        )
        page = context.new_page()

        try:
            _load_page(page, url)
            _handle_challenge_if_present(page, context)
            text = _extract_body(page)
        finally:
            context.close()
            browser.close()

        return text


def fetch_announcement_index(indices: str = _INDEX_MCX) -> list:
    """
    Fetch today's announcements from the LSEG News Explorer for a single index code.

    indices: one of _INDEX_MCX ('MCX'), _INDEX_AXX ('AXX'), _INDEX_SMX ('SMX').
    Call three times and concatenate to cover all three indices — combining them
    in a single URL occasionally returns zero results from LSEG.

    Handles the private investor challenge gate automatically.
    Expands pagination to 500 rows before scraping.

    Returns a list of row dicts with keys:
        ticker, company_name, announcement_type, source,
        source_url, published_at, price_pence, price_change_pct
    """
    url = _INDEX_TEST_URL  # TODO: restore filter URL once selector mechanism confirmed
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        storage_state = str(_COOKIES_PATH) if _COOKIES_PATH.exists() else None
        context = browser.new_context(
            storage_state=storage_state,
            user_agent=_USER_AGENT,
        )
        page = context.new_page()

        try:
            _load_page(page, url)
            _handle_challenge_if_present(page, context)
            rows = _scrape_index(page)
        finally:
            context.close()
            browser.close()

        return rows


# ── Internal helpers ────────────────────────────────────────────────────────────

def _load_page(page, url: str) -> None:
    try:
        page.goto(url, wait_until="networkidle", timeout=_GOTO_TIMEOUT)
    except PlaywrightTimeout:
        # Angular apps sometimes don't reach networkidle; proceed anyway.
        pass


def _handle_challenge_if_present(page, context) -> None:
    body_classes = page.locator("body").get_attribute("class") or ""
    if _CHALLENGE_BODY_CLASS not in body_classes:
        return
    print(f"[lseg_scraper] challenge gate detected — clicking private investor", flush=True)

    try:
        page.get_by_text(_PRIVATE_INVESTOR_TEXT).first.click(
            timeout=_CHALLENGE_TIMEOUT
        )
        page.wait_for_function(
            f'!document.body.classList.contains("{_CHALLENGE_BODY_CLASS}")',
            timeout=_CHALLENGE_TIMEOUT,
        )
    except PlaywrightTimeout:
        raise RuntimeError(
            "Challenge gate did not dismiss after clicking 'a private investor'. "
            "The page structure may have changed."
        )

    # Persist cookies so subsequent calls skip the gate.
    context.storage_state(path=str(_COOKIES_PATH))


def _extract_body(page) -> str:
    try:
        page.locator(_BODY_HOST_SELECTOR).wait_for(timeout=_ELEMENT_TIMEOUT)
    except PlaywrightTimeout:
        raise RuntimeError(
            f"Body host element '{_BODY_HOST_SELECTOR}' not found on page. "
            "The page may not be an announcement page, or content failed to load."
        )

    text = page.locator(_BODY_HOST_SELECTOR).evaluate(f"""
        el => {{
            if (el.shadowRoot) {{
                const content = el.shadowRoot.querySelector('.{_BODY_CONTENT_CLASS}');
                return content ? content.innerText : '';
            }}
            return el.innerText || '';
        }}
    """)

    text = (text or "").strip()
    if not text or text == "-":
        raise RuntimeError(
            "Body element found but contained no text. "
            "The challenge gate may still be active, or the page is still loading."
        )

    return text


# ── Index scraping helpers ───────────────────────────────────────────────────────

def _scrape_index(page) -> list:
    """Click Apply Filters, wait for results, expand pagination to 500, extract all rows."""
    print(f"[lseg_scraper] index page url={page.url!r} title={page.title()!r}", flush=True)
    body_classes = page.locator("body").get_attribute("class") or ""
    print(f"[lseg_scraper] body classes={body_classes!r}", flush=True)

    try:
        page.wait_for_selector(_ROW_SELECTOR, timeout=_ELEMENT_TIMEOUT)
    except PlaywrightTimeout:
        screenshot_path = str(Path(__file__).parent / "debug_screenshot.png")
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"[lseg_scraper] TIMEOUT waiting for {_ROW_SELECTOR!r} — screenshot saved to {screenshot_path}", flush=True)
        return []  # No results available (e.g. outside market hours)

    print(f"[lseg_scraper] {_ROW_SELECTOR} found — expanding to 500", flush=True)
    _expand_to_500(page)
    rows = _extract_index_rows(page)
    print(f"[lseg_scraper] extracted {len(rows)} rows", flush=True)
    return rows


def _expand_to_500(page) -> None:
    """Expand the Angular ng-select pagination dropdown to show 500 rows."""
    try:
        page.locator(_PAGINATION_SELECTOR).click(timeout=_ELEMENT_TIMEOUT)
        page.get_by_text(_PAGINATION_500_TEXT).first.click(timeout=_ELEMENT_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=_PAGINATION_TIMEOUT)
        print("[lseg_scraper] pagination expanded to 500", flush=True)
    except PlaywrightTimeout:
        print("[lseg_scraper] pagination expand timed out — proceeding with current view", flush=True)


def _extract_index_rows(page) -> list:
    """Extract all tr.slide-panel rows via a single JS evaluation."""
    raw = page.evaluate("""
        () => Array.from(document.querySelectorAll('tr.slide-panel')).map(row => {
            const cells = row.querySelectorAll('td');
            const link = cells[0] ? cells[0].querySelector('a.dash-link') : null;
            return {
                headline: cells[0] ? cells[0].innerText.trim() : '',
                url:      link ? link.href : '',
                source:   cells[1] ? cells[1].innerText.trim() : '',
                date:     cells[2] ? cells[2].innerText.trim() : '',
                time:     cells[3] ? cells[3].innerText.trim() : '',
                price:    cells[4] ? cells[4].innerText.trim() : '',
                change:   cells[5] ? cells[5].innerText.trim() : ''
            };
        })
    """)

    rows = []
    for item in raw:
        headline = item.get("headline", "")
        parts = headline.replace("\xa0", " ").split(" - ", maxsplit=2)
        company  = parts[0].strip() if parts else ""
        ticker   = parts[1].strip() if len(parts) > 1 else ""
        ann_type = parts[2].strip() if len(parts) > 2 else ""

        change_str = item.get("change", "").strip()
        rows.append({
            "ticker":           ticker,
            "company_name":     company,
            "announcement_type": ann_type,
            "source":           item.get("source", "").strip(),
            "source_url":       item.get("url", ""),
            "published_at":     _parse_index_dt(item.get("date", ""), item.get("time", "")),
            "price_pence":      _parse_index_price(item.get("price", "")),
            "price_change_pct": change_str if change_str and change_str != "-" else None,
        })

    return rows


def _parse_index_dt(date_str: str, time_str: str):
    """Parse 'DD.MM.YY' + 'HH:MM:SS' strings to a UTC-aware datetime."""
    try:
        d = datetime.strptime(date_str.strip(), "%d.%m.%y").date()
    except (ValueError, AttributeError):
        d = datetime.now(timezone.utc).date()

    try:
        t = datetime.strptime(time_str.strip(), "%H:%M:%S").time()
    except (ValueError, AttributeError):
        t = time(0, 0, 0)

    return datetime.combine(d, t, tzinfo=timezone.utc)


def _parse_index_price(val: str):
    """Parse a price string (pence) to float, or None for '-' / empty."""
    if not val or val == "-":
        return None
    try:
        return float(val.replace(",", ""))
    except ValueError:
        return None
