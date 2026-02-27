# Session Handover
**Date:** 27 February 2026
**Branch:** `master`
**Last commit:** (see git log)

---

## Current State

App is at **v2.18**. Running locally on WSL2 at `http://localhost:8501`.

| Tab | Status | Notes |
|---|---|---|
| Signals | Working | UI layout not yet reviewed against real usage |
| Discovery | Working | Assessed as **redundant** — see Next Steps |
| Universe | Working | Both manual add and file import confirmed end-to-end |
| Ingest | Working | Full workflow confirmed end-to-end. Auto-fetch & submit live. |
| Config | Working | Exclusion list editable; changes reflected in next parse |

**Local hosting active:** `./start_frontend.sh` from project root starts Streamlit nohup
on port 8501. Credentials via `frontend/.env` → `frontend/gcp-credentials.json`.
Community Cloud deployment archived (no longer primary).

Known UX issue: Streamlit re-render latency makes any multi-row operation slow
(2–5 seconds per action). Affects universe import absent-row review, Ingest muting,
and Ingest row hiding. Batch-action solution planned but deprioritised.

---

## What Was Built This Session (v2.16–v2.18)

### Playwright Auto-Fetch & Submit (v2.16–v2.18)

Phase 1 item 3 complete: headless browser body retrieval is live.

**`frontend/lseg_scraper.py`** (new module):
- Playwright (sync API) scraper for LSEG announcement body text
- Persistent cookie store at `frontend/lseg_cookies.json` (gitignored) — survives
  the LSEG private investor challenge gate between sessions
- Challenge gate handling: detects `body.block-scroll`, auto-clicks "a private investor",
  waits for gate to clear, saves cookies — fully transparent to the user
- Body extraction via JavaScript evaluation of shadow DOM:
  `div[itemprop="articleBody"]` → shadow root → `div.news-body-content`
- Graceful degradation: if `playwright` is not importable, `_PLAYWRIGHT_AVAILABLE` flag
  falls back to manual paste UI in the Ingest tab
- All failure modes raise `RuntimeError` with a descriptive message

**`frontend/app.py`** changes:
- v2.16: Added `🔍 Auto-fetch & submit` button in the Analyse sub-form.
  On success: fetches body, calls `submit_job`, closes sub-form, reruns.
  On failure: stores error in session state so it survives `st.rerun()` and displays
  persistently below the button.
- v2.17: Fixed error persistence bug — original code called `st.rerun()` outside the
  try/except block, wiping `st.error()` before Streamlit could render it.
- v2.18: Combined fetch + submit into a single button ("Auto-fetch & submit").
  Previously required three clicks: Analyse ▾ → Auto-fetch body → Submit for analysis.
  Now requires two: Analyse ▾ → Auto-fetch & submit.

**System dependency:** Playwright requires system libraries not shipped with the Python
package. One-time install in the frontend venv:
```bash
source frontend/venv/bin/activate
playwright install-deps chromium
```
This has been run on the current WSL2 environment and is working.

**`frontend/requirements.txt`:** `playwright` added.

---

## What Was Built This Session (v2.14–v2.15)

### Frontend Refactoring — Completed (v2.14–v2.15)

`app.py` (was ~1,400 lines) split into five modules:

| Module | Contents |
|--------|----------|
| `frontend/constants.py` | Emoji constants, config keys, default lists, outcome order/style |
| `frontend/ui_helpers.py` | `parse_analysis`, `get_field`, `recommended_action_badge`, `recommend_add_badge`, `format_timestamp` |
| `frontend/parse_helpers.py` | `_parse_lseg_excel`, `_parse_universe_csv`, `_compute_universe_delta` |
| `frontend/firestore_helpers.py` | All Firestore reads/writes/cache functions (20 functions) |
| `frontend/app.py` | Tab rendering + CSS only (~1,158 lines) |

Import order in `app.py`: streamlit, dotenv, `load_dotenv()`, VERSION, `set_page_config()`,
CSS, then `from constants/firestore_helpers/parse_helpers/ui_helpers import ...`.

