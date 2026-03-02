# UK Small Cap Universe Pipeline — Build Brief

**Version:** 2.2  
**Date:** 22 February 2026  

| Version | Change |
|---|---|
| 1.0 | Initial architecture. EODHD Fundamentals tier (~£47/month) as single enrichment source. |
| 2.0 | Free-first architecture. EODHD replaced by yfinance. FTSE Russell PDF downloads retained. Cost: £0/month. yfinance characterised as unofficial Yahoo Finance scraper (not an API or RSS feed). |
| 2.1 | Added formal source selection rationale (Section 4.4) documenting the decision to adopt yfinance as primary enrichment source, alternatives considered, trade-offs accepted, and upgrade trigger criteria. |
| 2.2 | Added Section 12: contingency alternatives to FTSE Russell PDF parsing. Covers iShares ETF CSV downloads, Finnhub symbol list API, yfinance ETF holdings, and HTML scraping, with a prioritised fallback table. |

---

## 1. Strategic Context

The universe is not itself a screening tool — it is the candidate pool upon which a separate, signal-based analysis engine operates. The pipeline's job is to keep that pool accurate, current, and clean. Downstream filtering handles investment decisions; this pipeline handles universe integrity.

The thesis is **growth in tangible businesses**. The universe should therefore reflect companies that:
- Are operating businesses with real revenues
- Are of a size where they are under-covered by institutional analysts, giving signal-based analysis an informational edge
- Are on a plausible growth trajectory toward higher market cap bands

---

## 2. MoSCoW Requirements

| Requirement | Priority | Resolution |
|---|---|---|
| Universe identification — Tier 1 and Tier 2 | **MUST** | FTSE Russell PDFs + yfinance market cap filter |
| Investment trust exclusion | **MUST** | Revenue filter (primary) + name pattern matching + yfinance sector (where available) |
| Current price data | **SHOULD** | yfinance — reliable for LSE/AIM |
| Current fundamentals (market cap, revenue, growth) | **SHOULD** | yfinance — good coverage for Tier 2 band; partial for smaller Tier 1 names |
| Sector classification | **COULD** | yfinance sector field where available; not relied upon for exclusion logic |
| Historical price and fundamentals | **COULD** | yfinance `history()` — up to 5 years EOD, free |

---

## 3. Universe Definition

### Tier 1 — Core Universe
**Definition:** Current members of the FTSE Small Cap index (FTSE Russell index code: SMX), after exclusions.

**Inclusions:**
- All LSE Main Market companies currently constituting the FTSE Small Cap index

**Exclusions:**
- Investment trusts — identified by three complementary filters applied in order:
  1. yfinance `sector` / `industry` field where present — financial vehicle classification
  2. Company name pattern matching — names containing "Trust", "Fund", "Income & Growth", "Investment Company" etc.
  3. Null or zero revenue — reliable catch-all for non-operational entities
- Any constituent where revenue is null or zero (data quality backstop)

**Approximate size:** 250–270 companies after filtering  
**Update cadence:** Quarterly, aligned with FTSE Russell index rebalancing (March, June, September, December)

---

### Tier 2 — Growth Trajectory Universe
**Definition:** LSE Main Market and AIM-listed companies with market cap between the FTSE Small Cap lower boundary and £1B, not already in Tier 1.

These are companies that have grown beyond small cap index territory but remain below the £1B threshold at which institutional coverage becomes dense. They sit in a relative information vacuum — the highest-edge segment for signal-based analysis.

**Inclusions:**
- All active LSE Main Market and AIM-listed companies within the market cap band
- REITs, SPACs, and royalty companies (included but flagged with category label — see Section 5)

**Exclusions:**
- All Tier 1 members (no double-counting)
- Investment trusts (same three-filter approach as Tier 1)
- Any company where revenue is null or zero
- International companies with a secondary LSE quote but primary listing elsewhere (e.g. NVIDIA, Apple appearing with LSE quote codes) — filtered by requiring presence in FTSE All-Share or AIM All-Share constituent lists

**Market cap band:**
- **Lower bound:** Dynamic — set to the market cap of the smallest current Tier 1 member at each quarterly refresh. Prevents boundary drift between rebalances.
- **Upper bound:** £1,000,000,000 (fixed)

