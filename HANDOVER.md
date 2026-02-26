# Session Handover
**Date:** 26 February 2026
**Branch:** `master`
**Last commit:** (see git log)

---

## Current State

App is at **v2.13**. The pipeline is fully operational end-to-end. Universe management
now has a full UI path: bulk CSV import via the `📂 Import from file` expander in the
Universe tab. The £1B market cap ceiling has been removed from all code (was a historical
workaround for incomplete constituent data; files are now pre-filtered at source).

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

## Frontend Refactoring — Deferred

`frontend/app.py` is currently ~1,400 lines (a single monolithic file). This is
normal for Streamlit due to its top-to-bottom re-execution model, but the file is
at a size where it warrants eventual refactoring.

**Decision: defer until the Ingest tab is stable and before the next major feature
addition** (e.g. director/insider buying lens, new tab).

**Recommended future structure:**

```
frontend/
├── app.py                  # ~150 lines: page config, db init, tab routing only
├── styles.css              # extracted CSS block (~200 lines, currently inline)
├── firestore_helpers.py    # all Firestore read/write/cache functions (no st.* calls)
├── parse_lseg.py           # _parse_lseg_excel() — pure logic, no st.* calls
└── tabs/
    ├── signals.py          # render_signals_tab(db)
    ├── discovery.py        # render_discovery_tab(db)
    ├── universe.py         # render_universe_tab(db)
    ├── ingest.py           # render_ingest_tab(db)
    └── config.py           # render_config_tab(db)
```

**Highest-value first step when refactoring begins:** extract `firestore_helpers.py`
— it's pure Python with no `st.*` calls, zero regression risk, and immediately
reduces `app.py` by ~250 lines.

**Constraint:** `frontend/` cannot import from `backend/` (Streamlit Community Cloud
deployment). `parse_lseg.py` must remain a separate copy of the backend parse logic
(already the case today).

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
| Dashboard | Live | Streamlit Community Cloud — v2.13, five tabs: Signals, Discovery, Universe, Ingest, Config |
| Cron schedule | Live | 07:00 UTC daily, logs to `~/pipeline.log` |

---

## Next Steps (Prioritised)

### 1. UI Refinement

The UI "works" but has not been reviewed systematically against actual usage. The plan
is to walk through each function in the interface and assess:

- **Signals tab:** card layout, LLM output formatting, dismiss flow, sort order
- **Discovery Queue tab:** card layout, Recommend Add badge, dismiss flow
- **Ingest tab:** table layout, body-paste flow, job status polling
- **Sidebar:** Universe Lookup edge cases, Filtration Rules edit UX

No architectural changes required for this phase — it is purely a UX/display pass.

### 2. Remaining Pinned Items

| Item | Status |
|---|---|
| RNS direct feed (EODHD ~$19–79/mo) | Not yet investigated — Priority 1 paid upgrade |
| Director/Insider buying lens workshop | Requires RNS feed — manual workshop viable now |
| Historic signal impact analysis | Prerequisite: RNS feed + price data |
| Step 12: extend config store to LLM params, universe criteria, thesis params | Foundation in place — `app_config` collection established |

### Completed in previous sessions

- Trust/fund company name filter (Filter 2.5) — Firestore-managed keywords ✓
- Universe file import via UI ✓
- CH pipeline scan fix (was silently scanning only ~6 companies) ✓
- Mute respected by autonomous pipeline ✓
- Dedup key alignment (URL-first) ✓

---

## Current Exclusion List (source of truth: Firestore app_config/lseg_filters)

Manageable via sidebar Filtration Rules panel:

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

**Intentionally NOT excluded:** `TR-1` (major holdings crossings = on-thesis),
`Transaction in Own Shares` (buybacks = management confidence = on-thesis).

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
