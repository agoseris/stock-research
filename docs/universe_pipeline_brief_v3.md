**UK Small Cap Universe Pipeline**

Build Brief

*Version 3.0 • February 2026 • Integrated Architecture Edition*

  ------------- -------------------------------------------------------------
  **Version**   **Change**

  1.0           Initial architecture. EODHD Fundamentals tier (\~£47/month)
                as single enrichment source.

  2.0           Free-first architecture. EODHD replaced by yfinance. FTSE
                Russell PDF downloads retained. Cost: £0/month.

  2.1           Added formal source selection rationale (Section 4.4)
                documenting yfinance decision, alternatives, trade-offs, and
                upgrade triggers.

  3.0           Integrated architecture edition. Storage migrated from
                SQLite/PostgreSQL to Firestore. Two new abstractions
                specified (MarketDataProviderBase,
                UniverseStorageProviderBase). AIM included with liquidity
                flag. Refresh cadence revised. Scheduling and alerting
                aligned to existing project infrastructure.
  ------------- -------------------------------------------------------------

**1. Strategic Context**

The universe is not itself a screening tool --- it is the candidate pool
upon which a separate, signal-based analysis engine operates. The
pipeline's job is to keep that pool accurate, current, and clean.
Downstream filtering handles investment decisions; this pipeline handles
universe integrity.

The thesis is growth in tangible businesses. The universe should
therefore reflect companies that:

-   Are operating businesses with real revenues

-   Are of a size where they are under-covered by institutional
    analysts, giving signal-based analysis an informational edge

-   Are on a plausible growth trajectory toward higher market cap bands

AIM is included in the universe. AIM stocks may be illiquid but
represent the highest-edge segment for signal-based analysis due to low
institutional coverage. A liquidity flag surfaces execution feasibility
alongside every signal, informing the investment decision without
filtering the signal.

**2. Architecture Integration**

This pipeline operates as a component within the broader stock research
tool architecture. All design decisions are consistent with the
project's five core architectural principles.

**2.1 New Abstractions**

Two new abstraction boundaries are introduced, numbered sequentially
after the five existing project abstractions.

**Abstraction 6 --- Market Data Provider (MarketDataProviderBase)**

The pipeline depends on yfinance for all market data enrichment.
yfinance is explicitly characterised in this brief as an unofficial
scraper of undocumented Yahoo Finance endpoints that can break without
warning. Hardcoding it into pipeline logic would violate abstraction
integrity and create unnecessary fragility.

MarketDataProviderBase defines the interface. The PoC implementation is
YFinanceProvider. Migration to EODHD or any other source is a contained
swap behind this interface, consistent with how LLMProviderBase and
StorageProviderBase work today.

Interface methods:

-   get_info(ticker_yahoo: str) -\> dict --- returns market cap,
    revenue, sector, industry, price fields

-   get_price_history(ticker_yahoo: str, period: str) -\> DataFrame ---
    returns OHLCV history

**Abstraction 7 --- Universe Storage Provider
(UniverseStorageProviderBase)**

The universe is a slowly-changing reference dataset, conceptually
distinct from the signal and discovery results managed by the existing
StorageProviderBase. It warrants its own abstraction rather than being
forced through announcement-oriented methods.

Interface methods:

-   save_universe(companies: List\[UniverseCompany\]) -\> None

-   get_universe(tier: Optional\[int\]) -\> List\[UniverseCompany\]

-   get_company(ticker_lse: str) -\> Optional\[UniverseCompany\]

-   save_refresh_log(log: RefreshLog) -\> None

-   get_last_refresh_log() -\> Optional\[RefreshLog\]

The PoC implementation is FirestoreUniverseProvider, writing to two new
Firestore collections: universe_companies and universe_refresh_log.

**2.2 Storage**

The original brief specified SQLite for development and PostgreSQL for
production. Both are replaced by Firestore throughout. Firestore is
already in place, already abstracted, already on the always-free tier,
and already the project's single source of truth for pipeline state. The
universe is a slowly-changing dataset queried quarterly; Firestore is
more than adequate for this workload.

Two new Firestore collections sit alongside the existing three
(announcements, signal_results, discovery_results):

-   universe_companies --- one document per company, keyed by
    ticker_lse. Full output schema from Section 8, plus tier,
    entity_type, and active flag. Overwritten on each quarterly full
    rebuild.

-   universe_refresh_log --- one document per refresh run, recording
    timestamp, tier 1 count, tier 2 count, data quality flag count,
    computed lower bound, and any pipeline errors. Supports health
    metrics and the DATA_QUALITY upgrade trigger.

