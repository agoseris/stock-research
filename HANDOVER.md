# Session Handover
**Last updated:** 3 March 2026 (session 2)
**App version:** 2.44
**Branch:** `master`

---

## Current State

App running locally on WSL2 at `http://localhost:8501`.

| Tab | Status | Notes |
|---|---|---|
| Signals | Working | **Phase 2c complete (v2.35); polished (v2.36–2.44).** Filter bar, grouped expandable sections (Urgent/Action/Monitor/No Action), compact cards with market cap + price, signal state badges with age, state-gated position controls (Act/Defer/Decline/Close/Dismiss), history toggle, urgency highlight for Acted+counter-signal. Declined signals hidden from default view. Headline links to source article. |
| Discovery | Working | Post-LLM discovery results — distinct from Ingest discovery candidates |
| Universe | Working | Manual add and file import both confirmed end-to-end |
| Ingest | Working | **Phase 1 complete (v2.31).** "Fetch from LSEG" button live. Three-index fetch: MCX (FTSE 250) + SMX (FTSE Small Cap) + AXX (FTSE AIM All-Share), each under 500 rows/day, merged + deduplicated on source_url. Excel upload retained as fallback. |
| Config | Working | Exclusion list editable; changes reflected on next parse |

**Next steps:** Phase 3 — Lens 1 validation (director/PDMR buying). See `ROADMAP.md`.

---

## Signals Tab — Position State Machine (3 March 2026, session 2)

Implemented proper state-gated button rendering and the `Close` position state transition. Prior to this session the button set was fixed (Act / Defer / Decline / Dismiss) regardless of current state, allowing invalid transitions such as Acted → Dismiss.

### State machine (valid transitions)

| Current position state | Available buttons |
|---|---|
| None / Closed | Act · Defer · Decline · Dismiss |
| Acted | Close |
| Deferred | Act · Decline · Dismiss |
| Declined | Dismiss |

- **Close:** exits a position. Resets `signal_state` to `watching` and `signal_state_since` to now (same as Decline). The company returns to a neutral state ready to be re-evaluated on future signals. Position state remains `closed` for audit trail.
- **Closed vs. Dismissed:** Close retains the signal_results document (history preserved); Dismiss hard-deletes it. Both return the company to `watching` state.
- **Closed for future signals:** treated identically to `None` — full button set shown, because a new incoming signal represents a fresh evaluation opportunity.
- **Declined signals:** hidden from the default "All" filter view. Accessible via the "Declined" radio option on the filter bar.

### `set_position_state` fix

`set_position_state` in `firestore_helpers.py` now resets `signal_state` → `watching` and `signal_state_since` → now when state is `closed` or `declined`. Previously this reset only happened in the backend pipeline; the frontend was leaving `signal_state` unchanged, causing stale state badges after position moves.

### Signal visibility fix

Prior to this session, signals for acted/deferred tickers could disappear from the Signals tab if pushed off by the 100-signal fetch limit (was exactly 100 signals in the system).

- Fetch limit raised: `get_signal_results` / `get_signal_results_all` default increased 100 → 500.
- Top-up pass added: after loading the main 500-signal list, `get_all_signals_for_ticker` is called for any acted/deferred ticker not already in the result set. Guarantees these always appear regardless of queue depth.

### New functions added

**`frontend/firestore_helpers.py`:**
- `get_all_signals_for_ticker(db, ticker)` — single-field equality query (auto-indexed), filters `dismissed` in Python, sorts newest-first. Used by the top-up pass and by the "Fetch & Analyse" body retrieval path.
- `cleanup_universe_orphans(db)` — deletes any `universe_companies` document that has no `ticker_lse` field. Returns count deleted. Clears universe caches. Called from Universe tab on load.

---

## Signal Card UI Improvements (3 March 2026, session 2)

Signal cards in the Signals tab were refined following user review:

| Issue | Fix |
|---|---|
| "Analysis detail" expander repeated Summary text verbatim | `SUMMARY` and `RECOMMENDED_ACTION` keys filtered out of the detail section (already shown on the card face) |
| Headline was not clickable | Headline text wrapped in `<a href=source_url>` with `target="_blank"` |
| Market cap had no label prefix | Changed from bare value to `"Market Cap £45m"` (or `—` if unavailable) |
| Positive price changes showed no arrow | `format_price_info` in `ui_helpers.py` now parses the change value numerically; LSEG often omits the `+` prefix on positive values, breaking the old string-prefix heuristic |
| Zero change shown as `▲ 0%` | Zero now shows `↔ 0%` |

---

## Universe Orphan Cleanup (3 March 2026, session 2)

**Root cause:** during an earlier session, a market cap propagation feature was briefly deployed to the VM before being rolled back. The `update_market_data` call created `universe_companies` documents containing only `market_cap_gbp` — no `ticker_lse`, `company_name`, or other fields — for tickers encountered in news that were not yet in the universe.

