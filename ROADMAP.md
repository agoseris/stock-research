# ROADMAP.md
**Last updated:** 2 March 2026
**Current version:** 2.31

---

## Overview

Four work streams, roughly in dependency order. Phases 1–2 can proceed in parallel.
Lens 5 (Signal Convergence) is blocked until at least two independent lenses are
operational and accumulating signal data.

---

## Phase 1 — Automation: LSEG Index Page Scraping ✅ COMPLETE (v2.31)

**Goal:** Eliminate the manual Excel export step. The Ingest table auto-populates
from LSEG directly via Playwright.

**Delivered:** v2.20–2.31 (2 March 2026)

**Confirmed figures (2 Mar 2026):** 471 total rows · 83 non-RNS skipped · 54 passed ·
200 non-universe skipped · 134 suppressed.

### Implementation notes

- **URL:** bare `https://www.londonstockexchange.com/news?tab=news-explorer` — no
  filter params. Angular URL filter params (`?indices=MCX&period=today`) cause "no
  results" in headless mode; date and index filtering is done in Python instead.
- **Pagination:** `#dropdownSize` ng-select; wait for `.ng-dropdown-panel` to render,
  click `.ng-dropdown-panel .ng-option` matching "500", wait for `tr.slide-panel`
  count > 20.
- **Row extraction:** single JS `evaluate()` over all `tr.slide-panel` elements;
  parses company/ticker/type from headline text, source URL from `a.dash-link` href.
- **Date filter:** `published_at.date() == datetime.now(UTC).date()` applied in Python
  after scraping — handles the bare URL returning all-time news.
- **Non-universe rows:** route to internal `discovery` list (hidden from table);
  metric label is "Non-universe (skipped)" — no intelligent candidate selection is
  performed. The Discovery pipeline for this use case does not yet exist.
- **Excel upload** retained as fallback.
- **Ingest UX improvements also delivered:**
  - Default outcome filter → "Passed" only
  - "Since" time-window selector (All today / Last 4h / Last 2h / Last 1h / Last 30m)
  - "Hide already analysed" checkbox (default ON) — excludes rows in `_get_processed_source_urls`
  - Observability: `[lseg_scraper]` print statements (flush=True) to `logs/streamlit.log`;
    screenshot saved to `frontend/debug_screenshot.png` on `tr.slide-panel` timeout

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

1. **Phase 2a** — Firestore schema for signal/position state (two-axis model)
2. **Phase 3** — manual validation of 10–20 Director/PDMR Dealing signals before building
   (use directordeals.co.uk; check 30/60/90-day price outcomes)
3. **Automated ingestion** (future) — hourly fetch 07:00–19:00, auto-submit all Passed
   rows, remove human from the loop. Prerequisite: Phase 2 state model (to avoid
   re-submitting already-analysed announcements). The "Hide already analysed" filter
   and `_get_processed_source_urls` deduplication are the building blocks.
