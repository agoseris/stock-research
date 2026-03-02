# Agentic Lens Suggestion — Discussion Note
**Date:** 23 February 2026
**Context:** Exploratory discussion on whether AI agents could propose and evaluate additional strategy lenses within the existing stock research system.

---

## The Question

Could an AI agent be built to suggest alternative or additional investment lenses, capable of surfacing novel ideas within the existing 845-company universe — ideas that the current regulatory catalyst lens would miss?

---

## Key Conclusions

### What is sound about the idea

1. **The architecture is fit for purpose.** The Strategy Lens abstraction (`StrategyLensBase`) already treats lenses as self-contained, pluggable components. An agent proposing a new lens is, in architectural terms, proposing a new implementation of that interface. No structural change to the system is needed.

2. **The universe is well-suited to agentic reasoning.** 845 companies is large enough to have real potential, and bounded enough that the reasoning space is tractable and outputs remain auditable.

### What is difficult about the idea

3. **Free-tier data presents real constraints.** Richness, availability, and rate limits will make automated lens testing difficult, particularly for strategies requiring price history, volume, or short interest data.

4. **Sophistication theatre is a genuine risk.** LLMs produce plausible-sounding investment frameworks readily. Without backtesting infrastructure, there is no reliable way to distinguish a well-argued lens from an empirically worthless one. Narrative coherence is not the same as signal validity.

---

## The Thesis Test — Central Discipline

Every lens must answer: *where is the informed confidence signal here?*

The lens concept is specifically about finding situations where people with knowledge, skin in the game, or material context are expressing — through their actions, not their words — that they believe value is coming. An agent proposing lenses must internalise this constraint, or it will generate thesis-violating strategies (momentum, sector rotation, cheap stock screening) that are technically coherent but philosophically wrong.

Human review of every agent-proposed lens against the thesis is non-negotiable, consistent with the system's anti-bias and auditability principles.

---

## Recommended Approach: Two-Stage

### Stage 1 — Lens Workshop (available now)
A structured human-LLM collaboration, not autonomous agents. Workflow:
- LLM reasons through candidate lens ideas using the thesis as the governing constraint
- Human acts as data retrieval layer for sources that resist programmatic access
- Together, evaluate each candidate against two explicit questions (see below)
- Human decides whether the lens has sufficient merit to build

This sidesteps the free-tier data problem for the evaluation phase entirely. It is available immediately with no additional infrastructure.

### Stage 2 — Proposal Agent (future, post-step 12)
A genuine agentic capability that monitors what existing lenses are surfacing and — crucially — what they are *suppressing*. The agent would reason from suppression log evidence about system gaps, and hypothesise why valid opportunities might be slipping through.

**This stage requires the Preference and Context Store (step 12) to be built first.** The suppression log is the input data the agent needs. It does not yet exist in structured form.

---

## The Two-Question Evaluation Framework

When workshopping any lens candidate, maintain a strict separation between:

**Question 1 — Signal coherence:** Is this a real behaviour by informed parties? Is it theoretically grounded? Does it pass the thesis test?

**Question 2 — Signal detectability:** Can this signal actually be found in available data sources, with sufficient frequency and lead time to be useful in practice?

A lens can pass Question 1 and fail Question 2. Sophistication theatre tends to answer Question 1 convincingly while quietly ignoring Question 2. Treat Question 2 as the harder bar.

---

## Suggested First Lens Workshop Candidate

**Director and insider open-market buying** — already listed as a candidate strategy in the project note. Recommended as the starting point because:
- Directional signal is unambiguous (insiders buying with their own money)
- Data is publicly available (regulatory filings, disclosed via RNS)
- Manual validation is feasible without programmatic data access
- Passes the thesis test cleanly: insiders acting with skin in the game

---

## Sequencing Recommendation

| Priority | Action |
|----------|--------|
| 1 | Complete step 12 — Preference and Context Store with suppression log |
| 2 | Complete step 13 — Twelve Data price/fundamentals integration |
| 3 | Run first Lens Workshop session (director buying recommended) |
| 4 | If lens validates, build it as a new `StrategyLensBase` implementation |
| 5 | After suppression log has accumulated real data, revisit Stage 2 proposal agent |

---

## Notes on Human-in-the-Loop Backtesting

For the workshop phase, the human acts as the data retrieval layer for sources that resist programmatic access (e.g. regulatory announcement archives, broker research, specialist financial databases). The LLM provides reasoning structure and thesis discipline. This is a pragmatic and intellectually honest approach to validation that does not require backtesting infrastructure.
