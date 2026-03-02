# ROADMAP.md
**Last updated:** 2 March 2026
**Current version:** 2.19

---

## Overview

Four work streams, roughly in dependency order. Phases 1–2 can proceed in parallel.
Lens 5 (Signal Convergence) is blocked until at least two independent lenses are
operational and accumulating signal data.

---

## Phase 1 — Automation: LSEG Index Page Scraping

**Goal:** Eliminate the manual Excel export step. The Ingest table auto-populates
from LSEG directly via Playwright. One button click replaces the current
Export → Upload workflow.

**Current state:** Body retrieval and auto-submit are complete (v2.16–2.18). The
remaining gap is scraping the LSEG announcement index.

**Dependencies:** None — independent of all other phases.

---

### Confirmed technical behaviour

**URL (single, covers all three indices):**
```
https://www.londonstockexchange.com/news?tab=news-explorer&indices=MCX,AXX,SMX&period=today
```
`MCX` = FTSE 250 · `AXX` = FTSE AIM All-Share · `SMX` = FTSE Small Cap

**Period:** `&period=today` in the URL locks to today's announcements. If a
different period is selected by the user, LSEG appends `&period=lastweek` or
`&period=custom&beforedate=...&afterdate=...` — the parameter scheme is clean.

**News Type filter:** defaults to "Show only Earnings, News & Reach" and is not
encoded in the URL. Leave it at the default. The existing Filter 1 in the parse
pipeline (source column check, drops non-RNS rows) handles any Reach/GNW content
that comes through. No Playwright interaction needed for this filter.

**Pagination:** dropdown at the top of the results table, alongside a sort
dropdown. Defaults to 20 rows. Options: 20 / 50 / 100 / 500. Playwright selects
"500" and waits for the count display (e.g. "254 results (showing 254)") to
confirm the full set is loaded before scraping.

**Sort:** defaults to "most recent" — no interaction needed.

**Source URLs:** confirmed present — each row carries a clickable link to the
full announcement page. The "✓ Analysed" indicator and "Auto-fetch & submit"
button both depend on this URL and will work correctly for scraped rows.

**Challenge gate:** already handled by existing `lseg_scraper.py` logic
(detects `body.block-scroll`, confirms private investor, saves cookies). The
same mechanism covers the index page.

**Source type column:** confirmed present. Values observed: RNS (dominant), PRN,
MFN, BZN, Reach, GNW, EQS. Filter 1 (`source == "RNS"`) works identically on
scraped rows — non-RNS rows route to suppressed as normal.

---

### Playwright interaction sequence

```
1. Navigate to URL (with challenge gate handling)
2. Wait for results table to appear
3. Click pagination dropdown → select "500"
4. Wait for count display to update (confirm full result set loaded)
5. Scrape all visible rows
6. Return list of row dicts
```

No filter interactions required. Sorting is already correct. One page load,
one dropdown interaction, one scrape pass.

---

### Delivery tasks

**Task 1.0 — DOM reconnaissance ✅ COMPLETE**

Confirmed selectors:

| Element | Selector | Notes |
|---------|----------|-------|
| Each row | `tr.slide-panel` | Confirmed: 196 rows = 196 results |
| Company + ticker | `td.news-title` first text node | e.g. "Nexus Infrastructure PLC - NEXS - " — split on " - " |
| Announcement type | `td.news-title a.dash-link` inner text | e.g. "Director Dealing" |
| Source URL | `td.news-title a.dash-link` href | Relative — prepend `https://www.londonstockexchange.com/` |
| Source type | `td.hide-on-portrait.rns-source` | e.g. "RNS", "Reach", "GNW" |
| Date | `td.hide-on-portrait:not(.rns-source)` index 0 | Format: `DD.MM.YY` |
| Time | `td.hide-on-portrait:not(.rns-source)` index 1 | Format: `HH:MM:SS` |
| Price | `td.hide-on-portrait:not(.rns-source)` index 2 | Numeric string or "-" |
| Price change | `td.hide-on-portrait:not(.rns-source)` index 3 | String e.g. "1.59%" or "-" |
| Pagination dropdown | `#dropdownSize` | Angular ng-select — click to open, then click option by text |
| Pagination option | `.ng-option:has-text("Show 500 news")` | Text match inside the open dropdown panel |
| Results count | `span.total-results` | Used as wait condition; matches row count |

