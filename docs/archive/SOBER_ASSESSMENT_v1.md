# AI-Assisted Stock Research Tool — Sober Assessment
**Date:** 24 February 2026
**Status:** Post-PoC evaluation

---

## 1. The Thesis

LSE small-cap stocks are sometimes mispriced around binary events. The mispricing
occurs because the market either hasn't noticed the event is coming, or hasn't fully
assessed the probability that it resolves favourably. When informed parties — people
with direct knowledge, financial exposure, or material context — express confidence
through their *actions* rather than their words, that expression constitutes a signal
that the market's current price may not reflect the likely outcome.

The system's job is to find those signals before the market corrects.

This thesis is sound. It is neither too broad nor too narrow. It has a clear
falsifiable core: either the signals precede price moves, or they don't.

---

## 2. What the Thesis Requires

In an unconstrained system, the thesis requires:

- **Primary disclosure access** — RNS announcements, Companies House filings, and
  director dealing disclosures at the moment they become public, not after aggregation
  by downstream sources
- **Point-in-time signal dating** — the precise timestamp at which each signal entered
  the public domain
- **Timely price data** — intraday or end-of-day price tracking before and after each
  signal, sufficient to measure market response
- **Universe discipline** — a defined, bounded set of companies, actively maintained,
  with explicit admission and removal decisions
- **Multiple signal classes** — different lenses targeting different informed-party
  behaviours, each independently expressing the thesis
- **Auditability** — a complete record of what was surfaced, what was suppressed, and
  why, so the system's behaviour can be evaluated and improved
- **Anti-bias controls** — no implicit preference learning; all filtering is explicit,
  time-bounded, and user-initiated

---

## 3. What Was Built

The following is operational as of 24 February 2026:

| Component | Status | Detail |
|---|---|---|
| Universe | Live | 845 companies (547 AIM + 298 FTSE All-Share), £1B market cap ceiling |
| Companies House ingestion | Live | 671 CH-matched companies, filings fetched daily |
| Pre-filter | Live | Two-stage: universal LSE filter + regulatory catalyst strategy filter |
| LLM analysis | Live | Gemini 2.0 Flash, regulatory catalyst lens |
| Deduplication | Live | SHA-256 fingerprint via Firestore, cross-run |
| Two-queue pipeline | Live | Signal queue (universe) + Discovery queue (non-universe) |
| Notifications | Live | Telegram |
| Dashboard | Live | Streamlit, Signals tab + Discovery tab, dismiss capability |
| Scheduling | Live | 07:00 UTC daily cron on GCP VM |
| Abstraction layer | Live | Seven abstract base classes — all components swappable |

The architecture is sound. The abstraction integrity has been maintained throughout.
The system is genuinely end-to-end and autonomous.

---

## 4. The Gap

### 4.1 The system is reading echoes, not signals

This is the central finding of the assessment.

Companies House filings are frequently a *consequence* of an RNS event, not the event
itself. A charge creation recorded at CH follows a financing that was announced via RNS.
A share allotment at CH follows a placing that was disclosed via RNS. The system is
currently reading the downstream record of events that have already entered the public
domain through the primary disclosure channel.

**The thesis requires primary disclosure. The PoC delivers secondary confirmation.**

The gap between RNS publication and CH filing can be hours, days, or weeks. During that
interval, the market has already had access to the material information. The system is
not finding signals before the market corrects — it is finding them after.

### 4.2 The signal class is public and well-understood

Regulatory and planning catalysts — the PoC's sole lens — are visible, widely followed,
and actively traded by specialist small-cap investors. The informed confidence signal
embedded in committed funding ahead of a binary event is real, but it is not obscure.
The edge, if it exists, is likely narrow and rapidly arbitraged.

### 4.3 The signal class is unvalidated

No historic impact analysis has been performed. It is currently unknown whether the
signals the system surfaces are followed by measurable price moves, at what lag, and
with what reliability. The system is operational but its investment value is unproven.

### 4.4 News sources are structurally late

Google News, Yahoo Finance, and similar aggregators are downstream of RNS. By the time
a filing or announcement has been indexed and is returnable via search, it has been
public for hours. These sources add sentiment — opinion formed after the fact — not
signal. For a system designed to act soon after a signal becomes clear, sentiment from
lagging sources is noise, not edge.

The one legitimate use of news sources is as a *corroborating* input: a CH filing
subsequently discussed positively in financial media may have a more sustained price
impact than one that passes unnoticed. This is a question for the historic impact
analysis phase, not for the current ingestion architecture.

### 4.5 Director dealings are absent

Director and insider open-market buying is the cleanest single expression of the thesis
— insiders committing personal capital is the most direct possible action-based
confidence signal. This lens is identified in the project note as the first candidate
for the lens workshop. It is currently entirely absent from the pipeline, and cannot
be added without a primary disclosure source (RNS or a specialist aggregator).

---

## 5. Ideas Explored and Set Aside — Implementation Difficulty

