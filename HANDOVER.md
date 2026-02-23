# Session Handover
**Date:** 23 February 2026
**Branch:** `master`
**Last commit:** `9ad048e` — "Park GoogleNewsProvider: GCP free trial blocks Custom Search JSON API"

---

## Current State

The pipeline is fully operational end-to-end and running on a daily cron schedule.
All seven abstractions are implemented. The universe is live in Firestore with 845
companies.

---

## Pipeline Summary

| Component | Status | Detail |
|---|---|---|
| Universe | Live | 845 companies (547 AIM + 298 FTSE), £1B ceiling applied |
| Companies House | Live | 671 companies matched, 0.85 confidence threshold, active-only |
| Google News / CSE | Parked | See below |
| LLM analysis | Live | Gemini, regulatory catalyst lens |
| Notifications | Live | Telegram |
| Dashboard | Live | Streamlit, reads Firestore directly |
| Cron schedule | Set | 07:00 UTC daily, logs to `~/pipeline.log` |

---

## News Ingestion — Current State and History

The pipeline currently ingests announcements from **Companies House only**.

### What was tried and why it failed

| Source | Outcome |
|---|---|
| Google News RSS (`news.google.com/rss`) | 503 — GCP IP ranges blocked by Google |
| Investegate RSS | Redirects to homepage — RSS format deprecated/removed |
| Yahoo Finance RSS | 429 Too Many Requests from GCP |
| Proactive Investors RSS | Login required (paywalled) |
| LSE API | 404 — endpoint not found |
| Google Custom Search JSON API | 403 PERMISSION_DENIED — GCP free trial blocks APIs with paid tiers |

### Google Custom Search — parked, not deleted

`backend/google_news_connector.py` contains a working `GoogleNewsProvider` class
using the Custom Search JSON API. It is commented out in `pipeline.py` with
reactivation instructions. To reactivate:

1. Upgrade the GCP account from free trial to standard (no immediate charge —
   trial credits continue to be used first)
2. Ensure Custom Search API is enabled in `stock-research-poc`
3. Add `GOOGLE_CSE_KEY` and `GOOGLE_CSE_ID` to `backend/.env`
4. Uncomment `GoogleNewsProvider()` in `pipeline.py`

The Programmable Search Engine (CSE ID: `e160e507a46a742d5`) is already configured
with 10 UK financial news sites and is ready to use.

The free tier is 100 queries/day. Five topic queries per pipeline run costs nothing.
Per-company queries (845/run at $0.004/run) are a noted future nice-to-have.

### Companies House as sole signal source

CH filings are well-aligned with the thesis — they record informed-party actions
directly:
- `SH01` — Return of Allotment of Shares (a placing has occurred)
- `AP01/TM01` — Director appointed/resigned
- `PSC01` — Person of Significant Control change
- `MR01/MR04` — Charge created/satisfied (debt raised or repaid)

Most routine filings (`AA` annual accounts, `CS01` confirmation statements) will
correctly fail the regulatory catalyst pre-filter and not reach the LLM.

---

## CH Confidence Scoring

Companies House numbers are matched by fuzzy name search at import time:
- **Threshold:** 0.85 (raised from 0.6 — false positive found at 0.733)
- **Active-only filter:** dissolved/liquidated companies excluded from matching
- **Confidence 1.0:** exact name match after normalisation
- **Confidence 0.85–<1.0:** fuzzy match — LLM prompt includes a DATA_QUALITY NOTE

---

## Universe Management

To refresh the universe with updated CSV files:

1. Replace `docs/AIM_data_complete_*.csv` and/or `docs/FTSE_AllShare_complete_*.csv`
   with fresh versions (date-stamped filename)
2. Commit to git
3. Deploy to VM (`git pull`)
4. Run `python import_universe_csv.py` — takes ~8–9 minutes (CH API rate limit)
5. Verify: `python pipeline.py` should report 845 (or updated count) companies

`save_universe()` deletes stale documents after each write — removing a company from
the CSV correctly removes it from Firestore on the next import.

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
cd ~/stock-research && /home/danjmorris/stock-research/backend/venv/bin/python backend/pipeline.py

# Check cron log
tail -50 ~/pipeline.log

# Check cron schedule
crontab -l
# Should show: 0 7 * * * cd /home/danjmorris/stock-research && /home/danjmorris/stock-research/backend/venv/bin/python backend/pipeline.py >> /home/danjmorris/pipeline.log 2>&1

# Re-import universe (after CSV update)
cd ~/stock-research/backend && python import_universe_csv.py
```

---

## File Map

| File | Role |
|------|------|
| `pipeline.py` | Core pipeline — entry point for daily runs |
| `import_universe_csv.py` | Manual universe import: CSV → CH lookup → Firestore |
| `google_news_connector.py` | `GoogleNewsProvider` — parked, intact for reactivation |
| `companies_house_connector.py` | CH filing ingestion, rate-limited at 0.6s/request |
| `storage_firestore_universe.py` | `FirestoreUniverseProvider` — universe read/write |
| `lens_regulatory_catalyst.py` | Strategy lens + LLM prompt builder |
| `abstractions.py` | All seven abstract base classes + dataclasses |
| `universe.py` | Static 5-company list — reference only, not used by pipeline |
| `universe_pipeline.py` | DORMANT — PDF-based dynamic pipeline, do not run |

---

## Known Gaps / Future Work

| Item | Notes |
|---|---|
| News ingestion | Parked — reactivate CSE when GCP account upgraded from free trial |
| Per-company news queries | Nice-to-have — 845 × CSE queries at ~$1.12/month once CSE active |
| Pipeline scheduling | Done — 07:00 UTC daily cron |
| Universe refresh automation | Manual for now — CSV update + import_universe_csv.py |
| RNS direct feed | Not yet investigated as a paid option (LSEG, EODHD) |
