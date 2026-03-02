# Session Handover
**Last updated:** 2 March 2026
**App version:** 2.29
**Branch:** `master`

---

## Current State

App running locally on WSL2 at `http://localhost:8501`.

| Tab | Status | Notes |
|---|---|---|
| Signals | Working | UI layout not yet reviewed against real usage |
| Discovery | Working | Post-LLM discovery results — distinct from Ingest discovery candidates |
| Universe | Working | Manual add and file import both confirmed end-to-end |
| Ingest | Working | **Phase 1 complete (v2.29).** "Fetch from LSEG" button live — 471 rows, 54 passed. Bare URL + Python date filter; Angular filter params abandoned (unreliable headless). Excel upload retained as fallback. |
| Config | Working | Exclusion list editable; changes reflected on next parse |

**Next steps:** see `ROADMAP.md`.

---

## Pipeline Summary

| Component | Status | Detail |
|---|---|---|
| Universe | Live | 847 companies (93 muted); no market cap ceiling in code |
| Companies House | Live | 673 CH-matched companies; full scan daily cron at 07:00 UTC, 2-day window |
| LSEG index fetch | Live (interactive) | "Fetch from LSEG" button — bare URL, expand to 500, Python date filter. 471 rows / 54 passed confirmed 2 Mar 2026. |
| LSEG Excel ingestion | Live (fallback) | Excel upload still available if fetch fails |
| Job runner | Live | Running as systemd service `job_runner` on VM |
| LLM analysis | Live | Gemini 2.0 Flash, regulatory catalyst lens |
| Notifications | Live | Telegram |
| Google News / CSE | Parked | Structurally late relative to RNS. See `docs/archive/SOBER_ASSESSMENT_v1.md`. |
| NewsAPI | Parked | |

---

## Current Exclusion List

Source of truth: **Firestore `app_config/lseg_filters`**. Editable via Streamlit sidebar → Filtration Rules.

| Type | Rationale |
|---|---|
| `Holding(s) in Company` | Passive shareholding disclosure |
| `Notice of AGM` | Pre-meeting admin |
| `Notice of Results` | Pre-results admin |
| `Annual Report` | Statutory filing |
| `Half-Year Report` | Statutory filing |
| `Interim Report` | Statutory filing |
| `Confirmation Statement` | Companies House admin |
| `Change of Registered Office` | Admin |
| `Change of Nominated Adviser` | Admin |
| `Change of Broker` | Admin |
| `Total Voting Rights` | Monthly admin disclosure |
| `Blocklisting Interim Review` | Admin |
| `Publication of Prospectus` | Admin/legal |
| `Result of AGM` | Post-vote admin |
| `Director Declaration` | Admin filing |
| `Conversion of B Shares` | Admin corporate action |
| `Transaction in Own Shares` | Daily buyback — signal is aggregate pattern, not per-announcement (Lens 3) |
| `Final Results` | Lagging indicator — priced in fast |
| `Interim Results` | Lagging indicator |
| `Preliminary Results` | Lagging indicator |
| `Half-year Financial Report` | Lagging indicator |
| `Half Year Results` | Lagging indicator |
| `Half-Year Financial Results` | Lagging indicator |
| `Half Yearly Results` | Lagging indicator |
| `Audited Results` | Lagging indicator |
| `Interim Financial Results` | Lagging indicator |
| `Gearing Announcement` | Investment trust admin |
| `Gearing disclosure` | Investment trust admin |
| `Monthly Factsheet` | Investment trust admin |
| `Monthly Fact sheet` | Investment trust admin |
| `Monthly report as at` | Investment trust admin |
| `Monthly Portfolio Update` | Investment trust admin |
| `Portfolio Update` | Investment trust admin |
| `Monthly Investor Report` | Investment trust admin |
| `Investor Presentation` | Marketing material |
| `Investor Webinar` | Marketing material |
| `Dividend Declaration` | Routine distribution admin |
| `Issue of Equity` | Admin corporate action |

**Intentionally NOT excluded:** `TR-1` — major holdings crossings are on-thesis (informed party building a position).

---

## Pending Jobs — Firestore Document Structure

All jobs share base fields:
```
"status": "pending" | "processing" | "complete" | "failed"
"submitted_at": SERVER_TIMESTAMP
"claimed_at": ISO string (set by job runner)
"processed_at": ISO string (set on completion)
"note": str | None
"error": str | None
```

**`lseg_ingest`** (written by `submit_job()` in Ingest tab):
```
"job_type": "lseg_ingest"
"ticker", "company_name", "headline", "body", "source_url", "published_at"
"price": float | None, "price_change": str | None
```

**`universe_admit`** (written by `submit_universe_admit_job()` in Universe tab):
```
"job_type": "universe_admit"
"ticker", "company_name", "market_cap_gbp", "listing_exchange"
"not_of_interest": bool, "source_discovery_id": str | None
```

**`universe_bulk_import`** (written by `submit_universe_bulk_import_job()` in Universe tab):
```
"job_type": "universe_bulk_import"
"new_companies":    [{ticker, company_name, market_cap_gbp, listing_exchange, tier}, ...]
"update_companies": [{ticker, company_name, market_cap_gbp, listing_exchange, tier}, ...]
"remove_tickers":   [str, ...]
"mute_tickers":     [str, ...]
```
