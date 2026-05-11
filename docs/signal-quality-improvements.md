# Signal Quality Improvements

**Audience:** Claude Code, working on the `agoseris/stock-research` repo.
**Goal:** Produce clearer, higher-conviction investment signals by improving how individual lenses classify signals and how the system aggregates across lenses.
**Primary success metric:** When the user receives an `act` recommendation, the median return over a 3–6 week holding window should be meaningfully positive, and catastrophic drawdowns (>30%) should be rare.

---

## 1. Background and motivation

The system currently runs three lenses — `regulatory_catalyst`, `director_purchasing`, `tr1_accumulation` — each operating as an independent classifier. A backtest of the 11 convergence (multi-lens) tickers and 72 standalone `tr1_accumulation` tickers with act-level signals stored March 10–25, 2026, measured to April 16, 2026, produced:

| Cohort       | n  | Mean return | Median return | Win rate | Worst case |
|--------------|----|-------------|---------------|----------|------------|
| Convergence  | 11 | +7.70%      | +4.02%        | 64%      | −7.7%      |
| Standalone   | 72 | +0.12%      | +2.62%        | 61%      | **−99.1%** |

The win rates are nearly identical. The gap in the means is driven almost entirely by a single catastrophic loss (<redacted>, <redacted>). Standalone TR-1 is finding inflexion candidates at a reasonable rate but cannot distinguish accumulation-before-rise from accumulation-before-collapse.

The architectural lesson: **convergence doesn't find more winners; it filters out disasters.** Non-firing lenses and noise-level signals from other lenses contain disqualification information that the system currently discards.

### 1.1 The failure mode (worked example)

This case should inform the implementation. The sequence:

1. **2026-03-13** — `tr1_accumulation` fires **strong / act**: *"upward crossing to 22.02% from an unstated previous holding… suggests significant conviction."*
2. **2026-03-20** — `regulatory_catalyst` fires a **noise / ignore** signal on a suspension warning: *"Delay in results publication and expected suspension of trading due to ongoing board investigation."*
3. **2026-04-01** — trading suspended. `tr1_accumulation` fires **another strong / act** on a 20% threshold crossing by "Rock Nominees Limited." `regulatory_catalyst` files the suspension itself as noise/ignore.

Two distinct failures:

- **Cross-lens blindness.** The `regulatory_catalyst` lens saw a red flag on March 20 and filed it as noise because it didn't fit its narrow thesis ("approval-type catalysts"). Had `tr1_accumulation` been able to see that signal, it could have suppressed the April 1 act.
- **Opacity misclassified as conviction.** "Rock Nominees Limited" is a nominee company — the beneficial owner is literally hidden. The lens read absence of evidence ("does not clearly indicate a passive fund") as positive evidence of conviction.

### 1.2 What the winners look like

Looking at top standalone performers (DOM, WJG, GROW, CAML, ASC), a consistent pattern emerges: **sustained accumulation campaigns**, visible as multiple threshold crossings over 3–5 weeks by the same or overlapping active managers (Saray Value Fund, Cobas, Frasers Group, Gresham House, Saba Capital, Van Eck). Single opaque crossings are not the winners — repeat crossings by identifiable active investors are.

---

## 2. Design principles for this change

1. **Preserve the early-move opportunity.** Do not require reinforcement before firing `act`. A strong single-lens signal is valuable — but it should be clearly distinguished from a reinforced signal.
2. **Non-firing lenses carry information.** Noise-level signals on red-flag keywords (suspension, investigation, delay, going concern, fundraise, placing) should disqualify or downgrade concurrent act signals.
3. **Opacity is not conviction.** If the notifier cannot be identified as a known active investor, confidence must be lower, not equal.
4. **Repeat behaviour beats one-shot behaviour.** Multiple active crossings within a rolling window should compound confidence.
5. **Investment amounts are small; false positives hurt less than false confidence hurts.** The cost of a missed signal is low. The cost of the user losing trust in the system is high. Signals must be honest about their maturity.

---

## 3. Changes to implement

