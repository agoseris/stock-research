# Operational Reference — Stock Research System

*Last updated: 2 March 2026 · App version: 2.19*

---

## 1. Infrastructure

### Local Development Machine
| Item | Value |
|------|-------|
| OS | Windows 11 with WSL2 (Ubuntu) |
| Editor | VS Code with Remote-WSL extension and Claude Code |
| WSL project path | `/home/agoseris/projects/stock-research` |

### GCP Virtual Machine
| Item | Value |
|------|-------|
| Instance name | `stock-research-vm` |
| Zone | `us-central1-a` |
| GCP project ID | `stock-research-poc` |
| Machine type | e2-micro (always-free tier) |
| OS | Debian |
| VM user | `danjmorris` |
| VM project path | `/home/danjmorris/stock-research` |
| Python venv | `/home/danjmorris/stock-research/backend/venv` |
| Credentials file | `backend/gcp-credentials.json` (never committed) |
| Environment file | `backend/.env` (never committed) |
| Pipeline log | `/home/danjmorris/pipeline.log` |
| Cron schedule | 07:00 UTC daily |

Exact crontab entry:
```
0 7 * * * cd /home/danjmorris/stock-research && /home/danjmorris/stock-research/backend/venv/bin/python backend/pipeline.py >> /home/danjmorris/pipeline.log 2>&1
```

### SSH Access
| Item | Value |
|------|-------|
| Method | GCP Identity-Aware Proxy (IAP) — no public IP, no open ports |
| SSH alias | `ssh gcp-backend` |
| SSH config location | `~/.ssh/config` (WSL) |
| SSH key | `~/.ssh/id_ed25519` (WSL) |

SSH config entry:
```
Host gcp-backend
    HostName stock-research-vm
    User danjmorris
    ProxyCommand gcloud compute start-iap-tunnel %h 22 --listen-on-stdin --zone us-central1-a
    IdentityFile ~/.ssh/id_ed25519
```

Full SSH command (without alias):
```bash
gcloud compute ssh danjmorris@stock-research-vm --project stock-research-poc --zone us-central1-a --tunnel-through-iap
```

---

## 2. Version Control & Deployment

| Item | Value |
|------|-------|
| Repository | `https://github.com/agoseris/stock-research` |
| Visibility | Private |
| Default branch | `master` |

**Deploy workflow:** local edit → commit → `git push` → VM runs `git pull`.
GitHub is always the intermediary. A `deploy` alias in WSL `~/.bashrc` handles
staging, commit prompt, push, and VM pull in one step.

**VM deploy script:** `~/deploy.sh` — pulls from `master` and restarts the job runner.
Correct contents:
```bash
#!/bin/bash
cd ~/stock-research
git pull origin master
sudo systemctl restart job_runner
echo "Deployment complete."
```

> **Note:** service name is `job_runner` (underscore). `job-runner` (hyphen) will fail.

---

## 3. Services & Credentials

### Streamlit — Local Hosting (primary)
| Item | Value |
|------|-------|
| URL | `http://localhost:8501` |
| Entry point | `frontend/app.py` |
| Python venv | `frontend/venv/` |
| Credentials | `frontend/gcp-credentials.json` (never committed) |
| Env file | `frontend/.env` — sets `GOOGLE_APPLICATION_CREDENTIALS` (never committed) |
| Start | `./start_frontend.sh` from project root — nohup, PID saved to `streamlit.pid` |
| Stop | `./stop_frontend.sh` from project root |
| Logs | `logs/streamlit.log` |

**Playwright system dependencies** (one-time setup, already done on current WSL2):
```bash
source frontend/venv/bin/activate
playwright install-deps chromium
```
Required for the "Auto-fetch & submit" button in the Ingest tab. If the system libraries
are missing, Chromium launches but immediately exits with `libnspr4.so: cannot open shared
object file`. The app degrades gracefully — the button is hidden if Playwright cannot be
imported, but once `install-deps` has been run the button is available on next restart.

