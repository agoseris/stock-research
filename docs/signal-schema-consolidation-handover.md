# Handover: Signal Schema Consolidation

## Context

This document is a handover from a Claude.ai chat session to Claude Code.
It describes a schema consolidation task for the stock research system's
signal data, and provides the reasoning and open questions needed to
complete it correctly.

Read `CLAUDE.md` and the standards repository before proceeding.

---

## The Problem

The system currently has two Firestore collections containing signal data:

- `signal_results` — populated by the **Regulatory Catalyst lens**
- `signals` — populated by the **Director Purchasing lens** (two paths:
  simple and agentic) and the **TR-1 Accumulation lens**

This is architecturally wrong. Three lenses, two collections, different
schemas. The same problem has been solved differently for each lens.
Consequences:

- A cross-lens query (e.g. "show me all strong signals across all lenses")
  requires querying two collections and reconciling incompatible schemas
- `signal_strength` is not comparable across lenses — it uses different
  value types and scales in different lens outputs
- Fields that represent the same concept have different names
- Absence of a field is ambiguous — does it mean "not applicable" or
  "not yet populated"?
- The proposal agent (Stage 2 of the meta-lens project) cannot reason
  across the full signal corpus without collection-aware logic

---

## The Goal

Design and implement a **single `signals` collection** with a **canonical
schema** that:

1. Is common across all lenses — every lens produces documents with the
   same core fields
2. Is extensible — lens-specific fields are permitted in a defined
   extension namespace, not scattered arbitrarily
3. Normalises `signal_strength` to a single consistent scale
4. Makes absence explicit — optional fields are present with a null/None
   value, not omitted
5. Is documented — the schema is the source of truth for all future lens
   development

The migration must preserve all existing data. Nothing is deleted without
explicit confirmation.

---

## What Is Known About Current Schemas

### `signal_results` (Regulatory Catalyst lens)

Known fields:
- `llm_analysis` — a single text blob containing labelled fields:
  ```
  RELEVANCE: 2/10
  SIGNAL_TYPE: Investor Relations Events
  CONFIDENCE_SIGNAL: None identified
  OUTCOME_PROBABILITY: N/A
  MARKET_MISPRICING: Insufficient information
  SOURCE_RELIABILITY: High - Official RNS Filing
  RECOMMENDED_ACTION: No
  SUMMARY: <text>
  ```
- `ticker` — links to `universe_companies`
- Likely: timestamp / `stored_at`
- Possibly: `dismissed` boolean

The `llm_analysis` blob needs to be **parsed into discrete fields** as
part of this migration — it should not survive as a blob in the unified
schema.

### `signals` (Director Purchasing lens — simple path)

Known fields:
- `ticker`
- `rationale`
- `limitations`
- `signal_strength` — inconsistent type (e.g. "Strong", "Noise", or numeric)
- Likely: timestamp, recommendation, summary

### `signals` (Director Purchasing lens — agentic path)

Known to be richer than the simple path. Specific additional fields
are not yet known — **this is an open question for Claude Code to
investigate**.

---

## Open Questions for Claude Code

These must be answered by reading the source code before designing
the schema. Do not guess.

### OQ1 — Complete field inventory

For each lens, what are the exact field names written to Firestore?
Read the following files:

- `backend/lens_regulatory_catalyst.py` — what does it write to
  `signal_results`?
- The director purchasing lens files (identify these from the file
  structure) — what does the simple path write? What additional fields
  does the agentic path write?
- `backend/storage_firestore.py` — what are the write methods and
  what fields do they accept?

Produce a complete field inventory table:

| Field name | Lens(es) | Type | Description | Required? |
|---|---|---|---|---|

### OQ2 — signal_strength values in the wild

What are all the actual values of `signal_strength` that appear in
the `signals` collection? Read the storage write calls and the lens
analysis output to enumerate every possible value across both paths.

Proposed normalised scale (validate and adjust based on findings):
- `none` — no signal identified
- `weak` — signal present but limited
- `moderate` — credible signal
- `strong` — high-confidence signal

Map every existing value to this scale. If the scale needs adjustment,
propose an alternative before implementing.

### OQ3 — llm_analysis blob structure

Is the `llm_analysis` field in `signal_results` always in the exact
format shown above? Are there variations? Read the prompt template
that generates it to confirm the expected output structure, then
confirm whether the parser needs to handle variations.

### OQ4 — Recommendation field

What field or value represents the lens recommendation (act / ignore /
monitor / etc.)? Is it consistent across lenses? What are all possible
values?

### OQ5 — Document IDs

How are document IDs currently generated for `signal_results` and
`signals`? Are they auto-generated, ticker-based, content-fingerprinted,
or something else? The unified collection needs a consistent ID strategy.

### OQ6 — Frontend dependencies

What fields does `frontend/app.py` and its helper modules read from
`signal_results` and `signals`? These must be preserved or explicitly
migrated. Read:
- `frontend/firestore_helpers.py`
- `frontend/app.py` (Signals and Discovery tabs)
- `frontend/ui_helpers.py`

A breaking change to the schema that is not reflected in the frontend
will produce a silent failure.

### OQ7 — State model linkage

