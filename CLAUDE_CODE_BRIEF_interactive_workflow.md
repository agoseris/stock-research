# Claude Code Session Brief
## Interactive Workflow & Streamlit Community Cloud Deployment
**Date:** 24 February 2026
**For:** Claude Code in VS Code

---

## Context — What This Project Is

An AI-assisted stock research tool for LSE small-cap investing. The system identifies
situations where informed parties express confidence through their actions rather than
their words. It ingests financial announcements, filters them against an investment
thesis, and uses an LLM to assess whether they represent meaningful signals.

The full architecture is documented in `CLAUDE.md`. Read that file first.
The current session state is documented in `HANDOVER.md`. Read that second.

---

## What Has Changed — The New Operational Model

The system previously operated as a daily batch pipeline triggered by cron at 07:00
UTC. A new human-in-the-loop ingestion workflow has been designed that requires
interactive, event-driven operation rather than scheduled batch execution.

The human now triggers pipeline execution through the UI, not via cron.
The Streamlit interface must therefore be always-on and accessible from a browser,
not run locally on demand.

---

## Architectural Decision — Accepted and Final

The following architecture has been decided. Do not propose alternatives.

**Streamlit Community Cloud** hosts the frontend UI:
- Always-on, accessible from any browser
- HTTPS and access controls handled by Streamlit's platform
- Connected to Firestore via service account key stored as a Streamlit secret
- Deployed automatically from GitHub on push

**GCP VM** remains the headless execution engine:
- Never internet-exposed — no nginx, no open ports required
- Runs a lightweight job runner (Python loop or APScheduler) polling a Firestore
  `pending_jobs` collection
- Executes LLM analysis and pipeline logic when jobs arrive
- Writes results back to Firestore
- Existing cron pipeline can coexist alongside the job runner

**Firestore** is the communication layer between UI and VM:
- New collection: `pending_jobs` — UI writes jobs here, VM picks them up
- Existing collections unchanged: `signal_results`, `discovery_results`,
  `announcements`, `universe_companies`, `universe_refresh_log`

**Key principle:** the VM is a headless worker. The UI is a thin client.
All state lives in Firestore. The two components are decoupled.

---

## New Ingestion Workflow — Five Stages

This is the workflow the new UI must support end-to-end.

**Stage 1 — Human export (outside the system)**
Human filters LSEG web interface (source=RNS, market=AIM/Small Cap) and exports
to Excel. This happens in a browser — nothing to build here.

**Stage 2 — Excel upload and pre-filtering (new UI + new backend component)**
Human uploads the Excel file via the Streamlit UI.
System parses the file and runs two pre-filter passes:
1. Universe filter — check ticker against Firestore universe_companies collection
2. Announcement type filter — remove routine noise based on headline string

No LLM calls at this stage. Existing SHA-256 Firestore deduplication runs automatically.

**Stage 3 — Filtered list display (new UI)**
Streamlit displays the filtered subset as a table.
Each row must include a clickable URL linking to the full announcement on LSEG.
Human reviews and selects which announcements to submit for LLM analysis.

**Stage 4 — Body paste and job submission (new UI + job queue)**
Human clicks through to LSEG, reads the announcement, copies the body text.
Human pastes body text into a Streamlit input field alongside the ticker.
Human submits — UI writes a job document to Firestore `pending_jobs` collection.

**Stage 5 — VM processes job and results appear (job runner + existing pipeline)**
VM job runner picks up the pending job from Firestore.
Runs existing LLM analysis (lens_regulatory_catalyst.py or appropriate lens).
Writes result to signal_results or discovery_results as appropriate.
Sends Telegram notification if signal is high-confidence.
UI polls Firestore and displays result when ready.

---

## New Component: LSEGExcelProvider

A new `AnnouncementProviderBase` implementation to be created at
`backend/lseg_excel_provider.py`.

### Excel File Structure (confirmed from sample data)

The file has no header row. Columns are:

| Col | Content | Notes |
|-----|---------|-------|
| 1 | `[Company Name] - [Ticker] - [Announcement Type]` | Hyperlink embedded on this cell |
| 2 | Source | `RNS`, `GNW`, `PRN`, `BZN` etc |
| 3 | Date | Format: `DD.MM.YY` e.g. `23.02.26` |
| 4 | Time | `datetime.time` object e.g. `16:06:03` |
| 5 | Price (pence) | Numeric, may be `-` for no price |
| 6 | Price change % | String e.g. `1.59%`, may be `-` |

The hyperlink URL on Column 1 is in the format:
`https://www.londonstockexchange.com/news-article/[TICKER]/[slug]/[ID]`

Extract the URL using `openpyxl` — it is stored as `cell.hyperlink.target`.
Extract ticker by parsing the combined string: split on ` - ` and take index 1.
Extract announcement type by taking index 2 onwards (may contain further ` - `).

### Pre-filter logic

**Source filter:** only process rows where Column 2 == `RNS`. Skip BZN, GNW, PRN,
Reach. Log skipped rows with reason.

**Universe filter:** look up parsed ticker in Firestore `universe_companies`
collection. If not found, route to discovery (do not discard — log as discovery
candidate with the URL preserved).

**Announcement type exclusion list** (configurable, not hardcoded):
The following strings in the announcement type field should be filtered out at
this stage with no LLM call:

```python
EXCLUDED_ANNOUNCEMENT_TYPES = [
    "Holding(s) in Company",
    "TR-1",
    "Transaction in Own Shares",
    "Notice of AGM",
    "Notice of Results",
    "Annual Report",
    "Half-Year Report",
    "Interim Report",
    "Confirmation Statement",
    "Change of Registered Office",
    "Change of Nominated Adviser",
    "Change of Broker",
    "Total Voting Rights",
    "Blocklisting Interim Review",
    "Publication of Prospectus",
    "Result of AGM",
]
```

This list must be stored as a configurable constant, not inline logic, so it can
be extended without modifying core code.

**Important:** announcements passing the universe filter but failing the type filter
should still be logged to the suppression log (when that feature is built — for now,
print to console with reason).

---

## New Component: Job Runner

A new script at `backend/job_runner.py`.

Polls Firestore `pending_jobs` collection every 10 seconds.
When a job is found with status `pending`:
1. Sets status to `processing` (prevents double-processing)
2. Extracts ticker, company_name, headline, body, source_url, published_at,
   price, price_change from the job document
3. Constructs an `Announcement` object
4. Runs it through the existing lens pipeline (same logic as pipeline.py)
5. Writes result to signal_results or discovery_results
6. Sets job status to `complete` or `failed`
7. Sends Telegram notification if appropriate

The job runner should run continuously as a separate process on the VM.
Add a systemd service file at `backend/systemd/job_runner.service` so it starts
on VM boot and restarts on failure.

**Firestore pending_jobs document structure:**
```
{
  "status": "pending" | "processing" | "complete" | "failed",
  "submitted_at": timestamp,
  "processed_at": timestamp (set on completion),
  "ticker": str,
  "company_name": str,
  "headline": str,
  "body": str,
  "source_url": str,
  "published_at": timestamp,
  "price": float | None,
  "price_change": str | None,
  "error": str | None (set on failure)
}
```

---

## Frontend Changes: frontend/app.py

The existing Streamlit app has two tabs: Signals and Discovery. Retain these.
Add a third tab: **Workflow** (or **Ingest** — your call on naming).

### Workflow tab — three sections

**Section 1: Upload and Pre-filter**
- File uploader widget accepting .xlsx files
- On upload: parse via LSEGExcelProvider logic, run pre-filters
- Display summary: X rows received, Y passed universe filter, Z passed type filter,
  N deduplicated (already seen)
- Display filtered results as a dataframe with columns:
  Company, Ticker, Announcement Type, Time, Price, Price Change, URL
- URL column must render as clickable links
- Each row has a checkbox or button: "Submit for analysis"

**Section 2: Submit for Analysis**
- For each selected row, show a text area for pasting the announcement body
- Ticker and headline pre-populated from the selected row
- Submit button writes job to Firestore pending_jobs
- Confirmation shown: "Job submitted for [TICKER] — [Headline]"

**Section 3: Job Status**
- Shows recent pending_jobs from Firestore with their status
- Auto-refreshes (use st.rerun() with a timer or manual refresh button)
- Links to completed signals in the Signals tab