**Approximate size:** 100–200 companies after filtering  
**Update cadence:** Quarterly, aligned with Tier 1 refresh

---

## 4. Data Sources

### Source A — FTSE Russell Constituent Downloads (Free, No Authentication)

**Purpose:** Definitive universe of formally listed UK companies. Ground truth for Tier 1 membership and starting point for Tier 2 candidate pool.

**Access method:** HTTP GET — returns a PDF file requiring table extraction via `pdfplumber`.

| Index | Code | Purpose |
|---|---|---|
| FTSE Small Cap | SMX | Tier 1 membership |
| FTSE All-Share | ASX | All Main Market companies — Tier 2 candidates |
| FTSE AIM All-Share | AXX | All AIM companies — Tier 2 candidates |

**Base URL pattern:**
```
https://research.ftserussell.com/analytics/factsheets/Home/DownloadConstituentsWeights/?indexdetails={CODE}
```

**What the PDFs contain:** Company name, ticker symbol, index weight. Market cap is not directly provided — derived from yfinance.

**Limitations:**
- PDF format requires extraction pipeline — not directly machine-readable
- Current constituents only — no historical membership records
- Quarterly cadence — intra-quarter additions and deletions not captured until next rebalance
- PDF structure may change without notice, breaking the extraction script

---

### Source B — yfinance (Free, Unofficial)

**Purpose:** Market cap filtering for Tier 2 construction; current fundamentals; current and historical price data.

**Installation:** `pip install yfinance`

**How it works:** yfinance is not an API and does not use RSS. It works by scraping Yahoo Finance's **internal, undocumented JSON endpoints** — the same endpoints Yahoo's own website uses to populate its pages. A call such as `yf.Ticker("SDY.L").info` results in an HTTP request to a URL of the form:
```
https://query1.finance.yahoo.com/v10/finance/quoteSummary/SDY.L?modules=...
```
The library parses the JSON response and presents it in a convenient Python interface. This approach is widely used and has been stable for years, but it carries important caveats (see below).

**Ticker format for UK stocks:** Yahoo Finance uses the `.L` suffix for LSE/AIM tickers (e.g. `SDY.L` for Speedy Hire, `JET2.L` for Jet2).

**Key data available per ticker:**

| Data point | yfinance field | Used for |
|---|---|---|
| Market capitalisation | `info['marketCap']` | Tier 2 band filter |
| Total revenue (TTM) | `info['totalRevenue']` | Revenue exclusion filter |
| Revenue growth (YoY) | `info['revenueGrowth']` | Fundamentals output |
| Sector | `info['sector']` | Investment trust detection (where available) |
| Industry | `info['industry']` | Investment trust detection (where available) |
| Current price | `info['currentPrice']` | Price signal |
| 52-week high/low | `info['fiftyTwoWeekHigh/Low']` | Price context |
| P/E ratio | `info['trailingPE']` | Fundamentals output |
| EPS | `info['trailingEps']` | Fundamentals output |
| EOD price history | `ticker.history(period="5y")` | Historical signal; "already priced in" check |

**Important caveats:**
- yfinance is **not an official API**. Yahoo Finance has no public API for this data and has not authorised programmatic access. Use is technically against Yahoo's Terms of Service for commercial purposes. For personal and research use this is widely tolerated and Yahoo has never actively blocked it, but this should be reviewed if the project becomes commercial in nature.
- **Can break without warning.** Yahoo has no obligation to maintain backward compatibility on internal endpoints. The yfinance GitHub repository has a history of periodic breakage events — typically resolved by the open source community within days of Yahoo changing something, but there is no guarantee of timelines.
- **No SLA, no official support.** If it breaks at the moment of a quarterly refresh, resolution depends on the open source community.
- Coverage is **good for Tier 2** (£300M–£1B range) but **patchy for smaller Tier 1 names**. Expect some null fields for less-covered companies.
- `sector` and `industry` fields are **inconsistently populated** for UK small caps — do not rely on them as the sole exclusion mechanism. Use as a supporting signal only.
- Rate limiting applies at scale. For full universe refreshes (potentially 400–600 tickers), introduce a short sleep between requests (e.g. 0.5s) to avoid throttling.