### Streamlit Community Cloud (archived — no longer primary)
| Item | Value |
|------|-------|
| App URL | `https://stock-research-j9xty7vswx3hxtwbk6ueol.streamlit.app/` |
| Dashboard | `https://share.streamlit.io` |
| GCP secret | Firestore service account JSON stored as `gcp_service_account` in Streamlit secrets (TOML format — see HANDOVER.md) |

> **Note:** Community Cloud deployment is no longer the primary interface. Local hosting
> is used instead, enabling future Playwright/headless browser integration. CC deployment
> remains available as a fallback but is not actively maintained.

### Google Cloud / Firestore
| Item | Value |
|------|-------|
| GCP project | `stock-research-poc` |
| Firestore mode | Native |
| Firestore region | `europe-west2` |
| Database | `(default)` |

Firestore collections:
| Collection | Purpose |
|------------|---------|
| `universe_companies` | 847-company investment universe |
| `universe_refresh_log` | Universe build history |
| `announcements` | Processed announcements (deduplication) |
| `signal_results` | LLM analysis results — monitored companies |
| `discovery_results` | LLM analysis results — new discoveries |
| `pending_jobs` | Interactive workflow job queue |
| `app_config` | Preference and configuration store (exclusion lists, future params) |

### API Credentials
All tokens stored in **password manager secure note**. Working copies in `backend/.env`
on VM only — never committed.

| Credential | Notes |
|------------|-------|
| Telegram bot token | Password manager |
| Telegram chat ID | Password manager |
| Companies House API key | Password manager |
| Gemini API key | Password manager |
| GCP service account JSON | `backend/gcp-credentials.json` on VM; reformatted copy in Streamlit secrets |

---

## 4. Data Sources

### LSEG News Explorer (Primary RNS Source)

| Filter | URL |
|--------|-----|
| **Daily operational (FTSE 250 + AIM + Small Cap)** | `https://www.londonstockexchange.com/news?tab=news-explorer&indices=MCX,AXX,SMX&period=today` |
| AIM only | `https://www.londonstockexchange.com/news?tab=news-explorer&indices=AXX&period=today` |
| FTSE Small Cap only | `https://www.londonstockexchange.com/news?tab=news-explorer&indices=SMX&period=today` |
| FTSE 250 only | `https://www.londonstockexchange.com/news?tab=news-explorer&indices=MCX&period=today` |

LSEG index codes: `MCX` = FTSE 250 · `AXX` = FTSE AIM All-Share · `SMX` = FTSE Small Cap

Notes on filter behaviour:
- `indices=` and `period=` are encoded in the URL and fully controllable
- News Type filter ("Earnings, News & Reach") is JavaScript state, not in URL — leave at default
- Pagination defaults to 20 rows; "Show 500" option available via on-page dropdown

> **Access:** Private investor exemption permits manual browsing and personal-use Excel export. Programmatic access prohibited by LSEG terms.

### Other Reference URLs
| Resource | URL |
|----------|-----|
| Directors Deals | `https://www.directordeals.co.uk` |
| Investegate | `https://www.investegate.co.uk` |
| GCP Console | `https://console.cloud.google.com` |

---

## 5. Operational Processes

### 5.1 Universe Management

The monitored universe is 847 LSE small-cap companies stored in Firestore
(`universe_companies`) and loaded by the pipeline at startup. No market cap ceiling
is enforced in code — files are pre-filtered at source (e.g. via LSEG screener)
before being committed to `docs/`.

**Source files (committed to git):**
- `docs/AIM_data_complete_*.csv`
- `docs/FTSE_AllShare_complete_*.csv`

**To refresh the universe — two paths:**

**Path A — Backend script** (full replace of Firestore universe; takes ~8–9 min):
1. Replace the CSV files in `docs/`
2. Commit and deploy to VM (`git pull`)
3. On the VM: `python backend/import_universe_csv.py`
4. Verify: `python backend/pipeline.py` should report the updated company count

