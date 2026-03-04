# Layer 1 — Platform Assessment Tools

## Purpose

Layer 1 evaluates whether a company is a sufficiently solid platform to trade safely, independently of the quality of the director buying signal. It asks: "Even if the signal does not play out, can I get out of this position quickly without being harmed by illiquidity or wide spread?"

Layer 1 runs in parallel with Layers 2 and 3. It has no visibility of their findings.

## Execution Environment

**All Layer 1 tool calls execute on the local machine (frontend).**

yfinance data retrieval is sensitive to IP-based rate limiting and blocking. The local machine's residential IP address is required. Index snapshot data is read from Firestore and is available to the backend, but all yfinance calls must originate locally.

---

## Tool Definitions

### `get_company_profile`

Returns basic company identity and classification from Firestore.

```
Input:
  ticker: string

Returns:
  company_name:       string
  ticker:             string
  market_cap_gbp:     float     ← may be stale, flag if > 90 days old
  market_cap_date:    date      ← date market cap was last updated
  market_cap_stale:   boolean   ← true if > 90 days old
  index_membership:   string    ← "AIM" | "MAIN_MARKET"
  sector:             string    ← from universe record
  universe_added_at:  date

Source: Firestore universe collection
Execution: backend or frontend (Firestore read, no IP sensitivity)
Status: EXISTS (market cap staleness flag is new)
Notes:
  - Market cap used at order-of-magnitude level only
  - Stale market cap does not block analysis but must be
    flagged in Layer 1 output
```

---

### `get_price_history`

Returns price history across four time windows using yfinance.

```
Input:
  ticker:   string    ← LSE format e.g. "FGEN.L"
                        AIM format e.g. "ECR.L"
  windows:  list      ← ["1w", "1m", "3m", "1y"]

Returns:
  current_price:      float
  current_volume:     integer
  per_window (for each of 1w, 1m, 3m, 1y):
    start_price:      float
    end_price:        float
    change_pct:       float
    high:             float
    low:              float
    avg_daily_volume: integer
  data_retrieved_at:  timestamp
  data_quality_flag:  string    ← "good" | "sparse" | "unavailable"
                                   AIM stocks may have gaps

Source: yfinance
Execution: local machine only
Status: NEEDS BUILDING
Notes:
  - LSE/AIM tickers require ".L" suffix in yfinance
    e.g. ticker "FGEN" becomes "FGEN.L"
  - Rate limiting: minimum 2 second delay between yfinance calls
  - Handle missing data gracefully — do not fail the pipeline
    if data is sparse; set data_quality_flag and continue
  - If current_price unavailable, attempt to use most recent
    closing price with a staleness note
```

---

### `get_volatility_metrics`

Returns volatility and liquidity indicators using yfinance.

```
Input:
  ticker:       string    ← LSE format e.g. "FGEN.L"
  period_days:  integer   ← default 30

Returns:
  volatility_30d:         float   ← annualised standard deviation
                                     of daily returns
  avg_daily_volume_30d:   integer
  volume_trend:           string  ← "increasing" | "decreasing" |
                                     "stable"
                                     based on comparing 10d vs 30d
                                     average volume
  liquidity_flag:         string  ← "high" | "medium" | "low" |
                                     "very_low"
                                     derived from avg daily volume
                                     relative to market cap
                                     (see thresholds below)
  bid_ask_spread:         float   ← if available; often not in
                                     yfinance — set null if unavailable
  data_quality_flag:      string  ← "good" | "sparse" | "unavailable"

Source: yfinance
Execution: local machine only
Status: NEEDS BUILDING
Notes:
  - Bid/ask spread: not reliably available via yfinance.
    Set null and flag as unresolved. Real spread data 
    requires paid data source (pinned for later).
  - Liquidity flag thresholds (provisional — refine with 
    real data during PoC):
      very_low:  avg daily volume < 10,000 shares
                 OR avg daily turnover < £5,000
      low:       avg daily volume 10,000 - 100,000
      medium:    avg daily volume 100,000 - 1,000,000
      high:      avg daily volume > 1,000,000
  - These thresholds are order-of-magnitude guidance only.
    Formal threshold definition is pinned for later.
```

---

### `get_index_snapshot`

Returns the most recent daily index snapshot from Firestore for relative performance benchmarking.

