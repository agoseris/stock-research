# TR-1 Significant Shareholder Accumulation Lens — Architecture Overview

## Purpose

This document describes the architecture of the TR-1 lens: a two-stage investigation
system for detecting meaningful significant shareholder threshold crossings on the LSE,
AIM, and lower FTSE 250.

It runs **in parallel** with the director buying lens and the regulatory catalyst lens,
sharing the same classification call and routing infrastructure. Results are surfaced
in the Streamlit Signals tab under a "TR-1 Accumulation" heading.

---

## Core Design Principles

- **Investor identity is the crux.** A TR-1 crossing by a known activist or value fund
  is qualitatively different from one by a custody bank or passive index tracker. The
  investigation must classify the notifier before rendering a verdict.
- **Simple first.** A two-stage approach (simple lens + investor research) is sufficient.
  The 3-layer agentic architecture used by the director lens is not warranted: there is
  no price context, director credibility, or signal freshness dimension to investigate.
  The only agentic step is understanding who the notifier is.
- **Direction matters.** Upward crossings express accumulation. Downward crossings
  express distribution. The simple lens classifies direction and magnitude before
  triggering investor research.
- **Free models throughout.** Gemini Flash (free tier) handles both the simple lens and
  investor research. Google Search grounding is the key capability — Claude's reasoning
  loop is not required here.

---

## Pipeline Overview

```
LSEG RNS Ingestion (hourly, local machine)
         │
         ▼
Phase 1: Headline filtering
         → keyword filter, universe filter
         │
         ▼
Phase 2: For each candidate article:
         → fetch full body text (headless browser)
         → classification + extraction call (Claude Sonnet — existing call)
              │
              ├─ article_type = pdmr_transaction ──► director buying lens
              │
              ├─ article_type = tr1_crossing          ← NEW
              │         │
              │         ├──► TR-1 simple lens (Gemini Flash)
              │         │         → extract: direction, old_pct, new_pct,
              │         │           notifier_name, nature_of_holding
              │         │         → classify: upward/downward crossing,
              │         │           threshold crossed, signal strength
              │         │
              │         └──► (if upward crossing and not clearly mechanical)
              │               Investor research layer (Gemini Flash + Search)
              │                    → classify notifier type
              │                    → identify track record if possible
              │                    → assess whether accumulation is conviction-led
              │
              ├─ article_type = regulatory_catalyst ──► catalyst lens
              │
              └─ article_type = administrative / substantive_news ──► existing paths
```

---

## Classification Extension

### Adding `tr1_crossing` as a 5th Article Type

The existing Anthropic classification call (Claude Sonnet) already processes every
ingested article. Adding `tr1_crossing` as a 5th article type costs zero additional
LLM calls — it upgrades the existing call's discriminating power.

**Classification change:**
- `article_type` enum extended: `["pdmr_transaction", "regulatory_catalyst",
  "substantive_news", "administrative", "tr1_crossing"]`
- Extraction schema for `tr1_crossing`:
  ```json
  {
    "article_type": "tr1_crossing",
    "extraction_status": "success" | "failed",
    "notifier_name": "<string>",
    "issuer_name": "<string>",
    "old_holding_pct": <float | null>,
    "new_holding_pct": <float | null>,
    "direction": "upward" | "downward" | "unknown",
    "threshold_crossed": <float | null>,
    "nature_of_holding": "<string>",
    "crossing_date": "<ISO date string | null>"
  }
  ```
- Classification rule: TR-1 filings have a standardised RNS headline beginning with
  "TR-1:" or "Holding(s) in Company". Headlines matching this pattern that disclose
  a percentage threshold crossing should be classified as `tr1_crossing`, not
  `administrative`.

**Routing in `job_runner.py`:**
- Add `elif classification["article_type"] == "tr1_crossing": self._run_tr1_flow(...)`
  after the pdmr_transaction branch.

---

