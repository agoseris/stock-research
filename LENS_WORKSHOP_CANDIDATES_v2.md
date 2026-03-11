# Lens Workshop — Top 5 Candidate Lenses + State Model
**Date:** 27 February 2026
**Version:** 2
**Status:** State model defined; no lenses yet validated
**Prerequisite reading:** `LENS_AGENT_DISCUSSION_v1.md` (23 Feb 2026)

---

## Context and Constraints

**Target universe:** LSE small-cap, lower FTSE 250 (soft ceiling ~£1.2B market cap), and AIM. Currently 847 companies (93 muted) with no hard cap ceiling enforced in code.

**Core thesis:** When informed parties express confidence through *actions* rather than words, the market's current price may not reflect the likely outcome. The system's job is to find those signals before the market corrects.

**Investment constraints:**
- Maximum £20,000/year across entire portfolio (ISA wrapper)
- Individual positions therefore likely £1,000–£4,000
- Stocks must be liquid enough that positions of this size do not materially move the price
- Spread between bid and ask must be narrow enough that the anticipated gain exceeds it
- Must be able to enter and exit within a reasonable timeframe

**Liquidity implication:** This naturally excludes the very bottom of AIM (sub-£10M market cap, very thin order books) while keeping the sweet spot of £30M–£1.2B where informational asymmetry exists but liquidity is adequate for retail-scale positions.

**Time horizons (per lens):**
- Short term: days to weeks
- Medium term: weeks to months
- Long term: 1 year to multi-years

**Current data sources:**
1. **LSEG Excel exports** (primary, interactive) — human downloads from LSEG web interface, uploads to Ingest tab. Covers all RNS announcement types.
2. **Companies House filings** (secondary, autonomous) — daily cron, 673 matched companies, 2-day window.

---

## Feasibility Tiers

Each lens is assessed against five tiers of data availability:

| Tier | Definition |
|------|------------|
| **T1 — Achievable today** | Can be implemented using current LSEG Excel + CH pipeline with no new data sources |
| **T2 — Alternative free sources** | Requires a new data source, but that source is free and publicly accessible |
| **T3 — Paid sources (identified)** | Requires a paid data source; the specific source and approximate cost are known |
| **T4 — Paid sources (likely)** | Requires paid data; a source probably exists but has not been formally investigated |
| **T5 — Theoretical / human-in-the-loop** | No reliable automated source identified; may require manual human data retrieval |

---

## Evaluation Framework (carried forward from 23 Feb discussion)

Every candidate lens must clear two bars:

**Question 1 — Signal coherence:** Is this a real behaviour by informed parties? Is it theoretically grounded? Does it pass the thesis test — *where is the informed confidence signal?*

**Question 2 — Detectability and actionability:** Can this signal be found in data sources available to the system, with sufficient frequency and lead time to be useful? This is the harder bar.

---

## Part A — State Model

### Overview

The system tracks two independent states per company:

- **Signal State** — system-managed, updated autonomously as announcements are classified
- **Position State** — human-managed, set exclusively by explicit user action

Signal state and position state are both indexed on the **company** (not on individual signals or announcements). There is exactly one signal state and one position state per company at any given time.

**Control flow:** Position state actively influences signal state (closing or declining a position forces signal state back to baseline). Signal state only *informs* the user, who takes control of setting position state manually. The system never modifies position state.

---

### Signal States

Signal state is managed entirely by the system. Transitions are triggered by the arrival and classification of new signals, and by time-based decay rules. The user cannot directly set signal state (except indirectly via position state changes that force a reset).

| State | Meaning | Entry condition |
|-------|---------|-----------------|
| **Watching** | Baseline. Company is in the universe and being scanned. No active signal. | Default state for all universe companies. Also the reset target when position state forces a downward transition. |
| **Monitor** | A weak signal has been received, below the confidence threshold. The system is paying closer attention. | A signal arrives but is classified below the confidence threshold (e.g. small token director purchase, routine CH filing with minor positive implications). |
| **Signal — Active** | A signal above the confidence threshold has been received. This is the trigger for a buy notification. | A single signal classified at or above the confidence threshold, OR cumulative signals in Monitor that together cross the threshold. |
| **Signal — Reinforced** | Subsequent signals support the original. Accumulated evidence is positive. | A confirming signal arrives within the confirmation window for a company already in Signal — Active or Signal — Reinforced. |
| **Signal — Mixed** | Contradictory signals coexist. Evidence is genuinely on both sides. | A signal arrives that actively contradicts the existing positive thesis — not merely ambiguous, but directionally opposed — while prior positive signals remain within their confirmation window. Example: one director buys, a different director sells. |
| **Signal — Negative** | A counter-signal has been received that challenges the thesis. | A hard counter-signal arrives: clustered director sales, a buy-then-sell reversal by the same director within 90 days, deeply discounted emergency placing, or equivalent. |