---

### Source C — stockanalysis.com (Free, Sanity Check Only)

**Purpose:** Manual cross-validation of universe completeness. Not used in the automated pipeline.

stockanalysis.com publishes daily-updated lists of all LSE Main Market and AIM stocks sorted by market cap, with revenue visible. Useful for manually verifying that the pipeline has not missed significant names, or to spot-check market cap values against yfinance output.

- LSE Main Market: `https://stockanalysis.com/list/london-stock-exchange/`
- AIM: `https://stockanalysis.com/list/london-stock-exchange-aim/`

**Note:** The LSE Main Market list includes international companies with secondary LSE quote codes (NVIDIA, Apple etc). Disregard these when cross-checking — they will not appear in the FTSE All-Share or AIM All-Share constituent PDFs.

---

### Source Selection Rationale — yfinance as Primary Enrichment Source

**Decision:** yfinance is the primary source for all universe data enrichment — market cap, revenue, fundamentals, price data, and sector classification signals.

**Context:** The initial architecture (v1.0) specified EODHD at the Fundamentals tier (~£39–£47/month) as the single enrichment source, providing a direct LSE data licence, structured API, and ICB sector codes. Following a requirements review, this was replaced with yfinance at £0/month.

**Factors driving the decision:**

The universe is a slowly-changing candidate pool, not a live trading signal. The FTSE Small Cap index rebalances quarterly; Tier 2 boundaries shift gradually between rebalances. Daily refreshes are the maximum cadence — in practice, quarterly aligned with FTSE rebalancing is the operating norm. Against this backdrop, paying a monthly subscription for a data source queried four times a year was not proportionate.

The investment strategy is growth-oriented and long-horizon. Survivorship bias in the constituent history is acceptable — the pipeline does not need to reconstruct what the universe looked like at a prior date, only what it looks like now. This removed one of EODHD's key differentiators.

Sector classification was downgraded from SHOULD to COULD. The revenue filter (null/zero revenue → exclude) and name pattern matching together carry sufficient weight to exclude investment trusts without needing authoritative ICB codes. The yfinance sector field is retained as a supporting signal where populated, but the exclusion logic is not dependent on it.

yfinance coverage is adequate for the Tier 2 band (£300M–£1B), where Yahoo Finance data tends to be reliable. Coverage gaps are more prevalent in smaller Tier 1 names; these are tracked via the `DATA_QUALITY` label and monitored as a pipeline health metric.

**Trade-offs accepted:**

| Trade-off | Accepted because |
|---|---|
| yfinance is not an official API — scrapes undocumented Yahoo Finance endpoints | Widely used, community-maintained, historically stable for years |
| Can break without warning when Yahoo changes internal structure | Mitigated by retaining previous snapshot; community typically patches within days |
| Technically against Yahoo ToS for commercial use | Personal/research use is widely tolerated; to be reviewed if project becomes commercial |
| No SLA or support channel | Quarterly cadence gives recovery time; EODHD upgrade path is pre-specified |
| Inconsistent sector/industry fields for UK small caps | Sector is a COULD; exclusion logic does not depend on it |

**Upgrade trigger:** If more than 10% of universe records are flagged `DATA_QUALITY` after two consecutive quarterly refreshes, the free architecture is not meeting SHOULD requirements and EODHD Option A should be adopted. See Section 11.

---

## 5. Entity Classification and Flagging

All universe members carry a classification label assigned during pipeline construction.

| Label | Definition | Tier 1 | Tier 2 |
|---|---|---|---|
| `OPERATING` | Operating company with confirmed revenue | ✅ Included | ✅ Included |
| `INVESTMENT_TRUST` | Closed-ended fund — identified by sector, name pattern, or zero revenue | ❌ Excluded | ❌ Excluded |
| `REIT` | Real estate investment trust | ❌ Excluded | ✅ Flagged |
| `SPAC` | Shell acquisition company — no revenue, recently listed | ❌ Excluded | ✅ Flagged |
| `ROYALTY` | Royalty or streaming company | ❌ Excluded | ✅ Flagged |
| `NO_REVENUE` | Revenue null or zero — not a REIT/SPAC/ROYALTY | ❌ Excluded | ❌ Excluded |
| `DATA_QUALITY` | yfinance returned incomplete or malformed data | ⚠️ Quarantined | ⚠️ Quarantined |

