# Universe Pipeline — Session Handover
**Date:** 22 February 2026
**Stopping point:** iShares URL validation complete, architecture decision pending

---

## What we were building

The dynamic universe management pipeline (`backend/universe_pipeline.py`) — Step 11a. Quarterly refresh that builds a two-tier monitored universe of LSE small-cap companies and writes it to Firestore.

- **Tier 1** (~250 companies): FTSE Small Cap index constituents, investment trusts excluded
- **Tier 2** (~100–200 companies): LSE Main Market + AIM companies with market cap between Tier 1 lower bound and £1B, not in Tier 1

All other pipeline code (abstractions, yfinance enrichment, Firestore storage, tests 2–5) is **complete and passing**.

---

## The blocker

The original plan used FTSE Russell constituent PDFs as the universe source. These PDFs:
- **SMX** (FTSE Small Cap): contains company name + index weight + country only — **no tickers**
- **ASX** (FTSE All-Share): same — **no tickers**
- **AXX** (FTSE AIM All-Share): URL returns "sorry, we can't find that file" — **dead**

The entire PDF extraction pipeline in `universe_pipeline.py` and `test_01_pdf_downloads.py` is therefore **irrelevant** and needs to be replaced.

---

## The new data source plan

Documented in `docs/universe_pipeline_brief.md` **Section 12** — read this before coding.

Priority order from the brief:
1. **iShares ETF CSV downloads** (Option A) — primary replacement
2. **Finnhub symbol list API** (Option B) — supplement for full exchange coverage

### iShares URL test results (run this session)

| ETF | Intended purpose | URL status | Notes |
|-----|-----------------|------------|-------|
| **CUKS** (MSCI UK Small Cap) | Tier 1 proxy | ✅ **200 OK, clean CSV** | Columns: Ticker, Name, Sector, Asset Class, Weight (%), Exchange, Market Currency. Dated 19 Feb 2026. **Ready to use.** |
| CUKX (FTSE 100) | Exclude large caps | ❌ Returns wrong fund | Product ID `253743` in brief is stale — returns US stocks (NVIDIA, AAPL, MSFT). Needs correct product ID. |
| MIDD (FTSE 250) | Tier 2 Main Market candidates | ❌ 404 Not Found | Product ID `251460` is dead. |

### CUKS caveat (important)
CUKS tracks **MSCI UK Small Cap**, not FTSE Small Cap. The MSCI methodology uses wider market cap boundaries — the live CUKS file includes WEIR GROUP (~£3B), DIPLOMA (~£4B), ST JAMES'S PLACE (~£4B) which are firmly FTSE 250 / mid-cap.

**Fix:** Apply a market cap ceiling (approx £800M, the FTSE Small Cap upper bound) when building Tier 1 from CUKS. Companies above this ceiling are filtered out via the yfinance market cap check.

The CUKS CSV URL that works:
```
https://www.ishares.com/uk/individual/en/products/253474/ishares-msci-uk-small-cap-ucits-etf/1506575576011.ajax?fileType=csv&fileName=CUKS_holdings&dataType=fund
```

---

## Decision needed before coding

Two options — pick one:

### Option 1 — Fix iShares URLs (no new registrations)
Navigate to `https://www.ishares.com/uk/individual/en/products/etf-investments` and find the correct download links for:
- CUKX (iShares Core FTSE 100 UCITS ETF) — needed to identify large caps
- MIDD (iShares FTSE 250 UCITS ETF) — needed for Tier 2 Main Market candidates

**Result:** Tier 1 from CUKS (MSCI proxy), Tier 2 Main Market from MIDD. **AIM still absent.**

### Option 2 — Add Finnhub (recommended, most complete)
Register for a free Finnhub API key at `https://finnhub.io/register` (email only, no card, instant).
Add `FINNHUB_KEY=your_key` to `backend/.env`.

Finnhub `GET /stock/symbol?exchange=L` returns all LSE Main Market symbols.
Finnhub `GET /stock/symbol?exchange=LN` (verify AIM exchange code on registration) returns AIM symbols.

**Result:** Tier 1 from CUKS (MSCI proxy), Tier 2 from full Finnhub LSE+AIM symbol list filtered by yfinance market cap. **Complete coverage.**

---

## What to build once the decision is made

Replace the entire PDF extraction approach. The new `universe_pipeline.py` needs:

### New Tier 1 construction
```
1. HTTP GET CUKS CSV → parse with pandas (skiprows=2, filter Asset Class == "Equity")
2. Extract Ticker column → list of LSE ticker strings (already without .L suffix)
3. Filter: skip any ticker where yfinance market_cap > £800M (MSCI mid-cap contamination)
4. Apply existing investment trust exclusion (unchanged)
5. Build UniverseCompany objects (unchanged)
6. Compute lower_bound_gbp = min(market_cap) from admitted Tier 1 companies
```

### New Tier 2 construction (Option 2 / Finnhub path)
```
1. Finnhub GET /stock/symbol?exchange=L → all LSE Main Market symbols
2. Finnhub GET /stock/symbol?exchange=LN (or equivalent) → all AIM symbols
3. Filter: type == "Common Stock" (remove ETFs, preference shares, etc.)
4. Convert Finnhub ticker format to Yahoo format (append .L)
5. Remove all Tier 1 tickers
6. For each remaining ticker:
   - yfinance market cap check
   - Apply band filter: lower_bound_gbp < market_cap < £1B
   - Apply trust exclusion (unchanged)
   - Label and build UniverseCompany (unchanged)
```

### New Tier 2 construction (Option 1 / MIDD path)
```
1. HTTP GET MIDD CSV → all FTSE 250 tickers
2. HTTP GET CUKX CSV → FTSE 100 tickers (to confirm what's excluded)
3. Combine MIDD + any AIM source
4. Same market cap / trust filtering as above
5. AIM coverage: absent (document as known gap)
```

---

## Files to change

| File | Change needed |
|------|--------------|
| `backend/universe_pipeline.py` | Full replacement of `_download_pdf`, `_extract_tickers_from_pdf`, `_extract_by_column_detection`, `_parse_rows_for_tickers`, and the `_build_tier1` / `_build_tier2` methods. yfinance enrichment, trust exclusion, Firestore write, and `run()` logic are unchanged. |
| `backend/tests/test_01_pdf_downloads.py` | Full rewrite — test iShares CSV download and parse instead of FTSE Russell PDFs. Same PASS threshold: Tier 1 source yields 50+ tickers, Tier 2 source yields 50+ tickers. |
| `backend/.env` | Add `FINNHUB_KEY=` if Option 2 chosen |
| `CLAUDE.md` | Update data source description |

**Do not change:** `abstractions.py`, `market_data_yfinance.py`, `storage_firestore_universe.py`, `pipeline.py`, tests 2–5. These are all correct and passing.

---

## Current git state

Branch: `master`
Last commit: `dbfe78c` — "Fix SMX column detection: wider bins, lower threshold, looser row grouping"
(This commit is now obsolete — the whole PDF extraction approach is being replaced.)

Tests 2–5 passed on the VM. Test 1 has never passed (it tests PDF extraction which is now known to be the wrong approach).

---

## VM reminders

- Connect as `danjmorris` (not `agoseris`)
- Venv: `source ~/stock-research/backend/venv/bin/activate`
- Run from: `cd ~/stock-research/backend`
- Deploy: local edit → commit → push → `cd ~/stock-research && git pull` on VM

---

## Brief location

Full specification: `docs/universe_pipeline_brief.md` (v2.2)
Section 12 is the key new section — contingency alternatives to PDF parsing.