**Note on Mixed vs. Negative:** Mixed describes a genuine conflict in the evidence — reasonable people could disagree on the net signal. Negative describes a clear counter-indicator where the weight of the new evidence is unambiguously against the thesis. The LLM classification prompt must distinguish these carefully.

#### Signal State Transitions

```
Watching ──[weak signal]──────────────────────► Monitor
Watching ──[strong signal]─────────────────────► Signal — Active

Monitor ──[further signal, cumulative threshold crossed]──► Signal — Active
Monitor ──[no signal within 30 days]───────────► Watching (automatic decay)
Monitor ──[negative signal]────────────────────► Watching (thesis invalidated before it formed)

Signal — Active ──[confirming signal]──────────► Signal — Reinforced
Signal — Active ──[contradictory signal]───────► Signal — Mixed
Signal — Active ──[hard counter-signal]────────► Signal — Negative
Signal — Active ──[no signal within confirmation window]──► Monitor (thesis expired, decay)

Signal — Reinforced ──[further confirming signal]──► Signal — Reinforced (refreshed)
Signal — Reinforced ──[contradictory signal]───► Signal — Mixed
Signal — Reinforced ──[hard counter-signal]────► Signal — Negative
Signal — Reinforced ──[last confirming signal older than 6 months]──► Signal — Active (reinforcement stale, decay)

Signal — Mixed ──[further confirming signal, resolves conflict]──► Signal — Reinforced
Signal — Mixed ──[further counter-signal]──────► Signal — Negative
Signal — Mixed ──[no resolution within 30 days]──► Signal — Active (conflict unresolved, decay to base)

Signal — Negative ──[no further signal within 90 days]──► Watching (automatic decay)
```

#### Confirmation Windows (configurable, stored in app_config)

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| Monitor decay | 30 days | Weak signal that doesn't develop is noise |
| Signal — Active confirmation window | 90 days | Academic evidence for PDMR buying suggests 1–6 month effect; 90 days captures the core window |
| Signal — Reinforced staleness | 6 months | After 6 months without new confirmation, the reinforcement is historical |
| Signal — Mixed resolution window | 30 days | Contradictory evidence should resolve or decay quickly |
| Signal — Negative decay to Watching | 90 days | Conservative — assumes the user may not have acted on the exit signal |

All windows are configurable per-lens if evidence suggests different lenses have different natural time horizons.

---

### Position States

Position state is set exclusively by the user via explicit action in the dashboard. The system never modifies position state. There is no default position state — a company that has never received a signal notification has no position state, which is functionally equivalent to "no relationship."

| State | Meaning | Set by |
|-------|---------|--------|
| **Acted** | The user has taken a position (bought shares). The system assumes real money is at risk. Maximum notification obligation. | User clicks "Acted" on a notified signal. |
| **Deferred** | The user wants to act but cannot right now (e.g. funds allocated elsewhere). The system treats this with the same notification priority as Acted. | User clicks "Deferred" on a notified signal. |
| **Declined** | The user explicitly chose not to act on this signal. An active, conscious decision. | User clicks "Declined" on a notified signal. |
| **Closed** | The user has exited a previously held position. | User clicks "Closed" on a company currently in Acted state. |

#### Position State Transitions

```
[no position state] ──[user acts on signal notification]──► Acted
[no position state] ──[user defers signal notification]──► Deferred
[no position state] ──[user declines signal notification]──► Declined

Acted ──[user closes position]─────────────────► Closed
Deferred ──[user acts]─────────────────────────► Acted
Deferred ──[user declines]─────────────────────► Declined
Deferred ──[no new signal within 60 days + user does not respond to nudge]──► Watching*
Declined ──[new signal cycle begins]───────────► [no position state] (fresh evaluation)
Closed ──[new signal cycle begins]─────────────► [no position state] (fresh evaluation)
```

