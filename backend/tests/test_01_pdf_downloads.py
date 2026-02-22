"""
test_01_pdf_downloads.py

Pre-build verification test 1 of 5.

Verifies:
  - FTSE Russell PDF downloads succeed for SMX, ASX, and AXX factsheets
  - pdfplumber can extract constituent rows from each PDF
  - Each PDF yields at least 50 ticker-name pairs

Uses the same browser-like headers and text-alignment extraction strategy
as the production universe_pipeline.py. If this test passes, the pipeline's
PDF layer is ready.

Run from the backend/ directory:
    python tests/test_01_pdf_downloads.py

Expected outcome: PASS for all three PDFs.
"""

import io
import re
import sys

MIN_TICKER_PAIRS = 50  # minimum extracted (ticker, name) pairs for PASS

FTSE_URLS = {
    "SMX (FTSE Small Cap — Tier 1)": (
        "https://research.ftserussell.com/analytics/factsheets/Home/"
        "DownloadConstituentsWeights/?indexdetails=SMX"
    ),
    "ASX (FTSE All-Share — Tier 2 candidates)": (
        "https://research.ftserussell.com/analytics/factsheets/Home/"
        "DownloadConstituentsWeights/?indexdetails=ASX"
    ),
    "AXX (FTSE AIM All-Share — Tier 2 candidates)": (
        "https://research.ftserussell.com/analytics/factsheets/Home/"
        "DownloadConstituentsWeights/?indexdetails=AXX"
    ),
}

# Same headers as universe_pipeline._REQUEST_HEADERS
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.9",
    "Referer": "https://research.ftserussell.com/",
}

# Same as universe_pipeline._TEXT_TABLE_SETTINGS
TEXT_TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "min_words_vertical": 3,
    "min_words_horizontal": 1,
}


def _looks_like_ticker(text):
    return (
        2 <= len(text) <= 6
        and text.replace(".", "").isalnum()
        and text[0].isalpha()
        and text == text.upper()
        and not text.replace(".", "").isdigit()
    )


def _looks_like_number(text):
    return bool(re.match(r'^[\d,\.%\-]+$', text))


def _extract_pairs(pdf_bytes):
    """
    Mirror of universe_pipeline._extract_tickers_from_pdf —
    returns (ticker, name) pairs using the three-strategy cascade.
    Also returns the raw row counts for each strategy for diagnostics.
    """
    import pdfplumber

    diagnostics = {}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:

        # Strategy 1: text-aligned
        rows1 = []
        for page in pdf.pages:
            try:
                t = page.extract_table(table_settings=TEXT_TABLE_SETTINGS)
                if t:
                    rows1.extend(t)
            except Exception:
                pass
        diagnostics["text_table_rows"] = len(rows1)
        pairs1 = _rows_to_pairs(rows1)
        if len(pairs1) >= 10:
            diagnostics["strategy_used"] = "text-aligned table"
            return pairs1, diagnostics

        # Strategy 2: default ruled-border
        rows2 = []
        for page in pdf.pages:
            t = page.extract_table()
            if t:
                rows2.extend(t)
        diagnostics["default_table_rows"] = len(rows2)
        pairs2 = _rows_to_pairs(rows2)
        if len(pairs2) >= 10:
            diagnostics["strategy_used"] = "default table"
            return pairs2, diagnostics

        # Strategy 3: word positions
        all_words = []
        for page in pdf.pages:
            words = page.extract_words()
            if words:
                all_words.extend(words)
        diagnostics["word_count"] = len(all_words)
        pairs3 = _words_to_pairs(all_words)
        diagnostics["strategy_used"] = "word positions"
        return pairs3, diagnostics


def _rows_to_pairs(rows):
    results = []
    seen = set()
    for row in rows:
        if not row:
            continue
        cells = [(c or "").strip() for c in row]
        ticker = None
        name = None
        for i, cell in enumerate(cells):
            if _looks_like_ticker(cell):
                ticker = cell.upper()
                candidates = [
                    cells[j] for j in range(len(cells))
                    if j != i and cells[j]
                    and not _looks_like_number(cells[j])
                    and not _looks_like_ticker(cells[j])
                    and len(cells[j]) > 2
                ]
                if candidates:
                    name = max(candidates, key=len)
                break
        if ticker and name and ticker not in seen:
            seen.add(ticker)
            results.append((ticker, name))
    return results


def _words_to_pairs(words):
    if not words:
        return []
    row_map = {}
    for w in words:
        key = round(w["top"] / 2)
        row_map.setdefault(key, []).append(w)
    rows = []
    for key in sorted(row_map):
        row_words = sorted(row_map[key], key=lambda w: w["x0"])
        rows.append([w["text"] for w in row_words])
    return _rows_to_pairs(rows)


def run_test():
    try:
        import requests
    except ImportError:
        print("FAIL — 'requests' not installed. Run: pip install requests")
        sys.exit(1)

    try:
        import pdfplumber
    except ImportError:
        print("FAIL — 'pdfplumber' not installed. Run: pip install pdfplumber")
        sys.exit(1)

    print("Test 1: FTSE Russell PDF downloads and extraction\n")
    all_passed = True

    for label, url in FTSE_URLS.items():
        print(f"  [{label}]")

        # --- Download ---
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"  FAIL — download error: {e}\n")
            all_passed = False
            continue

        content = resp.content
        content_type = resp.headers.get("content-type", "unknown")
        print(f"  HTTP {resp.status_code} | {len(content):,} bytes | {content_type}")

        if not content.startswith(b"%PDF"):
            print(f"  FAIL — not a PDF. First 200 bytes: {content[:200]}\n")
            all_passed = False
            continue

        # --- Extract ---
        try:
            pairs, diag = _extract_pairs(content)
        except Exception as e:
            print(f"  FAIL — extraction error: {e}\n")
            all_passed = False
            continue

        print(f"  Strategy used:    {diag.get('strategy_used', 'unknown')}")
        if "text_table_rows" in diag:
            print(f"  Text-table rows:  {diag['text_table_rows']}")
        if "default_table_rows" in diag:
            print(f"  Default-table rows: {diag['default_table_rows']}")
        print(f"  Ticker pairs found: {len(pairs)}")
        if pairs:
            print(f"  Sample pairs:     {pairs[:3]}")

        if len(pairs) < MIN_TICKER_PAIRS:
            print(
                f"  FAIL — only {len(pairs)} pairs extracted "
                f"(expected >= {MIN_TICKER_PAIRS}).\n"
            )
            all_passed = False
        else:
            print(f"  PASS\n")

    if all_passed:
        print("==> All PDF download tests PASSED.")
        sys.exit(0)
    else:
        print("==> One or more PDF download tests FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    run_test()