**Constraint still applies:** `frontend/` cannot import from `backend/`.

### Local Hosting Setup

Migrated Streamlit from Community Cloud to local WSL2 hosting:
- `start_frontend.sh` — nohup Streamlit on port 8501, PID to `streamlit.pid`, log to `logs/`
- `stop_frontend.sh` — clean shutdown via PID file
- `frontend/.env` — sets `GOOGLE_APPLICATION_CREDENTIALS` to `frontend/gcp-credentials.json`
- `logs/` and `streamlit.pid` added to `.gitignore`

**Rationale:** LSEG pages are JS-rendered (raw HTTP returns no content); GCP IPs risk
blocking (precedent: Google News). Local residential IP + local Playwright is the path
to automating body retrieval and index page scraping. See Next Steps.

### Exclusion List — Expanded

All previously-missing types added via Config tab UI. Full current list now includes
results announcements, gearing/portfolio disclosures, investor presentations, dividends.
See updated exclusion list table below.

**Key decision:** `Transaction in Own Shares` moved to EXCLUDED. Individual daily buyback
announcements carry near-zero signal value; the signal is aggregate pattern (Lens 3).
Results announcements (Final, Interim, Preliminary, Half-year, Audited) also excluded —
lagging indicators, priced in fast.

### Lens Workshop — Decisions (see `LENS_WORKSHOP_CANDIDATES_v2.md`)

Five candidate lenses prioritised. Lens 1 (Director/PDMR Open-Market Buying) is next
to implement. Two-axis state model designed (Signal State + Position State). Full detail
in `SESSION_SUMMARY_27FEB2026.md` and `LENS_WORKSHOP_CANDIDATES_v2.md`.

---

## What Was Built This Session (v2.12–v2.13)

### Universe File Import via UI (v2.13)

New `📂 Import from file` expander in the Universe tab. Provides a delta-based bulk
import path as an alternative to the backend-only `import_universe_csv.py` script.

**Flow:** upload CSV → compute delta (new / update / absent) → review absent companies
per-row (Mute / Remove / leave) → Commit writes a `universe_bulk_import` job → VM
job_runner processes.

**CSV format:** columns `Exchange, Code, Name, Market Cap` (market cap in £M).
Exchange `AIM` → `AIM`; anything else → `LSE_MAIN`.

**job_runner handler (`_process_universe_bulk_import_job`):**
- Processing order: Remove → Mute → Update → New (mandatory — prevents flag conflicts)
- **Remove:** `universe_companies` document deleted; discarded from `_universe_tickers`
- **Mute:** `not_of_interest: True` with `merge=True`; discarded from `_universe_tickers`
- **Update:** `merge=True` on Firestore directly — never `save_company()` which uses plain
  `set()` and would overwrite `not_of_interest`
- **New:** CH lookup (0.6s rate limit), build `UniverseCompany`, `save_company()`; progress
  logged every 10 companies

**Key constraint:** `save_universe()` (full-replace, destructive) is NOT used — manually
added companies not in the file are preserved.

**Session state:** `universe_import_file_key` (counter for widget reset on Cancel),
`universe_import_cache_key`, `universe_import_delta`, `universe_import_absent_decisions`,
`universe_import_submitted`.

### £1B Market Cap Ceiling Removed (v2.13)

`MARKET_CAP_CEILING_GBP` constant and the filter block removed from
`import_universe_csv.py`. No ceiling is enforced anywhere in code. Files are
pre-filtered at source before being committed to `docs/`. `CLAUDE.md` and the
Universe tab description updated accordingly.

### Ingest Table — Full Suppression Reason (v2.12)

The Action column in the Ingest unified table was showing only the matched keyword
in the suppression reason, not the full reason string. Fixed so the complete reason
(e.g. "Announcement type excluded: 'Annual Report'") is shown.

---

## What Was Built / Changed Previously (v2.9–v2.11)

### CH Pipeline Fix — Critical (backend)

