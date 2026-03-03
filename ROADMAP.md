# ROADMAP.md
**Last updated:** 2 March 2026
**Current version:** 2.31 (no frontend change for Phase 2a/2b)

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

**Confirmed figures (2 Mar 2026, three-index fetch):** 362 rows total (MCX 170 + SMX 98 + AXX 95,
after dedup) · 54 passed · ~200 non-universe skipped · ~134 suppressed.
(Earlier bare-URL single-fetch figures — 471 rows, 83 non-RNS skipped — are superseded by the
three-index approach, which provides complete daily coverage without hitting the 500-row cap.)

### Implementation notes

- **URL:** `https://www.londonstockexchange.com/news?tab=news-explorer&indices=MCX`
  (repeated for `SMX` and `AXX`). Three targeted fetches, one per market segment,
  each staying well under 500 rows/day. The `&indices=` param works reliably headless;
  `&period=today` does NOT — it causes "no results" in headless mode and is never appended.
  Date filtering is applied in Python after scraping.
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
- **Index params:** bare URL used initially because `?indices=MCX&period=today`
  gave "no results" headless — the `&period=today` param was the culprit, not MCX
  itself. Three targeted fetches replace the single bare-URL fetch:
  `MCX` (FTSE 250), `SMX` (FTSE Small Cap), `AXX` (FTSE AIM All-Share).
  Each stays well under 500 rows/day; results merged and deduplicated on
  `source_url`. SMX has no UI selector in the Index filter (URL param only);
  MCX and AXX have in-page selectors (`div.mcx-button` / `div.axx-button`)
  as fallback behind the `div.index-button` toggle.
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

### 2a — Firestore schema ✅ COMPLETE (2 March 2026)

`UniverseCompany` dataclass carries five new Optional fields (backwards-compatible):
`signal_state`, `signal_state_since`, `last_signal_at`, `position_state`,
`position_state_since`.

Subcollections per company document:
- `universe_companies/{ticker}/signal_history/{auto-id}` — one doc per state
  transition (timestamp, previous_state, new_state, trigger_source_url,
  trigger_headline, lens, signal_strength, llm_confidence)
- `universe_companies/{ticker}/position_history/{auto-id}` — one doc per
  position state change

`app_config/signal_config` config document seeded on first call (all decay window
defaults encoded in `_DEFAULT_SIGNAL_CONFIG` in `storage_firestore_universe.py`).

`UniverseStorageProviderBase` gains 8 new abstract methods; all implemented in
`FirestoreUniverseProvider`. `StrategyLensBase` gains abstract `name` property.

### 2b — Pipeline integration ✅ COMPLETE (2 March 2026)

`backend/signal_state.py` — new pure module (no Firestore imports):
- `classify_signal_strength`, `is_negative_signal`, `extract_confidence`
- `compute_signal_transition(current_state, strength, is_negative)` → new state
- `compute_decay_transitions(companies, config, now)` → [(ticker, old, new)]
- `TRANSITION_TABLE`, `DECAY_RULES` constants
- 29/29 self-tests pass (`python signal_state.py`)

`pipeline.py` wiring:
- `self.universe_storage` stored as instance attribute
- `_apply_state_transition` called after every `save_signal_result`
- `_run_decay_check` runs at end of each autonomous pipeline run

`job_runner.py` wiring:
- Same `_apply_state_transition` method called after `save_signal_result`
- `"lens"` key added to signal result dict (flows into signal_history records)

`RegulatoryCatalystLens.name = "regulatory_catalyst"`.

### 2c — Dashboard integration ⬜ NOT STARTED

In `frontend/app.py`, Signals tab:
- Show `signal_state` badge alongside each signal result
- Add position state controls (Acted / Deferred / Declined) to each signal card
- Signal history expander per company
- Notification priority matrix: surface Acted + Negative signals at maximum urgency

**Dependencies:** 2a + 2b complete ✅ — 2c can proceed immediately.

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
Phase 1 (Scraping) ─────────────────────────────────────────► ✅ COMPLETE (v2.31)

Phase 2 (State Model)
    2a Schema ──────────────────────────────────────────────► ✅ COMPLETE (2 Mar 2026)
    2b Pipeline integration ─────────────────────────────────► ✅ COMPLETE (2 Mar 2026)
    2c Dashboard integration ────────────────────────────────► ⬜ next

Phase 3 (Lens 1: Director Buying) ─────────────────────────► needs Phase 2a ✅
    └─ establishes lens implementation pattern for Phases 4–6

Phase 4 (Lens 2: TR-1) ────────────────────────────────────► needs Phase 2a ✅, 3
Phase 5 (Lens 3: Placing Quality) ─────────────────────────► needs Phase 2a ✅, 3
Phase 6 (Lens 4: Buyback Momentum) ────────────────────────► needs Phase 2a ✅, 3
Phase 7 (Lens 5: Convergence) ─────────────────────────────► needs Phases 3 + 4 live ≥30 days
```

---

## Next Actions

1. **Phase 2c** — Dashboard integration: signal_state badges on Signals tab, position
   state controls (Acted / Deferred / Declined), signal history expander
2. **Phase 3** — manual validation of 10–20 Director/PDMR Dealing signals before building
   (use directordeals.co.uk; check 30/60/90-day price outcomes). Phase 2a is now unblocked ✅.
3. **Automated ingestion** (future) — hourly fetch 07:00–19:00, auto-submit all Passed
   rows, remove human from the loop. The "Hide already analysed" filter and
   `_get_processed_source_urls` deduplication are the building blocks.
