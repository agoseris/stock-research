# Build Order and Acceptance Criteria

## Approach

Build and test one tool at a time. Each Claude Code session 
covers exactly one tool. Do not proceed to the next tool until 
all acceptance criteria for the current tool are met and 
signed off.

This approach:
- Keeps each session within context window limits
- Makes failures easy to isolate and fix
- Produces a verified, reliable foundation before 
  anything depends on it

## Session Template

Opening message for each Claude Code session:

```
Read docs/design/director_lens_overview.md and 
docs/design/[relevant_spec_file].md before starting.
We are building and testing [tool name] only.
No other components in this session.
Acceptance criteria are in 
docs/design/build_order_and_acceptance_criteria.md.
```

Exit condition for each session:
All acceptance criteria checked and confirmed by the investor 
before closing. No exceptions.

---

## Build Order

```
 1. director_name_normalisation utility
 2. get_company_profile
 3. get_company_ch_filings
 4. get_index_snapshot + daily LSEG scraper
 5. get_price_history
 6. get_volatility_metrics
 7. get_relative_performance
 8. get_director_transaction_history
 9. get_company_insider_activity
10. get_price_movement_context
11. get_company_news_history
12. get_director_companies_house_profile
13. get_index_context_for_freshness
```

Spec file reference per tool:
```
Tools 1:        utilities/director_name_normalisation.md
Tools 2-4, 7:   layer1_tools.md
Tools 8-9, 12:  layer2_tools.md
Tools 10-11:    layer3_tools.md
Tool 13:        layer3_tools.md + layer1_tools.md
```

---

## Acceptance Criteria

---

### 1. `director_name_normalisation` utility

Spec: `utilities/director_name_normalisation.md`

```
□ normalise_director_name() handles all formats from the 
  three real filing examples without error:
    "Stephanie Coxon"   → "stephanie coxon"        confidence: high
    "James S. Metcalf"  → "james metcalf"           confidence: medium
    "J. Low"            → "j low"  abbreviated:true confidence: medium
    "Jim Low"           → "jim low"                 confidence: high
□ names_match() returns correct result for:
    exact match    → method: "exact",        match: true
    abbreviated    → method: "abbreviated",  match: true
    fuzzy match    → method: "fuzzy",        match: true
    no match       → method: "no_match",     match: false
□ All test cases defined in spec pass without modification
□ rapidfuzz installed and importable
□ Module importable from utils/ without path errors
□ Both director_name_raw and director_name_normalised 
  fields written correctly in a test pdmr_transaction document
```

---

### 2. `get_company_profile`

Spec: `layer1_tools.md`

```
□ Returns correct data for at least 3 known universe tickers
□ market_cap_stale correctly set to true for any ticker 
  whose market_cap_date is > 90 days ago
□ market_cap_stale correctly set to false for recently 
  updated ticker
□ Returns graceful result (not exception) for unknown ticker
□ index_membership correctly returned as "AIM" or 
  "MAIN_MARKET" for test tickers
□ Firestore connection confirmed working from both 
  local machine and GCP backend
```

---

### 3. `get_company_ch_filings`

Spec: `layer2_tools.md`

```
□ Returns filings for at least 2 known tickers with 
  Companies House data in Firestore
□ director_changes correctly extracted from headlines:
    "Director Appointed: appoint-person..." → change_type: "appointed"
    "Director Resignation: terminate-person..." → change_type: "resigned"
□ board_stability correctly classified:
    "stable" for ticker with no changes in 90 days
    "active_change" for ticker with recent appointment 
    or resignation
□ Returns data_found = false gracefully for ticker 
  with no CH data
□ days_lookback filter working — results outside window 
  not returned
□ filing_count accurate against manual Firestore count
```

---

### 4. `get_index_snapshot` + daily LSEG scraper

Spec: `layer1_tools.md`

```
□ Scraper successfully retrieves all three index pages:
    AIM All-Share
    FTSE Small Cap
    FTSE 250
□ index_value, week_52_high, week_52_low populated 
  for all three indices
□ Documents written to Firestore with correct 
  document_id format: "{INDEX_NAME}_{YYYY-MM-DD}"
□ get_index_snapshot returns correct index for:
    AIM company           → AIM_ALL_SHARE
    Main market, < £500m  → FTSE_SMALL_CAP
    Main market, > £500m  → FTSE_250
□ Fallback to most recent available snapshot works 
  when today's snapshot unavailable
  (test by querying before scraper has run today)
□ snapshot_age_days correctly calculated
□ Scraper scheduled as daily job on local machine
□ Scraper confirmed to run without manual intervention
```

---

### 5. `get_price_history`

Spec: `layer1_tools.md`

