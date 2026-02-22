"""
test_01_pdf_downloads.py

Pre-build verification test 1 of 5.

Verifies that FTSE Russell PDFs download successfully and that the
column-detection extraction strategy finds enough ticker-name pairs.

Uses the same logic as universe_pipeline._extract_by_column_detection,
including the TICKER_STOPLIST filter. AXX (AIM All-Share) is tested but
non-fatal: if it fails, the test notes it and continues. The SMX and ASX
results determine overall PASS/FAIL.

Run from the backend/ directory:
    python tests/test_01_pdf_downloads.py

Expected outcome:
  SMX and ASX — PASS with 50+ ticker pairs each
  AXX         — may return HTML (non-fatal, logged as WARNING)
"""

import io
import re
import sys
from collections import Counter
from typing import List, Tuple

MIN_TICKER_PAIRS = 50  # minimum for SMX and ASX to be considered PASS

FTSE_URLS = {
    "SMX": (
        "https://research.ftserussell.com/analytics/factsheets/Home/"
        "DownloadConstituentsWeights/?indexdetails=SMX",
        True,   # fatal if fails
    ),
    "ASX": (
        "https://research.ftserussell.com/analytics/factsheets/Home/"
        "DownloadConstituentsWeights/?indexdetails=ASX",
        True,   # fatal if fails
    ),
    "AXX": (
        "https://research.ftserussell.com/analytics/factsheets/Home/"
        "DownloadConstituentsWeights/?indexdetails=AXX",
        False,  # non-fatal — may return HTML
    ),
}

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.9",
    "Referer": "https://research.ftserussell.com/",
}

TICKER_STOPLIST = frozenset([
    "FTSE", "INDEX", "WEIGHT", "SECTOR", "NAME", "ISIN", "SEDOL",
    "ICB", "RIC", "DATE", "PAGE", "TOTAL", "COUNT", "RANK", "TYPE",
    "CODE", "NOTE", "SEE", "REF",
    "PLC", "LTD", "LLC", "INC", "CORP", "CO", "SA", "AG", "NV",
    "SE", "SCA", "PTE", "PTY",
    "ETF", "REIT", "NAV", "AUM", "EPS", "PE", "ROE", "ROA",
    "AIM", "LSE", "LON", "GBP", "GBX", "USD", "EUR",
    "UK", "US", "EU", "GB", "UN", "UAE", "KINGDOM",
    "THE", "AND", "FOR", "BUT", "NOT", "ARE", "WAS", "HAS", "ITS",
    "NEW", "OLD", "ALL", "TOP", "BIG", "NET", "KEY",
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    "UNITED", "GLOBAL", "GROUP", "TRUST", "FUND", "WORLD",
    "HOLD", "INTL", "INT", "MGMT", "TECH", "PART",
])


def _looks_like_ticker(text: str) -> bool:
    t = text[:-2] if text.endswith(".L") else text
    return (
        2 <= len(t) <= 6
        and t.isalnum()
        and t[0].isalpha()
        and t == t.upper()
        and not t.isdigit()
    )


def _looks_like_number(text: str) -> bool:
    return bool(re.match(r'^[\d,\.%\-]+$', text))


def _normalise_ticker(text: str) -> str:
    return text.upper().removesuffix(".L")


def _extract_by_column_detection(all_words: list) -> List[Tuple[str, str]]:
    if not all_words:
        return []

    ticker_candidates = [
        w for w in all_words
        if _looks_like_ticker(w["text"]) and w["text"] not in TICKER_STOPLIST
    ]

    if len(ticker_candidates) < 5:
        return []

    x_counts = Counter(round(w["x0"] / 20) * 20 for w in ticker_candidates)
    threshold = max(5, len(ticker_candidates) * 0.04)
    valid_x = {x for x, count in x_counts.items() if count >= threshold}
    if not valid_x:
        valid_x = set(x_counts.keys())

    row_map: dict = {}
    for w in all_words:
        y_key = round(w["top"] / 4)
        row_map.setdefault(y_key, []).append(w)

    results = []
    seen: set = set()

    for y_key in sorted(row_map.keys()):
        row_words = sorted(row_map[y_key], key=lambda w: w["x0"])

        row_tickers = [
            (i, w) for i, w in enumerate(row_words)
            if (round(w["x0"] / 20) * 20 in valid_x
                and _looks_like_ticker(w["text"])
                and w["text"] not in TICKER_STOPLIST)
        ]

        for rank, (ti, ticker_word) in enumerate(row_tickers):
            ticker = _normalise_ticker(ticker_word["text"])
            if ticker in seen:
                continue

            left_x = 0.0
            if rank > 0:
                _, prev = row_tickers[rank - 1]
                left_x = prev["x1"]

            name_parts = []
            for w in row_words[:ti]:
                if w["x0"] < left_x:
                    continue
                t = w["text"].strip()
                if not t or _looks_like_number(t) or t in TICKER_STOPLIST:
                    continue
                name_parts.append(t)

            if not name_parts:
                for w in row_words[ti + 1:]:
                    if len(name_parts) >= 8:
                        break
                    t = w["text"].strip()
                    if not t:
                        continue
                    if _looks_like_number(t) or "%" in t:
                        break
                    if round(w["x0"] / 20) * 20 in valid_x:
                        break
                    if t not in TICKER_STOPLIST:
                        name_parts.append(t)

            name = " ".join(name_parts).strip()
            if name and len(name) > 2:
                seen.add(ticker)
                results.append((ticker, name))

    return results


