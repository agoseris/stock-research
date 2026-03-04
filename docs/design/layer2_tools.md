# Layer 2 — Director Purchase Analysis Tools

## Purpose

Layer 2 evaluates the director transaction itself and the director's 
credibility as a signal source. It asks: "Does this transaction 
represent genuine conviction from an informed party, and is there 
corroborating evidence from other insiders?"

Layer 2 runs in parallel with Layers 1 and 3. It has no visibility 
of their findings.

## Execution Environment

All Layer 2 tools read from Firestore or the Companies House API. 
There is no yfinance dependency. Most tools can execute on either 
the GCP backend or the local machine. The Companies House API tool 
executes on the GCP backend (API access, no IP sensitivity).

## Important PoC Constraint

The `pdmr_transactions` collection starts empty and accumulates over 
time. During early PoC operation, transaction history queries will 
frequently return sparse or empty results. This is expected pipeline 
behaviour, not a data error. All Layer 2 tools and the Layer 2 agent 
prompt must handle thin data gracefully and flag it explicitly.

## Dependency

**Director name normalisation utility must be built and tested before 
any Layer 2 tool is implemented.**
See `utilities/director_name_normalisation.md`.

---

## Tool Definitions

### `get_director_transaction_history`

Returns all known transactions for this specific director at this 
company from the `pdmr_transactions` Firestore collection.

```
Input:
  ticker:             string
  director_name:      string  ← raw name from current transaction
                                 utility normalises before querying
  exclude_id:         string  ← rns_article_id of current transaction
                                 prevents triggering transaction
                                 appearing in its own history

Returns:
  transactions:       array   ← all pdmr_transaction documents
                                 for this director at this company
                                 sorted by transaction_date_actual
                                 descending
                                 all transaction_categories included
                                 to show full holding trajectory
  total_count:        integer
  open_market_count:  integer ← open_market_purchase and
                                 open_market_disposal only
  date_range:
    earliest:         date
    latest:           date
  holding_trajectory: string  ← "building" | "reducing" |
                                 "stable" | "mixed" | "unknown"
                                 derived from direction of open
                                 market transactions over time
  data_maturity:      string  ← "sufficient": 3+ prior transactions
                                 "sparse": 1-2 prior transactions
                                 "empty": no prior transactions found
  collection_age_days: integer ← days since pdmr_transactions
                                   collection first populated
                                   context for sparse/empty results

Source: Firestore pdmr_transactions collection
Execution: backend or frontend (Firestore read)
Status: NEEDS BUILDING
Notes:
  - Use normalise_for_storage() on director_name before querying
  - Use names_match() to handle name format variations between
    query name and stored names
  - If data_maturity is "empty" and collection_age_days < 30,
    include note: "Pipeline recently deployed — transaction
    history will accumulate over time. Empty result does not
    indicate this is the director's first transaction."
  - holding_trajectory derived from open_market transactions
    only — plan purchases and awards excluded from trajectory
    calculation but included in full transaction array
```

---

### `get_company_insider_activity`

Returns all recent open market transactions across ALL directors 
at this company, to detect corroborating insider momentum.

```
Input:
  ticker:           string
  days_lookback:    integer ← default 90
  exclude_id:       string  ← rns_article_id of current transaction

Returns:
  transactions:     array   ← pdmr_transaction documents
                               filtered to open_market_purchase
                               and open_market_disposal only
                               sorted by transaction_date_actual
                               descending
  buy_count:        integer
  sell_count:       integer
  buying_directors: array   ← distinct normalised director names
  selling_directors:array   ← distinct normalised director names
  net_sentiment:    string  ← "buying" | "selling" | 
                               "mixed" | "none"
  cluster_detected: boolean ← true if 2+ distinct directors
                               (excluding triggering director)
                               bought within any rolling 30-day
                               window in the lookback period
  cluster_details:  array   ← if cluster_detected:
                               directors: array of names
                               window_start: date
                               window_end: date
                               transaction_count: integer
  data_maturity:    string  ← "sufficient" | "sparse" | "empty"

Source: Firestore pdmr_transactions collection
Execution: backend or frontend (Firestore read)
Status: NEEDS BUILDING
Notes:
  - cluster_detected is a meaningful signal amplifier
  - Exclude the triggering director from cluster detection
  - If 90-day lookback returns empty, agent may extend to
    180 days on a second call
  - Director name deduplication uses names_match() utility
```

---

### `get_director_companies_house_profile`

Returns Companies House profile for the director. Called only when 
the conditional credibility branch is triggered.

**Conditionally called only — see Conditional Branch below.**