**Root cause:** `pipeline.run(max_announcements=30)` caused `get_recent_announcements()`
to return early after 30 total filings — always the same first ~6 companies
alphabetically (4BB, 80M, AAS, AAU…). The 90-day cutoff meant those same filings
were re-ingested and deduplicated every day. The pipeline appeared to work but was
completely blind to 665+ companies.

**Fix (`companies_house_connector.py`):**
- Cutoff changed from 90 days → 2 days (only new filings per run)
- Early-exit `if len(announcements) >= max_results: return` removed — all 672 companies
  now scanned each run
- Default `max_results` raised to 500 (defensive ceiling only)

**Fix (`pipeline.py`):**
- `max_announcements=30` → 500

**Confirmed working:** 673 companies scanned, 43 had new filings, 79 ingested, 6 in
signal queue, 1 passed pre-filter, LLM analysis ran successfully.

### Observability Improvements (backend + CLAUDE.md)

**`storage_firestore.py`:** Added `get_existing_source(source_url, headline)` — returns
`source_name` of the original dedup record, or None if new. Uses same URL-first key as
`save_announcement()`, fixing a key misalignment where `headline_exists()` (headline key)
and `save_announcement()` (URL key) used different fingerprints.

**`pipeline.py`:** Replaced `headline_exists()` with `get_existing_source()`. Dedup
loop now tracks dupes by originating source. Log output:
```
Deduplicated (seen in previous runs): 70
  - Companies House: 70
```

**`companies_house_connector.py`:** Added scan progress marker every 100 companies,
per-company log line for companies with new filings, and scan-complete summary:
```
CH scan complete: 673 companies checked, 43 had new filings
```

**`CLAUDE.md`:** Added Principle 7 — Observability.

### Mute Respected by Autonomous Pipeline (backend)

`_load_universe()` in `pipeline.py` previously loaded all universe companies including
muted ones (the `not_of_interest` flag was read but silently dropped). Muted companies
were routing to the signal queue and could trigger LLM analysis and Telegram alerts.

**Fix:** one-line filter in `_load_universe()`:
```python
if not c.not_of_interest
```
Muted count is now logged at startup:
```
Universe loaded from Firestore: 754 companies (93 muted, excluded from pipeline).
```

Note: 93 muted out of 847 is worth reviewing in the Universe tab — may include bulk
mutes from trust/fund filter or other historical actions.

### Ingest Tab — Emoji Buttons (v2.9)

| Old label | New label | Column header |
|---|---|---|
| `✕` (dismiss) | `👎` | "Dismiss" → "Hide" |
| `Mute` | `🔇` | "Mute" (unchanged) |
| `+ Univ` | `⏫` | "Action" (unchanged) |
| `→ Pass` | `✅` | "Action" (unchanged) |
| `Submit ▾` | `Analyse ▾` | opens body sub-form |

### Market Cap Ceiling Removed (v2.10)

`st.number_input` for market cap in both the Ingest tab discovery admission form and
the Universe tab manual add form had `max_value=1000.0` (£1B). Removed — AIM/SMX
companies just above £1B are valid manual universe additions.

---

## What Was Built / Changed Previously (v2.4–v2.8)

### Deduplication Fix (backend)

- `storage_firestore.py`: `save_announcement()` now uses `source_url` as the Firestore
  document ID key (falls back to headline if URL absent). `announcement_exists()` now
  uses a direct document ID lookup (fast, no index scan) keyed on URL.
- `job_runner.py`: dedup check switched from `headline_exists()` to
  `announcement_exists(url, headline)`. `save_announcement()` moved to run immediately
  after the dedup check passes, before routing — ensures all attempted announcements are
  fingerprinted regardless of pre_filter or LLM outcome.

### Ingest Tab — Unified Announcement Table (v2.4)

Replaced the fragmented layout (Passed table → Step 2 expanders → Discovery expanders →
Suppressed expander) with a single unified table showing all announcements:

- **Outcome badge** (PASSED / DISCOVERY / MUTED / SUPPRESSED) with colour coding
- **Sort** (Outcome / Date / Ticker / Company) and **Filter** (All / Passed / Discovery /
  Muted / Suppressed) controls above the table
- **Dismiss (✕)** — removes a row from the current session without affecting Firestore
- **Mute** — calls `mark_not_of_interest()` and immediately reflects as MUTED without
  requiring a re-parse
- **Submit ▾** (Passed rows) — inline body-paste sub-form. Shows "✓ Analysed" if the
  row's source_url is already in the announcements dedup store.
- **+ Univ / → Pass** (Discovery rows) — admit to universe (sub-form) or promote to
  Passed inline
- Reason shown for MUTED/SUPPRESSED rows in action column
- Old standalone "Mute a ticker" section, "Step 2 — Submit for Analysis" expanders,
  "Discovery Candidates" expanders, and "Suppressed rows" expander are all removed
- Step 3 → Step 2 (Job Status)

Session state: `ingest_dismissed` (set of row UIDs), `ingest_session_muted` (set of
tickers muted this session), `ingest_subform_open` (row key of open sub-form).
Cleared on new file upload alongside `ingest_cache_key`.

### New Helper

`_get_processed_source_urls(db)` — `@st.cache_data(ttl=60)` — returns set of
`source_url` values already in the `announcements` collection. Used to show "✓ Analysed"
indicator on already-processed rows.

### UX Polish — Universe, Ingest, Discovery (v2.8)

**Universe tab — inline Mute/Unmute:**
Replaced `st.dataframe` + separate "Actions" section (which duplicated ticker/company
data) with a single per-row custom layout. Mute/Unmute button is now inline with each
row. Muted tickers rendered in grey.

**Ingest — submitted state indicator:**
After clicking Submit, the row now shows `⏳ Submitted` until the VM processes the job
and the URL appears in the dedup store, at which point it flips to `✓ Analysed`.
Previously the Submit ▾ button re-appeared immediately after submission.
New session state key: `ingest_session_submitted` (set of row UIDs). Cleared on new
file upload.

**Ingest sub-form — URL link and visible close:**
The LSEG URL link (`Open announcement on LSEG ↗`) and `✕` close button now appear at
the top of the body-paste sub-form. Previously there was no URL and Cancel was buried
below the text area in a narrow column.

**Discovery tab — empty state explanation:**
Replaced terse empty state with a caption explaining how to generate discovery results
(Ingest → find DISCOVERY row → `→ Pass` → Submit for analysis).

### Post-Launch Bug Fixes (v2.5–v2.7)

**v2.5 — Streamlit magic write SyntaxError**
The data cell render loop used a bare ternary expression
(`col.caption(val) if is_muted else col.markdown(...)`). Streamlit's magic write
intercepted it as a value to display, triggering `ast.parse` → `SyntaxError`.
Fixed by replacing with an explicit `if/else` statement.

**v2.6 — Promote-to-Passed did not remove row from Discovery**
`row.copy()` retained the `_row_id` key added at build time, so
`discovery.remove(orig)` never matched the stored dict and silently failed.
Fixed by parsing the index from `_row_id` (format `d_{idx}`) and using
`list.pop(idx)` directly.

**v2.7 — Stale exclusion list cache not cleared by direct Firestore edits**
The `@st.cache_data(ttl=60)` cache on `get_exclusion_list` is only cleared by
`save_exclusion_list()` (called via Config tab `×` button). Edits made directly
in the Firestore console bypass this, leaving the parse cache stale for up to
60 seconds. Fixed by force-clearing `get_exclusion_list` and `get_company_keywords`
caches at the start of every file upload, so the parse always uses fresh Firestore data.

---

## Frontend Refactoring — Completed (v2.14–v2.15)

`frontend/app.py` was split into five modules (see "What Was Built This Session" above).
`app.py` is now ~1,158 lines (tab rendering + CSS only).

**Constraint still applies:** `frontend/` cannot import from `backend/`. Parse logic
remains duplicated between `frontend/parse_helpers.py` and `backend/lseg_excel_provider.py`.