### Universe Lookup (add to existing interface, not the Workflow tab)
Add a sidebar widget: enter company name or ticker, return:
- Universe membership (yes/no)
- CH confidence score if matched
- Count of existing signals recorded against that company
- Link to most recent signal if any

---

## Streamlit Community Cloud Deployment

### What needs to change for cloud deployment

1. `frontend/app.py` must not import from `backend/` directly.
   It already communicates via Firestore only — verify this is clean.

2. A `requirements.txt` for the frontend must exist at `frontend/requirements.txt`
   (separate from `backend/requirements.txt`). It needs at minimum:
   `streamlit`, `google-cloud-firestore`, `openpyxl`

3. Firestore credentials: on Streamlit Community Cloud, the GCP service account
   JSON is stored as a Streamlit secret. The frontend code must read credentials
   from `st.secrets` rather than from a file path.
   Add a credential loader that tries `st.secrets` first, falls back to the
   environment variable path for local development.

4. A `.streamlit/config.toml` may be needed for theme or server settings.

### Deployment steps (for human to execute after build)
1. Push completed code to GitHub
2. Go to share.streamlit.io, connect GitHub repo, set `frontend/app.py` as the
   entry point
3. Add Firestore service account JSON as a secret named `gcp_service_account`
4. Deploy

---

## Key Design Principles — Do Not Violate

These are non-negotiable and must be respected throughout:

1. **Abstraction integrity:** all seven abstractions in `abstractions.py` must be
   respected. `LSEGExcelProvider` must implement `AnnouncementProviderBase`.
   Concrete providers are instantiated at entry points and injected — never
   instantiated inline in pipeline or UI logic.

2. **Anti-bias:** the announcement type exclusion list must be a configurable
   constant, not implicit logic. Nothing is filtered silently — suppression reasons
   are logged.

3. **Universe discipline:** the universe expands only through explicit human
   decision. Non-universe announcements from the Excel feed go to discovery,
   not the bin.

4. **Frontend/backend separation:** `frontend/app.py` communicates with the backend
   exclusively via Firestore. No direct imports from `backend/`.

5. **Free first:** no new paid services. The job runner uses existing Gemini API
   free tier. Firestore usage remains within free tier limits at these volumes.

6. **Auditability:** every filtering decision must be logged with a reason.
   The suppression log (when built) must capture Excel feed filtering decisions
   alongside pipeline filtering decisions.

---

## What Not To Change

- `backend/pipeline.py` — the existing cron pipeline continues to run unchanged
- `backend/abstractions.py` — no modifications to abstract base classes
- All existing Firestore collections and their document structures
- The existing Signals and Discovery tabs in `frontend/app.py`
- The Telegram notification logic
- The cron schedule on the VM

---

## Suggested Build Order

1. `backend/lseg_excel_provider.py` — parse, filter, return structured results
2. `frontend/app.py` — add Workflow tab and Universe Lookup sidebar widget
3. `backend/job_runner.py` — Firestore job queue polling and execution
4. `backend/systemd/job_runner.service` — systemd service definition
5. `frontend/requirements.txt` — frontend-specific dependencies
6. Credential loader update in `frontend/app.py` for Streamlit secrets
7. Test end-to-end with the sample Excel file (62 rows, 23 Feb 2026)

---

## Sample Data Available

A sample Excel export is available at:
`docs/LSEG_news_capture.xlsx` (or check uploads — copy here if not already present)

62 rows, single day (23 February 2026), AIM + Small Cap, mixed sources.
Use this for development and testing throughout.

Expected behaviour on this sample:
- Source filter removes non-RNS rows (GNW row 27, PRN row 62)
- Universe filter removes companies not in the 845-company Firestore universe
- Type filter removes routine announcements (Holdings, Transactions in Own Shares etc)
- Remaining rows should include items like:
  - Row 6: One Health Group — Planning sign off (regulatory catalyst signal)
  - Row 25: Empyrean Energy — Cash Call dispute Settlement
  - Row 39: Helix Exploration — Successful first Helium Gas Production
  - Row 5: James Halstead — PDMR Acquisition of Shares (director buying signal)