**2.3 Integration with the Signal Pipeline**

The signal pipeline's universe.py currently holds a hardcoded list of
five companies. With this pipeline in place, pipeline.py should read the
active universe from Firestore via
UniverseStorageProviderBase.get_universe() at startup rather than
importing a static list. universe.py is retained as a fallback during
the transition.

The routing logic in pipeline.py (signal queue vs discovery queue) is
unchanged --- only the source of the universe list changes, from a
static file to a Firestore read.

**2.4 Scheduling and Alerting**

The original brief proposed APScheduler or cron as new infrastructure.
Both are already pinned for the signal pipeline scheduler (Step 14 of
the build sequence). The universe refresh jobs should run on the same
scheduler instance on the same VM.

The original brief proposed email or Slack webhook for alerting. The
project already has a working Telegram notification channel behind
NotificationProviderBase. Universe pipeline alerts (refresh completion,
failure, DATA_QUALITY threshold breach) go through the same channel.

**3. MoSCoW Requirements**

  ---------------------------- -------------- ---------------------------------
  **Requirement**              **Priority**   **Resolution**

  Universe identification ---  MUST           FTSE Russell PDFs + yfinance
  Tier 1 and Tier 2                           market cap filter

  Investment trust exclusion   MUST           Revenue filter (primary) + name
                                              pattern matching + yfinance
                                              sector (where available)

  Liquidity flag               MUST           Derived from yfinance
                                              averageVolume × currentPrice ---
                                              see Section 9

  Current price data           SHOULD         yfinance --- reliable for LSE/AIM

  Current fundamentals (market SHOULD         yfinance --- good coverage for
  cap, revenue, growth)                       Tier 2 band; partial for smaller
                                              Tier 1 names

  Sector classification        COULD          yfinance sector field where
                                              available; not relied upon for
                                              exclusion logic

  Historical price and         COULD          yfinance history() --- up to 5
  fundamentals                                years EOD, free
  ---------------------------- -------------- ---------------------------------

**4. Universe Definition**

**4.1 Tier 1 --- Core Universe**

Definition: Current members of the FTSE Small Cap index (FTSE Russell
index code: SMX), after exclusions.

Inclusions:

-   All LSE Main Market companies currently constituting the FTSE Small
    Cap index

Exclusions --- investment trusts identified by three complementary
filters applied in order:

-   yfinance sector / industry field where present --- financial vehicle
    classification

-   Company name pattern matching --- names containing "Trust", "Fund",
    "Income & Growth", "Investment Company" etc.

-   Null or zero revenue --- reliable catch-all for non-operational
    entities

Also excluded: any constituent where revenue is null or zero (data
quality backstop).

Approximate size: 250--270 companies after filtering. Update cadence:
quarterly, aligned with FTSE Russell index rebalancing (March, June,
September, December).

**4.2 Tier 2 --- Growth Trajectory Universe**

Definition: LSE Main Market and AIM-listed companies with market cap
between the FTSE Small Cap lower boundary and £1B, not already in Tier
1.

These companies sit in a relative information vacuum --- beyond small
cap index territory but below the £1B threshold at which institutional
coverage becomes dense. This is the highest-edge segment for
signal-based analysis.

Inclusions:

-   All active LSE Main Market and AIM-listed companies within the
    market cap band

-   REITs, SPACs, and royalty companies (included but flagged with
    category label --- see Section 5)

Exclusions:

-   All Tier 1 members (no double-counting)

-   Investment trusts (same three-filter approach as Tier 1)

-   Any company where revenue is null or zero

-   International companies with a secondary LSE quote but primary
    listing elsewhere --- filtered by requiring presence in FTSE
    All-Share or AIM All-Share constituent lists

Market cap band:

-   Lower bound: Dynamic --- set to the market cap of the smallest
    current Tier 1 member at each quarterly refresh. Prevents boundary
    drift between rebalances.

-   Upper bound: £1,000,000,000 (fixed)

Approximate size: 100--200 companies after filtering. Update cadence:
quarterly, aligned with Tier 1 refresh.

**5. Refresh Cadence**

The original brief proposed a single quarterly cadence. This has been
reviewed against the volatility characteristics of the underlying data.

**5.1 Tier 1 Cadence Analysis**

FTSE Small Cap index membership is the most stable layer. FTSE Russell
reviews the UK index series quarterly and uses buffer zones around
market cap boundaries to suppress unnecessary turnover. A typical
quarterly review produces a small number of additions and deletions
across the whole UK series. A fast-entry mechanism exists but applies
only to companies with investable market cap above £1B --- well outside
this universe. Quarterly refresh aligned to rebalancing dates is the
correct and sufficient cadence for Tier 1.

