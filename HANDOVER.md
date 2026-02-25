# Session Handover
**Date:** 25 February 2026
**Branch:** `master`
**Last commit:** d434ef8 — Fix ingest cache: re-parse Excel when exclusion list changes (v1.7)

---

## Current State

The pipeline is fully operational end-to-end. The interactive LSEG Excel ingestion
workflow is working correctly in production (Streamlit Community Cloud + VM job runner).
Step 12 (Preference and Context Store) is now partially implemented — the filtration
rules config store is live.

---

## What Was Fixed / Built This Session

### Filter Tuning — COMPLETED

Reviewed the full list of announcement types passing the pre-filter using the 23 Feb
2026 dump. Key insight: the "type" field in LSEG Col 1 is **not a controlled taxonomy**
for genuine business announcements — it is the free-form RNS headline. Only admin
filings use consistent taxonomy labels (TR-1, Holding(s) in Company, etc.).

Consequence: the exclusion list should only target confirmed, consistently-labelled
LSEG admin types. Trying to block types like "Contract Award" or "Acquisition" is
wrong — those appear as free-form headlines and would never match.

**Changes made:**
- Added `Director Declaration` and `Conversion of B Shares` to exclusion list (v1.4)
- Removed `TR-1` and `Transaction in Own Shares` from exclusion list after review:
  - TR-1 upward crossings = informed party building a position = on-thesis
  - Buybacks = management capital deployment = on-thesis
  - LLM is better placed to evaluate these case-by-case (v1.5)

### Sidebar Fix

**Root cause:** `initial_sidebar_state="collapsed"` + `header { visibility: hidden; }`
CSS hid the sidebar toggle button (inside `<header>`).

**Fix:** `initial_sidebar_state="expanded"` + CSS override
`[data-testid="collapsedControl"] { visibility: visible !important; }` (v1.6)

### Step 12 — Preference and Context Store (partial)

**New Firestore collection: `app_config`**
- Document `lseg_filters`: `{ "excluded_announcement_types": [...] }`
- Seeds from `_DEFAULT_EXCLUDED_TYPES` on first app load if document absent

**Frontend (`frontend/app.py`):**
- `get_exclusion_list(_db)` — loads from Firestore, cached 60s, seeds on first run
- `save_exclusion_list(db, excluded_types)` — writes to Firestore, clears cache
- Removed hardcoded `EXCLUDED_ANNOUNCEMENT_TYPES` constant
- `_parse_lseg_excel()` now receives `excluded_types` as parameter
- Ingest parse cache keyed on `(filename, tuple(excluded_types))` — exclusion list
  changes automatically trigger re-parse (v1.7 fix)
- **Sidebar — Filtration Rules section:** live view of exclusion list with per-entry
  `×` remove button and Add input; writes to Firestore immediately

**Backend (`backend/lseg_excel_provider.py`):**
- `LSEGExcelProvider.__init__` accepts optional `excluded_types: List[str]`
  — falls back to module-level constant if not injected
- `_matches_exclusion` is now an instance method using `self._excluded_types`
- `load_exclusion_list(db)` module-level helper for Firestore load with fallback
- `__main__` test block loads from Firestore when available; graceful fallback

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
| Streamlit Community Cloud | Live | Deployed and confirmed working, v1.7 |

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

With the 23 Feb 2026 full AIM + Small Cap dump (653 rows):
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
ssh danjmorris@<vm-ip>

# Activate venv
source ~/stock-research/backend/venv/bin/activate

# Deploy latest code
cd ~/stock-research && git pull

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

---

## Pinned Items

| Item | Status |
|---|---|
| RNS direct feed (EODHD ~$19–79/mo) | Not yet investigated — Priority 1 paid upgrade |
| Director/Insider buying lens workshop | Requires RNS feed — manual workshop viable now |
| Historic signal impact analysis | Prerequisite: RNS feed + price data |
| Step 12: extend config store to LLM params, universe criteria, thesis params | Foundation in place — `app_config` collection established |