*Deferred decay: see below.

**Critical rule — position state forces signal state downward:**
- Setting **Declined** forces signal state to **Watching**. All accumulated signal history for that cycle is archived. The company returns to baseline monitoring.
- Setting **Closed** forces signal state to **Watching**. Same behaviour as Declined.
- Setting **Acted** or **Deferred** does *not* modify signal state. The signal assessment continues independently.

#### Deferred Behaviour

Deferred is the position state that requires the most careful handling because it represents an indefinite intention without commitment.

**Notification priority:** Identical to Acted. The system treats Deferred as "the user may enter this position at any time" and surfaces all subsequent signals with full urgency.

**New signals while Deferred:** Signal state updates normally. Notifications are generated at Acted-equivalent priority. The user's response options on each notification are: update to Acted (they've now bought), remain Deferred, or Decline (the opportunity has passed or the thesis has changed).

**Negative signals while Deferred:** Surfaced with appropriate urgency, but lower than Acted + Negative because no capital is at risk. The user may Decline (killing the thesis and resetting to Watching) or remain Deferred (they believe the negative signal is noise).

**Decay through silence:** If no new signals arrive for 60 days, the system generates a nudge notification: *"[Company X] has been deferred for 60 days with no new signals. Still active?"* Any new signal arrival resets the 60-day clock. If the user does not respond to the nudge within a further 14 days, position state is cleared (returns to no position state) and signal state follows its own decay rules independently.

**Note:** The 60-day deferred decay is the *one exception* to the rule that the system never modifies position state. It is justified because Deferred represents an intention that has not been acted upon, and indefinite deferral without re-confirmation would create noise. The nudge provides the user an opportunity to actively re-confirm before the decay occurs.

---

### Notification Priority Matrix

Notifications are driven by the combination of signal state and position state. Priority determines the notification channel (Telegram alert vs. dashboard-only) and visual prominence.

| Position State | Signal — Active | Signal — Reinforced | Signal — Mixed | Signal — Negative |
|---------------|----------------|--------------------|-----------------|--------------------|
| **Acted** | Standard alert | Confirmation alert | Review alert | **URGENT — counter-signal on held position** |
| **Deferred** | Standard alert | Confirmation alert | Review alert | Priority alert — thesis challenged |
| **Declined** | — (position declined, signals still recorded but no notification until new cycle) | — | — | — |
| **Closed** | — (position closed, signals still recorded but no notification until new cycle) | — | — | — |
| **[none]** | Standard alert (this is the initial buy signal) | Standard alert | Informational | Informational |

**Declined and Closed companies:** Signal state continues to be tracked at Watching baseline. If a *new* signal cycle begins (a genuinely new catalyst, not a continuation of the declined/closed cycle), the system surfaces it as a fresh notification with no position state. The dashboard shows historical context: "You previously held/declined this company on [date]. New signal received."

---

### Muting vs. Declining

These are distinct concepts that must not be conflated:

| | Mute | Decline |
|---|------|---------|
| **What it means** | "Stop scanning this company entirely" | "I evaluated this specific opportunity and chose not to act" |
| **Scope** | Company-level, permanent until manually reversed | Signal-cycle-level, resets to Watching |
| **Effect on pipeline** | Company excluded from all scanning (CH, LSEG, future sources) | Company remains in universe, fully monitored |
| **Where managed** | Universe tab (existing functionality) | Signal notification response |
| **When to use** | Repeated irrelevant notifications; company fundamentally outside investment interest | Specific signal evaluated and rejected on its merits |

The system does not automatically escalate repeated Declines to a Mute. It is the user's responsibility to identify patterns and manage muting through the existing Universe tab.

---

### Implementation Notes

**Firestore structure:** Signal state and position state are stored as fields on the company document in the `universe_companies` collection. Signal state transitions are logged to a `signal_history` subcollection for audit and future validation analysis.

**Signal history:** Every signal state transition is recorded with: timestamp, previous state, new state, triggering announcement (source_url, headline), lens that generated the classification, and LLM confidence score. This history is preserved across cycles (including Declined/Closed resets) and is the foundation for future historic signal impact analysis.

**Position state history:** Every position state change is recorded with: timestamp, previous state, new state, and user-provided reason (optional free text). This enables retrospective analysis of decision quality.

