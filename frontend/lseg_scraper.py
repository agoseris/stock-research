"""
lseg_scraper.py

Playwright-based scraper for LSEG RNS announcement body text.

Maintains a persistent browser cookie store so that the private investor
challenge gate survives between calls. Cookie file: lseg_cookies.json
in the same directory as this module (gitignored, never committed).

Usage:
    from lseg_scraper import fetch_announcement_body
    body = fetch_announcement_body("https://www.londonstockexchange.com/news-article/...")

Raises RuntimeError on failure (page not found, body empty, etc.).
"""

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

_GOTO_TIMEOUT    = 30_000   # ms — page navigation
_ELEMENT_TIMEOUT = 15_000   # ms — waiting for body element
_CHALLENGE_TIMEOUT = 10_000  # ms — challenge gate interaction

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