def run_test():
    try:
        import requests
    except ImportError:
        print("FAIL — 'requests' not installed.")
        sys.exit(1)
    try:
        import pdfplumber
    except ImportError:
        print("FAIL — 'pdfplumber' not installed.")
        sys.exit(1)

    print("Test 1: FTSE Russell PDF downloads and column-detection extraction\n")
    fatal_failures = []

    for label, (url, is_fatal) in FTSE_URLS.items():
        print(f"  [{label}]")

        # Download
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            msg = f"download error: {e}"
            if is_fatal:
                fatal_failures.append(f"{label}: {msg}")
                print(f"  FAIL — {msg}\n")
            else:
                print(f"  WARNING (non-fatal) — {msg}\n")
            continue

        content = resp.content
        content_type = resp.headers.get("content-type", "unknown")
        print(f"  HTTP {resp.status_code} | {len(content):,} bytes | {content_type}")

        if not content.startswith(b"%PDF"):
            msg = f"not a PDF (content-type: {content_type})"
            if is_fatal:
                fatal_failures.append(f"{label}: {msg}")
                print(f"  FAIL — {msg}\n")
            else:
                print(f"  WARNING (non-fatal) — {msg}")
                print(f"  AXX may require a session cookie or different URL.\n")
            continue

        # Extract
        all_words = []
        try:
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages:
                    words = page.extract_words()
                    if words:
                        all_words.extend(words)
        except Exception as e:
            msg = f"pdfplumber error: {e}"
            if is_fatal:
                fatal_failures.append(f"{label}: {msg}")
                print(f"  FAIL — {msg}\n")
            else:
                print(f"  WARNING (non-fatal) — {msg}\n")
            continue

        # Diagnostics before filtering
        raw_ticker_candidates = [
            w["text"] for w in all_words
            if _looks_like_ticker(w["text"])
        ]
        filtered_candidates = [t for t in raw_ticker_candidates if t not in TICKER_STOPLIST]
        x_counts = Counter(
            round(w["x0"] / 20) * 20 for w in all_words
            if _looks_like_ticker(w["text"]) and w["text"] not in TICKER_STOPLIST
        )
        threshold = max(5, len(filtered_candidates) * 0.04)
        valid_x = {x for x, cnt in x_counts.items() if cnt >= threshold}

        print(f"  Raw ticker-shaped words:    {len(raw_ticker_candidates)}")
        print(f"  After stop-list filter:     {len(filtered_candidates)}")
        print(f"  Column threshold:           {threshold:.1f}")
        print(f"  Ticker column x-positions:  {sorted(valid_x)}")

        # Full x-position distribution (top 12, helps diagnose column detection)
        print(f"  X-bin distribution (top 12 by count):")
        for xb, cnt in sorted(x_counts.items(), key=lambda kv: -kv[1])[:12]:
            marker = " ← qualifies" if cnt >= threshold else ""
            print(f"    x={xb:5.0f}: {cnt:3d}{marker}")

        # Word dump — first 80 words sorted by top/x0; marks ticker candidates
        print(f"  First 80 words (top→x0 order):")
        sorted_words = sorted(all_words, key=lambda w: (w["top"], w["x0"]))
        for dw in sorted_words[:80]:
            is_cand = _looks_like_ticker(dw["text"]) and dw["text"] not in TICKER_STOPLIST
            marker = " ←" if is_cand else ""
            print(f"    top={dw['top']:6.1f} x0={dw['x0']:6.1f} '{dw['text']}'{marker}")

        pairs = _extract_by_column_detection(all_words)
        print(f"  Ticker-name pairs found:    {len(pairs)}")
        if pairs:
            print(f"  Sample pairs: {pairs[:5]}")

        if len(pairs) < MIN_TICKER_PAIRS:
            msg = f"only {len(pairs)} pairs (expected >= {MIN_TICKER_PAIRS})"
            if is_fatal:
                fatal_failures.append(f"{label}: {msg}")
                print(f"  FAIL — {msg}\n")
            else:
                print(f"  WARNING (non-fatal) — {msg}\n")
        else:
            print(f"  PASS\n")

    if fatal_failures:
        print("==> Test 1 FAILED:")
        for f in fatal_failures:
            print(f"    {f}")
        sys.exit(1)
    else:
        print("==> Test 1 PASSED (SMX and ASX extraction successful).")
        sys.exit(0)


if __name__ == "__main__":
    run_test()