| Idea | Outcome |
|---|---|
| Google News RSS ingestion | 503 errors — GCP IP ranges blocked by Google |
| Investegate RSS | Redirects to homepage — RSS format deprecated |
| Yahoo Finance RSS | 429 Too Many Requests from GCP IP ranges |
| Proactive Investors RSS | Login/paywall required |
| LSE API | 404 — endpoint not found |
| lse.co.uk scraping | No programmatic access available. Note: preference for this source was a familiarity bias; no analytical justification over alternatives |
| Dynamic universe construction | No reliable free programmatic source for LSE/AIM constituent data. PDF-based FTSE Russell approach attempted — PDFs lack tickers, AXX URL dead. Resolved by static CSV approach |

---

## 6. Ideas Explored and Set Aside — Cost

| Idea | Cost | Reason deferred |
|---|---|---|
| Google Custom Search JSON API | Free tier: 100 queries/day | Unblocked by upgrading GCP from free trial. Deferred — but now assessed as solving the wrong problem (see below) |
| CityFALCON RNS + news feed | ~£50/month, 6-month minimum | Deferred pending PoC validation |
| Polygon.io price + fundamentals | ~$29/month | Deferred pending PoC validation |
| LSEG RNS direct feed | Enterprise pricing | Not yet formally investigated |
| EODHD RNS feed | ~$19–79/month depending on tier | Not yet formally investigated |
| Director dealings aggregator | Variable | Not yet formally investigated |

### Note on Google CSE

Google Custom Search was originally pursued as a route to free news ingestion.
On reflection, this was attempting to solve the wrong problem. News aggregators are
structurally late relative to RNS. Even if CSE were fully operational, it would deliver
sentiment — opinion formed after the fact — not primary signals. The resource cost of
reactivating CSE (GCP account upgrade) is not justified given this reassessment.
CSE remains parked.

---

## 7. What a Future Paid Phase Could Look Like

The reframe is important: the paid upgrade path is not about accessing *more news*.
It is about accessing *primary disclosure channels directly*, eliminating the lag
that currently makes the system a reader of echoes rather than signals.

### Priority 1 — RNS direct feed

The single highest-value upgrade. RNS announcements are the primary formal disclosure
channel for LSE-listed companies. Placings, major contracts, director dealings,
results, and regulatory decisions all appear here first. CH filings are often a
downstream consequence of an RNS event.

Candidates: **EODHD** (~$19–79/month) or **LSEG** (enterprise). EODHD is the
natural first investigation given cost alignment with the PoC philosophy.

This upgrade alone would shift the system from secondary confirmation to primary
signal detection — the most important single change available.

### Priority 2 — Director dealings data

Either via RNS direct (director dealing disclosures are RNS announcements) or via
a specialist aggregator. This unlocks the first additional lens — the cleanest
thesis expression — and requires no architectural change beyond implementing a new
`StrategyLensBase`.

### Priority 3 — Price and fundamentals

Polygon.io (~$29/month) or Twelve Data (already abstracted as the upgrade path).
Required for the historic signal impact analysis phase and for liquidity-aware
signal weighting. The `MarketDataProviderBase` abstraction is already implemented
and dormant — this is a contained activation.

### Priority 4 — Historic signal impact analysis

Once RNS data is flowing and price data is available, a dedicated analysis phase
should be run against historic signals to answer the fundamental question:
do these signals precede measurable price moves, at what lag, and with what
reliability? This is the honest validation of the thesis and should be treated
as a prerequisite before any capital is deployed based on system output.

---

## 8. Pinned Items Carried Forward

### 📌 Historic Signal Impact Analysis
Point-in-time signal dating + share price tracking around each signal. Detects
whether signals lead or lag price moves. A finding that price moves *precede*
public signals would suggest the signal class is already being traded before
it reaches the system. Required before any investment decisions are based on
system output. **Prerequisite: RNS feed + price data.**

### 📌 Lens Workshop — Director and Insider Buying
First candidate for the human-in-the-loop lens workshop. Cleanest thesis
expression. Requires RNS direct feed to operationalise. Workshop can proceed
manually using public data as the validation exercise before building.

### 📌 Preference and Context Store (Step 12)
Next item in the build sequence. Explicit, time-bounded user preferences with
review dates. Suppression log recording all filtered signals with reasons.
Required before the scheduler-driven autonomous mode is fully meaningful,
and a prerequisite for any future proposal agent that reasons from suppression
log evidence.

### 📌 Scheduler (Step 13 equivalent)
Daily cron is live. Preference and Context Store should be completed before
the autonomous pipeline is considered production-ready.

---

## 9. Summary Judgement

The PoC has achieved what a proof of concept should: it has demonstrated that the
architecture works, the pipeline is viable, the abstraction layer holds, and the
system can run autonomously at scale across 845 companies. These are meaningful
results.

What the PoC has not demonstrated is investment edge. The current signal source
(Companies House) delivers secondary confirmation of events that entered the public
domain earlier via RNS. The signal class (regulatory and planning catalysts) is
public and well-understood. Neither finding invalidates the thesis — they identify
the specific gaps that a funded phase must close.

The path forward is clear: access primary disclosure via RNS, validate the signal
class historically, then build additional lenses on proven foundations.