Flagged Tier 2 entries (REIT, SPAC, ROYALTY) are retained in the universe output but should be treated as lower-priority candidates by the downstream analysis engine unless a specific thesis applies.

**Investment trust name pattern matching — keyword list (expand as needed):**
```python
TRUST_KEYWORDS = [
    "trust", "fund", "income & growth", "investment company",
    "investment trust", "capital & income", "equity income",
    "growth & income", "managed income", "global income"
]
# Match case-insensitively against company_name.lower()
```

---

## 6. Pipeline Logic

### 6.1 Tier 1 Construction

```
1. HTTP GET FTSE Russell SMX factsheet → save as PDF
2. Extract constituent table using pdfplumber → list of (ticker, company_name)
3. For each ticker:
   a. Convert to Yahoo format: append ".L" suffix
   b. Query yfinance info → retrieve sector, industry, revenue, market_cap
   c. Apply investment trust filter (in order):
      - If sector/industry indicates financial vehicle → INVESTMENT_TRUST → EXCLUDE
      - If company_name matches trust keyword list → INVESTMENT_TRUST → EXCLUDE
      - If revenue is null or zero → NO_REVENUE → EXCLUDE
        (log as DATA_QUALITY if other fields present but revenue missing)
   d. Else → label OPERATING → add to Tier 1
4. Store minimum market_cap from Tier 1 as dynamic lower bound variable
5. Output: Tier 1 universe with labels and metadata
```

### 6.2 Tier 2 Construction

```
1. HTTP GET FTSE Russell ASX factsheet (FTSE All-Share) → extract tickers
2. HTTP GET FTSE Russell AXX factsheet (FTSE AIM All-Share) → extract tickers
3. Merge ASX and AXX ticker lists → deduplicate
4. Remove all Tier 1 tickers
5. For each remaining ticker:
   a. Convert to Yahoo format: append ".L" suffix
   b. Query yfinance info → retrieve market_cap, revenue, sector, industry
   c. If market_cap is null → label DATA_QUALITY → skip
   d. If market_cap < Tier 1 lower bound → EXCLUDE (too small)
   e. If market_cap > £1,000,000,000 → EXCLUDE (too large)
   f. Apply investment trust filter (same three-step logic as Tier 1) → EXCLUDE if matched
   g. If revenue is null or zero:
      - If listed < 2 years → label SPAC → retain
      - Else → NO_REVENUE → EXCLUDE
   h. If industry/name indicates REIT → label REIT → retain
   i. If industry/name indicates royalty company → label ROYALTY → retain
   j. Else → label OPERATING → add to Tier 2
6. Output: Tier 2 universe with labels and metadata
```

### 6.3 Refresh Cadence

| Event | Action |
|---|---|
| Quarterly FTSE rebalance | Full Tier 1 and Tier 2 rebuild from fresh PDF downloads |
| Between rebalances | Optional: re-query yfinance market caps for Tier 2 names within 10% of £1B boundary |
| Pipeline failure detected | Alert and retain previous universe snapshot — do not overwrite with partial data |

---

## 7. Pre-Build Verification Tests

All tests use free tools — no subscriptions required.

### Test 1 — FTSE Russell PDF downloads

```bash
curl -L -o ftse_smx.pdf \
  "https://research.ftserussell.com/analytics/factsheets/Home/DownloadConstituentsWeights/?indexdetails=SMX" \
  -w "SMX → HTTP %{http_code} | %{content_type} | %{size_download} bytes\n"

curl -L -o ftse_asx.pdf \
  "https://research.ftserussell.com/analytics/factsheets/Home/DownloadConstituentsWeights/?indexdetails=ASX" \
  -w "ASX → HTTP %{http_code} | %{content_type} | %{size_download} bytes\n"

curl -L -o ftse_axx.pdf \
  "https://research.ftserussell.com/analytics/factsheets/Home/DownloadConstituentsWeights/?indexdetails=AXX" \
  -w "AXX → HTTP %{http_code} | %{content_type} | %{size_download} bytes\n"
```