Desktop layout (`hide-on-portrait` columns) is used for all fields — cleaner
than the mobile `.show-on-portrait` equivalent. Headless Playwright uses a
desktop viewport by default so these columns are always present.

`_ngcontent-ng-lseg-c*` attributes are Angular encapsulation IDs that change
between deploys — never include in selectors.

Output: a short note (can be added directly to this section) with the selectors.
These are needed before Task 1.1 can be coded.

---

**Task 1.1 — `fetch_announcement_index()` in `frontend/lseg_scraper.py` ✅ COMPLETE**

New function alongside the existing `fetch_announcement_body()`:

```python
def fetch_announcement_index() -> list[dict]:
    """
    Scrape the LSEG News Explorer (MCX + AXX + SMX, today).
    Returns a list of row dicts:
      ticker, company_name, announcement_type, source, published_at,
      price_pence, price_change_pct, source_url
    'source' is 'RNS' if not extractable from the table, else the actual value.
    Raises RuntimeError on failure.
    """
```

Internal steps:
1. Navigate to the confirmed URL
2. Handle challenge gate (existing mechanism)
3. Wait for results table to appear
4. Click pagination dropdown, select "500"; wait for count display to confirm
5. Scrape all visible rows into list of dicts
6. Return list

The URL and selectors are stored as module-level constants in `lseg_scraper.py`,
not hardcoded inline, so they can be updated without touching logic.

---

**Task 1.2 — Filter pipeline generalisation in `frontend/parse_helpers.py` ✅ COMPLETE**

Currently `_parse_lseg_excel()` does parsing and filtering in one pass against
a file object. The scraper path provides pre-parsed rows and needs the filtering
stage only.

Extract the filter logic into a shared function:

```python
def _filter_announcement_rows(
    rows: list[dict],
    universe_tickers: set,
    excluded_types: list[str],
    company_keywords: list[str],
    not_of_interest_tickers: set,
) -> dict:  # same structure as _parse_lseg_excel() return value
```

Refactor `_parse_lseg_excel()` to call `_filter_announcement_rows()` internally.
The public interface of `_parse_lseg_excel()` is unchanged — callers in `app.py`
are unaffected.

---

**Task 1.3 — Ingest tab UI in `frontend/app.py` ✅ COMPLETE**

Add a "Fetch from LSEG" button above the existing Excel uploader:

```
[ 🔄 Fetch from LSEG ]
─────────────────────────────────────────
or upload manually:  [ 📂 Upload Excel ]
```

On click:
1. `with st.spinner("Fetching from LSEG (MCX · AXX · SMX · today)…"):`
2. Call `fetch_announcement_index()` from `lseg_scraper.py`
3. Call `_filter_announcement_rows()` with live Firestore config
4. Store result in `st.session_state["ingest_result"]` (same key as Excel path)
5. Clear session state keys (`ingest_dismissed`, `ingest_session_muted`,
   `ingest_session_submitted`, `ingest_subform_open`) — same as new file upload
6. `st.rerun()`

On failure: display `st.error()` with the RuntimeError message. Excel upload
remains available as fallback beneath the error.

The result dict structure is identical to the Excel path — all downstream
rendering code (table, filters, action buttons) works without modification.

---

### Acceptance criteria

- "Fetch from LSEG" populates the Ingest table with the same rows that would
  appear from an equivalent manual Excel export and upload
- All existing filters apply identically (universe, trust keywords, muted
  tickers, type exclusions)
- Each scraped row carries a source_url — "✓ Analysed" indicator and
  "Auto-fetch & submit" both work
- Excel upload remains fully functional as a fallback
- No changes required to any code downstream of `ingest_result` session state

---

## Phase 2 — Signal/Position State Model

**Goal:** Implement the two-axis state model in Firestore and wire it into the
pipeline and dashboard. This is the prerequisite for signals to accumulate meaning
over time.

**Current state:** Model fully designed in `LENS_WORKSHOP_CANDIDATES_v2.md` (Part A).
No Firestore schema or code exists yet.

### 2a — Firestore schema

Add fields to each `universe_companies` document:
- `signal_state` — one of: `watching`, `monitor`, `signal_active`,
  `signal_reinforced`, `signal_mixed`, `signal_negative`
- `signal_state_updated_at` — timestamp of last transition
- `position_state` — one of: `acted`, `deferred`, `declined`, `closed`, or absent
- `position_state_updated_at`

