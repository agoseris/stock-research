# CLAUDE.md — Stock Research Tool

## Standards Reference

This project follows the universal principles defined in the standards
repository. Before working in this codebase, read:

- `standards/CLAUDE.md` — universal principles
- `standards/principles/architecture.md` — abstraction and dependency rules
- `standards/principles/authentication.md` — credential handling
- `standards/principles/observability.md` — logging and auditability
- `standards/principles/deployment.md` — deployment workflow
- `standards/principles/data-models.md` — Firestore conventions
- `standards/principles/mcp.md` — if working on the MCP satellite project

Any principle not documented here defers to the standards repository.
Local exceptions to standards are documented below with explicit rationale.

---

## What This Project Is

An AI-assisted stock research tool for LSE small-cap investing (Main Market
and AIM). The system autonomously ingests financial news and regulatory
announcements, filters them against an investment thesis, and uses an LLM
to assess whether they represent meaningful catalyst opportunities. Results
are delivered via Telegram and a Streamlit interface.

**Overarching thesis:** identify LSE small-caps where informed or
knowledgeable parties have expressed confidence in an upcoming increase in
value — through their actions, not their words.

---

## Architecture: Six Abstractions — Non-Negotiable

All components communicate through abstract base class interfaces. Concrete
providers are instantiated once at the entry point and injected. See
`standards/principles/architecture.md` for the universal rule; this table
is the project-specific implementation.

| # | Abstraction | Implementation | Purpose |
|---|---|---|---|
| 1 | `AnnouncementProviderBase` | `CompaniesHouseProvider`, `LSEGExcelProvider` | News and filing ingestion |
| 2 | `LLMProviderBase` | `GeminiProvider` | Analysis and reasoning |
| 3 | `NotificationProviderBase` | `TelegramNotifier` | Alert dispatch |
| 4 | `StorageProviderBase` | `FirestoreProvider` | Signal/discovery results and deduplication |
| 5 | `StrategyLensBase` | `RegulatoryCatalystLens` | Investment strategy implementation |
| 6 | `UniverseStorageProviderBase` | `FirestoreUniverseProvider` | Universe company list and refresh log |

---

## Project File Structure

```
./
├── CLAUDE.md
├── HANDOVER.md                       # Session handover notes — update at end of each session
├── SOBER_ASSESSMENT_v1.md            # Post-PoC evaluation — gaps and upgrade path
├── .gitignore
├── backend/
│   ├── abstractions.py               # All six abstract base classes + dataclasses
│   ├── pipeline.py                   # Core signal pipeline orchestration — daily cron entry point
│   ├── job_runner.py                 # Interactive job queue worker — polls Firestore pending_jobs
│   ├── signal_state.py               # Pure state transition engine
│   ├── lseg_excel_provider.py        # AnnouncementProviderBase — LSEG Excel ingestion
│   ├── import_universe_csv.py        # Manual universe import: CSV → CH lookup → Firestore
│   ├── lens_base_filters.py          # Shared universe pre-filter
│   ├── lens_regulatory_catalyst.py   # Strategy Lens: regulatory/planning catalysts
│   ├── storage_firestore.py          # StorageProviderBase implementation
│   ├── storage_firestore_universe.py # UniverseStorageProviderBase implementation
│   ├── llm_gemini.py                 # LLMProviderBase implementation
│   ├── telegram_notifier.py          # NotificationProviderBase implementation
│   ├── companies_house_connector.py  # AnnouncementProviderBase — CH filing ingestion
│   ├── systemd/
│   │   └── job_runner.service        # systemd unit for always-on VM operation
│   ├── requirements.txt
│   └── .env                          # API keys — never commit, VM only
├── frontend/
│   ├── app.py                        # Streamlit interface — five tabs
│   ├── constants.py
│   ├── ui_helpers.py
│   ├── parse_helpers.py
│   ├── firestore_helpers.py
│   ├── lseg_scraper.py
│   └── requirements.txt
└── docs/
    ├── AIM_data_complete_*.csv
    ├── FTSE_AllShare_complete_*.csv
    └── LSEG_news_capture.xlsx
```

**Rule:** never place backend logic in `frontend/`. The Streamlit interface
communicates with the backend exclusively via Firestore. No backend imports
in frontend code.

---

## Infrastructure

- **Platform:** GCP, europe-west2
- **VM:** e2-micro, always-free tier, Debian, user `danjmorris`
- **Storage:** Cloud Firestore, Native mode, europe-west2, default database
- **LLM:** Gemini API (`gemini-2.0-flash`, `google-genai` package)
- **Notifications:** Telegram (`python-telegram-bot`)
- **Interface:** Streamlit — runs locally (WSL2/macOS) for development