```
□ Returns data for at least 3 tickers:
    one main market liquid stock
    one AIM stock
    one known thinly traded AIM stock
□ ".L" suffix handling confirmed:
    ticker "FGEN" correctly becomes "FGEN.L" in yfinance call
□ All four windows populated where data exists:
    1w, 1m, 3m, 1y — verify start_price, end_price, 
    change_pct, high, low, avg_daily_volume
□ change_pct verified manually for at least one window 
  on one ticker
□ data_quality_flag:
    "good" for liquid main market stock
    "sparse" or "unavailable" correctly set for 
    thinly traded AIM stock with data gaps
□ Returns graceful result (not exception) for 
  ticker not found in yfinance
□ Rate limiting delay confirmed present between calls
  (inspect code — minimum 2 second delay)
□ Tested during market hours: current_price populated
□ Tested outside market hours: most recent closing 
  price returned with appropriate note
```

---

### 6. `get_volatility_metrics`

Spec: `layer1_tools.md`

```
□ volatility_30d returned for 3 known tickers
□ volatility_30d verified as annualised standard deviation 
  of daily returns (not raw daily std)
□ liquidity_flag correctly assigned:
    "very_low" for thinly traded AIM stock
    "low", "medium", or "high" for other test tickers
    consistent with provisional thresholds in spec
□ volume_trend correctly classified:
    compare 10d vs 30d average volume for classification
    verify against raw yfinance data for one ticker
□ bid_ask_spread returns null gracefully — confirmed 
  as known limitation, not an error
□ data_quality_flag correctly set for sparse data ticker
```

---

### 7. `get_relative_performance`

Spec: `layer1_tools.md`

```
□ Returns relative performance for main market ticker 
  against FTSE Small Cap snapshot
□ Returns relative performance for AIM ticker 
  against AIM All-Share snapshot
□ position_in_52wk_range calculated correctly:
    verify manually: (current_price - 52wk_low) / 
    (52wk_high - 52wk_low) × 100
    confirm result is between 0% and 100%
□ relative_performance correctly calculated:
    stock_change_pct minus index_change_pct
    verify sign and magnitude for one ticker manually
□ vs_index_available = false set correctly when 
  index historical data not available
  (expected during early PoC — handle gracefully)
□ No exceptions thrown when index snapshot is missing
```

---

### 8. `get_director_transaction_history`

Spec: `layer2_tools.md`
Dependency: director_name_normalisation utility (tool 1)

```
□ Returns correct history for a ticker known to have 
  pdmr_transactions in Firestore
  (seed 2-3 synthetic documents if real data not yet present)
□ exclude_id correctly excludes the specified transaction
□ Name matching via normalisation utility confirmed:
  test with at least one name variation 
  e.g. "J. Smith" matching "John Smith" in Firestore
□ Results sorted by transaction_date_actual descending
□ open_market_count correctly counts only open_market_purchase 
  and open_market_disposal transactions
□ holding_trajectory correctly classified:
    "building" for ticker with increasing open market purchases
    "insufficient_data" for ticker with < 2 open market transactions
□ data_maturity correctly set:
    "empty" for ticker with no prior transactions
    "sparse" for ticker with 1-2 prior transactions
    "sufficient" for ticker with 3+ prior transactions
□ collection_age_days correctly calculated from 
  first document creation date in pdmr_transactions
□ Returns empty result gracefully for unknown director
□ Synthetic seed documents cleaned up after testing
```

---

### 9. `get_company_insider_activity`

Spec: `layer2_tools.md`
Dependency: director_name_normalisation utility (tool 1)

```
□ Returns correct activity for ticker with known transactions
  (seed synthetic documents if real data not yet present)
□ Only open_market_purchase and open_market_disposal 
  transactions returned — plan purchases excluded
□ buy_count and sell_count accurate against seeded data
□ buying_directors and selling_directors lists correct
□ net_sentiment correctly classified:
    "buying" for ticker with only purchases
    "selling" for ticker with only disposals
    "mixed" for ticker with both
    "none" for ticker with no activity
□ cluster_detected correctly identifies 2+ buys within 
  rolling 30-day window:
    test with synthetic data: 2 directors buying within 
    30 days → cluster_detected = true
    test with synthetic data: 2 directors buying 35 days 
    apart → cluster_detected = false
□ cluster_details populated correctly when cluster detected
□ exclude_id correctly excludes triggering transaction
□ days_lookback filter working correctly
□ Returns graceful empty result for ticker with no activity
□ Synthetic seed documents cleaned up after testing
```

---

### 10. `get_price_movement_context`

Spec: `layer3_tools.md`

```
□ price_at_transaction_date returned for a known past date
  verify against manual yfinance lookup
□ price_at_publication returned correctly
□ movement_since_transaction_pct calculated correctly:
  verify formula: (price_now - price_at_transaction) / 
  price_at_transaction × 100
□ movement_during_lag correctly calculated and populated 
  for a transaction with known reporting_lag_days > 5
  movement_during_lag_note present and descriptive
□ All short windows populated (3d, 1w, 2w, 1m):
  change_pct and volume_vs_30d_avg for each
□ volume_anomaly_detected = true for a window where 
  volume_vs_30d_avg > 1.5
  volume_anomaly_note present and observation-only 
  (no assertion of leakage)
□ position_in_52wk_range present and correct
□ Weekend transaction_date handled gracefully:
  uses nearest trading day, notes in data_quality_flag
□ price_data_cache accepted as input when provided:
  confirm no redundant yfinance call made when cache present
□ Rate limiting confirmed present
□ Graceful result (not exception) for unavailable ticker
```

