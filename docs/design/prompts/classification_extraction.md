# Prompt: Classification and Extraction

## Purpose

Single LLM call executed at ingestion time (Phase 2) for every candidate article. Returns both a classification and a structured extraction in one hit. Output is persisted to Firestore and used to route the article to the appropriate lens.

## Model

**Claude Sonnet** — classification accuracy and extraction reliability are critical here. Misclassification either misses genuine signals or routes noise into the agentic pipeline. Do not use Haiku for this call.

## Execution Environment

Local machine (frontend) — called immediately after headless browser retrieves article body text.

## When Called

After Phase 1 filtering. For every article that passes the keyword and universe filters.

---

## Prompt

```python
prompt = f"""You are a financial document classifier and data extractor 
specialising in LSE regulatory announcements.

Your task is to classify this announcement and extract structured data 
from it in a single response.

ANNOUNCEMENT:
Company: {announcement.company_name}
Ticker: {announcement.ticker}
Source: {announcement.source_name}
Headline: {announcement.headline}
Body: {announcement.body}

STEP 1 — CLASSIFY

Classify this article as exactly one of:
- pdmr_transaction: A director or PDMR share transaction notification
- regulatory_catalyst: Regulatory approval, planning permission, 
  licence, permit, or related catalyst event
- substantive_news: Trading update, financial results, operational 
  news, fundraising, prospecting results, or other material news
- administrative: Meeting notices, date changes, routine filings, 
  director appointments/resignations with no transaction, 
  or other non-material announcements

STEP 2 — EXTRACT TRANSACTIONS (only if pdmr_transaction)

If article_type is pdmr_transaction, extract ALL transactions present.
A single filing may contain multiple directors and multiple transaction 
dates — extract each as a separate transaction object.

For each transaction, extract:
  director_name: Full name as stated
  director_role: Role/position as stated
  transaction_type: "purchase" | "disposal" | "award" | "other"
  transaction_category: Classify as exactly one of:
    - open_market_purchase: Discretionary open market buy
    - open_market_disposal: Discretionary open market sell
    - drip_purchase: Dividend reinvestment plan purchase
    - sip_purchase: Share incentive plan purchase
    - sip_award: Share incentive plan award (granted at £0)
    - option_exercise: Exercise of share options
    - share_award_vesting: Vesting of share awards
    - other: Anything not fitting above — explain in confidence_notes
  transaction_date_actual: Date transaction occurred (YYYY-MM-DD)
  transaction_date_reported: Date of this RNS filing (YYYY-MM-DD)
  reporting_lag_days: Integer days between actual and reported
  shares_transacted: Number of shares (float — may be fractional)
  price_per_share_raw: Price exactly as stated in the document
  price_per_share_gbp: Price normalised to GBP pounds (not pence)
    - If stated in pence: divide by 100
    - If stated in USD: convert using approximate rate, flag in notes
    - If award at £0: set 0.00
  total_consideration_gbp: Total value in GBP pounds
  currency_original: Currency as stated (e.g. "GBP", "USD")
  previous_holding: Shares held before transaction (float)
  resulting_holding: Shares held after transaction (float)
  resulting_holding_pct: Percentage of issued capital after (float)
  isin: ISIN code if present
  lei: LEI code if present
  exchange: Exchange name as stated
  plan_name: Name of share plan if applicable, null otherwise
  extraction_confidence: "high" | "medium" | "low"
  confidence_notes: Note any anomalies, ambiguities, or data quality 
    issues. Examples:
    - "Price stated in pence, divided by 100 to normalise to GBP"
    - "Fractional shares — dividend reinvestment transaction"
    - "Price expressed in USD, approximate GBP conversion applied"
    - "Previous holding not stated — set to null"
    - "Double 'pp' typo in source document for price field"
    - "Reporting lag of X days — transaction occurred [date]"

STEP 3 — SUMMARISE

Provide a 2-3 sentence summary appropriate to the article type:
- pdmr_transaction: Who transacted, what they did, resulting position
- regulatory_catalyst: What event, what decision, what outcome
- substantive_news: Key facts, figures, and sentiment
- administrative: One sentence only

For substantive_news also provide:
  sentiment: "positive" | "neutral" | "negative"
  key_topics: List of 2-5 topic tags (e.g. ["drilling results", 
    "funding", "regulatory approval", "trading update"])
  article_subtype: "trading_update" | "results" | "operational" | 
    "fundraising" | "regulatory" | "other"

RESPOND IN EXACTLY THIS JSON FORMAT — no preamble, no markdown fences:

{{
  "article_type": "",
  "summary": "",
  "sentiment": null,
  "key_topics": null,
  "article_subtype": null,
  "transactions": [
    {{
      "director_name": "",
      "director_role": "",
      "transaction_type": "",
      "transaction_category": "",
      "transaction_date_actual": "",
      "transaction_date_reported": "",
      "reporting_lag_days": 0,
      "shares_transacted": 0.0,
      "price_per_share_raw": "",
      "price_per_share_gbp": 0.0,
      "total_consideration_gbp": 0.0,
      "currency_original": "",
      "previous_holding": 0.0,
      "resulting_holding": 0.0,
      "resulting_holding_pct": 0.0,
      "isin": "",
      "lei": "",
      "exchange": "",
      "plan_name": null,
      "extraction_confidence": "",
      "confidence_notes": ""
    }}
  ]
}}

If article_type is not pdmr_transaction, set transactions to an 
empty array [].
If sentiment, key_topics, or article_subtype are not applicable 
to the article_type, set to null.
"""
```

---

## Routing Logic (Post-Call)

```python
result = parse_json(llm_response)

if result["article_type"] == "pdmr_transaction":
    for transaction in result["transactions"]:
        persist_to_pdmr_transactions(transaction)
        if transaction["transaction_category"] in [
            "open_market_purchase", 
            "open_market_disposal"
        ]:
            trigger_lens_director_simple(transaction)
            trigger_lens_director_agentic(transaction)

elif result["article_type"] == "regulatory_catalyst":
    trigger_lens_catalyst(announcement)

elif result["article_type"] == "substantive_news":
    persist_to_company_news_summaries(result)
    # No lens triggered — future substantive news lens (pinned)

elif result["article_type"] == "administrative":
    # Update announcements record only — no child documents
    pass
```

---

## Error Handling

- If JSON parsing fails: set `extraction_status = "failed"` on the announcements record, log the raw response, do not trigger any lens
- If `article_type` is missing or unrecognised: treat as "administrative" and flag for review
- If `extraction_confidence = "low"` on any transaction: persist but flag in the signals record — do not suppress the signal, but surface the confidence issue to the investor

---

## Notes

- This prompt handles all three real-world PDMR filing formats observed:
  - Single director, clean table format (Foresight example)
  - Multiple directors, multiple dates, DRIP purchases (Ferguson example)
  - Mixed purchase and award transactions, SIP plan (Genus example)
- The `confidence_notes` field is the primary mechanism for surfacing data quality issues — it feeds directly into the investor transparency requirement
- JSON response format avoids the need for regex parsing and reduces extraction errors