**Fix:**
1. `cleanup_universe_orphans(db)` in `firestore_helpers.py` — deletes any universe doc missing `ticker_lse`. Called once on Universe tab load; no-ops silently if there are none.
2. Display-level safety net in Universe tab: filters the loaded company list to `c.get("ticker_lse")` truthy before rendering, preventing blank rows even if orphans are created in future.

**Market cap ingestion decision:** market cap data is only updated during quarterly CSV imports. Live market cap capture from LSEG news pages is not feasible — the news index page does not carry market cap data (it appears on the LSEG stock screener page, which is a separate workflow). Decision: live with quarterly refresh cadence.

---

## Data Fix — Market Cap Backfill (3 March 2026)

**Root cause:** `_parse_universe_csv` in `parse_helpers.py` always read column `"Market Cap"`. FTSE All-Share CSV uses `"Market Cap (m)"`. A bulk import via the UI overwrote all FTSE companies' market caps with null and cascaded through `update_companies` writes to zero out AIM companies too.

**Fix:** Changed `parse_helpers.py` line 198 to try both column names:
```python
mcap_raw = (row.get("Market Cap") or row.get("Market Cap (m)") or "").strip().replace(",", "")
```

**Data backfill:** 1089 companies updated directly in Firestore from source CSVs using a one-off Python script run locally (WSL2). 1 company (admitted via Discovery) has no market cap — expected.

**Price data:** Only available in `signal_results` for signals submitted via LSEG interactive path after commit `a301c99`. CH pipeline signals have no price data — this is expected and not backfillable.

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

## Phase 2c — Dashboard Integration (3 March 2026)

Frontend wiring of the two-axis state model into the Signals tab.

### What was built

**`frontend/ui_helpers.py`** — two new functions:
- `signal_state_badge(state)` → HTML badge for signal_state (WATCHING/MONITOR/SIGNAL/CONFIRMED/MIXED/NEGATIVE)
- `position_state_badge(state)` → HTML badge for position_state (ACTED/DEFERRED/DECLINED/CLOSED)

**`frontend/firestore_helpers.py`** — two new functions:
- `get_signal_history_for_ticker(_db, ticker, limit=10)` — cached 2 min; queries `signal_history` subcollection newest first
- `set_position_state(db, ticker, state)` — writes `position_state` + `position_state_since` (ISO string) to `universe_companies/{ticker}` with merge=True; clears relevant caches

**`frontend/app.py`** (v2.32):
- New CSS: 6 signal state badge classes, 4 position state badge classes, `.signal-card.urgent`, `.urgency-banner`, `.history-row`
- `company_map` built at startup from `get_all_universe_companies` (cached, no extra Firestore reads)
- Signals tab updated:
  - Sort: Acted+negative/mixed → top (urgency 0), then yes → monitor → other
  - Card HTML: signal_state badge + position_state badge appended to badge row
  - Urgency: dark red card border + "⚠ COUNTER-SIGNAL — REVIEW POSITION" banner for Acted+counter-signal
  - Position controls: Act / Defer / Pass buttons (show active state label if set); writes directly to Firestore
  - Signal history: per-card "Hist" toggle button (session_state); fetches and renders subcollection on open

### Key design notes
- History is fetched lazily (only when user clicks "Hist") — avoids N subcollection queries on every render
- `set_position_state` writes ISO string for `position_state_since` — consistent with how `update_position_state` stores it in the backend
- No position state toggle/clear in this version — clicking always sets (clear available via Firestore console if needed)
- Position history subcollection is NOT written from frontend — backend pipeline handles history recording when it reads position_state

---

## Backend Operational Fixes (3 March 2026)

### journalctl logging
`job_runner.service` lacked `Environment=PYTHONUNBUFFERED=1`. Python buffers stdout
when writing to a pipe (systemd journal), so all `print()` output was silently swallowed.
Fixed by adding the env var to the service file. Follow logs with:
```
journalctl -u job_runner -f
```

### Telegram event loop fix
`TelegramNotifier` instantiated `Bot` once in `__init__` and reused it across `send()`
calls. `python-telegram-bot` v20+ binds the bot's internal HTTP client to the first event
loop created by `asyncio.run()`. After that loop closes, subsequent calls raise
`RuntimeError('Event loop is closed')`. Fixed: `Bot` now created as an async context
manager inside a `_send()` coroutine, called fresh per `send()` invocation.

### Signal notification content enriched
`format_signal()` in `telegram_notifier.py` now includes:
- Headline as a clickable link (`[headline](source_url)` in Telegram Markdown)
- Recommended Action moved to top (immediately after Company/Ticker/Headline)
- Approx Market Cap (from `universe_companies` Firestore doc via `job_runner`)
- Approx Price (pence + change %, from LSEG index page, stored in `pending_jobs.price`)
`job_runner._process_job` enriches the result dict with `price_pence`, `price_change`,
and `market_cap_gbp` before calling `save_signal_result` and `_notify_signal`.

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