---

## What Was Built / Changed Previously (v2.1–v2.3)

### Trust / Fund Company Name Filter (Filter 2.5)

A new filter stage was added to the LSEG Excel parse pipeline, sitting between the
universe filter (Filter 2) and the announcement type filter (Filter 3).

**Mechanism:** case-insensitive substring match on the parsed company name.

**Keywords (both copies must be kept in sync):**
- `backend/lseg_excel_provider.py` → `TRUST_COMPANY_KEYWORDS`
- `frontend/app.py` → `_TRUST_COMPANY_KEYWORDS`

Current keywords: `["trust", "trst", "income", "growth", "grwth", "fund"]`

Matched rows route to `suppressed` (auditable), not silently discarded.

### Ingest Table — Date Column Added

The passed-rows table in the Ingest tab now shows `Date` (`dd Mon`) immediately before
`Time`, so announcements can be placed in chronological context without counting on the
user remembering which day the file was exported.

### Streamlit Deprecation Fix

`use_container_width=True` → `width="stretch"` on the Ingest dataframe.

---

## Pipeline Summary

| Component | Status | Detail |
|---|---|---|
| Universe | Live | 847 companies (93 muted); no market cap ceiling in code; file import via UI or `import_universe_csv.py` |
| Companies House | Live | 673 companies matched, full scan daily cron at 07:00 UTC, 2-day window |
| LSEG Excel ingestion | Live (interactive) | Human-triggered via Ingest tab — confirmed working end-to-end |
| Job runner | Live | Running as systemd service on VM, confirmed processing jobs |
| Google News / CSE | Parked | See SOBER_ASSESSMENT_v1.md |
| LLM analysis | Live | Gemini 2.0 Flash, regulatory catalyst lens |
| Notifications | Live | Telegram |
| Dashboard | Live | Local hosting (WSL2, port 8501) — v2.18, five tabs all working |
| Cron schedule | Live | 07:00 UTC daily, logs to `~/pipeline.log` |

---

## Next Steps (Prioritised)

### Phase 1 — Reduce manual toil

1. ~~**Headless browser body retrieval**~~ ✓ Complete (v2.16–v2.18) — "Auto-fetch & submit"
   button in the Analyse sub-form. Playwright fetches body and auto-submits on success.

2. **Headless browser index page scraping** — automate the "Fetch" Excel-upload step.
   Playwright scrapes the LSEG news explorer table. Replaces the manual Export → Upload
   flow with a single "Fetch" button in the Ingest tab.

### Phase 2 — Build first lens

3. **Lens 1 workshop** — manual validation of director/PDMR buying signal quality.
   Review historical PDMR announcements against subsequent price action to calibrate
   thresholds before committing to code.

4. **Signal state + position state schema** — design and implement in Firestore.
   Two-axis model: Signal State (system-managed: Watching → Monitor → Signal Active →
   Signal Reinforced/Mixed/Negative) + Position State (human-managed: Acted/Deferred/
   Declined/Closed). See `SESSION_SUMMARY_27FEB2026.md` for full spec.

5. **Lens 1 implementation** — Director/PDMR Open-Market Buying as new `StrategyLensBase`.
   Update notification logic to use state model.

### Phase 3 — Expand

6. Lens 2 — Significant Shareholder Accumulation (TR-1)
7. Lens 3 — Share Buyback Momentum (requires automated Transaction in Own Shares tracking)
8. Accumulate signal data for Lens 5 (Signal Convergence) once 2+ lenses are operational

### Deprioritised (not abandoned)

| Item | Status |
|---|---|
| Batch-action UX (multi-row Ingest/Universe operations) | Deferred — latency is a nuisance, not a blocker |
| Signals tab card layout review | Deferred |
| Discovery tab removal/repurposing | Deferred until RNS direct feed in scope |
| 🔇 Mute on Discovery rows is redundant | Deferred minor cleanup |
| RNS direct feed (EODHD ~$19–79/mo) | Priority 1 paid upgrade — not yet investigated |
| Historic signal impact analysis | Prerequisite: RNS feed + price data |
| Step 12: LLM params, universe criteria in config store | Foundation in place |

