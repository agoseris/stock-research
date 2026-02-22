"""
test_01_pdf_downloads.py

Pre-build verification test 1 of 5.

Verifies:
  - FTSE Russell PDF downloads succeed for SMX, ASX, and AXX factsheets
  - pdfplumber can extract tables from each PDF
  - Each PDF yields at least 200 rows with visible ticker symbols

This test makes live HTTP requests to the FTSE Russell website and requires
an internet connection. No authentication or API keys required.

Run from the backend/ directory:
    python tests/test_01_pdf_downloads.py

Expected outcome: PASS for all three PDFs.
"""

import io
import sys

# Minimum row count accepted before flagging a format change
MIN_ROW_COUNT = 200

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


def run_test():
    try:
        import requests
    except ImportError:
        print("FAIL — 'requests' package not installed. Run: pip install requests")
        sys.exit(1)

    try:
        import pdfplumber
    except ImportError:
        print("FAIL — 'pdfplumber' package not installed. Run: pip install pdfplumber")
        sys.exit(1)

    print("Test 1: FTSE Russell PDF downloads and pdfplumber extraction\n")
    all_passed = True

    for label, url in FTSE_URLS.items():
        print(f"  [{label}]")
        print(f"  URL: {url}")

        # --- Download ---
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"  FAIL — download error: {e}\n")
            all_passed = False
            continue

        content = resp.content
        print(f"  HTTP {resp.status_code} | {len(content):,} bytes")

        if not content.startswith(b"%PDF"):
            print(
                f"  FAIL — response is not a PDF "
                f"(content-type: {resp.headers.get('content-type', 'unknown')})\n"
            )
            all_passed = False
            continue

        # --- Extract ---
        rows = []
        try:
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    table = page.extract_table()
                    if table:
                        rows.extend(table)
        except Exception as e:
            print(f"  FAIL — pdfplumber extraction error: {e}\n")
            all_passed = False
            continue

        print(f"  Rows extracted: {len(rows)}")

        if len(rows) < MIN_ROW_COUNT:
            print(
                f"  FAIL — only {len(rows)} rows (expected >= {MIN_ROW_COUNT}). "
                "PDF format may have changed.\n"
            )
            all_passed = False
            continue

        # Show a sample of extracted rows
        sample = [r for r in rows[1:4] if r and any(cell for cell in r if cell)]
        print(f"  Sample rows: {sample}")

        # Spot-check: at least some cells look like short ticker symbols
        tickers_found = []
        for row in rows:
            if not row:
                continue
            cell = (row[0] or "").strip()
            if 1 <= len(cell) <= 6 and cell.replace(".", "").isalnum() and cell[0].isalpha():
                tickers_found.append(cell)
        print(f"  Ticker-shaped cells found: {len(tickers_found)}")

        if len(tickers_found) < 50:
            print(
                f"  FAIL — fewer than 50 ticker-shaped cells found ({len(tickers_found)}). "
                "Check PDF structure.\n"
            )
            all_passed = False
            continue

        print(f"  PASS\n")

    if all_passed:
        print("==> All PDF download tests PASSED.")
        sys.exit(0)
    else:
        print("==> One or more PDF download tests FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    run_test()
