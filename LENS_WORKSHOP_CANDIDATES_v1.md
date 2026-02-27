# Lens Workshop — Top 5 Candidate Lenses
**Date:** 27 February 2026
**Status:** Initial catalogue — no lenses yet validated
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

## Lens 1: Director and Insider Open-Market Buying (PDMR Dealings)

### The Signal

When a director or Person Discharging Managerial Responsibilities (PDMR) buys shares in their own company using personal capital on the open market, they are expressing confidence with skin in the game. UK Market Abuse Regulation requires disclosure within two business days via RNS.

### Thesis Fit — STRONG

This is the single cleanest expression of the informed confidence thesis. The director has asymmetric information about the company's prospects, regulatory position, pipeline, and balance sheet. They are choosing to commit personal wealth. The signal is directional and unambiguous.

**Key nuance:** The signal strength varies significantly:
- **Strong signal:** Open-market purchase using personal funds. Director chose the timing and size.
- **Moderate signal:** Exercise of options followed by retention (not immediate sale). Confidence expressed by holding rather than liquidating.
- **Weak/noise:** Exercise of options followed by immediate sale (liquidity event, tax-driven). Purchase required by employment terms. Small token purchases by newly appointed directors (ceremonial).
- **Negative signal (inverse lens):** Open-market sales by directors, particularly clustered sales by multiple directors, or sales shortly after positive announcements.

**Cluster effect:** A single director buying is informative. Multiple directors buying within a short window is substantially more informative — it suggests a shared view that the market is underpricing the company.

### Time Horizon

Short to medium term. Academic literature (notably Lakonishok & Lee, Jeng et al.) consistently finds abnormal returns in the 1–12 months following insider purchases in small-cap equities. The effect is strongest in the first 1–3 months and strongest for smaller companies where informational asymmetry is greatest — which maps directly to this universe.

### Detectability

| Aspect | Assessment |
|--------|------------|
| **Announcement type** | "Director/PDMR Dealing" — a standard RNS category on LSEG |
| **Frequency** | Regular — multiple per week across the AIM/small-cap universe |
| **Lead time** | 1–2 business days after the transaction (regulatory requirement) |
| **Body parsing** | Structured: transaction type (buy/sell/option exercise), number of shares, price per share, total value, director name, nature of interest. LLM can extract reliably. |
| **False positive risk** | Moderate — requires distinguishing genuine open-market buys from option exercises, automatic plans, and token purchases |

### Feasibility: T1 — Achievable today

PDMR dealings are RNS announcements. They already flow through the LSEG Excel export. A new `StrategyLensBase` implementation would:
1. Filter for "Director/PDMR Dealing" announcement type
2. Use LLM to parse the body: extract transaction type, value, whether open-market
3. Apply signal strength classification (strong / moderate / weak / noise)
4. Flag clusters (multiple directors at same company within rolling 30-day window)

**Enhancement with paid data (T3):** RNS direct feed via EODHD (~$19–79/month) would make this autonomous rather than requiring manual LSEG export. Directors Deals (directordeals.co.uk) is a specialist aggregator that pre-filters to this exact signal class.

### Actionability for £20K ISA Portfolio

High. Director buying is most informative in exactly the market cap range where £1–4K positions are viable. The signal typically precedes price moves by enough time to act. Bid-ask spreads in the £30M–£500M range are generally navigable for retail positions.

### Workshop Validation Approach

1. Use LSEG web interface to filter RNS for "Director/PDMR Dealing" across AIM + Small Cap
2. Alternatively, browse Directors Deals (directordeals.co.uk) which pre-curates this data
3. For each significant buy (>£10K personal capital, open-market), check subsequent 30/60/90-day price movement
4. Track hit rate and average return
5. Identify false positive patterns (option exercises mislabelled, token purchases)

---

## Lens 2: Significant Shareholder Accumulation (TR-1 Crossings)

### The Signal

When a significant shareholder crosses a notifiable threshold (typically 3%, 5%, 10%, 15%, 20%, 25%, 30% in the UK under the Disclosure Guidance and Transparency Rules), they must file a TR-1 notification via RNS. An *upward* crossing — an investor increasing their stake through a threshold — is an action expressing confidence.

### Thesis Fit — STRONG

Institutional investors, activist funds, and sophisticated private investors committing material capital to increase a position are informed parties acting with conviction. The TR-1 crossing is particularly informative because it represents a *sustained* accumulation, not a single transaction — the investor has been buying over a period and has now crossed a regulatory threshold.

