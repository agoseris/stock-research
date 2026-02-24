# Session Handover
**Date:** 24 February 2026
**Branch:** `master`
**Last commit:** `08561e6` — "Add interactive ingestion workflow: LSEG Excel → Firestore job queue"

---

## Current State

The pipeline is fully operational end-to-end on a daily cron schedule. A new
human-in-the-loop interactive ingestion workflow has been built and committed.
The Streamlit frontend is ready for Streamlit Community Cloud deployment.

---

## What Was Built This Session

### Interactive Ingestion Workflow

The system previously relied solely on Companies House filings, which are secondary
confirmations of RNS events (see `SOBER_ASSESSMENT_v1.md` for the full analysis).
A human-in-the-loop workflow has been added to enable direct RNS ingestion without
a paid API feed.

**Flow:**
1. Human filters LSEG web interface (Source=RNS, Market=AIM/Small Cap), exports to Excel
2. Human uploads Excel to the Streamlit Ingest tab
3. System parses and pre-filters: source filter (RNS only) → universe filter → type filter
4. Human reviews filtered table with clickable LSEG URLs, selects items of interest
5. Human pastes announcement body text from LSEG → submits job to Firestore `pending_jobs`
6. Job runner on VM picks up the job, runs full LLM analysis, writes result to Firestore
7. Result appears in the Signals tab; Telegram notification sent if high-confidence

### New Files

| File | Description |
|---|---|
| `backend/lseg_excel_provider.py` | `AnnouncementProviderBase` impl — parses LSEG Excel exports |
| `backend/job_runner.py` | Firestore job queue worker — polls `pending_jobs`, runs pipeline |
| `backend/systemd/job_runner.service` | systemd unit for always-on VM operation |
| `frontend/requirements.txt` | Streamlit Community Cloud deployment dependencies |
| `docs/LSEG_news_capture.xlsx` | Sample data: 62 rows, 23 Feb 2026, AIM + Small Cap |
| `SOBER_ASSESSMENT_v1.md` | Post-PoC evaluation — gaps, upgrade path, pinned items |
| `CLAUDE_CODE_BRIEF_interactive_workflow.md` | Build brief for this session |

### Modified Files

| File | Change |
|---|---|
| `frontend/app.py` | Third tab (Ingest), sidebar Universe Lookup, `st.secrets` credential loader |
| `backend/requirements.txt` | `openpyxl` added |

### Refactoring (same session, earlier commit `644a4a5`)

- Deleted `backend/universe_pipeline.py` (dormant PDF pipeline) and `backend/tests/test_01_pdf_downloads.py`
- Renamed `backend/rns_connector.py` → `backend/newsapi_connector.py`
- Archived `backend/tests/test_02` through `test_05` to `backend/tests/archive/`
- Updated stale `universe_pipeline.py` references in `import_universe_csv.py` and `universe.py`

---

## Pipeline Summary

| Component | Status | Detail |
|---|---|---|
| Universe | Live | 845 companies (547 AIM + 298 FTSE), £1B ceiling |
| Companies House | Live | 671 companies matched, daily cron at 07:00 UTC |
| LSEG Excel ingestion | Live (interactive) | Human-triggered via Ingest tab |
| Job runner | Built, not yet deployed | Needs `git pull` + systemd install on VM |
| Google News / CSE | Parked | See SOBER_ASSESSMENT_v1.md — solving wrong problem |
| LLM analysis | Live | Gemini 2.0 Flash, regulatory catalyst lens |
| Notifications | Live | Telegram |
| Dashboard | Live | Streamlit — three tabs: Signals, Discovery Queue, Ingest |
| Cron schedule | Live | 07:00 UTC daily, logs to `~/pipeline.log` |
| Streamlit Community Cloud | Ready | `frontend/requirements.txt` and credential loader in place |

---

## VM Setup Required — Job Runner

The job runner has been committed but not yet deployed to the VM. After `git pull`:

```bash
# 1. Install the systemd service
sudo cp ~/stock-research/backend/systemd/job_runner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable job_runner
sudo systemctl start job_runner

# 2. Verify it's running
sudo systemctl status job_runner
journalctl -u job_runner -f
```

The job runner polls `pending_jobs` every 10 seconds. It can run indefinitely
alongside the existing cron pipeline — there is no conflict.

---

## Streamlit Community Cloud Deployment

The frontend is ready for Community Cloud deployment. Steps (human to execute):

1. Push to GitHub (done — code is on master)
2. Go to `share.streamlit.io`, connect repo, set `frontend/app.py` as entry point
3. Add Firestore service account JSON as a secret named `gcp_service_account`
4. Deploy

The credential loader in `get_db()` tries `st.secrets["gcp_service_account"]` first,
falls back to `GOOGLE_APPLICATION_CREDENTIALS` env var for local development.

---

## News Ingestion — Current State

### Active sources
- **Companies House** (`companies_house_connector.py`) — sole autonomous source.
  Filings are secondary confirmations of RNS events (see `SOBER_ASSESSMENT_v1.md`).
- **LSEG Excel** (`lseg_excel_provider.py`) — interactive path. Human exports from
  LSEG web interface, uploads to Ingest tab. Provides genuine primary RNS access.

### Parked sources
- **Google News / CSE** — assessed as solving the wrong problem. News aggregators
  are structurally late relative to RNS. Resource cost of reactivating (GCP account
  upgrade) not justified. See `SOBER_ASSESSMENT_v1.md` §6.
- **NewsAPI.org** (`newsapi_connector.py`, formerly `rns_connector.py`) — parked.

### Upgrade path
- **RNS direct feed** — EODHD (~$19–79/month) or LSEG (enterprise). This is Priority 1
  from the paid upgrade path. Would shift the autonomous pipeline from secondary
  confirmation to primary signal detection.

---

## Ingest Tab — Pre-filter Behaviour

The `EXCLUDED_ANNOUNCEMENT_TYPES` list in both `backend/lseg_excel_provider.py` and
`frontend/app.py` controls what is silently suppressed at the type filter stage.
Both copies must be kept in sync. The list is a configurable constant — extend it
without modifying filter logic.

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

# Check job runner (after systemd install)
sudo systemctl status job_runner
journalctl -u job_runner -f

# Check cron log
tail -50 ~/pipeline.log

# Check cron schedule
crontab -l
# Should show: 0 7 * * * cd /home/danjmorris/stock-research && .../python backend/pipeline.py >> ~/pipeline.log 2>&1
```

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

## Pinned Items (from SOBER_ASSESSMENT_v1.md)

| Item | Status |
|---|---|
| RNS direct feed (EODHD ~$19–79/mo) | Not yet investigated — Priority 1 paid upgrade |
| Director/Insider buying lens workshop | Requires RNS feed — manual workshop viable now |
| Historic signal impact analysis | Prerequisite: RNS feed + price data |
| Preference and Context Store (Step 12) | Not yet built — prerequisite for full autonomy |
| Streamlit Community Cloud deployment | Ready — needs human to execute deploy steps |
| Job runner systemd install on VM | Built — needs human to install after `git pull` |