### Firestore Collections

| Collection | Purpose |
|---|---|
| `announcements` | SHA-256 headline fingerprints for deduplication |
| `signal_results` | Signal queue LLM analysis results |
| `discovery_results` | Discovery queue LLM analysis results |
| `universe_companies` | One document per company, keyed by `ticker_lse` |
| `universe_companies/{ticker}/signal_history` | Signal state transition history |
| `universe_companies/{ticker}/position_history` | Position state change history |
| `universe_refresh_log` | One document per pipeline refresh run |
| `pending_jobs` | Interactive ingestion job queue |
| `app_config/lseg_filters` | Exclusion lists — editable live via UI |
| `app_config/signal_config` | Signal decay window config |

**Note:** `dismissed + stored_at` query requires a composite index.
`app.py` handles the missing index error gracefully with a fallback.

---

## Environment Variables (.env)

```
COMPANIES_HOUSE_KEY=
GEMINI_KEY=
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
GOOGLE_APPLICATION_CREDENTIALS=/home/danjmorris/stock-research/backend/gcp-credentials.json
```

**Local exception — key file on VM:** the VM currently uses a service
account JSON key file referenced via `GOOGLE_APPLICATION_CREDENTIALS`.
New development environments (macOS) use keyless ADC instead. The VM
will be migrated to keyless authentication when next revisited.
See `standards/principles/authentication.md` for the preferred approach.

---

## Deployment Workflow

GitHub is always the intermediary. See `standards/principles/deployment.md`.

```bash
# Deploy alias (defined in WSL ~/.bashrc)
deploy   # stages, prompts for commit message, commits, pushes, triggers VM git pull
```

VM setup before running any script:
```bash
cd ~/stock-research && source backend/venv/bin/activate
```

---

## Frontend Version Number

The `VERSION` constant in `frontend/app.py` (line ~23) must be incremented
on every edit to that file. Patch bumping: `1.0` → `1.1`. Minor version
on significant workflow changes: `1.9` → `2.0`.

---

## Signal and Position State Model

Two-axis state model: signal state (system-managed) and position state
(human-managed).

Signal states:
`watching` → `monitor` → `signal_active` → `signal_reinforced` /
`signal_mixed` / `signal_negative`

- `signal_state.py` — pure transition engine (no Firestore)
- State transitions fire after every `save_signal_result`
- Decay check runs at end of each pipeline run
- History recorded to subcollections — append-only, never modified

---

## Commands

```bash
# Backend
cd backend && python pipeline.py
cd backend && python job_runner.py
cd backend && python import_universe_csv.py

# Frontend
cd frontend && streamlit run app.py

# Module self-tests
cd backend && python lens_base_filters.py
cd backend && python lens_regulatory_catalyst.py
cd backend && python storage_firestore.py
cd backend && python llm_gemini.py
cd backend && python telegram_notifier.py
cd backend && python companies_house_connector.py
cd backend && python signal_state.py
```

---

## Known Gotchas

- **VM user:** always connect as `danjmorris`, not `agoseris`
- **Large pastes:** GCP browser SSH terminal truncates large pastes.
  Use file upload or sshfs mount instead.
- **python-telegram-bot:** async library. Do not reuse a `Bot` instance
  across multiple `asyncio.run()` calls. Create `Bot` as an async context
  manager inside a coroutine; call that coroutine via `asyncio.run()` each
  time. See `telegram_notifier.py`.
- **systemd stdout buffering:** set `PYTHONUNBUFFERED=1` in the `[Service]`
  block. See `standards/principles/observability.md`.
- **Gemini package:** use `from google import genai` and `genai.Client()`.
  `google.generativeai` is deprecated.
- **Companies House:** covers England, Wales, Scotland, NI only.
  Offshore-incorporated LSE companies have no Companies House number.
- **CH confidence scoring:** fuzzy match threshold 0.85, active companies
  only. Confidence 1.0 = exact; 0.85–<1.0 = fuzzy. Flows through to
  `Announcement.companies_house_confidence` and into LLM prompts.
- **Universe is a snapshot:** `save_universe()` deletes stale documents
  after each write. Re-running `import_universe_csv.py` with a tighter
  filter correctly removes previously-admitted companies.
- **Pipeline raises RuntimeError if universe is empty.** Run
  `import_universe_csv.py` before the first pipeline run.