**Path B — UI import** (delta only; no VM CLI required):
1. Open Streamlit app → Universe tab → `📂 Import from file`
2. Upload a CSV with columns: `Exchange, Code, Name, Market Cap` (market cap in £M)
3. Review the computed delta: how many new, updated, absent
4. For absent companies, optionally set Mute or Remove per-row (default: leave unchanged)
5. Click **Commit import** — a `universe_bulk_import` job is submitted to the VM
6. job_runner processes: removes → mutes → updates (merge=True) → new CH lookups
   (~0.6s per new company for CH lookup)

Path B is delta-based — manually added companies not in the file are preserved.
Path A uses `save_universe()` which is destructive (full collection replace).

> **Known performance limitation (Path B):** each Remove or Mute decision in the
> absent-company review table triggers an individual Streamlit rerun (2–5 seconds).
> For large absent lists (100+ rows), this is slow. A batch-select solution is planned.
> Workaround: use Path A for large universe refreshes, Path B for small delta updates.

**Universe visibility:** the sidebar Universe Lookup panel in the Streamlit app lets
you query any ticker for membership status, CH confidence score, CH number, signal
count, and most recent signal.

---

### 5.2 Companies House Integration

`backend/companies_house_connector.py` provides two functions:

1. **At import time** (`import_universe_csv.py`): each company name in the CSV is
   matched against the Companies House API by fuzzy search to obtain a CH number.
   - Confidence 1.0 = exact name match
   - Confidence 0.85–<1.0 = fuzzy match (acceptable)
   - Below 0.85 or dissolved/inactive = no match assigned
   - 671 of 845 companies are currently CH-matched

2. **At pipeline runtime** (`pipeline.py` cron, 07:00 UTC daily): filing history is
   fetched for all CH-matched companies. These filings are secondary confirmations of
   RNS events — the CH source is structurally later than primary RNS disclosure.

CH confidence flows into LLM prompts as a data-quality note. It does not suppress
signals; the human decides.

---

### 5.3 News Acquisition

Two paths exist. Both ultimately route announcements through the same LLM analysis
pipeline.

#### Autonomous path — Companies House (daily cron)
- **Trigger:** cron at 07:00 UTC on the VM
- **Script:** `backend/pipeline.py`
- **Source:** CH filing history for 671 matched companies
- **Limitation:** secondary confirmation only — CH filings appear after RNS publication
- **Log:** `~/pipeline.log` on VM

#### Interactive path — LSEG Excel ingest
- **Trigger:** human-initiated via Ingest tab in the Streamlit app
- **Script:** `frontend/app.py` (parse + UI) + `backend/job_runner.py` (LLM analysis)
- **Source:** primary RNS disclosure via LSEG web interface export
- **Process:**
  1. Export from LSEG news explorer to Excel (Download button, top right)
  2. Open the Streamlit app → Ingest tab
  3. Upload the `.xlsx` file — pre-filters are applied automatically (source, universe, type)
  4. For each announcement of interest: click URL → read on LSEG → paste body text → Submit
  5. `job_runner.py` (running as systemd service `job_runner` on the VM) picks up the
     job from `pending_jobs` and runs full LLM analysis
  6. Results appear in the Signals tab and via Telegram notification

**Pre-filter stages (Ingest tab):**
1. Source filter — non-RNS rows discarded
2. Universe filter — non-universe rows routed to Discovery queue
3. Company name filter — rows matching trust/fund keywords suppressed (Firestore-managed)
4. Muted ticker filter — rows for muted companies suppressed
5. Type filter — excluded announcement types suppressed (see 5.4 below)

---

### 5.4 Exclusion List Management

The type filter (Step 3 above) suppresses announcement types that are structurally
incapable of carrying an investment signal — administrative filings with consistent
LSEG taxonomy labels (e.g. `Holding(s) in Company`, `Annual Report`).

**The authoritative list lives in Firestore:** `app_config/lseg_filters`,
field `excluded_announcement_types` (array of strings). Matching is
case-insensitive substring — `"Annual Report"` suppresses
`"2025 Annual Report and Notice of AGM"`.

**To view or edit the list:** open the Streamlit sidebar → Filtration Rules section.
- Click `×` next to an entry to remove it
- Type in the Add field and click Add to insert a new entry
- Changes take effect on the next Excel upload (or immediately if an Excel is already loaded)

