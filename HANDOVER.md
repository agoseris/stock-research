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
| Ingest | Working | **Phase 1 complete (v2.31).** "Fetch from LSEG" button live. Three-index fetch: MCX (FTSE 250) + SMX (FTSE Small Cap) + AXX (FTSE AIM All-Share), each under 500 rows/day, merged + deduplicated on source_url. Excel upload retained as fallback. |
| Config | Working | Exclusion list editable; changes reflected on next parse |

**Next steps:** see `ROADMAP.md`.

---

## Phase 2a/2b — Signal/Position State Model (2 March 2026)

Backend implementation complete. No version bump (no frontend changes).

### What was built

**New file: `backend/signal_state.py`** — pure transition engine, no Firestore imports.
- `classify_signal_strength(llm_analysis)` — parses RECOMMENDED_ACTION + CONFIDENCE_SIGNAL → `strong / moderate / weak / noise`
- `is_negative_signal(llm_analysis)` — True when RECOMMENDED_ACTION is "no"
- `compute_signal_transition(current_state, strength, is_negative)` → new state or None
- `compute_decay_transitions(companies, config, now)` → `[(ticker, old_state, new_state)]`
- `TRANSITION_TABLE` and `DECAY_RULES` constants — full transition matrix from workshop spec

**Schema changes (backwards-compatible):**
All new fields on `UniverseCompany` are `Optional` with `None` defaults. Existing Firestore documents load without error.

| New field | Type | Managed by |
|---|---|---|
| `signal_state` | Optional[str] | System |
| `signal_state_since` | Optional[datetime] | System |
| `last_signal_at` | Optional[datetime] | System |
| `position_state` | Optional[str] | Human |
| `position_state_since` | Optional[datetime] | Human |

**New Firestore subcollections** (under `universe_companies/{ticker}`):
- `signal_history/{auto-id}` — one doc per state transition (timestamp, previous_state, new_state, trigger_source_url, trigger_headline, lens, signal_strength, llm_confidence)
- `position_history/{auto-id}` — one doc per position state change

**New Firestore config document:** `app_config/signal_config` — seeded on first call with default decay windows (monitor=30d, signal_active=90d, signal_reinforced=180d, signal_mixed=30d, signal_negative=90d).

**New abstract methods on `UniverseStorageProviderBase`** (8 total):
`update_signal_state`, `update_position_state`, `record_signal_transition`, `record_position_transition`, `get_signal_history`, `get_position_history`, `get_decayed_companies`, `get_signal_config`

**`StrategyLensBase`** now has abstract `name` property. `RegulatoryCatalystLens.name = "regulatory_catalyst"`.

**Pipeline wiring:**
- `pipeline.py`: `_apply_state_transition` called after every `save_signal_result`; `_run_decay_check` runs at end of each autonomous pipeline run
- `job_runner.py`: same `_apply_state_transition` wired after `save_signal_result` in `_process_job`; `"lens"` key added to signal result dict

### Key design notes
- `update_signal_state(ticker, state, timestamp, update_since=True)` — pass `update_since=False` when there is no state change, to touch `last_signal_at` without resetting `signal_state_since` (which would prevent decay)
- `is_negative=True` overrides `signal_strength` in the transition lookup — any RECOMMENDED_ACTION: no verdict uses the `"negative"` row of the table regardless of classified strength
- `signal_negative` is a terminal state: all incoming signals (positive or negative) produce no transition — the company must decay back to `watching` first

---

## Pipeline Summary

| Component | Status | Detail |
|---|---|---|
| Universe | Live | 847 companies (93 muted); no market cap ceiling in code |
| Companies House | Live | 673 CH-matched companies; full scan daily cron at 07:00 UTC, 2-day window |
| LSEG index fetch | Live (interactive) | "Fetch from LSEG" button — three targeted fetches: MCX + SMX + AXX, each with `&indices=` URL param (no `&period=today`), Python date filter in Python. Results merged + deduplicated on source_url. |
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