```
Input:
  index_membership:   string    ← "AIM" | "MAIN_MARKET"
  market_cap_gbp:     float     ← used to select appropriate index

Returns:
  index_name:         string    ← "AIM_ALL_SHARE" | 
                                   "FTSE_SMALL_CAP" | 
                                   "FTSE_250"
  index_value:        float
  week_52_high:       float
  week_52_low:        float
  change_1d_pct:      float
  scrape_date:        date
  snapshot_age_days:  integer   ← days since last snapshot
                                   flag if > 2 (weekend gap acceptable)

Source: Firestore index_snapshots collection
        (written daily by local machine LSEG scraper)
Execution: backend or frontend (Firestore read)
Status: NEEDS BUILDING (both scraper and this tool)
Notes:
  - Index selection logic:
      AIM company               → AIM_ALL_SHARE
      Main market, cap < £500m  → FTSE_SMALL_CAP
      Main market, cap £500m+   → FTSE_250
  - If today's snapshot unavailable, use most recent
    available (index values change slowly enough that
    1-2 day old data is acceptable)
  - If no snapshot available at all, flag as unavailable
    and proceed — do not block the analysis pipeline
```

---

### `get_relative_performance`

Calculates stock performance relative to market benchmark across time windows. Derived from `get_price_history` and `get_index_snapshot` outputs — this may be a calculation layer rather than a separate tool call.

```
Input:
  price_history:      object    ← output of get_price_history
  index_snapshot:     object    ← output of get_index_snapshot
  windows:            list      ← ["1w", "1m", "3m"]

Returns:
  per_window:
    stock_change_pct:     float
    index_change_pct:     float  ← note: index snapshot provides
                                   current value and 52wk range
                                   only — intrawindow index change
                                   requires historical index data
                                   (see note below)
    relative_performance: float  ← stock_change_pct minus 
                                   index_change_pct
                                   null if index history unavailable
  position_in_52wk_range: float ← current price as % of 52wk range
                                   0% = at 52wk low
                                   100% = at 52wk high

Source: derived calculation
Execution: local machine
Status: NEEDS BUILDING
Notes:
  - IMPORTANT LIMITATION: LSEG index snapshots provide current
    value and 52wk range only, not historical daily values.
    True intrawindow relative performance (e.g. "stock vs index
    over last month") requires historical index data.
    Options for PoC:
      Option A: Use yfinance to retrieve index history
                (^FTAI for AIM, ^FTSC for FTSE Small Cap)
                alongside stock history — preferred approach
      Option B: Use 52wk range position as proxy for
                relative performance — simpler but less precise
    Recommend Option A — verify yfinance index ticker 
    availability before implementing.
  - position_in_52wk_range is always calculable from 
    get_price_history and provides useful context regardless.
```

---

## Layer 1 Agent Output Structure

The Layer 1 agent synthesises all tool outputs into this structure, passed to the Synthesis agent:

```
platform_risk_rating:     string  ← "low" | "medium" | "high" | 
                                     "very_high"
market_cap:               float   ← with staleness flag
market_cap_stale:         boolean
index_membership:         string

price_momentum:
  assessment:             string  ← "positive" | "neutral" | 
                                     "negative" | "mixed"
  evidence:               string  ← factual description of 
                                     price changes across windows

volatility_assessment:
  level:                  string  ← "low" | "moderate" | "high" |
                                     "very_high"
  liquidity_flag:         string
  evidence:               string

relative_performance:
  vs_market:              string  ← factual description
  position_in_52wk_range: float
  data_available:         boolean

data_quality:
  overall:                string  ← "good" | "partial" | "poor"
  flags:                  array   ← list of specific data issues

inference_notes:          string  ← explicit statement of what
                                     was inferred vs retrieved
```

---

## Data Source Status Summary

```
Tool                        Source          Status
─────────────────────────────────────────────────────────────
get_company_profile         Firestore       EXISTS
get_price_history           yfinance        NEEDS BUILDING
get_volatility_metrics      yfinance        NEEDS BUILDING
get_index_snapshot          Firestore       NEEDS BUILDING
                            (+ LSEG scraper)
get_relative_performance    derived         NEEDS BUILDING
```

---

## Unresolved Items (Pinned)

- **Bid/ask spread** — not available via yfinance. Real-time spread requires paid data source.
- **Liquidity thresholds** — provisional values defined above. Formal calibration deferred to post-PoC.
- **Sector-level benchmarking** — deferred. Market-level benchmarking (AIM vs main market) implemented instead.
- **yfinance index tickers** — verify ^FTAI (AIM All-Share) and ^FTSC (FTSE Small Cap) availability before implementing `get_relative_performance` Option A.
- **Market cap refresh** — universe market cap data decays. Periodic realignment deferred but flagged.
