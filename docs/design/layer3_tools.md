# Layer 3 — Signal Freshness Tools

## Purpose

Layer 3 evaluates whether the director buying signal has already 
been priced in by the market. It asks one core question:

**Has the market already anticipated this announcement, and is 
there remaining value in acting on it now?**

This is the most reasoning-intensive layer. The tools return 
factual data; the agent interprets that data to distinguish 
between anticipated and surprising announcements. The 
fact/inference boundary must be maintained explicitly in output.

Layer 3 runs in parallel with Layers 1 and 2. It has no 
visibility of their findings.

## Execution Environment

`get_company_news_history` and `get_index_context_for_freshness` 
read from Firestore — no IP sensitivity, can run on backend.

`get_price_movement_context` uses yfinance — must run on local 
machine (residential IP required).

## Important PoC Constraint

The `company_news_summaries` Firestore collection is new. It 
starts empty and accumulates over time. During early PoC 
operation, news history will frequently be sparse or empty. 
This is expected — the agent must reason transparently about 
what can and cannot be assessed given available data.

---

## Tool Definitions

### `get_company_news_history`

Returns recent news summaries for this company from Firestore, 
used to assess whether the announcement topic has been covered 
before and whether the market has had prior signals.

```
Input:
  ticker:           string
  days_lookback:    integer   ← default 90
  exclude_id:       string    ← current article's rns_article_id

Returns:
  articles:             array   ← company_news_summaries documents
                                   sorted by published_at descending
    headline:           string
    published_at:       timestamp
    article_subtype:    string
    sentiment:          string
    summary:            string
    key_topics:         array
  article_count:        integer
  topics_seen:          array   ← aggregated key_topics across
                                   all returned articles
                                   deduplicated
  topic_match:          boolean ← true if any key_topics from
                                   current article appear in
                                   topics_seen from prior articles
                                   indicating prior coverage
  sentiment_trend:      string  ← "improving" | "deteriorating" |
                                   "stable" | "mixed" |
                                   "insufficient_data"
                                   derived from sentiment field
                                   across articles chronologically
  data_maturity:        string  ← "sufficient" | "sparse" | "empty"
                                   "empty":      no prior summaries
                                   "sparse":     fewer than 3
                                   "sufficient": 3 or more
  collection_age_days:  integer ← context for sparse results

Source: Firestore company_news_summaries collection
Execution: backend or frontend
Status: NEEDS BUILDING (collection new — sparse during PoC)
Notes:
  - topic_match is the key output for freshness assessment
    If the current announcement topic (e.g. "regulatory approval")
    appears in prior articles, the market has likely been
    anticipating this category of news
  - topics_seen passed to agent for reasoning — do not
    pre-interpret; let the agent assess relevance
  - sentiment_trend provides trajectory context:
    improving sentiment before a positive announcement
    may mean the market was already aware
  - Empty history is expected during PoC and must not
    block analysis — agent states this explicitly
```

---

### `get_price_movement_context`

Returns recent price movement focused on the period around 
the director transaction and announcement dates. Used to assess 
whether the market has already reacted.

```
Input:
  ticker:               string    ← LSE format e.g. "FGEN.L"
  transaction_date:     date      ← actual date director transacted
  published_at:         date      ← date of RNS announcement
  reporting_lag_days:   integer   ← from pdmr_transaction record

Returns:
  price_at_transaction_date:    float   ← closing price on
                                           transaction date
                                           null if unavailable
  price_at_publication:         float   ← closing price on
                                           announcement date
  price_now:                    float   ← most recent price
  price_currency:               string  ← "GBP"

  movement_since_transaction:
    change_pct:                 float   ← % from transaction
                                           date to now
    direction:                  string  ← "up" | "down" | "flat"

  movement_since_publication:
    change_pct:                 float   ← % from publication
                                           date to now
    direction:                  string  ← "up" | "down" | "flat"

  movement_during_lag:
    change_pct:                 float   ← % change between
                                           transaction date and
                                           publication date
                                           null if lag = 0
    direction:                  string
    note:                       string  ← e.g. "Price rose 8%
                                           between transaction
                                           and publication —
                                           signal may be
                                           partially priced in"

  short_window_analysis:
    per_window (3d, 1w, 2w, 1m prior to publication):
      change_pct:               float
      avg_daily_volume:         integer
      volume_vs_30d_avg:        float   ← ratio: >1.5 = elevated
      volume_anomaly:           boolean ← true if volume_vs_30d_avg
                                           > 1.5 in any window
                                           before publication

  position_in_52wk_range:       float   ← 0% = at 52wk low
                                           100% = at 52wk high
  data_quality_flag:            string  ← "good"|"sparse"|
                                           "unavailable"

Source: yfinance — local machine only
Execution: local machine only (residential IP required)
Status: NEEDS BUILDING
Notes:
  - price_at_transaction_date is particularly valuable:
    if director bought at £0.50 and price is now £0.65,
    the signal may already be partially priced in
    If not available (weekend, holiday, AIM data gap):
    use nearest available date and note in data_quality_flag

  - volume_anomaly before publication date may indicate
    market anticipation or information leakage
    Note: flag this as OBSERVATION only — do not assert
    leakage, which has legal implications

  - movement_during_lag: meaningful only when
    reporting_lag_days > 5
    Short lags (0-5 days) produce noise not signal here

  - Coordinate with Layer 1 yfinance calls where possible
    Both layers use the same ticker and similar date ranges
    Consider implementing a shared price data cache to
    avoid duplicate yfinance calls within a single
    investigation event (see implementation note below)

  - LSE/AIM tickers require ".L" suffix in yfinance
  - Rate limiting: minimum 2 second delay between calls
```