Add subcollection `signal_history` under each company document:
- One document per state transition: timestamp, previous state, new state,
  triggering announcement (source_url, headline), lens, LLM confidence score

Add `position_history` subcollection:
- One document per position state change: timestamp, previous state, new state,
  optional user reason

Add confirmation window config to `app_config` collection (document
`signal_config`):
- `monitor_decay_days` (default 30)
- `active_confirmation_window_days` (default 90)
- `reinforced_staleness_days` (default 180)
- `mixed_resolution_days` (default 30)
- `negative_decay_days` (default 90)
- `deferred_nudge_days` (default 60)
- `deferred_decay_days` (default 14)

### 2b — Pipeline integration

In `pipeline.py`, after each LLM analysis result is stored:
- Apply state transition logic based on signal confidence and direction
- Write new state + history entry to Firestore
- Apply decay checks on every run: query companies whose signal state has exceeded
  its confirmation window and apply the appropriate decay transition

### 2c — Dashboard integration

In `frontend/app.py`, Signals tab:
- Show `signal_state` badge alongside each signal result
- Add position state controls (Acted / Deferred / Declined) to each signal card
- Notification priority matrix: surface Acted + Negative signals at maximum urgency

**Dependencies:** None — can proceed in parallel with Phase 1 and Phase 3.

---

## Phase 3 — Lens 1: Director/PDMR Open-Market Buying

**Goal:** First production lens. Detect and classify director and PDMR open-market
share purchases as they come through the RNS pipeline.

**Current state:** Not started. Full spec in `LENS_WORKSHOP_CANDIDATES_v2.md`
(Lens 1). Recommended validation approach: use LSEG web interface to filter for
"Director/PDMR Dealing" and manually check 10–20 signals for subsequent price
behaviour before building.

**Deliverables:**
- `backend/lens_director_buying.py` — `DirectorBuyingLens(StrategyLensBase)`
  - Pre-filter: announcement type contains "Director" or "PDMR" or "Dealing"
  - LLM prompt: extract transaction type, whether open-market, value (£),
    director name and role, classify signal strength (strong / moderate / weak / noise)
  - Cluster detection: flag if another director at the same company has bought
    within the previous 30 days
  - State model output: strong → `signal_active`, moderate → `monitor`,
    noise → no transition, director sale on active company → `signal_mixed` or
    `signal_negative`
- Wire into `pipeline.py` alongside existing `RegulatoryCatalystLens`

**Dependencies:**
- Phase 2a (Firestore schema) should be complete before state transitions are written.
  However, the lens logic can be built and the LLM extraction validated before
  Phase 2 is done — decouple if needed.

---

## Phase 4 — Lens 2: Significant Shareholder Accumulation (TR-1)

**Goal:** Detect upward TR-1 threshold crossings by quality investors.

**Current state:** Not started. TR-1 notifications already pass through the LSEG
pipeline and are intentionally not excluded. Full spec in
`LENS_WORKSHOP_CANDIDATES_v2.md` (Lens 2).

**Deliverables:**
- `backend/lens_tr1_accumulation.py` — `TR1AccumulationLens(StrategyLensBase)`
  - Pre-filter: announcement type contains "TR-1"
  - LLM prompt: extract direction (upward/downward crossing), previous %, new %,
    notifier identity, classify investor type (activist / value fund / institutional
    passive / unknown), classify signal strength
  - State model output: upward crossing by quality investor → `signal_active`,
    unknown investor → `monitor`, downward crossing on active company → `signal_mixed`
    or `signal_negative`

**Dependencies:** Phase 2a (Firestore schema), Phase 3 (pattern established for
lens implementation).

---

## Phase 5 — Lens 3: Fundraising Quality (Placing Analysis)

**Goal:** Classify equity fundraising quality; flag high-quality raises as
entry-window signals and distressed raises as counter-signals.

**Current state:** Not started. Full spec in `LENS_WORKSHOP_CANDIDATES_v2.md`
(Lens 4 — reordered here ahead of Buyback Momentum due to lower implementation
complexity).

**Deliverables:**
- `backend/lens_placing_quality.py` — `PlacingQualityLens(StrategyLensBase)`
  - Pre-filter: announcement type contains "Placing", "Subscription",
    "Open Offer", or "Fundraising"
  - LLM prompt: extract placing price, discount %, proceeds (£M), use of proceeds,
    whether oversubscribed, named investors, classify quality
  - State model output: high-quality → `signal_active` (with recovery window note),
    distressed → `signal_negative`, neutral → `monitor`