### 3.1 Two-tier ACT recommendation

Introduce a new field on signals to distinguish signal maturity. The `recommendation` field stays as-is (`act` / `monitor` / `ignore`). Add a new field:

```
signal_maturity: "first_signal" | "reinforced"
```

**Definitions:**

- `first_signal` — this is the first qualifying signal for this ticker across any lens within a rolling 30-day window. Act on it if you believe the early-move thesis; accept that some will be false positives.
- `reinforced` — this ticker already had at least one other qualifying signal (from any lens, any type, at `monitor` or `act` level) in the preceding 30 days. Treat as higher conviction.

**Reinforcement rules:**

- Reinforcement can come from **any** lens, including the same lens firing again with a different signal instance (e.g. two TR-1 crossings by different notifiers).
- A signal is reinforced if there exists at least one prior non-dismissed signal for the same ticker within the 30-day lookback window.
- The lookback window is rolling and based on `stored_at`, not `published_at`.
- `noise / ignore` signals do not count towards reinforcement (they may count towards disqualification — see §3.2).

**Telegram notifications** should render this clearly:

- `🟢 ACT (first signal): TICKER — <summary>` — for first_signal
- `🟢🟢 ACT (reinforced): TICKER — <summary>` — for reinforced, with a one-line note naming the prior signal(s) and their lens(es)

The user will treat these differently in their own decision process; the system's job is to surface the distinction, not to make the investment decision.

### 3.2 Cross-lens disqualification layer

Before any lens fires `act` or `monitor`, check the signal history for the same ticker over the preceding 30 days. If any prior signal (including those classified as `noise / ignore`) matches any of the disqualification keywords below in either its summary or its signal_type, downgrade the recommendation.

**Disqualification keywords (case-insensitive substring match on summary + signal_type):**

```
suspension, suspended, investigation, delay in results, delayed results,
going concern, material uncertainty, placing, equity raise, open offer,
rescue financing, covenant breach, restatement, auditor resign,
qualified opinion, board investigation, profit warning
```

**Downgrade rules:**

- If a disqualification keyword is found in the prior 30 days → max recommendation for the new signal is `monitor`, regardless of what the classifier would have returned.
- Store the disqualifying prior signal's ID in a new field `disqualification_refs: [signal_id, ...]` on the new signal, so the reasoning is auditable.
- Add the disqualification reason to the summary shown in Telegram: *"Downgraded from act to monitor: prior signal 2026-03-20 flagged 'suspension' risk."*

**Rationale:** the backtest showed the `regulatory_catalyst` lens correctly identified the suspension risk eleven days before trading was halted. The information existed in the system but was silently discarded. This rule is the single highest-leverage change in the document.

### 3.3 Notifier opacity filter (TR-1 lens)

The `tr1_accumulation` classifier prompt treats unidentifiable notifiers as neutral ("notifier name does not clearly indicate a passive fund"). This reads absence of evidence as positive evidence. Change the prompt so that opacity reduces confidence.

**Implementation:** modify the TR-1 classifier prompt to classify the notifier into one of four categories, and constrain the maximum allowable `signal_strength` / `recommendation` accordingly.

| Notifier category                 | Examples                                                        | Max signal_strength | Max recommendation |
|-----------------------------------|-----------------------------------------------------------------|---------------------|--------------------|
| Identified active manager         | Saba Capital, Cobas, Gresham House, Artemis, Fulcrum, Van Eck, Frasers | `strong`            | `act`              |
| Identified passive / custody      | BlackRock, Vanguard, State Street, JPMorgan Chase (custody arm) | `noise`             | `ignore`           |
| Named individual (PDMR-like)      | Real person's name, no corporate wrapper                        | `strong`            | `act`              |
| Opaque / nominee / unidentifiable | "Rock Nominees Limited", generic "XYZ Ltd", non-specific SPVs   | `moderate`          | `monitor`          |

