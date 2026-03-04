# Firestore Data Schema — Director Buying Lens

## Design Principles

- **Summarise, don't store raw text.** Article body text is extracted and summarised at ingestion time. Raw text is never persisted.
- **TTL-based ageing.** All content documents carry an `expires_at` timestamp. Firestore TTL policy auto-deletes expired documents.
- **One-to-many for transactions.** A single RNS article may contain multiple directors and multiple transaction dates. Each transaction generates a separate `pdmr_transactions` document.
- **Deduplication key.** `rns_article_id` (SHA-256 hash) is the deduplication key across all child documents.

---

## Collections

### `announcements` (existing)

Base record for every ingested article. Unchanged from current implementation except for new fields added.

```
document_id:        string  ← SHA-256 hash (existing dedup key)
company_name:       string
ticker:             string
headline:           string
published_at:       timestamp
source_name:        string  ← "LSEG RNS" | "Companies House"
source_url:         string
stored_at:          timestamp

[NEW FIELDS]
article_type:       string  ← "pdmr_transaction" | 
                               "regulatory_catalyst" |
                               "substantive_news" | 
                               "administrative" |
                               "unclassified"
extraction_status:  string  ← "pending" | "complete" | "failed"
summary:            string  ← 2-3 sentence narrative (all types)
expires_at:         timestamp  ← TTL field
```

**TTL policy by article type:**
```
pdmr_transaction:     no expiry on base record
regulatory_catalyst:  3 months
substantive_news:     3 months
administrative:       2 weeks
```

---

### `pdmr_transactions` (new)

One document per director per transaction date. Created only for `pdmr_transaction` articles. Linked to parent `announcements` document.

```
document_id:              string  ← auto-generated

[Linkage]
rns_article_id:           string  ← FK to announcements document_id
ticker:                   string
company_name:             string

[Transaction identity]
director_name:            string
director_role:            string
transaction_date_actual:  date    ← date transaction occurred
transaction_date_reported:date    ← date of RNS filing
reporting_lag_days:       integer ← calculated at extraction time

[Transaction details]
transaction_type:         string  ← "purchase" | "disposal" | "award" | "other"
transaction_category:     string  ← see Transaction Category enum below
is_open_market:           boolean ← true only for open_market_purchase
                                     or open_market_disposal
plan_name:                string  ← e.g. "International Share Incentive Plan"
                                     null for open market transactions

[Quantities and prices]
shares_transacted:        float   ← float to handle fractional shares
price_per_share_gbp:      float   ← normalised to GBP pounds (not pence)
price_per_share_raw:      string  ← original value verbatim (e.g. "2,550p")
total_consideration_gbp:  float   ← normalised to GBP pounds
currency_original:        string  ← e.g. "GBP" | "USD"

[Holdings]
previous_holding:         float   ← shares held before transaction
resulting_holding:        float   ← shares held after transaction
resulting_holding_pct:    float   ← % of issued capital after transaction

[Instrument]
isin:                     string
lei:                      string
exchange:                 string  ← e.g. "LONDON STOCK EXCHANGE (XLON)"

[Extraction quality]
extraction_confidence:    string  ← "high" | "medium" | "low"
confidence_notes:         string  ← flags anomalies, ambiguities, 
                                     data quality issues
                                     e.g. "price expressed in pence, 
                                     normalised to GBP"

[Housekeeping]
created_at:               timestamp
expires_at:               timestamp  ← TTL: 24 months
```

**Transaction Category enum:**
```
"open_market_purchase"    ← strong signal — triggers lens_director
"open_market_disposal"    ← negative signal — triggers lens_director
"drip_purchase"           ← weak signal — stored, no lens triggered
"sip_purchase"            ← weak signal — stored, no lens triggered
"sip_award"               ← no signal — stored, no lens triggered
"option_exercise"         ← context dependent — stored, no lens triggered
"share_award_vesting"     ← no signal — stored, no lens triggered
"other"                   ← flagged for human review
```

