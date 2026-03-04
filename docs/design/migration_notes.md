# Migration Notes — One-Time Backfill

## Overview

This document describes the one-time migration required when deploying 
the new director buying lens pipeline against an existing Firestore 
database. It covers two distinct concerns:

1. **General legacy document strategy** — how to handle the large 
   population of existing `announcements` documents that predate 
   the new schema
2. **Targeted backfill** — a small population of seven high-value 
   documents corresponding to Acted and Deferred signals that 
   warrant structured extraction

---

## General Legacy Document Strategy

**Approach: forward-only, no bulk backfill**

Existing `announcements` documents that do not correspond to Acted 
or Deferred signals are left as-is. New fields are not written to 
these documents.

All new pipeline code handles missing fields gracefully:

```python
# Always use .get() with defaults for new fields
article_type = doc.get("article_type", "unclassified")
extraction_status = doc.get("extraction_status", "legacy")
summary = doc.get("summary", None)
expires_at = doc.get("expires_at", None)
```

Legacy documents will age out naturally or remain inert. They will 
not interfere with new pipeline operation.

**Do not set TTL on legacy documents in bulk** — the risk of 
accidentally expiring documents needed for reference outweighs 
the storage benefit during the PoC phase.

---

## Targeted Backfill — Acted and Deferred Signals

### Scope

Seven documents requiring backfill, all triggered by `lens_catalyst` 
(regulatory catalyst pathway). None are PDMR transaction articles.

```
Acted signals (4):    QHE, STAR, AVG, EGT
Deferred signals (3): MBO, SRT, ROSE
```

All seven articles were published within the last 10 days. 
Source URLs have been confirmed as still accessible on LSEG.

### What the Backfill Does

For each of the seven documents:

1. Retrieve existing `announcements` document from Firestore
2. Fetch article body text via headless browser using `source_url`
3. Run classification and extraction prompt 
   (see `prompts/classification_extraction.md`)
4. Verify `article_type` = `"regulatory_catalyst"` 
   (expected for all seven — flag if different)
5. Write new fields back to existing `announcements` document:
   - `article_type`: `"regulatory_catalyst"`
   - `extraction_status`: `"backfilled"`
   - `summary`: 2-3 sentence structured summary
   - `key_topics`: array of topic tags
   - `sentiment`: positive/neutral/negative
   - `expires_at`: 24 months from `published_at` 
     (extended retention — these are reference documents 
     for active investment positions)
6. Create `company_news_summaries` child document if not exists

### What the Backfill Does NOT Do

- Does **not** create `pdmr_transactions` documents 
  (these are regulatory catalyst articles, not PDMR filings)
- Does **not** modify or overwrite existing `signals` documents —
  investor decisions (Acted/Deferred state) must be preserved exactly
- Does **not** trigger `lens_director_simple` or 
  `lens_director_agentic` — these articles have already been 
  actioned through `lens_catalyst`

### Extended TTL Rationale

Standard TTL for regulatory catalyst articles is 3 months. 
Backfilled Acted and Deferred documents receive 24 months because:
- They correspond to active or recently considered investment positions
- Historical catalyst context may be needed for future reference
- The population is small (7 documents) — storage impact is negligible

### Implementation Notes

**Run as a supervised, interactive script — not automated.**

Given these are live investment positions, each extraction output 
should be reviewed before writing back to Firestore. Suggested flow:

```python
for ticker in ["QHE", "STAR", "AVG", "EGT", "MBO", "SRT", "ROSE"]:
    # 1. Retrieve announcement
    doc = firestore.get_announcement_by_ticker(ticker)
    
    # 2. Fetch body text
    body = headless_browser.fetch(doc["source_url"])
    
    # 3. Run extraction
    result = llm.classify_and_extract(
        company_name=doc["company_name"],
        ticker=doc["ticker"],
        headline=doc["headline"],
        body=body
    )
    
    # 4. REVIEW result before proceeding
    print(f"\n--- {ticker} ---")
    print(json.dumps(result, indent=2))
    confirm = input("Write to Firestore? (y/n): ")
    
    if confirm.lower() == "y":
        # 5. Write back
        firestore.update_announcement(doc.id, {
            "article_type": result["article_type"],
            "extraction_status": "backfilled",
            "summary": result["summary"],
            "key_topics": result["key_topics"],
            "sentiment": result["sentiment"],
            "expires_at": doc["published_at"] + timedelta(days=730)
        })
        print(f"{ticker}: backfill complete")
    else:
        print(f"{ticker}: skipped")
```

**Handle the case where a ticker has multiple announcements.**
Query by ticker and filter to the specific announcement that 
triggered the signal — use `published_at` or `headline` to 
disambiguate if needed.

**If article body fetch fails:**
- Log the failure
- Set `extraction_status` = `"backfill_failed"`
- Do not attempt to extract from headline alone
- Note for manual review

---

## Post-Migration Verification

After running the backfill, verify:

```
□ All 7 documents have extraction_status = "backfilled"
□ All 7 documents have article_type = "regulatory_catalyst"
□ All 7 documents have expires_at set to ~24 months from published_at
□ All 7 documents have non-null summary, key_topics, sentiment
□ No signals documents have been modified
□ Investor action states (Acted/Deferred) are unchanged
```

---

## Timing

Run the backfill **before** deploying the new pipeline to production. 
This ensures:
- The seven documents are in a clean state before new ingestion begins
- The `extraction_status` values are consistent from day one
- No risk of the new pipeline attempting to re-process these articles

---

## Future Backfill Considerations

If the Acted/Deferred population grows significantly before the new 
pipeline is deployed, re-run this assessment to identify additional 
documents requiring backfill. The same approach applies.

This document should be archived (not deleted) after the migration 
is complete — it serves as an audit record of what was changed 
and why.