Then test extraction:
```python
import pdfplumber

for filename in ["ftse_smx.pdf", "ftse_asx.pdf", "ftse_axx.pdf"]:
    with pdfplumber.open(filename) as pdf:
        rows = []
        for page in pdf.pages:
            table = page.extract_table()
            if table:
                rows.extend(table)
    print(f"{filename}: {len(rows)} rows extracted — sample: {rows[1:3]}")
```

**Expected outcome:** Each PDF yields 200+ rows with visible ticker symbols and company names.

---

### Test 2 — yfinance: market cap and revenue for a known Tier 1 company

```python
import yfinance as yf

# SDY = Speedy Hire — known FTSE Small Cap operating company
ticker = yf.Ticker("SDY.L")
info = ticker.info

print(f"Name:       {info.get('longName')}")
print(f"Market cap: {info.get('marketCap')}")
print(f"Revenue:    {info.get('totalRevenue')}")
print(f"Sector:     {info.get('sector')}")
print(f"Industry:   {info.get('industry')}")
```

**Expected outcome:** Non-null market cap and revenue. Sector/industry may or may not be populated — either is acceptable as this is a COULD.

---

### Test 3 — yfinance: investment trust detection

```python
# ADIG = ABRDN Diversified Income and Growth — known investment trust
ticker = yf.Ticker("ADIG.L")
info = ticker.info

print(f"Name:     {info.get('longName')}")
print(f"Revenue:  {info.get('totalRevenue')}")
print(f"Sector:   {info.get('sector')}")
print(f"Industry: {info.get('industry')}")
```

**Expected outcome:** Revenue is null or zero (primary exclusion trigger). Confirm the name contains "Income & Growth" (name pattern trigger). All three exclusion methods should independently identify this as an investment trust.

---

### Test 4 — yfinance: historical price data

```python
ticker = yf.Ticker("SDY.L")
history = ticker.history(period="5y")

print(f"Rows returned:  {len(history)}")
print(f"Date range:     {history.index[0].date()} → {history.index[-1].date()}")
print(history.tail(3)[["Open", "High", "Low", "Close", "Volume"]])
```

**Expected outcome:** ~1,250 rows of clean OHLCV data.

---

### Test 5 — yfinance: rate limiting behaviour at scale

```python
import yfinance as yf
import time

test_tickers = [
    "SDY.L", "JET2.L", "CVSG.L", "VLX.L", "RNWH.L",
    "CRW.L", "DATA.L", "FEVR.L", "SRC.L", "BUR.L",
    "HCM.L", "ROSE.L", "YCA.L", "GRP.L", "ADIG.L",
    "RWS.L", "BOY.L", "NCC.L", "GGP.L", "UPR.L"
]

results = []
for t in test_tickers:
    try:
        info = yf.Ticker(t).info
        results.append({
            "ticker": t,
            "market_cap": info.get("marketCap"),
            "revenue": info.get("totalRevenue"),
            "ok": True
        })
    except Exception as e:
        results.append({"ticker": t, "ok": False, "error": str(e)})
    time.sleep(0.5)

success = sum(1 for r in results if r["ok"])
print(f"Success: {success}/{len(test_tickers)}")
print(f"Failures: {[r['ticker'] for r in results if not r['ok']]}")
```

**Expected outcome:** 18–20 successes. Any consistent failures indicate incorrect ticker format or a gap in Yahoo Finance coverage for that name.

---

## 8. Output Schema