**Signal routing rule:**
```
transaction_category IN ("open_market_purchase", "open_market_disposal")
  → trigger lens_director_simple AND lens_director_agentic
  
ALL other categories
  → store only, no lens triggered
```

---

### `company_news_summaries` (new)

For substantive news articles. Used by Layer 3 (Signal Freshness) to assess whether a signal has been priced in.

```
document_id:        string  ← auto-generated
rns_article_id:     string  ← FK to announcements document_id
ticker:             string
published_at:       timestamp

article_subtype:    string  ← "trading_update" | "results" | 
                               "operational" | "fundraising" |
                               "regulatory" | "other"
sentiment:          string  ← "positive" | "neutral" | "negative"
summary:            string  ← 3-5 sentences, key facts and figures preserved
key_topics:         array   ← e.g. ["drilling results", "funding", 
                               "regulatory approval"]

created_at:         timestamp
expires_at:         timestamp  ← TTL: 3 months
```

---

### `index_snapshots` (new)

Daily index values scraped from LSEG by the local machine. Read by backend at analysis time for relative performance assessment.

```
document_id:        string  ← "{index_name}_{date}" 
                               e.g. "AIM_ALL_SHARE_2026-03-04"
index_name:         string  ← "AIM_ALL_SHARE" | 
                               "FTSE_SMALL_CAP" | 
                               "FTSE_250"
index_value:        float
week_52_high:       float
week_52_low:        float
change_1d_pct:      float   ← if available on LSEG page
scraped_at:         timestamp
scrape_date:        date

expires_at:         timestamp  ← TTL: 90 days
```

**Index routing logic:**
```
AIM-listed company           → AIM_ALL_SHARE
Main market, cap < ~£500m   → FTSE_SMALL_CAP
Main market, cap £500m-£1.3bn → FTSE_250
```

---

### `signals` (new)

One document per RNS article that triggers a lens. Aggregates both simple and agentic outputs. Deduplication anchor — prevents a single article generating two separate signal records.

```
document_id:              string  ← rns_article_id (dedup key)
ticker:                   string
company_name:             string
published_at:             timestamp
signal_type:              string  ← "director_buying" | 
                                     "director_disposal"

[Simple lens output]
simple_completed_at:      timestamp
simple_transaction_nature:string
simple_position_change_pct:float
simple_signal_strength:   integer  ← 1-10
simple_signal_direction:  string   ← "Positive"|"Negative"|"Neutral"
simple_recommended_action:string   ← "Investigate further"|
                                      "Monitor"|"Ignore"
simple_limitations:       string
simple_summary:           string

[Agentic lens output]
agentic_completed_at:     timestamp
agentic_status:           string   ← "pending"|"running"|
                                      "complete"|"failed"
agentic_layer1_output:    map      ← platform assessment result
agentic_layer2_output:    map      ← director analysis result
agentic_layer3_output:    map      ← signal freshness result
agentic_recommendation:   string
agentic_justification:    string
agentic_limitations:      string
agentic_token_usage:      map      ← tokens per layer + synthesis
                                     for cost instrumentation

[Investor decision]
investor_action:          string   ← "acted"|"passed"|"pending"
investor_notes:           string   ← free text
decision_at:              timestamp

[Housekeeping]
created_at:               timestamp
expires_at:               timestamp  ← TTL: 24 months
```

---

## TTL Summary

```
Collection                TTL
──────────────────────────────────────────
announcements             varies by type (see above)
pdmr_transactions         24 months
company_news_summaries    3 months
index_snapshots           90 days
signals                   24 months
```

---

## Free Tier Considerations

Firestore free tier: 1GB storage, 50,000 reads/day, 20,000 writes/day.

Mitigation strategies:
- Summarise at ingestion — never store raw article text
- TTL ageing removes stale documents automatically
- Administrative articles generate minimal storage
- Index snapshots are small (3 documents/day)
- At ~20 signals/day maximum, `signals` collection growth is bounded