**Key nuance:**
- **Strong signal:** Upward crossing by a known activist investor, value fund, or sector specialist. Crossing from below 3% to above 3% (new position initiation). Multiple upward crossings in sequence (3% → 5% → 10%).
- **Moderate signal:** Upward crossing by a passive/index fund (may be mechanical rebalancing). Crossing driven by a corporate action (share consolidation, buyback reducing denominator).
- **Weak/noise:** Downward crossings that are disclosed alongside upward crossings (portfolio reshuffling). Crossings driven by derivative instruments rather than physical holdings.
- **Negative signal (inverse):** Downward crossings by known quality investors. Disposal below 3% (exiting the position entirely).

**Investor identity matters:** A TR-1 crossing by Slater Investments, Crystal Amber, or Gresham House carries different information than one by a custody bank acting as nominee. The LLM analysis should attempt to classify the notifier.

### Time Horizon

Medium term. Significant accumulation typically precedes a catalyst (activist campaign, takeover approach, operational turnaround, or re-rating) by weeks to months. The crossing itself is a lagging indicator of the accumulation, but a leading indicator of whatever the investor believes will happen.

### Detectability

| Aspect | Assessment |
|--------|------------|
| **Announcement type** | "TR-1: Standard form..." — already flowing through LSEG Excel and *intentionally not excluded* from the current filter |
| **Frequency** | Several per week across the universe |
| **Lead time** | Crossing must be disclosed within 2 trading days |
| **Body parsing** | Semi-structured: the TR-1 form contains previous holding %, new holding %, nature of holding (direct/indirect/financial instruments), notifier identity. LLM can extract these fields. |
| **False positive risk** | Moderate-to-high — requires distinguishing genuine accumulation from mechanical rebalancing, derivative-driven crossings, and corporate-action-driven threshold changes |

### Feasibility: T1 — Achievable today

TR-1 notifications are already passing through the LSEG Excel pipeline (intentionally not excluded). A new lens would:
1. Filter for TR-1 announcement types
2. Use LLM to extract: direction (up/down), thresholds crossed, notifier identity, nature of holding
3. Classify signal strength based on direction, investor type, and whether the crossing represents new position initiation vs. incremental accumulation
4. Cross-reference with universe to check whether the company is monitored
5. Flag sequences (same investor, same company, multiple upward crossings)

### Actionability for £20K ISA Portfolio

Good, but with caveats. TR-1 crossings in small-caps often precede takeover approaches where the price jumps discontinuously — excellent for this portfolio size. However, the signal can also precede protracted activist campaigns where the timeline is uncertain. Position sizing should reflect the wider range of outcomes.

### Workshop Validation Approach

1. Filter recent LSEG exports for TR-1 announcements within the universe
2. For each upward crossing: identify the notifier, classify their type, note the threshold crossed
3. Track 30/60/90-day price movement from the crossing date
4. Cross-reference with subsequent corporate events (takeover, fundraise, board changes) to understand what the accumulator may have been anticipating
5. Identify noise patterns (passive fund rebalancing, derivative-driven crossings)

---

## Lens 3: Company Share Buyback Momentum

### The Signal

When a company buys back its own shares on the open market, management is making a capital allocation decision that its shares are undervalued relative to alternative uses of cash (investment, debt reduction, dividends). An *acceleration* or *initiation* of buybacks is a stronger signal than ongoing programmatic repurchases.

### Thesis Fit — MODERATE-TO-STRONG

Management teams are informed parties with deep knowledge of the company's cash generation, pipeline, and strategic position. A buyback commits company capital to the proposition that the shares are cheap. However, the signal is somewhat diluted by the fact that buybacks can also be driven by EPS management, return-of-capital policies, or option dilution offset rather than genuine value conviction.

**Key nuance:**
- **Strong signal:** Initiation of a new buyback programme, particularly by a company that has not previously bought back shares. Acceleration of an existing programme (larger daily volumes, extension of the programme). Buyback announced alongside a trading update that is neutral-to-positive (management has just confirmed outlook and is immediately committing capital).
- **Moderate signal:** Ongoing execution of a previously announced programme at steady pace. Buyback as part of a stated return-of-capital policy.
- **Weak/noise:** Buyback to offset dilution from employee share schemes (mechanical, not conviction-driven). Very small buybacks relative to market cap (<0.1%).
- **Enhancement signal:** Buyback occurring *simultaneously* with director personal buying (Lens 1 convergence) is substantially more informative than either alone.

### Time Horizon

Medium term. Buyback programmes typically run over weeks to months. The signal effect is cumulative — sustained buying pressure supports the share price and signals ongoing management confidence. Academic evidence (Ikenberry et al.) shows abnormal returns of 2–5% in the 1–6 months following buyback announcements in small-caps, with long-term outperformance over 1–4 years.