**5.2 Tier 2 Cadence Analysis**

AIM is in a structural contraction. Approximately 92 companies delisted
from AIM in the year to October 2024, a 62% increase on the prior year,
against approximately 10 new IPOs --- bringing total AIM listings to a
23-year low. Companies disappear from the AIM universe at a rate that a
quarterly refresh would not reliably capture. However, delistings tend
to be preceded by significant RNS activity (takeover announcements,
financial difficulties, suspension notices) which the signal pipeline
will surface regardless. This reduces the operational risk of a stale
universe between quarterly refreshes.

The LSE Main Market component of Tier 2 is more stable. Delistings tend
to be high-profile events generating significant advance notice via RNS.

**5.3 Adopted Cadence**

  -------------- ------------- ---------------------- -----------------------------
  **Job**        **Cadence**   **Trigger**            **Action**

  Full rebuild   Quarterly     March, June,           Download fresh PDFs, rebuild
                               September, December    both tiers from scratch,
                               rebalancing dates      re-apply all filters,
                                                      overwrite Firestore
                                                      universe_companies collection

  Boundary check Optional ---  Manual trigger or      Re-query yfinance market caps
                 between       lightweight scheduled  for Tier 2 names within 10%
                 rebalances    job                    of £1B upper boundary only.
                                                      Does not rebuild tiers.

  Failure        On any        Automatic              Alert via Telegram
  handling       pipeline                             (NotificationProviderBase).
                 error                                Retain previous universe
                                                      snapshot. Do not overwrite
                                                      with partial data.
  -------------- ------------- ---------------------- -----------------------------

A daily or weekly full refresh is not warranted. The FTSE Russell PDFs
themselves only reflect quarterly changes, and the yfinance rate
consumption across 400--600 tickers is not justified for a dataset with
low intra-quarter volatility at the index level.

**6. Data Sources**

**6.1 Source A --- FTSE Russell Constituent Downloads**

Purpose: Definitive universe of formally listed UK companies. Ground
truth for Tier 1 membership and starting point for Tier 2 candidate
pool.

Access method: HTTP GET --- returns a PDF file requiring table
extraction via pdfplumber.

  ------------------- ----------- ---------------------------------------
  **Index**           **Code**    **Purpose**

  FTSE Small Cap      SMX         Tier 1 membership

  FTSE All-Share      ASX         All Main Market companies --- Tier 2
                                  candidates

  FTSE AIM All-Share  AXX         All AIM companies --- Tier 2 candidates
  ------------------- ----------- ---------------------------------------

Base URL pattern:

> https://research.ftserussell.com/analytics/factsheets/Home/DownloadConstituentsWeights/?indexdetails={CODE}

What the PDFs contain: company name, ticker symbol, index weight. Market
cap is not directly provided --- derived from yfinance.

Limitations:

-   PDF format requires extraction pipeline --- not directly
    machine-readable

-   Current constituents only --- no historical membership records

-   Quarterly cadence --- intra-quarter additions and deletions not
    captured until next rebalance

-   PDF structure may change without notice, breaking the extraction
    script

**6.2 Source B --- yfinance (via MarketDataProviderBase /
YFinanceProvider)**

Purpose: Market cap filtering for Tier 2 construction; current
fundamentals; liquidity flag calculation; current and historical price
data.

Important: yfinance is accessed through the MarketDataProviderBase
abstraction (Abstraction 6). Pipeline logic never calls yfinance
directly --- it calls the abstract interface. YFinanceProvider is the
concrete implementation. This makes the enrichment source swappable
without touching pipeline logic.