**Dependencies:** Phase 2a (Firestore schema), Phase 3.

---

## Phase 6 — Lens 4: Share Buyback Momentum

**Goal:** Detect buyback programme initiations and accelerations via cumulative
tracking of "Transaction in Own Shares" announcements.

**Current state:** Not started. Full spec in `LENS_WORKSHOP_CANDIDATES_v2.md`
(Lens 3). Individual daily buyback announcements are currently excluded from
the pipeline (correctly — they carry near-zero signal value individually). The
signal is in the aggregate pattern.

**Deliverables:**
- `backend/lens_buyback_momentum.py` — `BuybackMomentumLens(StrategyLensBase)`
  - Re-include "Transaction in Own Shares" for this lens only (currently excluded
    in `app_config/lseg_filters.excluded_announcement_types`)
  - Firestore tracking: cumulative buyback store per company — shares purchased,
    rolling 30-day volume, programme start date, last seen date
  - Detection logic: initiation (first Transaction in Own Shares after >90-day
    gap), acceleration (volume in latest 30 days >2× prior 30-day rolling average)
  - State model output: initiation or acceleration → `signal_active`, suspension
    → `signal_negative` (if company was in active state)

**Dependencies:** Phase 2a (Firestore schema), Phase 3. Note: requires resolving
the tension between the global announcement type exclusion list and per-lens
inclusion — the exclusion list should become per-lens configurable, or this lens
handles its own pre-filter independently.

---

## Phase 7 — Lens 5: Signal Convergence (Meta-lens)

**Goal:** Detect when multiple independent lenses fire on the same company within
a 30-day rolling window. The convergence itself is the signal.

**Current state:** Deferred. Full spec in `LENS_WORKSHOP_CANDIDATES_v2.md` (Lens 5).

**Deliverables:**
- `backend/lens_convergence.py` — `ConvergenceLens` (meta-lens, not a
  `StrategyLensBase` implementation — operates on signal_history, not announcements)
- Convergence patterns ranked by expected strength (from workshop doc):
  1. Director buying + significant shareholder accumulation
  2. Director buying + company buyback initiation
  3. High-quality placing + director participation
  4. Regulatory catalyst + director buying
  5. Any three lenses within 30 days
- State model output: amplifier — two lenses on `signal_active` →
  `signal_reinforced` with elevated conviction flag; three lenses → high conviction

**Blocked until:** Lenses 1 and 2 (Phases 3–4) are operational and have been
accumulating signal data for at least 30 days.

---

## Paid Data Upgrades (when earned)

Not scheduled. Triggered by demonstrated need, not assumption (free-first principle).

| Upgrade | Cost (approx) | Unlocks |
|---------|---------------|---------|
| RNS direct feed (EODHD) | ~$19–79/month | Lenses 1–4 become autonomous (no manual LSEG export) |
| Price data (Polygon.io / Twelve Data) | ~$29/month | Post-signal price tracking; recovery window detection for Lens 3 |

---

## Dependency Summary

```
Phase 1 (Scraping) ─────────────────────────────────────────► standalone

Phase 2 (State Model) ──────────────────────────────────────► standalone
    └─ blocks Phase 3–6 (state writes) but not lens logic development

Phase 3 (Lens 1: Director Buying) ─────────────────────────► needs Phase 2a
    └─ establishes lens implementation pattern for Phases 4–6

Phase 4 (Lens 2: TR-1) ────────────────────────────────────► needs Phase 2a, 3
Phase 5 (Lens 3: Placing Quality) ─────────────────────────► needs Phase 2a, 3
Phase 6 (Lens 4: Buyback Momentum) ────────────────────────► needs Phase 2a, 3
Phase 7 (Lens 5: Convergence) ─────────────────────────────► needs Phases 3 + 4 live ≥30 days
```

---

## Next Actions

1. **Phase 1** — extend `frontend/lseg_scraper.py` with `fetch_announcement_index()`
2. **Phase 2a** — design and write Firestore schema migration for signal/position state
3. **Phase 3** — manual validation of 10–20 Director/PDMR Dealing signals before building
   (use LSEG web interface or directordeals.co.uk; check 30/60/90-day price outcomes)