### Detectability

| Aspect | Assessment |
|--------|------------|
| **Announcement type** | "Transaction in Own Shares" — already flowing through LSEG Excel and *intentionally not excluded* |
| **Frequency** | Daily during active programmes; several companies active at any time |
| **Lead time** | Disclosed daily (each day's transactions announced next day) |
| **Body parsing** | Structured: number of shares purchased, price range, total programme progress. LLM can extract and track cumulative progress. |
| **False positive risk** | Low for detection, moderate for signal strength classification. The announcement itself is unambiguous; the question is whether it reflects genuine conviction or mechanical capital return. |

### Feasibility: T1 — Achievable today

"Transaction in Own Shares" announcements are already passing through the pipeline. A new lens would:
1. Filter for this announcement type
2. Use LLM to extract: daily purchase volume, price range, cumulative programme progress, total programme size
3. Detect *initiation* (first buyback announcement for a company) vs. *continuation*
4. Detect *acceleration* (increasing daily volumes or programme extension)
5. Track programme size relative to market cap (materiality filter)
6. Flag convergence with Lens 1 (director buying at same company)

### Actionability for £20K ISA Portfolio

Good. Buybacks create direct price support (the company is a buyer), and they reduce the outstanding share count which mechanically increases per-share value. For small-cap positions, the ongoing buyback buying pressure is helpful for liquidity — there is a consistent buyer in the market alongside you.

### Workshop Validation Approach

1. Filter recent LSEG exports for "Transaction in Own Shares" within the universe
2. Identify companies with active buyback programmes
3. For each: note when the programme was initiated, its size relative to market cap, and the current pace
4. Track price movement from initiation date and from any acceleration points
5. Cross-reference with director dealing data (Lens 1) to identify convergence cases

---

## Lens 4: Fundraising Quality — Placing and Subscription Analysis

### The Signal

When a company raises equity capital through a placing or subscription, the terms of the raise reveal the market's informed assessment of the company. The *quality* of the raise — who participated, at what price relative to market, whether it was oversubscribed, and what the proceeds are for — is the signal, not the raise itself.

### Thesis Fit — MODERATE-TO-STRONG

Institutional investors participating in a placing are making a commitment of capital based on their assessment of the company's prospects. The signal is in the *details*: a placing that attracts quality institutional names, at a modest discount, for growth capex, that is oversubscribed, is a strong informed confidence signal. A placing at a steep discount, for working capital or debt repayment, that barely gets away, is a distress signal.

**Key nuance:**
- **Strong signal:** Placing oversubscribed. Discount to market price is modest (≤10%). Proceeds for growth capex, acquisition, or specific identified opportunity. Participation by recognisable institutional names (named in the announcement). Company raised *more* than initially targeted.
- **Moderate signal:** Placing at market or modest discount. Proceeds for general corporate purposes. Standard institutional participation without named investors.
- **Weak signal:** Placing at significant discount (>15%). Proceeds for working capital or debt reduction. Small or undisclosed investor base.
- **Negative signal:** Deeply discounted placing (>25%). Proceeds explicitly for balance sheet repair. Open offer (existing shareholders rather than new institutional money). Accompanied by profit warning or downgrade.

**The inverse is also informative:** A company that announced intention to raise but *withdrew* the placing due to insufficient demand is a strong negative signal.

### Time Horizon

Short to medium term. Post-placing price dynamics are well-studied: shares often dip at announcement (dilution concern), then recover over 1–3 months if the raise was high quality. The recovery period is the entry window. For strong quality signals, the 2–4 week post-announcement dip can be the optimal entry point.

### Detectability

| Aspect | Assessment |
|--------|------------|
| **Announcement type** | "Result of Placing", "Placing and Open Offer", "Subscription", etc. — these flow through LSEG Excel |
| **Frequency** | Several per week across AIM/small-cap, more during active market windows |
| **Lead time** | Announced same day or next day; price impact is immediate but recovery takes weeks |
| **Body parsing** | Semi-structured: the announcement body contains placing price, discount %, number of shares, gross proceeds, intended use, bookrunner, and sometimes named investors. LLM analysis required to extract and classify. |
| **False positive risk** | Moderate — requires LLM to correctly assess quality dimensions (discount, use of proceeds, investor base) from announcement text |

### Feasibility: T1 — Achievable today

Placing announcements flow through LSEG Excel. A new lens would:
1. Filter for placing/subscription announcement types
2. Use LLM to extract: placing price, discount to market, gross proceeds, use of proceeds, whether oversubscribed, named investors if disclosed
3. Classify fundraising quality on a scale (strong / moderate / weak / negative)
4. Flag "recovery window" opportunities — strong quality raises where the share price has dipped post-announcement
5. Cross-reference with subsequent price data (when available) to validate the quality assessment

**Enhancement with paid data (T3):** Price data (Polygon.io or Twelve Data, ~$29/month) would enable automated tracking of post-placing price recovery, confirming whether the quality assessment translates to actual returns.

### Actionability for £20K ISA Portfolio

High, with a specific tactical pattern. The optimal play is to identify a high-quality placing (oversubscribed, modest discount, growth-oriented) and buy during the post-announcement price dip. This is a well-known but under-exploited pattern in AIM/small-cap because it requires monitoring a large number of placings and acting within a specific time window — exactly what a systematic pipeline is suited to.

### Workshop Validation Approach

1. Filter recent LSEG exports for placing/subscription announcements within the universe
2. For each: extract quality dimensions (discount, use of proceeds, investor names, oversubscription status)
3. Track price movement: (a) at announcement, (b) 5-day post, (c) 30-day post, (d) 90-day post
4. Correlate quality classification with subsequent price performance
5. Identify the optimal entry window for high-quality raises

---

## Lens 5: Convergence — Multi-Signal Stacking

### The Signal

When multiple independent lenses fire on the same company within a defined time window, the convergence itself is a signal of elevated confidence. This is not a new data source — it is a meta-lens that operates on the outputs of Lenses 1–4 (and the existing regulatory catalyst lens).

### Thesis Fit — VERY STRONG

The thesis is that informed parties expressing confidence through actions is predictive. When *multiple* informed parties independently express confidence — a director buys shares, a significant shareholder increases their stake, and the company initiates a buyback, all within a 30-day window — the probability that the market is mispricing the company increases substantially. Each signal alone has moderate predictive power; their convergence has multiplicative information value.

**Key convergence patterns (ranked by expected strength):**

1. **Director buying + significant shareholder accumulation** — two independent informed parties, both committing capital
2. **Director buying + company buyback initiation** — personal conviction aligned with corporate capital allocation
3. **High-quality placing + director participation in the placing** — the director both supported the dilution and invested personally
4. **Regulatory catalyst (existing lens) + director buying** — a positive CH/RNS filing followed by director purchasing
5. **Any three lenses firing on the same company within 30 days** — regardless of combination

### Time Horizon

Short to medium term. Convergence signals, where validated, should produce faster and more reliable price responses than single signals, because they represent a broader base of informed conviction. Expected actionable window: days to weeks for entry, weeks to months for the thesis to play out.

### Detectability

| Aspect | Assessment |
|--------|------------|
| **Data source** | Derived entirely from Lenses 1–4 and the existing regulatory catalyst lens |
| **Frequency** | Low — true multi-signal convergence is rare, which is what makes it valuable |
| **Lead time** | Depends on the component signals; the convergence is detected when the second/third signal arrives |
| **False positive risk** | Low — the requirement for multiple independent signals substantially reduces false positives |

### Feasibility: T1 (deferred) — Achievable with current architecture once 2+ lenses are operational

This lens requires no new data sources. It requires:
1. At least two other lenses (Lenses 1–4) to be operational and recording signals in Firestore
2. A convergence detection layer that monitors signals per company across a rolling 30-day window
3. Alert escalation when convergence thresholds are met (e.g., 2+ signals = elevated, 3+ = high conviction)

**Implementation sequence:** Build after at least Lenses 1 and 2 are operational and have accumulated enough signal history to validate the convergence hypothesis.

### Actionability for £20K ISA Portfolio

Very high. Convergence signals are rare but high-conviction. When they occur, they may warrant a larger position size (closer to £4K than £1K) given the elevated confidence level. The rarity means portfolio concentration risk is manageable — you won't get 20 convergence signals in a year.

### Workshop Validation Approach

This lens cannot be workshopped independently — it requires data from other lenses. The validation approach is:
1. Build and operate Lenses 1 and 2 first
2. After 3–6 months of signal data, retrospectively identify cases where signals from different lenses coincided on the same company
3. Compare price outcomes for convergence cases vs. single-signal cases
4. Determine whether convergence genuinely adds predictive value or merely correlates with it

---

## Implementation Priority and Sequencing

| Priority | Lens | Feasibility | Dependencies | Estimated Build Effort |
|----------|------|-------------|--------------|----------------------|
| **1** | Director/PDMR Buying | T1 | None — new `StrategyLensBase` implementation | Low: announcement type filter + LLM prompt |
| **2** | TR-1 Accumulation | T1 | None | Low-to-moderate: LLM prompt needs to handle TR-1 form structure |
| **3** | Share Buyback Momentum | T1 | None, but benefits from temporal tracking (Firestore state) | Moderate: needs cumulative tracking across announcements |
| **4** | Fundraising Quality | T1 | Benefits from price data (T3) for recovery tracking | Moderate: LLM quality classification is nuanced |
| **5** | Convergence | T1 (deferred) | Lenses 1 + 2 operational with signal history | Moderate: cross-lens query layer |

**Note on sequencing:** Lens 1 is recommended as the first build because it has the cleanest thesis fit, the most tractable body parsing, and the most established academic evidence base. It is also the lens most amenable to manual workshop validation using publicly available data (Directors Deals, LSEG web interface).

---

## Lenses Considered and Deferred

The following were evaluated and not included in the top 5, with reasons:

| Candidate | Reason for deferral |
|-----------|-------------------|
| **Short interest decline (FCA register)** | T2/T3 — FCA publishes positions >0.5%, but the register is clunky to access programmatically and updates are delayed. The signal is real (short sellers covering = bearish thesis weakening) but detectability is poor without a paid aggregator. Revisit if a paid short interest feed becomes available. |
| **Broker/Nomad upgrade** | Currently excluded in the LSEG filter. Signal is real but very infrequent and the causal chain is indirect (better adviser → *future* corporate activity → *future* re-rating). Too many intervening steps for reliable near-to-medium term prediction. |
| **Earnings surprise / unscheduled trading statement** | Overlaps heavily with the existing regulatory catalyst lens. The *tone* of a trading statement is already something the LLM should be assessing. Better implemented as an enhancement to the existing lens than as a separate lens. |
| **Patent/IP milestones** | Too sector-specific (biotech/pharma/tech). The universe is diversified; a lens that only applies to 10–15% of companies lacks the frequency needed. |
| **Index promotion (AIM → Main Market, FTSE admission)** | Well-known catalyst, usually priced in quickly. The informed confidence signal is weak — the promotion is a consequence of market cap growth, not an action expressing forward-looking confidence. |
| **Debt reduction / charge satisfaction** | Partially observable via CH filings today (charge satisfaction). However, the causal chain is slow (debt reduction → improved balance sheet → re-rating over quarters/years). Better suited to a long-term value lens, which is outside the near-to-medium term focus. Could be revisited as a long-term lens. |

---

## Data Source Enhancement Roadmap

These paid upgrades would unlock specific capabilities across multiple lenses:

| Upgrade | Approximate Cost | Lenses Enhanced | Capability Unlocked |
|---------|-----------------|-----------------|-------------------|
| **RNS direct feed (EODHD)** | ~$19–79/month | All — makes Lenses 1–4 autonomous | Shifts from interactive (LSEG Excel) to autonomous daily detection |
| **Price data (Polygon.io / Twelve Data)** | ~$29/month | All, especially Lens 4 | Post-signal price tracking, recovery window detection, historic impact analysis |
| **Directors Deals aggregator** | Free (web) / varies (data feed) | Lens 1 specifically | Pre-curated director dealing data, reduces LLM parsing burden |
| **Short interest data** | Variable (paid aggregator) | Deferred short interest lens | Would unlock a fundamentally different signal class (bearish thesis weakening) |

---

## Session Progress Record

**For continuity if this session expires:**

| Step | Status | Detail |
|------|--------|--------|
| 1. Ingest project context | ✅ Complete | HANDOVER.md v2.13 + historical conversation context loaded |
| 2. Clarify scope and constraints | ✅ Complete | 5 lenses, feasibility-tiered, £20K ISA constraint, open time horizons |
| 3. Generate candidate catalogue | ✅ Complete | This document — 5 lenses + 6 deferred candidates |
| 4. Workshop Lens 1 (Director Buying) | ⬜ Not started | Recommended next step: manual validation using LSEG web interface or Directors Deals |
| 5. Workshop Lens 2 (TR-1 Accumulation) | ⬜ Not started | |
| 6. Build Lens 1 as `StrategyLensBase` implementation | ⬜ Not started | Requires workshop validation first |
| 7. Build Lens 2 | ⬜ Not started | |
| 8. Accumulate signal data (3–6 months) | ⬜ Not started | Required before Lens 5 (Convergence) can be validated |

**To resume in a new session:** Share this document and the latest HANDOVER.md. The next action is to run the Lens 1 workshop — manual validation of director buying signals using publicly available data.