| Field | Type | Source | Notes |
|---|---|---|---|
| `ticker_lse` | string | FTSE Russell PDF | Native LSE ticker (e.g. `SDY`) |
| `ticker_yahoo` | string | Pipeline | Yahoo Finance format (e.g. `SDY.L`) |
| `isin` | string | yfinance | `info['isin']` — may be null for some names |
| `company_name` | string | FTSE Russell PDF | Legal name from index factsheet |
| `tier` | integer (1 or 2) | Pipeline logic | Universe tier assignment |
| `entity_type` | string | Pipeline logic | `OPERATING`, `REIT`, `SPAC`, `ROYALTY` |
| `market_cap_gbp` | float | yfinance | At time of last refresh |
| `revenue_ttm` | float | yfinance | Trailing twelve months |
| `revenue_growth_yoy` | float | yfinance | Year-on-year % |
| `sector` | string | yfinance | May be null — COULD field |
| `industry` | string | yfinance | May be null — COULD field |
| `listing_exchange` | string | Source PDF | `LSE_MAIN` or `AIM` |
| `last_price` | float | yfinance | Most recent EOD close |
| `last_price_date` | date | yfinance | Derived from last history row |
| `fifty_two_week_high` | float | yfinance | `fiftyTwoWeekHigh` |
| `fifty_two_week_low` | float | yfinance | `fiftyTwoWeekLow` |
| `universe_added_date` | date | Pipeline | When first included |
| `last_refreshed` | datetime | Pipeline | Timestamp of last pipeline run |

---

## 9. Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| yfinance breaks due to Yahoo Finance internal endpoint change | High | Pin yfinance version; monitor yfinance GitHub issues; retain previous universe snapshot as fallback on failed runs — community typically patches within days |
| Yahoo Finance Terms of Service exposure | Low-Medium | Acceptable for personal/research use; review if project becomes commercial in nature |
| Incomplete yfinance coverage for smaller Tier 1 names | Medium | Flag as `DATA_QUALITY`; track null-revenue record count per run as a pipeline health metric |
| FTSE Russell PDF structure changes | Medium | Quarterly manual inspection before automated extraction; alert on row count drop >10% vs previous run |
| International secondary-listed companies entering Tier 2 | Medium | Only include tickers present in FTSE All-Share or AIM All-Share PDFs — if absent from both, exclude |
| Investment trust slips through all three exclusion filters | Low | Quarterly manual spot-check of 10–20 random universe members; add misclassifications to exclusion list |
| yfinance rate throttling at scale (400–600 tickers) | Low | 0.5s sleep between requests; batch price history pulls using `yf.download()` |
| Dynamic lower bound calculation error | Low | Log computed lower bound on each run; alert if it deviates >20% from previous run |

---

## 10. Technology Stack

| Component | Tool | Notes |
|---|---|---|
| HTTP downloads | `curl` or `requests` (Python) | PDF downloads from FTSE Russell |
| PDF extraction | `pdfplumber` (Python) | Best-in-class for structured PDF tables |
| Market data | `yfinance` (Python) | Free, no API key required |
| Data manipulation | `pandas` (Python) | DataFrame filtering, merging, deduplication |
| Storage | SQLite (development) / PostgreSQL (production) | Universe table with full refresh history and previous snapshots |
| Scheduling | `cron` or `APScheduler` (Python) | Quarterly refresh; optional weekly boundary-check job |
| Alerting | Python `logging` + email or Slack webhook | On pipeline failure, unexpected row count drops, or yfinance errors |

**Installation:**
```bash
pip install yfinance pdfplumber pandas requests
```

---

## 11. Upgrade Path

If the free architecture proves insufficient — specifically if yfinance coverage gaps become material or reliability issues accumulate — the recommended upgrade path is:

**Option A — EODHD Fundamentals tier (~£39/month on annual plan)**  
Replaces yfinance entirely. Direct LSE data licence, structured API, consistent coverage, ICB sector codes. See v1.0 of this brief for full EODHD specification.

**Option B — Hybrid (still £0/month)**  
Retain yfinance for price data. Add EODHD free tier (20 calls/day, no card required) for fundamentals on the specific tickers where yfinance returns null fields. Requires managing two sources but stays free.

**Decision trigger for upgrading:** If more than 10% of universe records are flagged `DATA_QUALITY` after two consecutive quarterly refreshes, the free architecture is not meeting the SHOULD requirements and Option A should be adopted.

---

---

## 12. Contingency: Alternatives to FTSE Russell PDF Parsing

PDF parsing (Source A) has proven fragile in practice. The FTSE Russell constituent PDFs are the only official free source of index membership data, but they come without a parsing guarantee and will likely change format without notice over the life of the project. This section documents alternative approaches for obtaining universe membership data without PDF parsing, in priority order.

---

### Option A — iShares ETF Holdings CSV (Recommended First Fallback)