---

### 11. `get_company_news_history`

Spec: `layer3_tools.md`

```
□ Returns summaries for ticker with known data in 
  company_news_summaries collection
  (seed 3-4 synthetic documents if real data not yet present)
□ Results sorted by published_at descending
□ topics_seen correctly aggregated across all returned articles
  (deduplicated list of all key_topics from all articles)
□ topic_match = true when current article key_topics 
  overlap with topics_seen from prior articles
  test: seed prior article with topic "regulatory approval",
  pass current article with same topic → topic_match = true
□ topic_match = false when no overlap
□ sentiment_trend correctly classified:
    "improving": majority of recent articles positive
    "deteriorating": majority negative
    "stable": consistent neutral
    "mixed": varied
    "insufficient_data": fewer than 3 articles
□ data_maturity correctly set
□ collection_age_days correctly calculated
□ exclude_id correctly excludes current article
□ Returns graceful empty result for ticker with no history
□ Synthetic seed documents cleaned up after testing
```

---

### 12. `get_director_companies_house_profile`

Spec: `layer2_tools.md`
Dependency: director_name_normalisation utility (tool 1)

```
□ CH officer search API call successful:
  GET /search/officers?q={normalised_name} returns results
□ Returns correct profile for a known director name:
  use a director from one of the Acted/Deferred signals
  (QHE, STAR, AVG, EGT, MBO, SRT, ROSE) as test case
□ current_appointments populated correctly
□ resigned_appointments populated for last 5 years only
□ disqualified flag present (false for known clean director)
□ tenure_at_this_company correctly calculated from 
  appointed_on date in CH data
□ name_match_confidence correctly set:
    "high" for unambiguous match
    "medium" for abbreviated name match
    "low" for ambiguous common name
□ Low confidence match returns data_found = false:
  test with a common name e.g. "John Smith"
  confirm no false positive returned
□ API authentication confirmed working — same credentials 
  as existing CH ingestion pipeline
□ Graceful result (not exception) when director not found
```

---

### 13. `get_index_context_for_freshness`

Spec: `layer3_tools.md` + `layer1_tools.md`

```
□ Confirmed as shared reference to Layer 1 get_index_snapshot
  — no duplicate implementation exists in codebase
□ Returns identical data to get_index_snapshot for same input
□ index_movement_1w and index_movement_1m return null 
  gracefully when < 30 daily snapshots available
  (expected during early PoC)
□ historical_available = false correctly set when 
  derived fields are null
□ historical_available = true and fields populated when 
  sufficient snapshot history exists
  (test after scraper has been running > 30 days,
  or seed historical snapshots for testing)
□ No import errors — shared module correctly referenced 
  from both layer1 and layer3 tool modules
```

---

## Notes on Tools 8, 9, 11

These tools depend on new Firestore collections 
(`pdmr_transactions`, `company_news_summaries`) that start 
empty. For testing, seed synthetic documents into Firestore 
at the start of the session and clean them up at the end.

Synthetic seeding is part of the Claude Code session scope 
for these tools. Claude Code should:
1. Create seed documents at session start
2. Build and test the tool against seed data
3. Verify all acceptance criteria
4. Delete seed documents at session end
5. Confirm deletion before closing

---

## Sign-Off Checklist

At the end of each session, before closing:

```
□ All acceptance criteria for this tool checked
□ No failing tests left unresolved
□ Code committed to GitHub
□ Any deviations from spec documented in code comments
□ Any new unresolved items added to relevant spec file 
  under "Unresolved Items (Pinned)"
□ Investor sign-off confirmed
```

---

## Tracking

Mark each tool as complete when signed off:

```
 1. director_name_normalisation    [✅] signed off 4 Mar 2026 — 46/46 tests
 2. get_company_profile            [✅] signed off 4 Mar 2026 — 33/33 tests (28 unit + 5 integration)
 3. get_company_ch_filings         [✅] signed off 4 Mar 2026 — 83/83 tests (77 unit + 6 integration)
 4. get_index_snapshot + scraper   [✅] signed off 5 Mar 2026 — 48/48 tests (42 unit + 6 integration)
 5. get_price_history              [✅] signed off 5 Mar 2026 — 39/39 tests (32 unit + 7 integration)
 6. get_volatility_metrics         [✅] signed off 5 Mar 2026 — 55/55 tests (47 unit + 8 integration)
 7. get_relative_performance       [✅] signed off 5 Mar 2026 — 67/67 tests (61 unit + 6 integration)
 8. get_director_transaction_history [✅] signed off 5 Mar 2026 — 65/65 tests (56 unit + 9 integration)
 9. get_company_insider_activity   [✅] signed off 5 Mar 2026 — 61/61 tests (48 unit + 13 integration)
10. get_price_movement_context     [✅] signed off 5 Mar 2026 — 103/103 tests (95 unit + 8 integration)
11. get_company_news_history       [ ]
12. get_director_companies_house_profile [ ]
13. get_index_context_for_freshness [ ]
```
