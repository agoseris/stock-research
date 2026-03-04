# Prompt: lens_director_simple

## Purpose

One-shot baseline assessment of a director/PDMR open market transaction. Analogous in structure to `lens_catalyst`. Runs in parallel with `lens_director_agentic` on the same signal. Provides the baseline against which the agentic version is measured.

## Model

**Claude Haiku** — this is a single-call, structured assessment from already-extracted data. Speed and cost efficiency appropriate here.

## Execution Environment

Local machine (frontend) or GCP backend — no IP-sensitive data retrieval required. Input is pre-extracted structured data from Firestore.

## When Called

After classification confirms `transaction_category` is `open_market_purchase` or `open_market_disposal`.

## Input

Structured transaction data from `pdmr_transactions` Firestore document plus basic company profile from universe collection. No live market data. No price history. No director history.

---

## Prompt

```python
prompt = f"""You are an investment research analyst specialising in 
LSE-listed small-cap companies.

Your task is to assess a director or PDMR open market share transaction 
and determine whether it represents a meaningful signal worthy of 
further investigation.

IMPORTANT: You have access only to the details of this specific 
transaction and basic company information. You do not have access to 
price history, director transaction history, or live market data. 
Your assessment must be honest about these limitations — the 
LIMITATIONS field is as important as the recommendation itself.

COMPANY INFORMATION:
Company: {transaction.company_name}
Ticker: {transaction.ticker}
Market Cap (may be stale): {transaction.market_cap_gbp}
Market Cap Stale: {transaction.market_cap_stale}
Index: {transaction.index_membership}

TRANSACTION DETAILS:
Director/PDMR: {transaction.director_name}
Role: {transaction.director_role}
Transaction Category: {transaction.transaction_category}
Transaction Date: {transaction.transaction_date_actual}
Reporting Date: {transaction.transaction_date_reported}
Reporting Lag (days): {transaction.reporting_lag_days}
Shares Transacted: {transaction.shares_transacted}
Price Per Share (GBP): {transaction.price_per_share_gbp}
Total Consideration (GBP): {transaction.total_consideration_gbp}
Previous Holding: {transaction.previous_holding}
Resulting Holding: {transaction.resulting_holding}
Resulting Holding (% of issued capital): {transaction.resulting_holding_pct}
Extraction Confidence: {transaction.extraction_confidence}
Extraction Notes: {transaction.confidence_notes}

ASSESSMENT INSTRUCTIONS:

1. TRANSACTION_NATURE
   Classify as exactly one of:
   - First entry: previous holding was zero — director investing 
     for the first time. Strong conviction signal.
   - Position increase: director adding to existing holding.
     Note relative size of addition.
   - Position decrease: partial disposal. Note proportion sold.
   - Complete exit: resulting holding is zero or near-zero.
     Strong negative signal.
   - Other: explain

2. POSITION_CHANGE_PCT
   Calculate the percentage change in holding this transaction 
   represents relative to previous holding.
   If previous holding is zero, state "New position".
   Show your calculation.

3. COMMITMENT_SIZE
   Assess the significance of the total consideration in context.
   A director of a £20m market cap company spending £50,000 is 
   more significant than the same amount at a £500m company.
   Is this a token gesture or a material financial commitment?

4. HOLDING_SIGNIFICANCE
   Assess the resulting holding percentage. Does the director 
   now have a meaningful personal financial stake in the company?
   Consider both absolute value and percentage of issued capital.

5. SIGNAL_STRENGTH
   Score 1-10:
   - First entry with material consideration: 7-9
   - Large position increase (>20% of existing holding): 6-8
   - Small position increase (<5% of existing holding): 3-5
   - Token purchase, plan-driven, or very small consideration: 1-3
   - Partial disposal: 2-4
   - Complete exit: 1-2
   Adjust for reporting lag: > 30 days lag reduces score by 1-2 points.
   Adjust for extraction confidence: low confidence reduces score by 1.

6. SIGNAL_DIRECTION
   - Positive: purchase or first entry
   - Negative: disposal or exit
   - Neutral: ambiguous, token size, or insufficient information

7. REPORTING_LAG_ASSESSMENT
   If reporting lag is within 0-5 days: "Within normal range"
   If 6-30 days: note explicitly — moderately affects freshness
   If > 30 days: flag prominently — materially affects signal 
   freshness. The market may already have reacted.

8. LIMITATIONS
   List explicitly and specifically what cannot be assessed from 
   this transaction alone. Be precise — this field directly informs 
   what the agentic investigation will address.
   
   Always include:
   - Whether current share price represents good or poor value
   - Whether other insiders are moving in the same direction
   - Whether the company is a suitable platform (liquidity, 
     volatility, spread)
   - Whether this signal has already been priced in by the market
   - Director's track record and credibility 
     (unless role/holding makes this less relevant)
   
   Add any additional limitations specific to this transaction.

9. RECOMMENDED_ACTION
   - Investigate further: warrants deeper analysis — 
     signal is meaningful and direction is positive
   - Monitor: mildly interesting — watch for corroborating signals 
     before acting
   - Ignore: insufficient signal, noise, very small consideration, 
     or negative signal

10. SUMMARY
    2-3 sentences. Lead with the single most important factor 
    driving your recommendation. Be direct.

Respond in exactly this format:
TRANSACTION_NATURE: [classification]
POSITION_CHANGE_PCT: [percentage or "New position"] — [calculation]
COMMITMENT_SIZE: [assessment]
HOLDING_SIGNIFICANCE: [assessment]
SIGNAL_STRENGTH: [score]/10 — [brief justification]
SIGNAL_DIRECTION: [Positive/Negative/Neutral]
REPORTING_LAG_ASSESSMENT: [assessment]
LIMITATIONS: [explicit numbered list]
RECOMMENDED_ACTION: [Investigate further/Monitor/Ignore]
SUMMARY: [2-3 sentences]"""
```

---

## Output Persistence

Results are written to the `signals` Firestore document (keyed on `rns_article_id`) under the `simple_*` fields. See `data_schema.md` for full field list.

---

## Design Notes

**On LIMITATIONS** — this is the most important field for the proof of concept. Reading what the simple lens cannot determine, then observing whether the agentic version addresses those limitations, is the primary measure of agentic value.

**On COMMITMENT_SIZE** — the LLM is asked to reason about significance in context rather than calculate a ratio. A £5,000 purchase by a director of a £10m company is more significant than the same amount at a £500m company. Contextual judgement is more valuable than a formula here.

**On score calibration** — scoring guidance is deliberately prescriptive to ensure consistency across signals and comparability with agentic output scores.

**On POSITION_CHANGE_PCT** — asking the LLM to show its calculation serves two purposes: it catches arithmetic errors, and it makes the reasoning auditable.

**On extraction confidence passthrough** — the `extraction_confidence` and `confidence_notes` from the classification step are passed directly into this prompt. Low extraction confidence should propagate into the signal strength score and limitations.