BlackRock's iShares range publishes daily-updated ETF holdings as a directly downloadable CSV file — no authentication, no parsing, no PDF. The URL pattern is stable and machine-readable:

```
https://www.ishares.com/uk/individual/en/products/{PRODUCT_ID}/{ETF_TICKER}/{TIMESTAMP}.ajax?fileType=csv&fileName={ETF_TICKER}_holdings&dataType=fund
```

**Relevant ETFs:**

| Index / Purpose | ETF Ticker | Notes |
|---|---|---|
| FTSE 100 | CUKX | Tells you what is large cap — exclude these |
| FTSE 250 | MIDD | Tells you what is mid cap — exclude these |
| FTSE All-World UK | ISF | Broad Main Market coverage |
| MSCI UK Small Cap (proxy) | CUKS | Closest available ETF to FTSE Small Cap — see caveat below |

**Important caveat — CUKS tracks MSCI, not FTSE:**
There is no iShares ETF that directly tracks the FTSE Small Cap index (SMX). CUKS tracks the MSCI UK Small Cap index, which covers a similar but not identical universe. The two indices use different methodologies and membership will not be perfectly aligned. For most analytical purposes, the CUKS holdings list is an adequate proxy — it covers the same economic segment and is updated daily. However, it should be documented in the pipeline that Tier 1 membership is proxied by MSCI UK Small Cap when operating in fallback mode, not formal FTSE Small Cap membership.

**An alternative indirect approach:** Download the CUKX (FTSE 100) and MIDD (FTSE 250) holdings CSVs to establish what is large and mid cap on the Main Market. Then download the full LSE symbol list from Finnhub (see Option B) and exclude CUKX and MIDD members — what remains is a reasonable approximation of Main Market small cap. This approach is slightly more complex but stays closer to the FTSE methodology.

**Stability:** BlackRock has maintained this CSV download pattern for years. It is more robust than PDF parsing because it is a structured machine-readable file rather than a document formatted for human readers. That said, it is still an unofficial download and BlackRock could change the URL scheme.

**Python snippet:**
```python
import pandas as pd

ISHARES_URLS = {
    "ftse100": "https://www.ishares.com/uk/individual/en/products/253743/ishares-core-ftse-100-ucits-etf/1506575576011.ajax?fileType=csv&fileName=CUKX_holdings&dataType=fund",
    "ftse250": "https://www.ishares.com/uk/individual/en/products/251460/ishares-ftse-250-ucits-etf/1506575576011.ajax?fileType=csv&fileName=MIDD_holdings&dataType=fund",
    "msci_uk_small_cap": "https://www.ishares.com/uk/individual/en/products/253474/ishares-msci-uk-small-cap-ucits-etf/1506575576011.ajax?fileType=csv&fileName=CUKS_holdings&dataType=fund",
}

def fetch_ishares_holdings(url):
    # iShares CSVs have metadata rows at the top — skip until the header row
    df = pd.read_csv(url, skiprows=2)
    df = df[df['Ticker'].notna() & (df['Ticker'] != 'Ticker')]
    return df[['Ticker', 'Name', 'ISIN', 'Asset Class', 'Weight (%)']]
```

**Note:** The exact `skiprows` count and timestamp component of the URL may need verification against live files. The `1506575576011` timestamp segment in the URL appears to be a static product page identifier, not a date — it has remained stable across years.

---

### Option B — Finnhub Stock Symbols Endpoint (Free API, No PDF)

Finnhub provides a documented REST API endpoint that returns a complete list of all symbols traded on a given exchange, including the London Stock Exchange and AIM. This is a proper API with an API key, official documentation, and a structured JSON response.

**Free tier:** 60 API calls/minute. Registration required (email only, no card). API key issued immediately.

