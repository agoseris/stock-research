# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
# Stock Research Tool — Claude Code Context

## What This Project Is

An AI-assisted stock research tool for LSE small-cap investing (Main Market and AIM).
The system autonomously ingests financial news and regulatory announcements, filters
them against an investment thesis, and uses an LLM to assess whether they represent
meaningful catalyst opportunities. Results are delivered via Telegram and a Streamlit
interface.

**Overarching thesis:** identify LSE small-caps where informed or knowledgeable parties
have expressed confidence in an upcoming increase in value — through their actions, not
their words.

---

## Architecture: Seven Abstractions — Non-Negotiable

All components communicate through abstract base class interfaces. Concrete providers
are instantiated **once at the entry point** and injected. Never instantiate a concrete
provider inside pipeline logic, the UI layer, or another provider.

| # | Abstraction | PoC Implementation | Purpose |
|---|---|---|---|
| 1 | `AnnouncementProviderBase` | `GoogleNewsRSSProvider`, `CompaniesHouseProvider` | News and filing ingestion |
| 2 | `LLMProviderBase` | `GeminiProvider` | Analysis and reasoning |
| 3 | `NotificationProviderBase` | `TelegramNotifier` | Alert dispatch |
| 4 | `StorageProviderBase` | `FirestoreProvider` | Signal/discovery results and deduplication |
| 5 | `StrategyLensBase` | `RegulatoryCatalystLens` | Investment strategy implementation |
| 6 | `MarketDataProviderBase` | `YFinanceProvider` | Market cap, fundamentals, price, liquidity data |
| 7 | `UniverseStorageProviderBase` | `FirestoreUniverseProvider` | Universe company list and refresh log |

---

## Project File Structure

Monorepo with two top-level directories. All new universe pipeline code goes in `backend/`.

```
./
├── CLAUDE.md
├── HANDOVER.md                       # Session handover notes — update at end of each session
├── .gitignore
├── backend/
│   ├── abstractions.py               # All seven abstract base classes + dataclasses
│   ├── pipeline.py                   # Core signal pipeline orchestration — daily entry point
│   ├── import_universe_csv.py        # Manual universe import: CSV → CH lookup → Firestore
│   ├── universe.py                   # Static 5-company list — reference only, not used by pipeline
│   ├── universe_pipeline.py          # DORMANT — PDF-based dynamic pipeline, do not run
│   ├── lens_base_filters.py          # Shared universe pre-filter (passes_universe_filter)
│   ├── lens_regulatory_catalyst.py   # Strategy Lens: regulatory/planning catalysts
│   ├── storage_firestore.py          # StorageProviderBase implementation (Firestore)
│   ├── storage_firestore_universe.py # UniverseStorageProviderBase implementation (Firestore)
│   ├── market_data_yfinance.py       # MarketDataProviderBase implementation (dormant)
│   ├── llm_gemini.py                 # LLMProviderBase implementation
│   ├── telegram_notifier.py          # NotificationProviderBase implementation
│   ├── google_news_connector.py      # AnnouncementProviderBase — two-phase RSS ingestion
│   ├── companies_house_connector.py  # AnnouncementProviderBase — CH filing ingestion
│   ├── requirements.txt              # Python dependencies
│   └── .env                          # API keys — never commit, VM only
├── frontend/
│   └── app.py                        # Streamlit interface — runs locally, not on VM
└── docs/
    ├── AIM_data_complete_*.csv        # AIM universe source (date-versioned)
    ├── FTSE_AllShare_complete_*.csv   # FTSE All-Share universe source (date-versioned)
    └── universe_pipeline_brief.md    # Universe pipeline specification (for reference)
```

**Rule:** never place backend logic in `frontend/` and never import from `backend/` inside `frontend/`. The Streamlit interface communicates with the backend exclusively via Firestore.

---

## Infrastructure

- **Platform:** Google Cloud Platform (GCP)
- **VM:** e2-micro, always-free tier, Debian 6.1.162-1, user `danjmorris`
- **Storage:** Cloud Firestore, Native mode, europe-west2, default database
- **LLM:** Gemini API free tier (`gemini-2.0-flash`, `google-genai` package)
- **Notifications:** Telegram (`python-telegram-bot`)
- **Interface:** Streamlit, runs locally on Windows 11 WSL2 (Ubuntu), reads Firestore directly

### Firestore Collections