### Completed This Session

- Frontend refactoring (v2.14–v2.15) — `app.py` split into 5 modules ✓
- Local hosting setup — `start_frontend.sh`, `stop_frontend.sh`, `frontend/.env` ✓
- Exclusion list expanded — results, gearing, presentations, dividends added ✓
- Transaction in Own Shares moved to excluded ✓
- Lens workshop — 5 candidates documented, state model designed ✓
- Playwright auto-fetch & submit (v2.16–v2.18) — `frontend/lseg_scraper.py` + button ✓
- Error persistence fix — fetch errors survive `st.rerun()` via session state ✓

### Previously Completed

- Trust/fund company name filter (Filter 2.5) — Firestore-managed keywords ✓
- Universe file import via UI ✓
- CH pipeline scan fix (was silently scanning only ~6 companies) ✓
- Mute respected by autonomous pipeline ✓
- Dedup key alignment (URL-first) ✓
- All five tabs confirmed working against live data ✓

---

## Current Exclusion List (source of truth: Firestore app_config/lseg_filters)

Manageable via sidebar Filtration Rules panel.

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
| `Transaction in Own Shares` | Daily buyback disclosure — signal is aggregate pattern, not per-announcement |
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

**Intentionally NOT excluded:** `TR-1` — major holdings crossings are on-thesis
(informed party building a position). LLM evaluates these.

---

## Ingest Tab — Pre-filter Behaviour

The exclusion list lives in Firestore `app_config/lseg_filters`. Changes via the
sidebar Filtration Rules panel take effect immediately — the parse cache is keyed on
`(filename, tuple(excluded_types))` so any edit triggers a re-parse.

With the 23 Feb 2026 full AIM + Small Cap dump (653 rows, pre-trust filter):
- 69 skipped (non-RNS)
- 293 suppressed by type filter
- 263 passed to Step 2
- 28 routed to Discovery

---

## Step 12 — Preference and Context Store (partial)

**Done:** Filtration rules config store. Firestore `app_config` collection established
as the pattern for future preferences.

**Remaining:** LLM parameters, universe criteria, notification thresholds, investment
thesis parameters. These can be added as further documents in `app_config`.

---

## Streamlit Community Cloud — Secret Format

In Streamlit Community Cloud → App Settings → Secrets, the GCP service account is
added in TOML format:

```toml
[gcp_service_account]
type = "service_account"
project_id = "stock-research-poc"
private_key_id = "..."
private_key = "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
```

Values come from `backend/gcp-credentials.json` on the VM. The `private_key` value
requires literal `\n` characters preserved.

---

## VM Operations

```bash
# Connect
ssh gcp-backend   # IAP tunnel alias defined in ~/.ssh/config

# Activate venv
source ~/stock-research/backend/venv/bin/activate

# Deploy latest code (pull + restart job_runner)
~/deploy.sh

# Run pipeline manually
cd ~/stock-research/backend && python pipeline.py

# Run job runner manually (for testing)
cd ~/stock-research/backend && python job_runner.py

# Check job runner
sudo systemctl status job_runner
journalctl -u job_runner -f

# Check cron log
tail -50 /home/danjmorris/pipeline.log

# Check cron schedule
crontab -l
# 0 7 * * * cd /home/danjmorris/stock-research && /home/danjmorris/stock-research/backend/venv/bin/python backend/pipeline.py >> /home/danjmorris/pipeline.log 2>&1
```

---

## News Ingestion — Current State

### Active sources
- **Companies House** (`companies_house_connector.py`) — sole autonomous source.
  Filings are secondary confirmations of RNS events.
- **LSEG Excel** (`lseg_excel_provider.py`) — interactive path. Human exports from
  LSEG web interface, uploads to Ingest tab. Confirmed working end-to-end.