**Relevant endpoint:**
```
GET https://finnhub.io/api/v1/stock/symbol?exchange=L&token={API_KEY}
```
The `exchange=L` parameter returns LSE Main Market symbols. For AIM, the exchange code is `LN` (verify at registration — Finnhub's exchange codes for UK venues have varied in documentation).

**What it returns per symbol:** ticker, ISIN, company name, currency, exchange, type (Common Stock, ETF, etc.)

**What it does not return:** market cap, revenue, or index membership. It is a symbol directory, not an enrichment source. You would still need yfinance to get market cap for Tier 2 filtering — but this replaces the need for the FTSE All-Share and AIM All-Share PDFs entirely.

**Practical approach using Finnhub:**
1. Call `stock/symbol?exchange=L` → all Main Market tickers
2. Call `stock/symbol?exchange=LN` (or equivalent) → all AIM tickers
3. Filter by `type = "Common Stock"` to remove ETFs, preference shares, and other instruments
4. For Tier 1 proxy: use iShares CUKS CSV (Option A) or market cap band
5. For Tier 2: apply yfinance market cap filter as normal

**Limitation:** Finnhub's index constituent endpoint (`/indices/const`) supports a documented set of major indices including `^GSPC`, `^NDX`, and `^DJI` — US indices only as of the time of writing. FTSE Small Cap (`^SMX`) is not listed in the supported set. Finnhub is therefore useful as a symbol directory for full exchange coverage, but cannot directly provide FTSE index membership.

**Python snippet:**
```python
import requests

def get_lse_symbols(api_key):
    url = f"https://finnhub.io/api/v1/stock/symbol?exchange=L&token={api_key}"
    response = requests.get(url)
    symbols = response.json()
    # Filter to common stock only
    return [s for s in symbols if s.get('type') == 'Common Stock']
```

---

### Option C — yfinance ETF Holdings (Zero Additional Dependencies)

yfinance can retrieve ETF holdings via the `get_holdings()` method, which means it is possible to pull the CUKS or MIDD holdings list without any additional library or API registration — using a source you already have in the pipeline.

```python
import yfinance as yf

cuks = yf.Ticker("CUKS.L")
holdings = cuks.get_holdings()
# Returns a DataFrame with tickers, weights, and names
```

This has the same caveat as Option A (MSCI, not FTSE) and the same yfinance instability risk, but has no additional dependencies. If the pipeline is already using yfinance extensively, this may be the lowest-friction fallback.

**Stability concern:** The `get_holdings()` method is less widely used than `info` and `history()`, and is therefore more likely to break silently on a Yahoo Finance API change. Test carefully before committing.

---

### Option D — HTML Scraping of LSE Website or Hargreaves Lansdown

The LSE website (`londonstockexchange.com/indices/ftse-smallcap/constituents/table`) publishes a constituent table as HTML. Hargreaves Lansdown's index page (`hl.co.uk/shares/stock-market-summary/ftse-small-cap`) similarly renders a constituent list in the browser. Both are JavaScript-rendered, meaning a simple `requests` call will not work — a headless browser (Playwright or Selenium) would be required.

This approach is technically feasible but introduces significant additional complexity and is the most fragile option: JavaScript-rendered pages change more often than CSVs, and the HL/LSE websites have no obligation to maintain their structure for programmatic access.

**Verdict:** This is a last resort. It is not recommended unless Options A, B, and C have all failed.

---

### Fallback Priority Order

| Priority | Option | Cost | Stability | FTSE Accuracy | Complexity |
|---|---|---|---|---|---|
| 1 | iShares CSV (CUKS + CUKX + MIDD) | £0 | High | Proxied (MSCI ≈ FTSE) | Low |
| 2 | Finnhub symbol list + yfinance market cap | £0 | Medium-High | Approximate (market cap band) | Low-Medium |
| 3 | yfinance ETF holdings | £0 | Medium | Proxied (MSCI ≈ FTSE) | Very Low |
| 4 | HTML scraping (LSE / HL) | £0 | Low | Exact | High |
| 5 | EODHD paid API | ~£39/month | Very High | Exact | Low |

**Recommended contingency strategy:** If PDF parsing proves unviable, adopt Option A (iShares CSV) as the primary replacement for FTSE Russell PDFs, supplemented by Option B (Finnhub symbol list) for full exchange coverage. Accept the MSCI proxy for Tier 1 membership with appropriate documentation. The enrichment pipeline (yfinance for market cap, revenue, prices) is unchanged.

---

*End of brief. Next step: run pre-build verification tests (Section 7) before writing any pipeline code.*