How it works: yfinance scrapes Yahoo Finance's internal, undocumented
JSON endpoints. It is not an official API. A call such as
yf.Ticker(\"SDY.L\").info results in an HTTP request to a Yahoo Finance
quoteSummary endpoint. This approach is widely used and has been stable
for years, but carries important caveats documented in Section 6.4.

Ticker format for UK stocks: Yahoo Finance uses the .L suffix for
LSE/AIM tickers (e.g. SDY.L for Speedy Hire).

  ---------------------- ---------------------------------- ----------------------------
  **Data point**         **yfinance field**                 **Used for**

  Market capitalisation  info\[\'marketCap\'\]              Tier 2 band filter

  Total revenue (TTM)    info\[\'totalRevenue\'\]           Revenue exclusion filter

  Revenue growth (YoY)   info\[\'revenueGrowth\'\]          Fundamentals output

  Sector                 info\[\'sector\'\]                 Investment trust detection
                                                            (where available)

  Industry               info\[\'industry\'\]               Investment trust detection
                                                            (where available)

  Average daily volume   info\[\'averageVolume\'\]          Liquidity flag calculation
  (30d)                                                     

  Current price          info\[\'currentPrice\'\]           Liquidity flag calculation;
                                                            price signal

  52-week high/low       info\[\'fiftyTwoWeekHigh/Low\'\]   Price context

  P/E ratio              info\[\'trailingPE\'\]             Fundamentals output

  EPS                    info\[\'trailingEps\'\]            Fundamentals output

  EOD price history      ticker.history(period=\'5y\')      Historical signal; "already
                                                            priced in" check
  ---------------------- ---------------------------------- ----------------------------

**6.3 Source C --- stockanalysis.com (Sanity Check Only)**

Purpose: Manual cross-validation of universe completeness. Not used in
the automated pipeline. Useful for spot-checking market cap values
against yfinance output and verifying that the pipeline has not missed
significant names.

**6.4 Source Selection Rationale**

yfinance was selected over EODHD (\~£39/month on annual plan) as the
primary enrichment source. The universe is a slowly-changing candidate
pool queried four times per year at the quarterly rebalance --- paying a
monthly subscription for a source queried at quarterly cadence is not
proportionate. The investment strategy is growth-oriented and
long-horizon; survivorship bias in constituent history is acceptable.

Trade-offs accepted:

  ------------------------------- ---------------------------------------
  **Trade-off**                   **Accepted because**

  Not an official API --- scrapes Widely used, community-maintained,
  undocumented endpoints          historically stable for years

  Can break without warning       Mitigated by retaining previous
                                  snapshot; community typically patches
                                  within days; quarterly cadence gives
                                  recovery time

  Technically against Yahoo ToS   Personal/research use is widely
  for commercial use              tolerated; to be reviewed if project
                                  becomes commercial

  No SLA or support channel       Quarterly cadence gives recovery time;
                                  EODHD upgrade path is pre-specified

  Inconsistent sector/industry    Sector is a COULD; exclusion logic does
  fields for UK small caps        not depend on it
  ------------------------------- ---------------------------------------

Upgrade trigger: if more than 10% of universe records are flagged
DATA_QUALITY after two consecutive quarterly refreshes, adopt EODHD
Option A. See Section 12.

**7. Entity Classification and Flagging**

  ------------------ ----------------------------- ------------- -------------------
  **Label**          **Definition**                **Tier 1**    **Tier 2**

  OPERATING          Operating company with        Included      Included
                     confirmed revenue                           

  INVESTMENT_TRUST   Closed-ended fund ---         Excluded      Excluded
                     identified by sector, name                  
                     pattern, or zero revenue                    

  REIT               Real estate investment trust  Excluded      Flagged

  SPAC               Shell acquisition company --- Excluded      Flagged
                     no revenue, recently listed                 

  ROYALTY            Royalty or streaming company  Excluded      Flagged

  NO_REVENUE         Revenue null or zero --- not  Excluded      Excluded
                     a REIT/SPAC/ROYALTY                         

  DATA_QUALITY       yfinance returned incomplete  Quarantined   Quarantined
                     or malformed data                           
  ------------------ ----------------------------- ------------- -------------------

Flagged Tier 2 entries (REIT, SPAC, ROYALTY) are retained in the
universe output but treated as lower-priority candidates by the
downstream analysis engine unless a specific thesis applies.

Investment trust name pattern matching --- keyword list (expand as
needed):

> TRUST_KEYWORDS = \[\
> \"trust\", \"fund\", \"income & growth\", \"investment company\",\
> \"investment trust\", \"capital & income\", \"equity income\",\
> \"growth & income\", \"managed income\", \"global income\"\
> \]\
> \# Match case-insensitively against company_name.lower()

**8. Liquidity Flag**

Every universe member carries a liquidity flag, derived during the
yfinance enrichment pass at no additional API cost.

**8.1 Derived Metric**

Average Daily Traded Value (ADTV) in GBP:

> adtv_gbp = averageVolume × currentPrice

ADTV is preferred over raw volume because it normalises across companies
with very different share prices. A stock trading 1 million shares at 5p
is far less liquid than one trading 100,000 shares at £5.

**8.2 Classification**

  ------------ ----------------- -----------------------------------------
  **Label**    **ADTV (GBP)**    **Interpretation**

  LIQUID       \> £500,000       Retail position sizing is straightforward

  MODERATE     £100,000 --       Executable with care; size positions
               £500,000          accordingly

  ILLIQUID     \< £100,000       Meaningful execution risk; treat signals
                                 as indicative only
  ------------ ----------------- -----------------------------------------

Thresholds are starting points and may be calibrated once the
distribution across the actual universe is observed. At £100k ADTV, a
£5,000 position represents 5% of a day's volume --- market-moving
territory for a retail investor seeking a clean entry.

**8.3 Design Principle**

The liquidity flag informs the execution decision, not the signal
decision. An illiquid stock with a strong signal is still worth
surfacing --- the flag tells the investor whether acting on it is
practical. This is consistent with the anti-bias principle: the pipeline
surfaces everything; the human decides.

The flag should appear in the Streamlit signals view alongside the
existing action badge, so it is immediately visible when evaluating a
signal.

**9. Pipeline Logic**

**9.1 Tier 1 Construction**

> 1\. HTTP GET FTSE Russell SMX factsheet → save as PDF\
> 2. Extract constituent table using pdfplumber → list of (ticker,
> company_name)\
> 3. For each ticker:\
> a. Convert to Yahoo format: append \".L\" suffix\
> b. Call MarketDataProvider.get_info() → retrieve sector, industry,
> revenue, market_cap\
> c. Apply investment trust filter (in order):\
> - If sector/industry indicates financial vehicle → INVESTMENT_TRUST →
> EXCLUDE\
> - If company_name matches trust keyword list → INVESTMENT_TRUST →
> EXCLUDE\
> - If revenue is null or zero → NO_REVENUE → EXCLUDE\
> (log as DATA_QUALITY if other fields present but revenue missing)\
> d. Calculate adtv_gbp = averageVolume × currentPrice → assign
> liquidity_flag\
> e. Else → label OPERATING → add to Tier 1\
> 4. Store minimum market_cap from Tier 1 as dynamic lower bound
> variable\
> 5. Output: Tier 1 universe with labels, metadata, and liquidity flags

**9.2 Tier 2 Construction**

> 1\. HTTP GET FTSE Russell ASX factsheet (FTSE All-Share) → extract
> tickers\
> 2. HTTP GET FTSE Russell AXX factsheet (FTSE AIM All-Share) → extract
> tickers\
> 3. Merge ASX and AXX ticker lists → deduplicate\
> 4. Remove all Tier 1 tickers\
> 5. For each remaining ticker:\
> a. Convert to Yahoo format: append \".L\" suffix\
> b. Call MarketDataProvider.get_info() → retrieve market_cap, revenue,
> sector, industry,\
> averageVolume, currentPrice\
> c. If market_cap is null → label DATA_QUALITY → skip\
> d. If market_cap \< Tier 1 lower bound → EXCLUDE (too small)\
> e. If market_cap \> £1,000,000,000 → EXCLUDE (too large)\
> f. Apply investment trust filter (same three-step logic as Tier 1) →
> EXCLUDE if matched\
> g. If revenue is null or zero:\
> - If listed \< 2 years → label SPAC → retain\
> - Else → NO_REVENUE → EXCLUDE\
> h. If industry/name indicates REIT → label REIT → retain\
> i. If industry/name indicates royalty company → label ROYALTY →
> retain\
> j. Calculate adtv_gbp = averageVolume × currentPrice → assign
> liquidity_flag\
> k. Else → label OPERATING → add to Tier 2\
> 6. Output: Tier 2 universe with labels, metadata, and liquidity flags

**9.3 Persistence**

On successful completion of both tiers,
FirestoreUniverseProvider.save_universe() is called once with the
combined list. This overwrites the universe_companies collection. A
refresh log entry is written to universe_refresh_log recording tier
counts, DATA_QUALITY count, computed lower bound, and run timestamp.

On any pipeline failure, the previous universe snapshot is retained and
an alert is sent via NotificationProviderBase. The universe_companies
collection is never overwritten with partial data.

**10. Output Schema**

  --------------------- ------------ -------------- --------------------------
  **Field**             **Type**     **Source**     **Notes**

  ticker_lse            string       FTSE Russell   Native LSE ticker (e.g.
                                     PDF            SDY)

  ticker_yahoo          string       Pipeline       Yahoo Finance format (e.g.
                                                    SDY.L)

  isin                  string       yfinance       info\[\'isin\'\] --- may
                                                    be null for some names

  company_name          string       FTSE Russell   Legal name from index
                                     PDF            factsheet

  tier                  integer (1   Pipeline logic Universe tier assignment
                        or 2)                       

  entity_type           string       Pipeline logic OPERATING, REIT, SPAC,
                                                    ROYALTY

  market_cap_gbp        float        yfinance       At time of last refresh

  revenue_ttm           float        yfinance       Trailing twelve months

  revenue_growth_yoy    float        yfinance       Year-on-year %

  sector                string       yfinance       May be null --- COULD
                                                    field

  industry              string       yfinance       May be null --- COULD
                                                    field

  listing_exchange      string       Source PDF     LSE_MAIN or AIM

  adtv_gbp              float        yfinance       averageVolume ×
                                                    currentPrice at time of
                                                    refresh

  liquidity_flag        string       Pipeline       LIQUID, MODERATE, ILLIQUID

  last_price            float        yfinance       Most recent EOD close

  last_price_date       date         yfinance       Derived from last history
                                                    row

  fifty_two_week_high   float        yfinance       fiftyTwoWeekHigh

  fifty_two_week_low    float        yfinance       fiftyTwoWeekLow

  universe_added_date   date         Pipeline       When first included

  last_refreshed        datetime     Pipeline       Timestamp of last pipeline
                                                    run
  --------------------- ------------ -------------- --------------------------

**11. Pre-Build Verification Tests**

All tests use free tools --- no subscriptions required. Run all tests
before writing any pipeline code.

**Test 1 --- FTSE Russell PDF Downloads**

> curl -L -o ftse_smx.pdf \\\
> \"https://research.ftserussell.com/analytics/factsheets/Home/\
> DownloadConstituentsWeights/?indexdetails=SMX\" \\\
> -w \"SMX → HTTP %{http_code} \| %{content_type} \| %{size_download}
> bytes\\n\"\
> \
> curl -L -o ftse_asx.pdf \\\
> \"https://research.ftserussell.com/analytics/factsheets/Home/\
> DownloadConstituentsWeights/?indexdetails=ASX\" \\\
> -w \"ASX → HTTP %{http_code} \| %{content_type} \| %{size_download}
> bytes\\n\"\
> \
> curl -L -o ftse_axx.pdf \\\
> \"https://research.ftserussell.com/analytics/factsheets/Home/\
> DownloadConstituentsWeights/?indexdetails=AXX\" \\\
> -w \"AXX → HTTP %{http_code} \| %{content_type} \| %{size_download}
> bytes\\n\"

Then test extraction:

> import pdfplumber\
> \
> for filename in \[\"ftse_smx.pdf\", \"ftse_asx.pdf\",
> \"ftse_axx.pdf\"\]:\
> with pdfplumber.open(filename) as pdf:\
> rows = \[\]\
> for page in pdf.pages:\
> table = page.extract_table()\
> if table:\
> rows.extend(table)\
> print(f\"{filename}: {len(rows)} rows extracted --- sample:
> {rows\[1:3\]}\")

Expected outcome: Each PDF yields 200+ rows with visible ticker symbols
and company names.

**Test 2 --- yfinance: Market Cap and Revenue for a Known Tier 1
Company**

> import yfinance as yf\
> \
> \# SDY = Speedy Hire --- known FTSE Small Cap operating company\
> ticker = yf.Ticker(\"SDY.L\")\
> info = ticker.info\
> \
> print(f\"Name: {info.get(\'longName\')}\")\
> print(f\"Market cap: {info.get(\'marketCap\')}\")\
> print(f\"Revenue: {info.get(\'totalRevenue\')}\")\
> print(f\"Sector: {info.get(\'sector\')}\")\
> print(f\"Industry: {info.get(\'industry\')}\")\
> print(f\"Avg volume: {info.get(\'averageVolume\')}\")\
> print(f\"Current price:{info.get(\'currentPrice\')}\")

Expected outcome: Non-null market cap, revenue, averageVolume, and
currentPrice. Sector/industry may or may not be populated --- either is
acceptable.

**Test 3 --- yfinance: Liquidity Flag Calculation**

> import yfinance as yf\
> \
> def liquidity_flag(avg_volume, price):\
> if avg_volume is None or price is None:\
> return \"UNKNOWN\"\
> adtv = avg_volume \* price\
> if adtv \> 500000:\
> return \"LIQUID\"\
> elif adtv \> 100000:\
> return \"MODERATE\"\
> else:\
> return \"ILLIQUID\"\
> \
> test_tickers = \[\"SDY.L\", \"JET2.L\", \"ADIG.L\"\]\
> for t in test_tickers:\
> info = yf.Ticker(t).info\
> vol = info.get(\'averageVolume\')\
> price = info.get(\'currentPrice\')\
> adtv = (vol \* price) if vol and price else None\
> flag = liquidity_flag(vol, price)\
> print(f\"{t}: ADTV=£{adtv:,.0f} → {flag}\" if adtv else f\"{t}: data
> unavailable\")

Expected outcome: SDY and JET2 should return non-null ADTV with a
classifiable flag. ADIG (investment trust) may return null revenue
confirming exclusion logic.

**Test 4 --- yfinance: Investment Trust Detection**

> import yfinance as yf\
> \
> \# ADIG = ABRDN Diversified Income and Growth --- known investment
> trust\
> ticker = yf.Ticker(\"ADIG.L\")\
> info = ticker.info\
> \
> print(f\"Name: {info.get(\'longName\')}\")\
> print(f\"Revenue: {info.get(\'totalRevenue\')}\")\
> print(f\"Sector: {info.get(\'sector\')}\")\
> print(f\"Industry: {info.get(\'industry\')}\")

Expected outcome: Revenue is null or zero (primary exclusion trigger).
Name contains \'Income & Growth\' (name pattern trigger). All three
exclusion methods should independently identify this as an investment
trust.

**Test 5 --- yfinance: Rate Limiting at Scale**

> import yfinance as yf\
> import time\
> \
> test_tickers = \[\
> \"SDY.L\", \"JET2.L\", \"CVSG.L\", \"VLX.L\", \"RNWH.L\",\
> \"CRW.L\", \"DATA.L\", \"FEVR.L\", \"SRC.L\", \"BUR.L\",\
> \"HCM.L\", \"ROSE.L\", \"YCA.L\", \"GRP.L\", \"ADIG.L\",\
> \"RWS.L\", \"BOY.L\", \"NCC.L\", \"GGP.L\", \"UPR.L\"\
> \]\
> \
> results = \[\]\
> for t in test_tickers:\
> try:\
> info = yf.Ticker(t).info\
> results.append({\
> \"ticker\": t,\
> \"market_cap\": info.get(\"marketCap\"),\
> \"revenue\": info.get(\"totalRevenue\"),\
> \"adtv\": (info.get(\"averageVolume\") or 0) \*
> (info.get(\"currentPrice\") or 0),\
> \"ok\": True\
> })\
> except Exception as e:\
> results.append({\"ticker\": t, \"ok\": False, \"error\": str(e)})\
> time.sleep(0.5)\
> \
> success = sum(1 for r in results if r\[\"ok\"\])\
> print(f\"Success: {success}/{len(test_tickers)}\")\
> print(f\"Failures: {\[r\[\'ticker\'\] for r in results if not
> r\[\'ok\'\]\]}\")

Expected outcome: 18--20 successes. Any consistent failures indicate
incorrect ticker format or a coverage gap.

**12. Risks and Mitigations**

  ---------------------- -------------- ---------------------------------------
  **Risk**               **Severity**   **Mitigation**

  yfinance breaks due to High           Pin yfinance version; monitor yfinance
  Yahoo Finance internal                GitHub issues; retain previous universe
  endpoint change                       snapshot as fallback on failed runs

  Yahoo Finance Terms of Low-Medium     Acceptable for personal/research use;
  Service exposure                      review if project becomes commercial

  Incomplete yfinance    Medium         Flag as DATA_QUALITY; track
  coverage for smaller                  null-revenue record count per run as
  Tier 1 names                          pipeline health metric

  FTSE Russell PDF       Medium         Quarterly manual inspection before
  structure changes                     automated extraction; alert on row
                                        count drop \>10% vs previous run

  International          Medium         Only include tickers present in FTSE
  secondary-listed                      All-Share or AIM All-Share PDFs
  companies entering                    
  Tier 2                                

  Investment trust slips Low            Quarterly manual spot-check of 10--20
  through all three                     random universe members
  exclusion filters                     

  AIM company delistings Low-Medium     Signal pipeline will surface RNS
  between quarterly                     activity preceding delistings; universe
  refreshes                             staleness risk is mitigated by advance
                                        notice in the news flow

  yfinance rate          Low            0.5s sleep between requests; batch
  throttling at scale                   price history pulls using yf.download()
  (400--600 tickers)                    

  Dynamic lower bound    Low            Log computed lower bound on each run;
  calculation error                     alert if it deviates \>20% from
                                        previous run
  ---------------------- -------------- ---------------------------------------

**13. Technology Stack**

  ----------------- ----------------------------- ----------------------------
  **Component**     **Tool**                      **Notes**

  HTTP downloads    requests (Python)             PDF downloads from FTSE
                                                  Russell

  PDF extraction    pdfplumber (Python)           Best-in-class for structured
                                                  PDF tables

  Market data       MarketDataProviderBase /      Abstracted --- yfinance is
                    YFinanceProvider              the PoC implementation

  Universe storage  UniverseStorageProviderBase / Abstracted --- Firestore
                    FirestoreUniverseProvider     replaces SQLite/PostgreSQL
                                                  from earlier brief versions

  Data manipulation pandas (Python)               DataFrame filtering,
                                                  merging, deduplication

  Scheduling        APScheduler or cron (shared   Same scheduler instance as
                    with signal pipeline)         Step 14 of the build
                                                  sequence

  Alerting          NotificationProviderBase /    Shared with signal pipeline
                    existing Telegram channel     --- no new infrastructure
                                                  required

  Platform          GCP e2-micro, always-free     Same VM as signal pipeline
                    tier                          backend
  ----------------- ----------------------------- ----------------------------

Installation (VM):

> pip install yfinance pdfplumber pandas requests

All other dependencies (google-cloud-firestore, python-dotenv,
python-telegram-bot) are already installed on the VM.

**14. Upgrade Path**

If the free architecture proves insufficient --- specifically if
yfinance coverage gaps become material or reliability issues accumulate
--- the recommended upgrade path is:

**Option A --- EODHD Fundamentals Tier (\~£39/month on annual plan)**

Replaces YFinanceProvider entirely behind the MarketDataProviderBase
interface. Direct LSE data licence, structured API, consistent coverage,
ICB sector codes. The pipeline and universe storage layers are
unaffected --- only the concrete MarketDataProvider implementation
changes.

**Option B --- Hybrid (still £0/month)**

Retain YFinanceProvider for price data. Add EODHD free tier (20
calls/day, no card required) as a secondary implementation behind
MarketDataProviderBase, called only for tickers where yfinance returns
null fields. Requires managing two source implementations but stays
free.

Decision trigger for upgrading: if more than 10% of universe records are
flagged DATA_QUALITY after two consecutive quarterly refreshes, Option A
should be adopted.

**15. Pinned for Later**

+-----------------------------------------------------------------------+
| **📌 Boundary Check Job**                                             |
|                                                                       |
| An optional lightweight job to re-query yfinance market caps for Tier |
| 2 names within 10% of the £1B upper boundary between quarterly full   |
| rebuilds. Implement after the quarterly refresh is confirmed stable.  |
| Uses the same APScheduler instance as the signal pipeline.            |
+-----------------------------------------------------------------------+

+-----------------------------------------------------------------------+
| **📌 Streamlit Universe Tab**                                         |
|                                                                       |
| A universe management tab in app.py showing tier counts, liquidity    |
| distribution, DATA_QUALITY flagged companies, and the last refresh    |
| log. Allows manual trigger of the boundary check job. Implement after |
| the pipeline is confirmed working end-to-end.                         |
+-----------------------------------------------------------------------+

+-----------------------------------------------------------------------+
| **📌 Semantic Deduplication for Universe**                            |
|                                                                       |
| If the same company appears in both FTSE All-Share and AIM All-Share  |
| PDFs (edge case), deduplication is handled by the                     |
| merge-and-deduplicate step in Tier 2 construction. No additional work |
| required at this stage.                                               |
+-----------------------------------------------------------------------+

+-----------------------------------------------------------------------+
| **📌 Liquidity Threshold Calibration**                                |
|                                                                       |
| The LIQUID / MODERATE / ILLIQUID thresholds (£500k and £100k ADTV)    |
| are starting points. After the first full universe refresh, plot the  |
| ADTV distribution across all universe members and adjust thresholds   |
| if the classification is not meaningfully discriminating.             |
+-----------------------------------------------------------------------+

**16. Key Design Principles --- Do Not Compromise**

-   Anti-bias: no implicit preference learning. All adaptation is
    explicit, time-bounded, and user-initiated.

-   Auditability: the suppression log records everything filtered out.
    The user can always see what was suppressed and why.

-   Universe discipline: the monitored universe is explicit and
    deliberate. Discovery is a separate queue. Admission is a human
    decision.

-   Abstraction integrity: all components communicate through their base
    class interfaces. Concrete providers are instantiated at the entry
    point and injected. No shortcuts. This now includes
    MarketDataProviderBase (Abstraction 6) and
    UniverseStorageProviderBase (Abstraction 7).

-   Confidence before investment: the system earns its paid data feeds
    by demonstrating end-to-end functionality first.

-   Liquidity transparency: every universe member carries a liquidity
    flag. Signals on illiquid stocks are surfaced, not suppressed. The
    human decides whether to act.

*End of brief. Next step: run pre-build verification tests (Section 11)
before writing any pipeline code.*