### Parked sources
- **Google News / CSE** — assessed as solving the wrong problem. See `SOBER_ASSESSMENT_v1.md`.
- **NewsAPI.org** (`newsapi_connector.py`) — parked.

### Upgrade path
- **RNS direct feed** — EODHD (~$19–79/month) or LSEG (enterprise). Priority 1
  paid upgrade. Would shift autonomous pipeline from secondary confirmation to
  primary signal detection.

---

## Pending Jobs Document Structure

Three job types are handled by `job_runner.py`. All share base fields:
```
"status": "pending" | "processing" | "complete" | "failed"
"submitted_at": SERVER_TIMESTAMP
"claimed_at": ISO string (set by job runner)
"processed_at": ISO string (set on completion)
"note": str | None (e.g. "deduplicated", "admitted: CH=matched")
"error": str | None (set on failure)
```

**`lseg_ingest`** — written by `submit_job()` in Ingest tab:
```
"job_type": "lseg_ingest"
"ticker", "company_name", "headline", "body", "source_url", "published_at"
"price": float | None, "price_change": str | None
```

**`universe_admit`** — written by `submit_universe_admit_job()` in Universe/Discovery tabs:
```
"job_type": "universe_admit"
"ticker", "company_name", "market_cap_gbp", "listing_exchange"
"not_of_interest": bool, "source_discovery_id": str | None
```

**`universe_bulk_import`** — written by `submit_universe_bulk_import_job()` in Universe tab:
```
"job_type": "universe_bulk_import"
"new_companies":    [{ticker, company_name, market_cap_gbp, listing_exchange, tier}, ...]
"update_companies": [{ticker, company_name, market_cap_gbp, listing_exchange, tier}, ...]
"remove_tickers":   [str, ...]
"mute_tickers":     [str, ...]
```

---

## Universe Management

Two paths for bulk universe refresh:

**Path A — Backend script (full replace, takes ~8–9 min):**
1. Replace `docs/AIM_data_complete_*.csv` and/or `docs/FTSE_AllShare_complete_*.csv`
2. Commit to git and deploy to VM (`git pull`)
3. On VM: `python import_universe_csv.py`
4. Verify: `python pipeline.py` should report updated company count

**Path B — UI import (delta only, no VM CLI required):**
1. Open Universe tab → `📂 Import from file`
2. Upload a CSV with columns: `Exchange, Code, Name, Market Cap` (market cap in £M)
3. Review the computed delta (new / update / absent)
4. For absent companies: optionally set Mute or Remove per-row (default is leave)
5. Click **Commit import** — job submitted to VM job_runner
6. Job_runner processes: removes → mutes → updates (merge=True) → new CH lookups
   Progress visible in Job Status section of Ingest tab

Note: Path B is delta-based — manually added companies not in the file are preserved.
Path A is destructive (full replace via `save_universe()`).

---

## File Map

| File | Role |
|------|------|
| `pipeline.py` | Core pipeline — entry point for daily cron runs |
| `job_runner.py` | Interactive ingestion job queue worker — runs continuously |
| `lseg_excel_provider.py` | `LSEGExcelProvider` — parses LSEG Excel exports |
| `import_universe_csv.py` | Manual universe import: CSV → CH lookup → Firestore |
| `newsapi_connector.py` | `NewsAPIProvider` — parked (formerly rns_connector.py) |
| `google_news_connector.py` | `GoogleNewsProvider` — parked |
| `companies_house_connector.py` | CH filing ingestion, rate-limited at 0.6s/request |
| `storage_firestore.py` | `FirestoreProvider` — signal/discovery results + deduplication |
| `storage_firestore_universe.py` | `FirestoreUniverseProvider` — universe read/write |
| `lens_regulatory_catalyst.py` | Strategy lens + LLM prompt builder |
| `abstractions.py` | All seven abstract base classes + dataclasses |
| `universe.py` | Static 5-company list — reference only, not used by pipeline |
| `systemd/job_runner.service` | systemd unit for VM autostart |
