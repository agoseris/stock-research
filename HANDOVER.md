# Session Handover
**Date:** 25 February 2026
**Branch:** `master`
**Last commit:** pending (this session's work)

---

## Current State

The pipeline is fully operational end-to-end. The interactive LSEG Excel ingestion
workflow is working correctly in production (Streamlit Community Cloud + VM job runner).
Two signals were successfully generated and confirmed in the Signals tab during this
session.

---

## What Was Fixed / Built This Session

### Firestore Fixes (job runner)

| Issue | Fix |
|---|---|
| `UserWarning: positional .where() arguments` | Updated to `filter=FieldFilter(...)` syntax |
| `Poll error: 400 — query requires an index` | Created composite index on `pending_jobs (status ASC, submitted_at ASC)` via Firebase Console link in error message |

### Streamlit Community Cloud Deployment

- App deployed and confirmed working at `share.streamlit.io`
- `gcp_service_account` secret added (TOML format — see below)
- Initial `OSError` on first load was a typo in the secret — corrected by user

### LSEG Excel Parser Fix (`\xa0` non-breaking space)

**Root cause:** LSEG Excel exports use `\xa0` (non-breaking space) as the separator
between ticker and announcement type in Col 1, not a regular space. The `" - ".split()`
only fired once, concatenating ticker + type into a single field (e.g.
`"PULS -\xa0Holding(s) in Company"` instead of `"PULS"`). Zero rows were passing the
universe filter as a result.

**Fix:** `.replace("\xa0", " ")` before splitting, applied in both:
- `frontend/app.py` `_parse_lseg_excel()`
- `backend/lseg_excel_provider.py` `_parse_col1()`

### UI Improvements

- `VERSION` constant added to `frontend/app.py` (currently `1.3`). Displayed in
  terminal header. **Must be incremented on every edit** — see CLAUDE.md rule.
- LSEG URL now remains visible after job submission (previously hidden by `continue`)
- Debug lines removed (were added temporarily to diagnose the `\xa0` issue)

### Filter Tuning — Pending

293 rows are correctly suppressed; 263 pass to Step 2. However, some announcement
types are passing that are purely administrative with no investment signal value.
Identified so far:

- `Director Declaration` — director appointment/resignation. Should be suppressed.

**Next session:** review the full list of passing announcement types and extend
`EXCLUDED_ANNOUNCEMENT_TYPES` in both `frontend/app.py` and
`backend/lseg_excel_provider.py`. Both copies must stay in sync.

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
| Streamlit Community Cloud | Live | Deployed and confirmed working |

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
tail -50 ~/pipeline.log

# Check cron schedule
crontab -l
# Should show: 0 7 * * * cd /home/danjmorris/stock-research && .../python backend/pipeline.py >> ~/pipeline.log 2>&1
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

## Ingest Tab — Pre-filter Behaviour

The `EXCLUDED_ANNOUNCEMENT_TYPES` list in both `backend/lseg_excel_provider.py` and
`frontend/app.py` controls what is suppressed at the type filter stage.
**Both copies must be kept in sync.** The list is a configurable constant — extend it
without modifying filter logic.

With the 23 Feb 2026 full AIM + Small Cap dump (653 rows):
- 69 skipped (non-RNS)
- 293 suppressed by type filter
- 263 passed to Step 2
- 28 routed to Discovery

**Known gap:** `Director Declaration` is passing but should be suppressed. Review and
extend the list at the start of the next session.

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
| Extend `EXCLUDED_ANNOUNCEMENT_TYPES` — `Director Declaration` + full review | **Next session — Priority 1** |
| RNS direct feed (EODHD ~$19–79/mo) | Not yet investigated — Priority 1 paid upgrade |
| Director/Insider buying lens workshop | Requires RNS feed — manual workshop viable now |
| Historic signal impact analysis | Prerequisite: RNS feed + price data |
| Preference and Context Store (Step 12) | Not yet built — prerequisite for full autonomy |
