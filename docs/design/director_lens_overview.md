# Director Buying Lens — Architecture Overview

## Purpose

This document describes the architecture of the director buying lens: a parallelised, three-layer agentic investigation system for detecting meaningful open-market director share purchases on the LSE, AIM, and lower FTSE 250.

It runs **in parallel** with the existing `lens_catalyst` (regulatory/planning catalyst lens), not as a replacement. During the proof of concept phase, both outputs are surfaced simultaneously in the Streamlit frontend to allow quality and cost comparison.

---

## Core Design Principles

- **Human decision at the end.** The system produces a structured briefing and recommendation. The investor makes the final buy/no-buy decision based on factors that may be extrinsic (sector preference, ethical stance) or intrinsic (available funds, portfolio composition) and are outside the system's knowledge.
- **Transparency over automation.** Every output must distinguish clearly between fact (retrieved data) and inference (LLM reasoning). Thin or inconclusive data must be flagged explicitly.
- **Anti-bias by parallelisation.** The three investigation layers run independently with no visibility of each other's findings, preventing implicit cross-contamination between layers.
- **Agents make judgement calls; tools do mechanical work.** Deterministic data retrieval belongs in well-defined tools. LLM reasoning is reserved for interpretation, classification, and synthesis.
- **No duplicate signals.** A single underlying RNS article must not generate two separate signals in the state model, even if processed by both the simple and agentic pathways.

---

## Pipeline Overview

```
LSEG RNS Ingestion (hourly, local machine)
         │
         ▼
Phase 1: Headline filtering
         → keyword filter (remove noise)
         → universe filter (remove out-of-universe companies)
         → remaining: "candidate articles"
         │
         ▼
Phase 2: For each candidate article:
         → fetch full body text (headless browser, local machine)
         → classification + extraction call (single LLM call)
              │
              ├─ article_type = pdmr_transaction
              │  AND transaction_category = open_market_purchase
              │         │
              │         ├──► lens_director_simple (one-shot)
              │         │         │
              │         └──► lens_director_agentic (three-layer)
              │                   │
              │              [both results persisted and surfaced]
              │
              ├─ article_type = regulatory_catalyst
              │         └──► lens_catalyst (existing)
              │
              ├─ article_type = substantive_news
              │         └──► persist summary only [FUTURE LENS]
              │
              └─ article_type = administrative
                        └──► persist classification only, discard
```

---

## Agentic Investigation Architecture

### Three Parallel Layers

All three layers receive the same inputs simultaneously and run independently:

- The RNS article structured extraction output
- The company profile from Firestore
- Their own specific tool set

They have **no visibility of each other's findings.**

**Layer 1 — Platform Assessment**
Evaluates whether the company is a sufficiently solid platform to trade safely. Focuses on liquidity, volatility, price momentum, and relative market performance. Output: platform risk rating with evidence.

**Layer 2 — Director Purchase Analysis**
Evaluates the transaction itself and the director's credibility as a signal source. Contains one genuine agentic decision point: whether to pursue director credibility research depends on what transaction history reveals. Output: signal strength assessment with evidence.

**Layer 3 — Signal Freshness**
Evaluates whether the signal has already been priced in. Reasons about whether the announcement was anticipated or surprising, using prior news history and normalised price movement. Output: freshness assessment with explicit fact/inference distinction.

### Synthesis Layer

Receives all three layer outputs simultaneously. Does not conduct further investigation. Produces the final structured briefing:
- Three layer assessments with evidence bases
- Explicit flags where data was thin or conclusions were inferred
- A recommendation with clear statement of what drove it
- A limitations section covering what could not be assessed

### Model Tiering

```
Classification + extraction call  → Claude Sonnet
Layer 1 (platform assessment)     → Claude Haiku
Layer 2 (director analysis)       → Claude Haiku
                                    (Sonnet if credibility 
                                     research triggered)
Layer 3 (signal freshness)        → Claude Sonnet
                                    (reasoning-heavy)
Synthesis                         → Claude Sonnet
lens_director_simple              → Claude Haiku
```

---

## Infrastructure

```
Local machine (frontend):
  - All LSEG interaction (news ingestion, index page scraping)
  - All yfinance price data retrieval
  - Headless browser for article body extraction
  - Daily index snapshot scrape (AIM, FTSE Small Cap, FTSE 250)
  - Agentic pipeline execution (Layer 1 tool calls require local IP)

GCP backend (e2-micro VM, us-central1-a):
  - Companies House API ingestion (daily)
  - Firestore persistence
  - Telegram notifications
  - Streamlit frontend serving

Firestore:
  - Primary persistence layer
  - Shared between frontend and backend
  - TTL-based ageing for article content
```

---

## Deduplication Constraint

A single RNS article processed by both `lens_director_simple` and `lens_director_agentic` must produce **one signal record** in the state model, not two. Both assessments are stored as sub-documents or fields within that single signal record. The state model tracks the article's `rns_article_id` as the deduplication key.

---

## Proof of Concept Success Criteria

1. Both simple and agentic outputs surface for the same signal in Streamlit with clear visual differentiation
2. Token usage is instrumented and logged per analysis event
3. Cost per signal is calculable from logs
4. The `LIMITATIONS` field of the simple lens maps visibly to what the agentic layer addresses
5. At least 5 real historical signals processed end-to-end without pipeline errors

---

## Pinned for Later

- **Substantive news lens** — extracting signal from trading updates, operational news, prospecting results
- **Lens selection** — meta-capability for agent to identify novel analytical lenses from data patterns
- **Bid/ask spread** — real-time spread data requires paid data source
- **Sector index mapping** — granular sector benchmarking deferred; market-level benchmarking (AIM vs main market) implemented instead
- **Universe market cap realignment** — periodic refresh of stale market cap data
- **Liquidity thresholds** — formal definition of volume/market cap thresholds for liquidity flagging