**Important:** `TR-1` is intentionally NOT excluded — major holdings crossings are
on-thesis (informed party building a position). The LLM evaluates these.

`Transaction in Own Shares` is **excluded**: individual daily buyback announcements carry
near-zero signal value. The signal is in the aggregate pattern (initiation, acceleration),
which requires Lens 3 (Share Buyback Momentum) rather than per-announcement manual review.

The `backend/lseg_excel_provider.py` module contains a fallback default list
(`EXCLUDED_ANNOUNCEMENT_TYPES`) used only when Firestore is unavailable (e.g. offline
test runs). The Firestore list is the production source of truth.

---

## 6. Daily Workflow

1. Start the app if not running: `./start_frontend.sh` → open `http://localhost:8501`
2. **Ingest tab:** click **🔄 Fetch from LSEG** — scrapes today's full RNS feed (~471 rows),
   applies all pre-filters, populates the announcement table automatically.
   *(Excel upload remains available as fallback if the fetch fails.)*
3. For announcements of interest: click **Analyse ▾** → **🔍 Auto-fetch & submit**
   (Playwright fetches body and auto-submits). Manual paste still available if needed.
4. **Signals tab:** review LLM analysis; dismiss reviewed items to archive them
5. **Discovery Queue tab:** shows post-LLM results for non-universe companies submitted
   via Ingest. If a company is worth analysing, add to universe first via Universe tab.

**Fetch implementation notes:**
- Navigates to bare LSEG News Explorer URL (no filter params — Angular filter state
  unreliable in headless mode). Expands pagination to 500 via ng-select dropdown.
- Filters to today's UTC date in Python. Universe/source/type filtering via standard pipeline.
- On failure: `st.warning()` shown in UI; check `logs/streamlit.log` for `[lseg_scraper]` lines.

---

## 7. Key File Locations

| File | Role |
|------|------|
| `start_frontend.sh` | Start Streamlit locally (nohup, port 8501, logs to logs/) |
| `stop_frontend.sh` | Stop Streamlit (kills by PID file) |
| `frontend/app.py` | Streamlit dashboard — Signals, Discovery, Universe, Ingest, Config tabs |
| `frontend/lseg_scraper.py` | Playwright scraper — LSEG announcement body + challenge gate handling |
| `frontend/requirements.txt` | Frontend Python dependencies |
| `frontend/.env` | Sets GOOGLE_APPLICATION_CREDENTIALS — not committed |
| `frontend/gcp-credentials.json` | GCP service account key — not committed |
| `backend/pipeline.py` | Autonomous cron pipeline — entry point for daily CH run |
| `backend/job_runner.py` | Interactive job queue worker — runs as systemd service |
| `backend/lseg_excel_provider.py` | LSEG Excel parser + pre-filter logic |
| `backend/import_universe_csv.py` | Universe import: CSV → CH lookup → Firestore |
| `backend/companies_house_connector.py` | CH filing ingestion and name matching |
| `backend/lens_regulatory_catalyst.py` | Strategy lens + LLM prompt builder |
| `backend/abstractions.py` | All seven abstract base classes + dataclasses |
| `backend/storage_firestore.py` | Signal/discovery results + deduplication |
| `backend/storage_firestore_universe.py` | Universe read/write |
| `backend/systemd/job_runner.service` | systemd unit for VM autostart |
| `docs/AIM_data_complete_*.csv` | AIM universe source (date-versioned) |
| `docs/FTSE_AllShare_complete_*.csv` | FTSE All-Share universe source (date-versioned) |
| `docs/LSEG_news_capture.xlsx` | Sample LSEG export — reference/testing |
| `CLAUDE.md` | Architecture and implementation reference (for Claude Code) |
| `HANDOVER.md` | Current state, exclusion list, pending jobs schema |
| `backend/gcp-credentials.json` | VM only — never committed |
| `backend/.env` | VM only — never committed |
| `~/deploy.sh` | VM home — pull script |
