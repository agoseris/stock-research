# Session Handover
**Date:** 25 February 2026
**Branch:** `master`
**Last commit:** (see git log)

---

## Current State

App is at **v2.7**. The pipeline is fully operational end-to-end. The Ingest tab has
been overhauled with a unified announcement table and the deduplication system has been
fixed to use URL-based fingerprinting. Several post-launch bugs have been fixed.

---

## What Was Built / Changed This Session (v2.4–v2.7)

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
| Universe | Live | 845 companies (547 AIM + 298 FTSE), £1B ceiling |
| Companies House | Live | 671 companies matched, daily cron at 07:00 UTC |
| LSEG Excel ingestion | Live (interactive) | Human-triggered via Ingest tab — confirmed working |
| Job runner | Live | Running as systemd service on VM, confirmed processing jobs |
| Google News / CSE | Parked | See SOBER_ASSESSMENT_v1.md |
| LLM analysis | Live | Gemini 2.0 Flash, regulatory catalyst lens |
| Notifications | Live | Telegram |
| Dashboard | Live | Streamlit Community Cloud — three tabs: Signals, Discovery Queue, Ingest |
| Cron schedule | Live | 07:00 UTC daily, logs to `~/pipeline.log` |
| Streamlit Community Cloud | Live | Deployed and confirmed working, v2.1 |

---

## Next Steps (Prioritised)

### 1. UI Refinement — PRIMARY NEXT TASK

The UI "works" but has not been reviewed systematically against actual usage. The plan
is to walk through each function in the interface and assess:

- **Signals tab:** card layout, LLM output formatting, dismiss flow, sort order
- **Discovery Queue tab:** card layout, Recommend Add badge, dismiss flow
- **Ingest tab:**
  - Step 1 (upload + parse): table layout, column widths, filter summary metrics
  - Step 2 (submit for analysis): expander UX, body-paste flow, submission feedback,
    job status polling
- **Sidebar:**
  - Universe Lookup: result display, edge cases (not found, no signals)
  - Filtration Rules: exclusion list display and edit UX

No architectural changes required for this phase — it is purely a UX/display pass.

### 2. Trust / Fund Keywords — Make Maintainable via UI (SECONDARY)

Currently `TRUST_COMPANY_KEYWORDS` / `_TRUST_COMPANY_KEYWORDS` are hardcoded in both
`lseg_excel_provider.py` and `frontend/app.py`. The pattern for making them
user-editable already exists — the announcement type exclusion list uses the same
Firestore + sidebar approach.

**Proposed implementation:**
- Store keywords in Firestore `app_config/lseg_filters` as a new field:
  `excluded_company_keywords: [...]`
- Seed from `TRUST_COMPANY_KEYWORDS` constant on first load (same pattern as
  `excluded_announcement_types`)
- Load in `frontend/app.py` alongside the existing exclusion list
- Pass to `_parse_lseg_excel()` as a new parameter; include in parse cache key
- Add a "Company Name Keywords" section to the sidebar Filtration Rules panel
  (same × remove + Add input pattern)
- `LSEGExcelProvider.__init__` accepts optional `trust_keywords: List[str]` parameter
  (falls back to module constant if not injected)
- `load_exclusion_list(db)` → extend to also return `excluded_company_keywords`, or
  add a parallel `load_company_keywords(db)` helper

This is a clean extension of the existing config store pattern. No new abstractions needed.

### 3. Remaining Pinned Items

| Item | Status |
|---|---|
| RNS direct feed (EODHD ~$19–79/mo) | Not yet investigated — Priority 1 paid upgrade |
| Director/Insider buying lens workshop | Requires RNS feed — manual workshop viable now |
| Historic signal impact analysis | Prerequisite: RNS feed + price data |
| Step 12: extend config store to LLM params, universe criteria, thesis params | Foundation in place — `app_config` collection established |

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

Written by `frontend/app.py` `submit_job()`:

```
{
  "status": "pending" | "processing" | "complete" | "failed",
  "submitted_at": SERVER_TIMESTAMP,
  "claimed_at": ISO string (set by job runner),
  "processed_at": ISO string (set on completion),
  "ticker": str,
  "company_name": str,
  "headline": str,
  "body": str,
  "source_url": str,
  "published_at": datetime,
  "price": float | None,
  "price_change": str | None,
  "note": str | None (e.g. "deduplicated", "no_result"),
  "error": str | None (set on failure)
}
```

---

## Universe Management

To refresh the universe with updated CSV files:

1. Replace `docs/AIM_data_complete_*.csv` and/or `docs/FTSE_AllShare_complete_*.csv`
2. Commit to git and deploy to VM (`git pull`)
3. Run `python import_universe_csv.py` — takes ~8–9 minutes (CH API rate limit)
4. Verify: `python pipeline.py` should report updated company count

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