| Collection | Purpose |
|---|---|
| `announcements` | SHA-256 headline fingerprints for cross-run deduplication |
| `signal_results` | Signal queue LLM analysis results |
| `discovery_results` | Discovery queue LLM analysis results |
| `universe_companies` | NEW — one document per company, keyed by `ticker_lse` |
| `universe_refresh_log` | NEW — one document per pipeline refresh run |

---

## Environment Variables (.env)

```
COMPANIES_HOUSE_KEY=
GEMINI_KEY=
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
GOOGLE_APPLICATION_CREDENTIALS=/home/danjmorris/stock-research/backend/gcp-credentials.json
```

---

## Deployment Workflow

**Always:** local edit → commit → push to GitHub → VM pulls from GitHub.
GitHub is always the intermediary. Never push directly from local to VM.

```bash
# Deploy alias (defined in WSL ~/.bashrc)
deploy   # stages, prompts for commit message, commits, pushes, triggers VM git pull
```

VM setup before running any script:
```bash
cd ~/stock-research && source backend/venv/bin/activate
```

---

## Key Design Principles

1. **Anti-bias:** no implicit preference learning. All adaptation is explicit,
   time-bounded, and user-initiated.

2. **Auditability:** the suppression log records everything filtered out.
   The user can always see what was suppressed and why.

3. **Universe discipline:** the monitored universe is explicit and deliberate.
   Discovery is a separate queue. Admission is a human decision.

4. **Abstraction integrity:** all seven abstractions must be respected.
   Concrete providers are injected — never instantiated inline.

5. **Free first, paid when earned:** all PoC work uses free-tier sources.
   Paid upgrades are triggered by demonstrated need, not assumption.

6. **Liquidity transparency:** every universe member carries a liquidity flag.
   Signals on illiquid stocks are surfaced, not suppressed. The human decides.

---

## Known Gotchas

- **VM user:** always connect as `danjmorris`, not `agoseris`
- **File upload to VM:** use gear icon → Upload File in SSH terminal.
  GCP Cloud Shell Editor cannot access `/home/danjmorris` directly. Note that sshfs has been implemented successfully, so a local mount point for VM filesystem can be made available.
- **Large pastes:** GCP browser SSH terminal truncates large pastes.
  Use file upload instead.
- **python-telegram-bot:** async library — use `asyncio.run()` from sync context
- **Gemini package:** use `from google import genai` and `genai.Client()`.
  `google.generativeai` is deprecated.
- **Firestore composite index:** `dismissed + stored_at` query requires a composite
  index. `app.py` handles the error gracefully with a fallback.
- **Companies House:** covers England, Wales, Scotland, NI only.
  Offshore-incorporated LSE companies have no Companies House number.
- **CH confidence scoring:** `import_universe_csv.py` matches company names to CH
  numbers via fuzzy search (threshold 0.85, active companies only). Confidence 1.0 =
  exact match; 0.85–<1.0 = fuzzy. Dissolved/inactive companies are excluded from
  matching regardless of similarity score. The confidence flows through to
  `Announcement.companies_house_confidence` and into LLM prompts as a data quality note.
- **Google News rate limiting:** Phase 2 (per-company queries) runs at 1.0s/request.
  845 companies ≈ 14 minutes. Pipeline total runtime ~20–25 minutes. Plan scheduling
  accordingly.
- **Firestore universe is a snapshot:** `save_universe()` deletes stale documents after
  each write. Re-running `import_universe_csv.py` with a tighter filter will correctly
  remove previously-admitted companies from Firestore.

---

## Current Build Status

The signal pipeline is fully operational end-to-end. All seven abstractions are
implemented. The universe is live in Firestore with 845 companies.

**Universe management (static CSV approach):**
- `docs/AIM_data_complete_*.csv` and `docs/FTSE_AllShare_complete_*.csv` are the
  source of truth. Committed to git for version history.
- `import_universe_csv.py` imports CSVs → runs CH lookup → writes to Firestore.
  Re-run whenever CSVs are updated. Takes ~8–9 minutes (CH API rate limit).
- £1B market cap ceiling applied at import. 845 companies currently admitted
  (547 AIM + 298 FTSE). 244 large-caps excluded.
- `pipeline.py` reads the universe from Firestore at startup. **Raises RuntimeError
  if Firestore is empty** — there is no fallback to `universe.py`. Run
  `import_universe_csv.py` before the first pipeline run.