## Simple Lens

**Model:** Gemini Flash (free tier)
**Input:** Structured extraction from classification call + company profile from Firestore
**Output:** Signal strength, direction, recommendation, limitations

### Signal Strength Classification

| Strength | Criteria |
|----------|----------|
| **Strong** | Upward crossing. Notifier name suggests fund/activist/private investor. Crossing initiates a new position (old_pct < 3%, new_pct ≥ 3%) or adds a material increment. |
| **Moderate** | Upward crossing. Notifier is unclassified but not obviously mechanical. Modest increment above an existing position. |
| **Weak** | Upward crossing driven by share issuance rather than open-market purchase (nature_of_holding indicates derivative/pledge). |
| **Noise** | Downward crossing. Crossing driven by corporate action. Notifier is clearly a custody bank, index fund, or ETF provider. |
| **Negative** | Downward crossing by a previously identified quality investor. Disposal below 3% (full exit). |

### Simple Lens Output Schema

```json
{
  "signal_strength": "strong" | "moderate" | "weak" | "noise" | "negative",
  "direction": "upward" | "downward",
  "threshold_crossed": 3 | 5 | 10 | 15 | 20 | 25 | 30,
  "recommendation": "Investigate" | "Monitor" | "Ignore",
  "rationale": "<string>",
  "trigger_investor_research": true | false,
  "limitations": "<string>"
}
```

### Gate for Investor Research

Investor research is triggered when:
- `direction == "upward"` AND
- `signal_strength` is `"strong"` or `"moderate"` AND
- `notifier_name` is not clearly a custody bank / index provider

If `trigger_investor_research == false`, the signal doc is persisted with
`agentic_status="skipped"` and no further investigation occurs.

---

## Investor Research Layer

**Model:** Gemini Flash with native Google Search grounding
**Purpose:** Classify the notifier and assess whether accumulation is conviction-led

### Why Gemini Flash + Search grounding?

Investor identity research is an open-web lookup task, not a multi-step reasoning
problem. Gemini Flash's native Google Search grounding is better suited than Claude's
tool-use loop for this:
- Single-step: query → classify → done
- No intermediate tool calls needed
- Grounding citations provide auditability
- Free tier (up to 1M tokens/month + generous search queries)

### Research Prompt (outline)

```
You are a financial research assistant. A significant shareholder notification has been
filed by "{notifier_name}" disclosing an upward crossing of the {threshold_crossed}%
threshold in {issuer_name} (LSE-listed).

Using Google Search, research:
1. Is this notifier an investment fund, activist investor, private equity firm,
   family office, or something else?
2. Is there publicly available evidence of their investment philosophy or track record?
3. Have they previously accumulated positions in similar companies?
4. Is there anything about the notifier or this transaction that changes the signal
   interpretation?

Return a structured assessment: notifier_type, track_record_summary,
conviction_assessment ("conviction_likely" | "mechanical_likely" | "unclear"),
confidence ("high" | "medium" | "low"), search_citations (list of URLs used).
```

### Investor Research Output Schema

```json
{
  "notifier_type": "activist_fund" | "value_fund" | "index_fund" | "custody_bank"
                 | "private_investor" | "family_office" | "unknown",
  "conviction_assessment": "conviction_likely" | "mechanical_likely" | "unclear",
  "track_record_summary": "<string | null>",
  "confidence": "high" | "medium" | "low",
  "search_citations": ["<url>", ...],
  "limitations": "<string>"
}
```

---

## Persistence

TR-1 signals are stored in the `signals` Firestore collection alongside director buying
signals. Routing and display are distinguished by the `signal_type` field.

### Signal Document Structure

