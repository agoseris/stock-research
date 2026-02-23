# Session Handover
**Date:** 23 February 2026
**Branch:** `master`
**Last commit:** `0bb2dc4` — "Fix save_universe to delete stale Firestore documents"

---

## Current State

The signal pipeline is fully operational end-to-end. Universe management is working via
static CSV files imported into Firestore. All seven abstractions are implemented.

---

## What Was Built This Session

### Universe management (static CSV approach)

The dynamic pipeline (`universe_pipeline.py`) is on hold — no reliable free programmatic
source for LSE/AIM constituent data exists. Instead:

- `docs/AIM_data_complete_v20260223.csv` — 554 AIM stocks
- `docs/FTSE_AllShare_complete_v20260223.csv` — 535 FTSE All-Share stocks
- `backend/import_universe_csv.py` — imports CSVs → Firestore. Apply £1B market cap
  ceiling (consistent with thesis Tier 2 upper bound). 845 companies admitted
  (547 AIM + 298 FTSE); 244 large-caps excluded.
- `pipeline.py` reads the universe exclusively from Firestore. Raises `RuntimeError`
  if Firestore is empty — no fallback to the static `universe.py` list.

### Companies House number lookup

`import_universe_csv.py` looks up each company's CH registration number via the CH API
at import time using fuzzy name matching:

- **Threshold:** 0.85 (raised from 0.6 after a false positive at 0.733 — a dissolved
  company matched to a live AIM stock)
- **Active-only filter:** dissolved, liquidated, and struck-off companies are excluded
  from matching regardless of similarity score
- **Confidence score:** stored on each `UniverseCompany` as `companies_house_confidence`
  (1.0 = exact match, 0.85–<1.0 = fuzzy match accepted)
- **Rate limit:** 0.6s between requests (600 req/5-min CH limit)
- **Offline companies:** offshore-incorporated AIM companies (Cayman, Jersey, IoM etc.)
  correctly return no CH number

The confidence score flows through to:
- `Announcement.companies_house_confidence` (set by `CompaniesHouseProvider`)
- `lens_regulatory_catalyst.py` `build_prompt()` — adds a `DATA_QUALITY NOTE` when
  confidence < 1.0, telling the LLM the CH match was fuzzy and to factor it into
  `SOURCE_RELIABILITY`

### Google News two-phase ingestion

`GoogleNewsRSSProvider` now runs in two phases:

**Phase 1 — topic queries (discovery)**
Five broad topic searches. Results have `ticker="UNKNOWN"` and are routed to the
discovery queue unless name/ticker matches a universe member. Fast (~5 seconds).

**Phase 2 — per-company queries (signal coverage)**
One query per universe company. Uses exchange-appropriate qualifier:
- `LSE_MAIN`: `"<Company Name>" LSE`
- `AIM`: `"<Company Name>" "AIM listed"`

Results have `ticker` set directly (reliable signal routing). Rate-limited at 1.0s
between requests. Runtime: ~14 minutes for 845 companies. Progress printed every
50 companies.

### Companies House connector rate limiting

`CompaniesHouseProvider.get_recent_announcements()` now sleeps 0.6s between per-company
filing fetches. Previously ran in a tight loop — fine for 3 companies, problematic at
hundreds of CH-matched universe members.

### Firestore stale document fix

`save_universe()` previously only upserted documents, never deleted. This caused
the original 1089-company (unfiltered) import to persist alongside the 845-company
(filtered) import. Now deletes any document whose ticker is absent from the new
universe after each write.

---

## VM State — Actions Required

The following changes have been pushed but the VM needs to pull and the import
needs to be re-run:

```bash
cd ~/stock-research && git pull
source backend/venv/bin/activate
cd backend && python import_universe_csv.py
```

This will:
1. Re-run CH lookup with the new 0.85 threshold and active-only filter
2. Delete the 244 stale large-cap documents (Firestore will settle at 845)
3. Takes ~8–9 minutes (845 companies × 0.6s CH API rate limit)

After the import, verify:
```bash
python pipeline.py
# Should report: "Universe loaded from Firestore: 845 companies"
```

---

## Known Gaps / Next Considerations

### Scheduling
The pipeline is not yet scheduled. For daily execution, set up a cron job on the VM:
```bash
crontab -e
# Add: 0 6 * * * cd /home/danjmorris/stock-research && source backend/venv/bin/activate && python backend/pipeline.py >> /home/danjmorris/pipeline.log 2>&1
```
Daily cadence is sufficient for the PoC. The pipeline takes ~20–25 minutes per run
(Google News Phase 2 ~14 min + CH filing fetch variable).

### Universe refresh cadence
CSVs are currently dated 23 Feb 2026. Update them monthly or when universe composition
changes materially. Re-run `import_universe_csv.py` after each CSV update.

### `universe_pipeline.py`
Dormant. Contains PDF extraction code for FTSE Russell constituent lists — now
obsolete (PDFs lack tickers, AXX URL is dead). Retained for reference only.
Do not run it.

### `universe.py` static list
Retained as reference (5 hand-picked companies). No longer used by `pipeline.py`.
`get_active_companies()` is not called anywhere in the live system.

---

## File Map — What Does What

| File | Role |
|------|------|
| `pipeline.py` | Core pipeline orchestration — entry point for daily runs |
| `import_universe_csv.py` | Manual universe import: CSV → CH lookup → Firestore |
| `abstractions.py` | All seven abstract base classes + `UniverseCompany`, `RefreshLog` |
| `storage_firestore_universe.py` | `FirestoreUniverseProvider` — reads/writes universe_companies collection |
| `google_news_connector.py` | Two-phase Google News RSS ingestion |
| `companies_house_connector.py` | CH filing ingestion, rate-limited, confidence-aware |
| `lens_regulatory_catalyst.py` | Strategy lens + LLM prompt builder (CH confidence note) |
| `universe.py` | Static 5-company fallback — reference only, not used by pipeline |
| `universe_pipeline.py` | Dormant — PDF-based dynamic pipeline, do not run |

---

## VM Reminders

- Connect as `danjmorris` (not `agoseris`)
- Venv: `source ~/stock-research/backend/venv/bin/activate`
- Credentials: `backend/.env` → `GOOGLE_APPLICATION_CREDENTIALS=/home/danjmorris/stock-research/backend/gcp-credentials.json`
- Deploy: local edit → commit → push → `git pull` on VM