The `universe_companies` collection carries `signal_state` and
`position_state`. State transitions are triggered by signal saves.
Read `backend/signal_state.py` and `backend/storage_firestore_universe.py`
to understand exactly what fields from a signal document trigger state
transitions. These fields must be present in the unified schema.

---

## Proposed Unified Schema (Draft — Validate Against OQ Findings)

This is a starting point. Do not implement until the open questions
are answered and the schema is confirmed.

### Core fields (every lens, always present)

```
ticker              string      LSE ticker — foreign key to universe_companies
lens_id             string      Identifies the lens: "regulatory_catalyst",
                                "director_purchasing_simple",
                                "director_purchasing_agentic"
stored_at           timestamp   Firestore Timestamp — when written
source_url          string      URL of the source announcement or filing
source_date         timestamp   Date of the original announcement
signal_strength     string      Normalised: "none" | "weak" | "moderate" | "strong"
recommendation      string      "act" | "monitor" | "ignore"
summary             string      One-paragraph human-readable summary
rationale           string      Why the lens reached this conclusion
limitations         string      What would change the assessment; data gaps
dismissed           boolean     Human dismissed this signal (default: false)
dismissed_at        timestamp   When dismissed (null if not dismissed)
```

### Extension namespace

Lens-specific fields live under a `lens_data` map field. This keeps
the top-level document clean and makes it immediately clear what is
canonical vs lens-specific.

```
lens_data           map         Lens-specific fields — structure varies by lens_id
```

Examples of what might live in `lens_data`:

For `regulatory_catalyst`:
```
lens_data.signal_type           string    e.g. "Investor Relations Events"
lens_data.outcome_probability   string
lens_data.market_mispricing     string
lens_data.source_reliability    string
lens_data.relevance_score       integer   parsed from "RELEVANCE: 2/10"
```

For `director_purchasing_agentic`:
```
lens_data.investigation_layers  list      Results from each agent layer
lens_data.synthesis             string    Synthesis agent output
lens_data.confidence_score      float     Numeric confidence from agentic pipeline
```

### What this achieves

- Cross-lens queries work on core fields without collection switching
- `signal_strength` is always a normalised string — comparable across lenses
- `lens_id` allows lens-specific queries without separate collections
- `lens_data` provides extensibility without polluting the core schema
- Frontend reads core fields — works for all lenses without lens-aware logic
- Proposal agent queries core fields for pattern analysis across all lenses

---

## Migration Plan (Outline — Implement After Schema Confirmation)

### Phase 1 — Inventory and confirm (Claude Code task)
Answer all open questions. Produce the confirmed unified schema.
Get human sign-off before writing any migration code.

### Phase 2 — Write migration script
A standalone script `backend/migrate_signals.py` that:
- Reads all documents from `signal_results` and `signals`
- Transforms each to the unified schema
- Writes to a **new collection** `signals_unified` (not overwriting either
  existing collection)
- Logs every document: source collection, document ID, transformation
  applied, any fields that could not be mapped
- Produces a summary: total documents, successful transforms, failures

Do not delete `signal_results` or rename `signals` until the migrated
data has been verified by the human.

### Phase 3 — Frontend migration
Update `frontend/firestore_helpers.py` to read from `signals_unified`.
Update field references in `frontend/app.py` and helpers.
Test all five tabs.

### Phase 4 — Backend migration
Update lens write paths to write to `signals_unified` using the unified
schema. Update `storage_firestore.py` write methods.
Update state transition triggers if field names have changed.

### Phase 5 — Verification and cutover
Human verifies data in `signals_unified` against original collections.
Rename `signals_unified` → `signals` (or update all references).
Archive `signal_results` (do not delete — keep for 30 days as rollback).

### Phase 6 — Cleanup
Delete `signal_results` after 30-day rollback window.
Update `CLAUDE.md` Firestore collections table.
Update `standards/principles/data-models.md` if Firestore conventions
were refined during this work.

---

## Principles in Force

From `standards/principles/architecture.md`:
- Abstraction integrity — the `StorageProviderBase` write interface must
  reflect the unified schema. All lenses write through the abstraction.

From `standards/principles/data-models.md`:
- One canonical definition, one place
- Schema changes are explicit and versioned
- Null handling is explicit — absent optional fields are null, not omitted
- Enumerations over strings — `signal_strength` and `recommendation`
  are defined constant sets, not free-form strings

From `standards/principles/observability.md`:
- The migration script logs every decision
- A run with no output is indistinguishable from a failed run — the
  script always produces a summary

---

## Human Checkpoints

Do not proceed past these points without explicit human confirmation:

1. **After OQ answers** — confirm the unified schema before writing
   migration code
2. **After Phase 2** — human reviews `signals_unified` before any
   existing collection is touched
3. **After Phase 3+4** — human tests frontend and backend against
   migrated data before cutover
4. **Before Phase 6** — human confirms `signal_results` can be deleted

---

## Related Context

- The motivation for this work is the **Stage 2 proposal agent** — an
  agent that reasons across the full signal corpus to identify lens gaps.
  The unified schema is a prerequisite for that agent.
- The MCP satellite project (`mcp-lens-maker`) has a working server
  that currently reads from the existing collections. MCP tool definitions
  should be updated after schema migration is complete — not before.
- The standards repository (`~/Projects/standards/`) contains the
  principles governing this work. Read it.