```
signals/{rns_article_id}
  signal_type:               "tr1_crossing"
  ticker:                    "<string>"
  headline:                  "<string>"
  source_url:                "<string>"
  announcement_published_at: "<ISO datetime>"
  agentic_status:            "pending" | "complete" | "skipped"

  # From classification extraction:
  notifier_name:             "<string>"
  direction:                 "upward" | "downward"
  old_holding_pct:           <float | null>
  new_holding_pct:           <float | null>
  threshold_crossed:         <float | null>
  nature_of_holding:         "<string>"

  # From simple lens:
  simple_lens_result:        { signal_strength, direction, recommendation, rationale, limitations }

  # From investor research (if triggered):
  investor_research:         { notifier_type, conviction_assessment, track_record_summary,
                               confidence, search_citations, limitations }

  # State model:
  stored_at:                 <Firestore timestamp>
  expires_at:                <Firestore timestamp>   # TTL: 90 days from announcement_published_at
```

### Deduplication

The `rns_article_id` (SHA-256 of headline + source_url) is the deduplication key,
consistent with the director buying lens. A single TR-1 filing processed twice will
upsert the same document, not create a duplicate.

---

## State Model Integration

TR-1 signals feed the same per-company signal state machine as director buying signals:

| Signal Strength | State Transition |
|----------------|-----------------|
| Strong upward crossing, conviction_likely | → Signal — Active |
| Moderate upward crossing, unclear | → Monitor |
| Sequential upward crossings (same notifier) | → Signal — Reinforced |
| Downward crossing, conviction_likely investor | → Signal — Negative (if active) |
| Noise / mechanical | → no state change |

State transitions fire via the same `_apply_state_transition` call in `job_runner.py`
after the TR-1 signal is persisted.

---

## UI Presentation

**Deferred until signals are flowing.** The Signals tab currently groups by lens under
separate headings. TR-1 signals will appear under a "TR-1 Accumulation" heading with
a card layout analogous to the Director Buying section.

**Card content (planned):**
- Headline (clickable link) + datetime
- Direction badge (UPWARD CROSSING / DOWNWARD CROSSING) + threshold %
- Old holding % → New holding %
- Notifier name + notifier_type badge
- Conviction assessment
- Market cap + price
- Signal strength badge
- Simple lens expander / investor research expander (stacked, full-width)
- Action buttons (Act / Defer / Decline / Dismiss)

---

## Implementation Sequence

1. Add `tr1_crossing` as 5th article type to Anthropic classification prompt +
   extraction schema in `utilities/orchestrator/classification.py`
2. Add TR-1 routing branch in `backend/job_runner.py` (`_run_tr1_flow` method)
3. Build TR-1 simple lens (Gemini Flash, `utilities/orchestrator/lens_tr1_simple.py`)
4. Build investor research layer (Gemini Flash + Search grounding,
   `utilities/orchestrator/lens_tr1_investor_research.py`)
5. Wire persistence in `_run_tr1_flow` — signals doc with `signal_type="tr1_crossing"`
6. Update `scripts/run_orchestrator.py` to handle `signal_type="tr1_crossing"` polling
7. UI card in `frontend/tab_signals.py` under "TR-1 Accumulation" heading

---

## Open Questions (resolve before build)

- **Orchestrator polling:** should `run_orchestrator.py` poll `signals` for all
  `agentic_status="pending"` regardless of `signal_type`, or route separately? Current
  design: single poll, branch on signal_type.
- **Gemini Flash Search grounding quota:** confirm free tier search budget is sufficient
  for expected TR-1 frequency (est. 5–15 per week across the universe).
- **`nature_of_holding`:** some TR-1 filings disclose synthetic/derivative exposure
  rather than physical shares. Classification prompt must distinguish these, as they
  carry weaker signals.
- **Multi-issuer TR-1:** a single TR-1 may cover holdings in multiple companies
  (group-level disclosures). Extraction must handle this gracefully.

---

## Change Log

| Version | Date | Changes |
|---------|------|---------|
| 1 | 10 Mar 2026 | Initial architecture — Option B (simple lens + investor research). Decisions from session 7 planning session. |