**Implementation note — shared price data cache:**

Layer 1 and Layer 3 both call yfinance for the same ticker 
within a single investigation event. To avoid redundant calls:

```python
# In the investigation orchestrator (not within layers):
# Pre-fetch price data once and pass to both Layer 1 and Layer 3
# as a shared input, rather than each layer fetching independently.
# This reduces yfinance calls by ~50% and respects rate limits.
#
# Layers remain analytically independent — they receive the
# same data but reason about it differently.
# This is a performance optimisation, not a design compromise.
```

---

### `get_index_context_for_freshness`

Returns index snapshot for normalisation context. Functionally 
identical to Layer 1's `get_index_snapshot`.

**Implementation:** Use the same function as Layer 1. 
Do not duplicate the implementation.

```
Input:
  ticker:           string
  index_membership: string    ← "AIM" | "MAIN_MARKET"
  market_cap_gbp:   float

Returns:
  Same structure as Layer 1 get_index_snapshot
  (see layer1_tools.md)

  Additionally, if multiple daily snapshots are available
  in Firestore (after scraper has been running > 1 month):
  index_movement_1w:    float   ← derived from snapshot history
  index_movement_1m:    float   ← derived from snapshot history
  historical_available: boolean ← true if derived fields populated

Source: Firestore index_snapshots collection
Execution: backend or frontend
Status: NEEDS BUILDING (same as Layer 1 tool)
        Implement once, reference from both layers
Notes:
  - index_movement fields require multiple daily snapshots
    Not available during first month of PoC operation
    Set historical_available = false and note limitation
  - Used by Layer 3 to contextualise price movement:
    if stock rose 5% but index rose 6%, the stock
    actually underperformed — this is relevant to
    freshness assessment
```

---

## Layer 3 Agent Output Structure

Passed to the Synthesis agent:

```
freshness_assessment:
  overall:                  string  ← "likely_fresh" |
                                       "possibly_priced_in" |
                                       "likely_priced_in" |
                                       "insufficient_data"

  price_evidence:
    already_moved:          boolean ← significant movement
                                       between transaction
                                       and now
    movement_company_specific: boolean ← movement exceeds
                                           index movement
                                           materially
    movement_during_lag:    string  ← factual description
                                       null if lag <= 5 days
    position_in_52wk_range: float
    volume_anomaly_detected: boolean
    factual_summary:        string  ← factual description only
                                       no interpretation

  news_evidence:
    prior_topic_coverage:   boolean ← topic seen before
    sentiment_trajectory:   string
    relevant_prior_articles: array  ← summaries of most
                                       relevant prior articles
                                       max 3
    factual_summary:        string  ← factual description only

  index_context:
    index_name:             string
    index_movement_context: string  ← factual comparison
    historical_available:   boolean

  inference_assessment:     string  ← THIS IS THE LLM REASONING
                                       SECTION — explicitly labelled
                                       as inference, not fact
                                       Agent reasons about whether
                                       announcement was anticipated
                                       based on the factual evidence
                                       above

data_quality:
  news_history_maturity:    string  ← "sufficient"|"sparse"|"empty"
  price_data_available:     boolean
  index_history_available:  boolean
  overall:                  string  ← "good"|"partial"|"poor"
  collection_age_days:      integer

inference_notes:            string  ← summary of fact/inference
                                       boundary for this assessment
limitations:                string  ← explicit list of what
                                       could not be assessed
```

**Critical design note on fact/inference separation:**

Layer 3 involves more LLM reasoning than Layers 1 or 2. The 
agent must maintain a strict boundary between factual retrieval 
and inference. The output structure enforces this:

- `price_evidence.factual_summary` — facts only, no interpretation
- `news_evidence.factual_summary` — facts only, no interpretation
- `inference_assessment` — clearly labelled reasoning section

The Synthesis agent and the investor both need to see this 
boundary clearly. The agent prompt must reinforce this structure.

---

## Data Source Status Summary

```
Tool                            Source          Status
──────────────────────────────────────────────────────────────
get_company_news_history        Firestore       NEEDS BUILDING
                                                (collection new)
get_price_movement_context      yfinance        NEEDS BUILDING
                                                (local machine)
get_index_context_for_freshness Firestore       NEEDS BUILDING
                                                (shared with L1)
```

---

## Shared Tools with Layer 1

```
get_index_snapshot / get_index_context_for_freshness
  → implement once, reference from both layers

Price data (yfinance)
  → consider shared pre-fetch in orchestrator
    to reduce yfinance call volume
    (see implementation note in get_price_movement_context)
```

---

## Unresolved Items (Pinned)

- **Index movement history** — derived index movement fields 
  (index_movement_1w, index_movement_1m) require multiple 
  daily snapshots. Not available at PoC launch. 
  Resolves naturally over time as scraper runs.

- **Volume anomaly interpretation** — elevated pre-publication 
  volume may indicate anticipation or leakage. Flag as 
  observation only. Legal implications prevent stronger assertion.

- **Sector-level normalisation** — deferred from Layer 1 
  decision. Market-level index context only during PoC.

- **Company_news_summaries maturity** — this collection will 
  be sparse for the first 1-3 months of PoC operation. 
  Layer 3 freshness assessments will carry high uncertainty 
  during this period. This is expected and acceptable.