**Confirmation windows:** Stored in `app_config` collection, configurable per-lens. Default values as specified in the table above. Changes take effect on the next pipeline run.

**Decay timers:** Implemented as background checks during each pipeline run. The pipeline queries for companies where the current signal state has exceeded its confirmation window without a new signal, and applies the appropriate decay transition.

---

## Part B — Lens Catalogue

### Lens 1: Director and Insider Open-Market Buying (PDMR Dealings)

#### The Signal

When a director or Person Discharging Managerial Responsibilities (PDMR) buys shares in their own company using personal capital on the open market, they are expressing confidence with skin in the game. UK Market Abuse Regulation requires disclosure within two business days via RNS.

#### Thesis Fit — STRONG

This is the single cleanest expression of the informed confidence thesis. The director has asymmetric information about the company's prospects, regulatory position, pipeline, and balance sheet. They are choosing to commit personal wealth. The signal is directional and unambiguous.

**Signal strength classification:**
- **Strong signal:** Open-market purchase using personal funds. Director chose the timing and size. Material value relative to company market cap.
- **Moderate signal:** Exercise of options followed by retention (not immediate sale). Confidence expressed by holding rather than liquidating.
- **Weak/noise:** Exercise of options followed by immediate sale (liquidity event, tax-driven). Purchase required by employment terms. Small token purchases by newly appointed directors (ceremonial).
- **Negative signal (inverse):** Open-market sales by directors, particularly clustered sales by multiple directors, or sales shortly after positive announcements.

**Cluster effect:** A single director buying is informative. Multiple directors buying within a short window is substantially more informative — it suggests a shared view that the market is underpricing the company.

#### Time Horizon

Short to medium term. Academic literature consistently finds abnormal returns in the 1–12 months following insider purchases in small-cap equities. The effect is strongest in the first 1–3 months and strongest for smaller companies where informational asymmetry is greatest.

#### Detectability

| Aspect | Assessment |
|--------|------------|
| **Announcement type** | "Director/PDMR Dealing" — a standard RNS category on LSEG |
| **Frequency** | Regular — multiple per week across the AIM/small-cap universe |
| **Lead time** | 1–2 business days after the transaction (regulatory requirement) |
| **Body parsing** | Structured: transaction type, number of shares, price per share, total value, director name, nature of interest. LLM can extract reliably. |
| **False positive risk** | Moderate — requires distinguishing genuine open-market buys from option exercises, automatic plans, and token purchases |

#### Feasibility: T1 — Achievable today

PDMR dealings are RNS announcements. They already flow through the LSEG Excel export. A new `StrategyLensBase` implementation would:
1. Filter for "Director/PDMR Dealing" announcement type
2. Use LLM to parse the body: extract transaction type, value, whether open-market
3. Apply signal strength classification (strong / moderate / weak / noise)
4. Flag clusters (multiple directors at same company within rolling 30-day window)

**Enhancement with paid data (T3):** RNS direct feed via EODHD (~$19–79/month) would make this autonomous rather than requiring manual LSEG export.

#### State Model Integration

- A strong open-market purchase → signal state transitions to **Signal — Active**
- A moderate signal (option exercise with retention) → signal state transitions to **Monitor**
- A second director buying within 30 days → **Signal — Reinforced** (cluster detection)
- A director *selling* on a company already in Signal — Active or Reinforced → **Signal — Mixed** (single sale) or **Signal — Negative** (clustered sales, or buy-then-sell reversal by same director)

#### Actionability for £20K ISA Portfolio

High. Director buying is most informative in exactly the market cap range where £1–4K positions are viable.

#### Workshop Validation Approach

1. Use LSEG web interface to filter RNS for "Director/PDMR Dealing" across AIM + Small Cap
2. Alternatively, browse Directors Deals (directordeals.co.uk) which pre-curates this data
3. For each significant buy (>£10K personal capital, open-market), check subsequent 30/60/90-day price movement
4. Track hit rate and average return
5. Identify false positive patterns (option exercises mislabelled, token purchases)

---

### Lens 2: Significant Shareholder Accumulation (TR-1 Crossings)

#### The Signal

When a significant shareholder crosses a notifiable threshold (3%, 5%, 10%, 15%, 20%, 25%, 30% under UK Disclosure Guidance and Transparency Rules), they must file a TR-1 notification via RNS. An *upward* crossing — an investor increasing their stake through a threshold — is an action expressing confidence.