The classifier must pick exactly one category. If it genuinely cannot place the notifier in one of the three identified categories, it **must** pick opaque/nominee — not identified active manager. The prompt should explicitly instruct: *"If you are uncertain whether the notifier is an active investor, classify as 'opaque'. Uncertainty is not evidence of conviction."*

### 3.4 Repeat-signal reinforcement (TR-1 lens)

When the same ticker receives two or more `tr1_accumulation` signals within a rolling 30-day window and **both** of the following are true:

- At least two distinct notifiers (or the same active manager crossing a higher threshold), AND
- All relevant notifiers fall into the "identified active manager" or "named individual" categories from §3.3

Then the most recent signal should be classified as `reinforced` (per §3.1) and its `signal_strength` should be `strong` even if the individual crossing would only merit `moderate` in isolation.

**Rationale:** the backtest winners (DOM, WJG, GROW, ASC, CAML) all exhibited this pattern. Current lens treats each crossing as independent, losing the compounding evidence.

### 3.5 Signal proposal agent (new component)

Introduce a thin proposal/aggregation agent that runs after individual lenses and produces the user-facing `act` recommendation. Lenses emit **candidate** signals; the proposal agent decides what to surface.

Responsibilities:

1. Apply the cross-lens disqualification check (§3.2).
2. Compute `signal_maturity` (§3.1) by checking for prior qualifying signals on the same ticker within 30 days, across all lenses.
3. Compute `active_lens_count` and `active_lenses` at time of firing (already present in the corpus summary tool — make it part of the signal record itself).
4. Format the Telegram notification with the tier indicator.

This agent is cheap — it's Firestore lookups and formatting, not an LLM call. Keep it deterministic.

### 3.6 Schema additions

Add these fields to the `signals_unified` Firestore schema:

```
signal_maturity:       "first_signal" | "reinforced"
reinforcement_refs:    [signal_id, ...]   # prior signals that trigger reinforced status
disqualification_refs: [signal_id, ...]   # prior signals that triggered downgrade
notifier_category:     "active_manager" | "passive_custody" | "named_individual" | "opaque"   # TR-1 only, nullable for other lenses
active_lens_count_at_fire: int            # snapshot at time of firing, not live
```

Migration: backfill is not required. Existing signals can have these fields null. New signals going forward must populate them.

---

## 4. Out of scope for this change

- Adding new lenses. The existing three plus the proposal agent are enough to deliver the described improvement.
- Changing the universe or ingestion pipeline.
- Changing the Gemini Flash model choice — the prompt changes work within the existing cost envelope.
- Position sizing or portfolio construction. The system surfaces signals; the user decides stake sizes.
- Selling / exit signals. This spec is about entry signals only.

---

## 5. Validation

After implementation, re-run the backtest described in §1 against new signals from the two weeks following deployment. The target outcome:

1. The `reinforced` cohort should outperform the `first_signal` cohort on median return and should show lower drawdown variance.
2. The specific failure mode should not recur: any ticker with a disqualification keyword in its recent history should either not fire `act` or should fire `monitor` with the disqualifying reason surfaced.
3. The user should report that the Telegram notifications contain information they use — specifically, that the `first_signal` / `reinforced` distinction influences their decision whether to act immediately or wait.

If (1) fails to hold after ~30 new signals, the reinforcement definition probably needs tightening (e.g. require the reinforcement to come from a *different* lens, not just a repeat within the same lens). If (2) fails, the keyword list in §3.2 needs extension. If (3) fails, the Telegram formatting needs rework.

---

## 6. Implementation order

Suggested sequence, smallest-blast-radius first:

1. **Schema additions** (§3.6) — pure additive, no behavioural change.
2. **Notifier opacity filter** (§3.3) — prompt change to TR-1 lens only, easy to A/B.
3. **Cross-lens disqualification** (§3.2) — highest value; implement as a post-classification check in the proposal agent.
4. **Two-tier ACT** (§3.1) and **proposal agent** (§3.5) — together, since they share the same aggregation logic.
5. **Repeat-signal reinforcement** (§3.4) — refinement that builds on the proposal agent.

Each step should be independently testable against the existing corpus before moving on.