- `universe_pipeline.py` is **dormant** — PDF-based dynamic pipeline, do not run.

**News ingestion:**
- `GoogleNewsRSSProvider` runs in two phases: 5 topic queries (discovery) + one
  per-company query for all 845 universe members (signal coverage). Phase 2 takes
  ~14 minutes at 1.0s/request rate limit.
- `CompaniesHouseProvider` fetches filing history for companies with a matched CH
  number. Rate-limited at 0.6s/request.

## Commands

**Run the backend pipeline:**
```bash
cd backend && python pipeline.py
```

**Run the frontend dashboard:**
```bash
cd frontend && streamlit run app.py
```

**Import universe from CSV into Firestore (run once, then re-run when CSVs are updated):**
```bash
cd backend && python import_universe_csv.py
```

**Test individual backend modules** (each has a `__main__` block):
```bash
cd backend
python universe.py                      # Verify CSV sources and static fallback
python lens_base_filters.py             # Test universal pre-filters
python lens_regulatory_catalyst.py      # Test regulatory catalyst strategy
python storage_firestore.py             # Test Firestore connectivity
python llm_gemini.py                    # Test Gemini API
python telegram_notifier.py             # Test Telegram channel
python google_news_connector.py         # Test Google News RSS provider
python companies_house_connector.py     # Test Companies House API
```

**Environment:** Backend requires a `.env` file at `backend/.env` with keys for: Gemini, Companies House, Telegram, and a Google Cloud credentials JSON path for Firestore.

## Architecture

This is an autonomous investment research platform for LSE small-cap stocks. It ingests market news, filters it through investment lenses, analyzes signals with an LLM, stores results in Firestore, and surfaces them via a Streamlit dashboard and Telegram notifications.

### Pipeline Flow (`backend/pipeline.py`)

```
Data Ingestion → Deduplication (Firestore) → Routing → Pre-filtering → LLM Analysis → Storage → Notification
```

**Two parallel queues:**
- **Signal Queue** — companies in the monitored universe (loaded from Firestore). Receives full LLM analysis; high-confidence results trigger Telegram alerts and appear in the dashboard.
- **Discovery Queue** — companies NOT in the universe. Receives a lightweight LLM assessment to recommend whether a company should be added. The universe only grows through explicit human decision (anti-bias principle).

### Pluggable Architecture (`backend/abstractions.py`)

All major components follow abstract base classes, making them swappable:
- `AnnouncementProviderBase` — news sources (Google News RSS, Companies House, RNS stub)
- `LLMProviderBase` — LLM backends (currently Gemini 2.0 Flash via `llm_gemini.py`)
- `NotificationProviderBase` — channels (currently Telegram)
- `StorageProviderBase` — backends (currently Firestore via `storage_firestore.py`)
- `StrategyLensBase` — investment strategies (currently Regulatory Catalyst)

Adding a new lens, data source, or LLM means implementing the relevant abstract class and wiring it into `pipeline.py`.

### Filtering Layer

Pre-filtering happens in two stages before LLM calls (to save cost and latency):
1. **Universal filters** (`lens_base_filters.py`) — reject residential noise, local authority decisions, SPVs; require LSE context signals (plc, AIM, RNS, placing, etc.)
2. **Strategy filters** (`lens_regulatory_catalyst.py`) — require regulatory/planning keywords relevant to the active investment lens

### Firestore Collections

- `announcements` — deduplication store keyed by headline fingerprint
- `signal_results` — full LLM analysis for universe companies
- `discovery_results` — lightweight assessments for non-universe companies

### Frontend (`frontend/app.py`)

Streamlit dashboard with two tabs — **Signals** (actionable opportunities with full LLM output) and **Discovery Queue** (universe candidates). Reads directly from Firestore. Supports dismissing results (soft-archive) and shows statistics.

### Monitored Universe

845 LSE small-cap companies stored in Firestore (`universe_companies` collection),
loaded at pipeline startup via `FirestoreUniverseProvider`. Sourced from
`docs/AIM_data_complete_*.csv` and `docs/FTSE_AllShare_complete_*.csv`, imported
via `import_universe_csv.py`. Each company carries a `companies_house_number` and
`companies_house_confidence` score populated at import time.

`backend/universe.py` contains a legacy 5-company hardcoded list (REE, ACG, ECOR,
MCMM, GMR). It is **not used by the pipeline** — retained for reference only.