#### Thesis Fit — STRONG

Institutional investors, activist funds, and sophisticated private investors committing material capital to increase a position are informed parties acting with conviction. The TR-1 crossing represents *sustained* accumulation — the investor has been buying over a period and has now crossed a regulatory threshold.

**Signal strength classification:**
- **Strong signal:** Upward crossing by a known activist investor, value fund, or sector specialist. Crossing from below 3% to above 3% (new position initiation). Multiple upward crossings in sequence (3% → 5% → 10%).
- **Moderate signal:** Upward crossing by a passive/index fund (may be mechanical rebalancing). Crossing driven by a corporate action.
- **Weak/noise:** Downward crossings disclosed alongside upward crossings (portfolio reshuffling). Crossings driven by derivative instruments rather than physical holdings.
- **Negative signal (inverse):** Downward crossings by known quality investors. Disposal below 3% (exiting entirely).

**Investor identity matters:** A TR-1 crossing by a known activist fund carries different information than one by a custody bank. The LLM should attempt to classify the notifier.

#### Time Horizon

Medium term. Significant accumulation typically precedes a catalyst by weeks to months.

#### Detectability

| Aspect | Assessment |
|--------|------------|
| **Announcement type** | "TR-1: Standard form..." — already flowing through LSEG Excel, intentionally not excluded |
| **Frequency** | Several per week across the universe |
| **Lead time** | 2 trading days from crossing |
| **Body parsing** | Semi-structured: previous %, new %, nature of holding, notifier identity. LLM can extract. |
| **False positive risk** | Moderate-to-high — requires distinguishing genuine accumulation from mechanical rebalancing |

#### Feasibility: T1 — Achievable today

TR-1 notifications are already passing through the pipeline. A new lens would filter, extract direction and investor type, classify signal strength, and flag sequences.

#### State Model Integration

- Upward crossing by known quality investor → **Signal — Active**
- Upward crossing by unclassified investor → **Monitor**
- Sequential upward crossings (same investor) → **Signal — Reinforced**
- Downward crossing on a company in Signal — Active → **Signal — Mixed** or **Signal — Negative** depending on investor and magnitude

#### Implementation Decisions (session 7, 10 Mar 2026)

Architecture: **Option B — simple lens + one investor research layer.** Full 3-layer
agentic treatment (as used by the director buying lens) is not warranted. Investor
identity is the crux of the TR-1 signal; a single investor research step resolves it.

**Classification:** `tr1_crossing` added as a **5th article type** in the existing
Anthropic classification call (Claude Sonnet). Zero marginal LLM cost — the call
already happens for every ingested article. TR-1 filings were previously classified
as `administrative`; they now route to the TR-1 lens directly.

**Models:**
- Simple lens: Gemini Flash (free tier)
- Investor research: Gemini Flash + native Google Search grounding

Rationale for Gemini Flash on investor research: investor identity is an open-web
lookup task, not a multi-step reasoning problem. Gemini's native Search grounding
handles it in a single step with citation evidence. Claude's tool-use loop is not
required.

**Materiality gate:** same pattern as director lens — if simple lens returns "Ignore"
or `trigger_investor_research=false`, `agentic_status="skipped"` and no further
investigation. Configurable via Firestore (`app_config/tr1_lens_config`).

**Signals collection:** `signal_type="tr1_crossing"` field distinguishes TR-1 signals
from director buying signals within the shared `signals` collection.

**UI:** deferred until signals are flowing. See `docs/design/tr1_lens_overview.md`
for full architecture, implementation sequence, and card design.

---

### Lens 3: Company Share Buyback Momentum

#### The Signal

When a company buys back its own shares on the open market, management is making a capital allocation decision that its shares are undervalued. An *acceleration* or *initiation* of buybacks is a stronger signal than ongoing programmatic repurchases.

#### Thesis Fit — MODERATE-TO-STRONG

Management teams are informed parties. A buyback commits company capital to the proposition that the shares are cheap. Diluted somewhat by the fact that buybacks can be driven by EPS management or dilution offset rather than genuine value conviction.