```
Input:
  director_name:  string  ← normalised name
  company_name:   string  ← used to disambiguate common names
  ticker:         string

Returns:
  ch_person_id:   string  ← null if not found
  current_appointments: array
    company_name: string
    company_number: string
    role:         string
    appointed_on: date
    is_active:    boolean
  resigned_appointments: array  ← last 5 years only
    company_name: string
    company_number: string
    role:         string
    appointed_on: date
    resigned_on:  date
  total_current_appointments: integer
  disqualified:   boolean ← hard negative signal regardless
                             of transaction size
  data_found:     boolean
  match_confidence: string ← "high" | "medium" | "low"
                              confidence that CH record matches
                              the filing director
  confidence_notes: string

Source: Companies House API
        GET /search/officers?q={director_name}
        https://api.company-information.service.gov.uk
Execution: GCP backend (API, no IP sensitivity)
Status: NEEDS BUILDING
        CH API connector exists for filing ingestion —
        officer search is a new endpoint
Notes:
  - API endpoint: /search/officers?q={name}&items_per_page=10
  - Use names_match() to identify correct record when
    multiple results returned
  - Use company_name to disambiguate common names:
    check appointments for company match
  - Disqualification check:
    GET /officers/{officer_id}/disqualifications
  - Rate limit: 600 requests/5min — well within our volume
  - If data_found = false: return empty profile gracefully
  - match_confidence "low" means possible wrong person —
    treat as no data rather than incorrect data
```

---

### `get_company_ch_filings`

Returns recent Companies House filings for the company from 
Firestore. Provides director tenure context from already-ingested 
data without additional API calls.

```
Input:
  ticker:         string
  days_lookback:  integer ← default 365

Returns:
  recent_filings: array
    headline:     string
    published_at: timestamp
    source_url:   string
  director_changes: array ← appointments and resignations
    director_name:  string ← normalised
    change_type:  string  ← "appointed" | "resigned"
    date:         date
  filing_count:   integer
  data_found:     boolean
  data_maturity:  string  ← "sufficient" | "sparse" | "empty"

Source: Firestore announcements collection
        where source_name = "Companies House"
Execution: backend or frontend (Firestore read)
Status: EXISTS (partial) — data already in Firestore
        Query wrapper and director_changes extraction needed
Notes:
  - Director changes extracted from headline text via
    pattern matching — not an LLM call
    Patterns: "Director Appointed", "Director Resigned",
    "Termination of appointment", "Appointment of director"
  - Cross-reference with current transaction director
    to establish tenure:
    appointed < 90 days ago: "recently appointed"
    appointed > 2 years ago: "established tenure"
```

---

## Conditional Branch — The Agentic Decision Point

After calling `get_director_transaction_history`, the agent evaluates:

```
TRIGGER credibility research if ANY of:
  resulting_holding_pct < 0.5%
  OR previous_holding == 0       (first entry)
  OR transaction_history.total_count == 0
  OR transaction_history.data_maturity == "empty"

IF triggered:
  → call get_director_companies_house_profile
  → call get_company_ch_filings
  → credibility_research_triggered = true

ELSE:
  → skip credibility research
  → credibility_research_triggered = false
  → note: "Material existing holding or established history —
    director credibility evidenced by financial stake and
    transaction pattern rather than external research"
```

The 0.5% threshold is provisional. Review after PoC and adjust.

---

## Layer 2 Agent Output Structure

```
signal_assessment:
  transaction_nature:           string  ← "first_entry" |
                                           "position_increase" |
                                           "position_decrease" |
                                           "complete_exit"
  position_change_pct:          float   ← null if first entry
  commitment_significance:      string  ← "material" | "moderate" |
                                           "token"
  director_pattern:             string  ← factual description of
                                           prior history or explicit
                                           statement of unavailability
  holding_trajectory:           string
  insider_momentum:             string  ← factual description
  cluster_detected:             boolean
  cluster_details:              array   ← null if not detected

credibility_research_triggered: boolean
credibility_assessment:         string  ← null if not triggered
  disqualified:                 boolean ← always included if triggered

signal_strength:                integer ← 1-10
signal_direction:               string  ← "positive" | "negative" |
                                           "neutral"

data_quality:
  transaction_history_maturity: string
  credibility_data_found:       boolean ← null if not triggered
  overall:                      string  ← "good"|"partial"|"poor"
  collection_age_note:          string  ← if collection < 30 days

inference_notes:                string
limitations:                    string
```

---

## Data Source Status Summary

```
Tool                                 Source       Status
─────────────────────────────────────────────────────────────────
get_director_transaction_history     Firestore    NEEDS BUILDING
                                                  (new collection)
get_company_insider_activity         Firestore    NEEDS BUILDING
                                                  (new collection)
get_director_companies_house_profile CH API       NEEDS BUILDING
                                                  (new CH endpoint)
get_company_ch_filings               Firestore    EXISTS (partial)
                                                  (query wrapper new)
```

---

## Build Order

```
1. director_name_normalisation utility  ← prerequisite
2. get_company_ch_filings               ← uses existing Firestore data
3. get_director_transaction_history     ← depends on normalisation
4. get_company_insider_activity         ← depends on normalisation
5. get_director_companies_house_profile ← new CH endpoint
```