**Signal strength classification:**
- **Strong signal:** Initiation of a new buyback programme. Acceleration of an existing programme. Buyback announced alongside neutral-to-positive trading update.
- **Moderate signal:** Ongoing execution at steady pace. Buyback as part of stated return-of-capital policy.
- **Weak/noise:** Buyback to offset employee scheme dilution. Very small buybacks relative to market cap (<0.1%).
- **Enhancement signal:** Buyback occurring simultaneously with director personal buying (Lens 1 convergence).

#### Time Horizon

Medium term. Buyback programmes run over weeks to months. Academic evidence shows 2–5% abnormal returns in 1–6 months following buyback announcements in small-caps.

#### Detectability

| Aspect | Assessment |
|--------|------------|
| **Announcement type** | "Transaction in Own Shares" — already flowing through LSEG Excel, intentionally not excluded |
| **Frequency** | Daily during active programmes |
| **Lead time** | Next-day disclosure |
| **Body parsing** | Structured: shares purchased, price range, cumulative programme progress. |
| **False positive risk** | Low for detection, moderate for strength classification |

#### Feasibility: T1 — Achievable today

Requires cumulative tracking across daily announcements in Firestore to detect initiation and acceleration patterns.

#### State Model Integration

- Buyback programme initiation → **Signal — Active**
- Acceleration of existing programme → **Signal — Reinforced**
- Programme cessation or suspension → **Signal — Negative** (if company was in Active/Reinforced)
- Steady-state continuation → no state change (already priced in)

---

### Lens 4: Fundraising Quality — Placing and Subscription Analysis

#### The Signal

When a company raises equity capital, the *quality* of the raise reveals the market's informed assessment. Who participated, at what discount, whether oversubscribed, and what the proceeds are for — this is the signal, not the raise itself.

#### Thesis Fit — MODERATE-TO-STRONG

Institutional investors participating in a placing commit capital based on their assessment. The signal is in the details.

**Signal strength classification:**
- **Strong signal:** Oversubscribed. Discount ≤10%. Proceeds for growth capex or acquisition. Named institutional participants. Raised more than initially targeted.
- **Moderate signal:** Placing at modest discount. General corporate purposes. Standard institutional participation.
- **Weak signal:** Discount >15%. Working capital or debt reduction. Small/undisclosed investor base.
- **Negative signal:** Deeply discounted placing (>25%). Balance sheet repair. Accompanied by profit warning.

#### Time Horizon

Short to medium term. Post-placing dynamics: shares often dip at announcement (dilution), then recover over 1–3 months if the raise was high quality. The recovery period is the entry window.

#### Detectability

| Aspect | Assessment |
|--------|------------|
| **Announcement type** | "Result of Placing", "Placing and Open Offer", "Subscription" — flow through LSEG Excel |
| **Frequency** | Several per week across AIM/small-cap |
| **Lead time** | Announced same day; price recovery takes weeks |
| **Body parsing** | Semi-structured: placing price, discount %, proceeds, use of proceeds, investors. LLM analysis required. |
| **False positive risk** | Moderate — nuanced quality classification |

#### Feasibility: T1 — Achievable today

**Enhancement with paid data (T3):** Price data enables automated post-placing recovery tracking.

#### State Model Integration

- High-quality placing → **Signal — Active** (with "recovery window" flag — optimal entry may be during post-announcement dip)
- Low-quality or distressed placing → **Signal — Negative** (if company was in a positive signal state)
- Placing on a company with no prior signal state → **Monitor** (fundraising quality noted, watching for follow-through)

---

### Lens 5: Convergence — Multi-Signal Stacking

#### The Signal

When multiple independent lenses fire on the same company within a defined time window, the convergence itself is a signal of elevated confidence. This is a meta-lens operating on the outputs of Lenses 1–4 and the existing pipeline.

#### Thesis Fit — VERY STRONG

When multiple informed parties independently express confidence — a director buys, a significant shareholder increases their stake, and the company initiates a buyback within a 30-day window — the probability of mispricing increases substantially.

**Convergence patterns (ranked by expected strength):**
1. Director buying + significant shareholder accumulation
2. Director buying + company buyback initiation
3. High-quality placing + director participation in the placing
4. Regulatory catalyst (existing lens) + director buying
5. Any three lenses firing within 30 days

#### Time Horizon

Short to medium term. Convergence signals should produce faster and more reliable price responses than single signals.

#### Feasibility: T1 (deferred) — Requires 2+ lenses operational

No new data sources needed. Requires a convergence detection layer monitoring signals per company across a rolling 30-day window.

#### State Model Integration

Convergence detection acts as an *amplifier* of signal state transitions rather than a separate state:
- Two lenses firing within 30 days on a company already in Signal — Active → **Signal — Reinforced** with elevated conviction flag
- Three or more lenses → **Signal — Reinforced** with high conviction flag
- Convergence of a positive and negative signal from different lenses → **Signal — Mixed** (the system surfaces the conflict rather than resolving it)

---

## Part C — Lenses Considered and Deferred

| Candidate | Reason for deferral |
|-----------|-------------------|
| **Short interest decline (FCA register)** | T2/T3. FCA publishes positions >0.5% but access is clunky and delayed. Revisit if a paid short interest feed becomes available. |
| **Broker/Nomad upgrade** | Currently excluded in LSEG filter. Real but very infrequent signal with indirect causal chain. Too many intervening steps for near-to-medium term. |
| **Earnings surprise / unscheduled trading statement** | Overlaps with existing regulatory catalyst lens. Better as an enhancement to the existing lens than a new one. |
| **Patent/IP milestones** | Too sector-specific (biotech/pharma/tech). Insufficient frequency across the diversified universe. |
| **Index promotion (AIM → Main Market)** | Well-known catalyst, usually priced in quickly. Promotion is a consequence, not an informed action. |
| **Debt reduction / charge satisfaction** | Partially observable via CH today. Causal chain is slow (quarters to years). Better suited to a long-term lens outside current focus. |

---

## Part D — Data Source Enhancement Roadmap

| Upgrade | Approximate Cost | Lenses Enhanced | Capability Unlocked |
|---------|-----------------|-----------------|-------------------|
| **RNS direct feed (EODHD)** | ~$19–79/month | All — makes Lenses 1–4 autonomous | Shifts from interactive LSEG Excel to autonomous daily detection |
| **Price data (Polygon.io / Twelve Data)** | ~$29/month | All, especially Lens 4 | Post-signal price tracking, recovery window detection, historic impact analysis |
| **Directors Deals aggregator** | Free (web) / varies (data feed) | Lens 1 | Pre-curated director dealing data |
| **Short interest data** | Variable | Deferred lens | Fundamentally different signal class (bearish thesis weakening) |

---

## Part E — Session Progress Record

**For continuity if this session expires:**

| Step | Status | Detail |
|------|--------|--------|
| 1. Ingest project context | ✅ Complete | HANDOVER.md v2.13 + historical conversation context |
| 2. Clarify scope and constraints | ✅ Complete | 5 lenses, feasibility-tiered, £20K ISA, open time horizons |
| 3. Generate candidate catalogue | ✅ Complete | 5 lenses + 6 deferred, documented in Part B |
| 4. Define state model | ✅ Complete | Two-axis model (signal state + position state), documented in Part A |
| 5. Workshop Lens 1 (Director Buying) | ⬜ Not started | Recommended next step |
| 6. Workshop Lens 2 (TR-1 Accumulation) | ⬜ Not started | |
| 7. Build Lens 1 as `StrategyLensBase` | ⬜ Not started | Requires workshop validation first |
| 8. Build state model in Firestore | ⬜ Not started | Can proceed in parallel with lens workshops |
| 9. Build Lens 2 | ⬜ Not started | |
| 10. Accumulate signal data (3–6 months) | ⬜ Not started | Required before Lens 5 (Convergence) validation |

**To resume in a new session:** Share this document and the latest HANDOVER.md. The next actions are:
- **Workshop Lens 1** — manual validation of director buying signals using LSEG web interface or Directors Deals
- **Design Firestore schema** for signal state, position state, and signal history — can begin in parallel

---

## Change Log

| Version | Date | Changes |
|---------|------|---------|
| 1 | 27 Feb 2026 | Initial catalogue — 5 lenses with feasibility tiers |
| 2 | 27 Feb 2026 | Added Part A (State Model): signal state and position state as independent axes; notification priority matrix; deferred behaviour; mute vs. decline distinction; implementation notes. Updated all lenses with state model integration sections. |
| 3 | 10 Mar 2026 | Lens 2 (TR-1): added implementation decisions from session 7 planning — Option B architecture, Gemini Flash + Search grounding, tr1_crossing as 5th classification type, materiality gate, signals collection routing. Full architecture in docs/design/tr1_lens_overview.md. |
