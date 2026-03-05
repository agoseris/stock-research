# Orchestrator Design

## Purpose

The orchestrator is the component that coordinates the end-to-end 
agentic investigation pipeline. It sits between the ingestion 
pipeline (which produces classified, structured PDMR data) and 
the Streamlit frontend (which displays results to the investor).

The orchestrator has four responsibilities:
1. Receive a verified open market PDMR transaction
2. Run the simple lens and agentic investigation in parallel
3. Pass all outputs to the Synthesis agent
4. Persist results and trigger notification

---

## Architecture Position

```
Ingestion pipeline (Phase 2)
         │
         ▼
Classification + extraction call
         │
         ├─ article_type = pdmr_transaction
         │  AND transaction_category = open_market_purchase
         │  OR open_market_disposal
         │         │
         │         ▼
         │    ORCHESTRATOR  ◄── entry point
         │         │
         │         ├──► lens_director_simple (one-shot)
         │         │
         │         └──► Agentic investigation
         │                   │
         │              ┌────┼────┐
         │              ▼    ▼    ▼
         │            L1   L2   L3  (parallel)
         │              └────┼────┘
         │                   ▼
         │            Synthesis agent
         │                   │
         │                   ▼
         │            Firestore signals
         │                   │
         │                   ▼
         │            Streamlit notification
         │
         └─ other article types → existing pipeline
```

---

## Execution Environment

The orchestrator runs on the **local machine (frontend)**.

Rationale:
- Layer 1 and Layer 3 tool calls require yfinance, which 
  requires a residential IP address to avoid rate limiting 
  and IP blocking
- The ingestion pipeline already runs locally — the 
  orchestrator is a natural extension of this
- Keeping orchestrator local avoids the need for a proxy 
  endpoint between GCP backend and local yfinance calls

**Important note on infrastructure:** Running the orchestrator 
locally is a **tactical PoC decision**, not an architectural 
principle. The constraints driving it are practical:
- Remote hosting (GCP or Streamlit Cloud) introduced 
  complexity, cost, security, and accessibility challenges 
  not worth resolving at this stage
- Companies House data is already handled by the GCP backend 
  via API — demonstrating that cloud execution is viable 
  where IP sensitivity is not a constraint
- In a future production phase, the full pipeline would be 
  cloud-hosted. The local machine dependency is a known 
  limitation to be revisited, not a design commitment

The GCP backend is not involved in orchestrator execution 
during the PoC. It continues to handle Companies House 
ingestion and serves as the Firestore persistence layer.

---

## Build Sequence

Build and test in this order. Do not proceed to the next 
stage until the current stage is verified.

```
Stage 1: Classification and extraction call
Stage 2: lens_director_simple
Stage 3: Agentic orchestrator — sequential (one layer at a time)
Stage 4: Agentic orchestrator — parallel (all three layers)
Stage 5: Synthesis agent call
Stage 6: Streamlit display extension
```

---

## Stage 1: Classification and Extraction Call

### What it does

Extends the existing Phase 2 ingestion loop. For every candidate 
article that has passed Phase 1 filtering, fetches the article 
body and calls the classification and extraction prompt.

### Where it fits in existing code

The existing Phase 2 loop calls the LLM with `lens_catalyst` 
for every candidate article. The classification and extraction 
call is inserted before this existing call and gates the routing.

```python
# Existing Phase 2 loop (simplified):
for article in candidate_articles:
    body = headless_browser.fetch(article.source_url)
    result = llm.call(lens_catalyst_prompt, body)
    store_result(result)

# New Phase 2 loop:
for article in candidate_articles:
    body = headless_browser.fetch(article.source_url)
    
    # NEW: classify and extract
    classification = llm.call(
        classification_extraction_prompt, 
        body,
        model="claude-sonnet-4-20250514"
    )
    
    # NEW: persist classification to announcements
    update_announcement(article.id, classification)
    
    # NEW: route based on classification
    if classification.article_type == "pdmr_transaction":
        for transaction in classification.transactions:
            persist_pdmr_transaction(transaction)
            if transaction.transaction_category in [
                "open_market_purchase", 
                "open_market_disposal"
            ]:
                # NEW: trigger orchestrator
                orchestrator.run(article, transaction)
    
    elif classification.article_type == "regulatory_catalyst":
        # EXISTING: call lens_catalyst as before
        result = llm.call(lens_catalyst_prompt, body)
        store_result(result)
    
    elif classification.article_type == "substantive_news":
        # NEW: persist summary, no lens
        persist_news_summary(classification)
    
    elif classification.article_type == "administrative":
        # NEW: update classification only
        pass
```

### Acceptance criteria

```
□ Classification prompt called for every candidate article
□ article_type correctly written to announcements collection
□ extraction_status = "complete" written on success
□ extraction_status = "failed" written on LLM error
□ pdmr_transactions documents created for all transactions 
  in a multi-director filing (one document per transaction)
□ transaction_category correctly routes:
    open_market_purchase → orchestrator triggered
    sip_purchase → stored only, orchestrator not triggered
□ regulatory_catalyst → existing lens_catalyst called as before
□ substantive_news → summary persisted, no lens triggered
□ administrative → classification only, no child documents
□ No existing lens_catalyst behaviour broken
□ Test against at least one real PDMR filing of each type:
    open market purchase, SIP purchase, disposal
```

---

## Stage 2: lens_director_simple

### What it does

A single LLM call producing the simple one-shot director signal 
assessment. Runs synchronously within the orchestrator before 
the agentic investigation begins.

### Implementation

```python
def run_simple_lens(transaction: PdmrTransaction, 
                    company_profile: CompanyProfile) -> dict:
    """
    Calls lens_director_simple prompt.
    Returns structured output dict.
    Persists to signals document under simple_* fields.
    """
    prompt = build_lens_director_simple_prompt(
        transaction, company_profile
    )
    
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    result = parse_simple_lens_output(response.content[0].text)
    
    # Persist to signals document
    signals_ref = firestore.collection("signals").document(
        transaction.rns_article_id
    )
    signals_ref.set({
        "ticker": transaction.ticker,
        "company_name": transaction.company_name,
        "published_at": transaction.transaction_date_reported,
        "signal_type": "director_buying" 
                       if transaction.transaction_category == 
                       "open_market_purchase" 
                       else "director_disposal",
        "simple_completed_at": datetime.utcnow(),
        "simple_transaction_nature": result["TRANSACTION_NATURE"],
        "simple_signal_strength": result["SIGNAL_STRENGTH"],
        "simple_signal_direction": result["SIGNAL_DIRECTION"],
        "simple_recommended_action": result["RECOMMENDED_ACTION"],
        "simple_limitations": result["LIMITATIONS"],
        "simple_summary": result["SUMMARY"],
        "agentic_status": "pending",
        "investor_action": "pending",
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(days=730)
    }, merge=True)
    
    return result
```

### Acceptance criteria

```
□ Simple lens called for every open market transaction
□ Output correctly parsed from LLM response text
□ All simple_* fields written to signals Firestore document
□ signals document uses rns_article_id as document_id
  (deduplication key)
□ agentic_status = "pending" set on signals document creation
□ investor_action = "pending" set on creation
□ Token usage logged (input and output tokens)
□ Test against at least 3 real PDMR transactions of varying types:
    first entry, position increase, small token purchase
□ Telegram notification fires for signals above threshold
  (confirm existing notification logic still works)
□ Streamlit displays simple lens output for new signals
```

---

## Stage 3: Agentic Orchestrator — Sequential

### What it does

Runs the three layer agents one after another (not yet in 
parallel). This stage verifies that each layer agent call 
works correctly with real tool outputs before adding 
the complexity of concurrency.

### Pre-fetch shared price data

Before calling any layer agent, pre-fetch price data once 
for the ticker. This data is passed to both Layer 1 and 
Layer 3, avoiding redundant yfinance calls.

```python
def prefetch_price_data(ticker: str, 
                        transaction_date: date,
                        published_at: date) -> dict:
    """
    Pre-fetches all price data needed by Layer 1 and Layer 3.
    Called once per investigation event.
    Returns combined price data cache passed to both layers.
    """
    price_history = get_price_history(
        ticker, windows=["1w", "1m", "3m", "1y"]
    )
    price_movement = get_price_movement_context(
        ticker, transaction_date, published_at
    )
    return {
        "price_history": price_history,
        "price_movement": price_movement
    }
```

### Layer agent calls

Each layer agent is called using the Anthropic API with 
tool use enabled. The agent receives its inputs, calls 
tools as needed, and returns a JSON output.

```python
def run_layer_agent(layer: int, 
                    inputs: dict, 
                    tools: list,
                    model: str) -> tuple[dict, dict]:
    """
    Runs a single layer agent.
    Returns (output_json, token_usage).
    
    Uses Anthropic API tool use pattern:
    1. Send initial message with inputs and tool definitions
    2. Handle tool_use blocks by calling actual tools
    3. Send tool results back
    4. Repeat until text response received
    5. Parse JSON from final text response
    """
    messages = [
        {"role": "user", "content": build_layer_prompt(layer, inputs)}
    ]
    
    while True:
        response = anthropic_client.messages.create(
            model=model,
            max_tokens=2000,
            tools=tools,
            messages=messages
        )
        
        # Check for tool use
        tool_use_blocks = [
            b for b in response.content 
            if b.type == "tool_use"
        ]
        
        if tool_use_blocks:
            # Execute tool calls and collect results
            tool_results = []
            for block in tool_use_blocks:
                result = execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result)
                })
            
            # Add assistant response and tool results to messages
            messages.append({
                "role": "assistant", 
                "content": response.content
            })
            messages.append({
                "role": "user",
                "content": tool_results
            })
        
        else:
            # Final text response — extract JSON
            text_blocks = [
                b for b in response.content 
                if b.type == "text"
            ]
            output_json = json.loads(text_blocks[0].text)
            token_usage = {
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens
            }
            return output_json, token_usage
```

### Sequential orchestrator

```python
def run_agentic_investigation_sequential(
    article: Announcement,
    transaction: PdmrTransaction,
    company_profile: CompanyProfile,
    simple_lens_output: dict
) -> dict:
    """
    Runs three layer agents sequentially.
    Used for testing and debugging before parallelisation.
    """
    
    # Update signals document: investigation starting
    update_signal_status(transaction.rns_article_id, "running")
    
    # Pre-fetch shared price data
    price_cache = prefetch_price_data(
        transaction.ticker,
        transaction.transaction_date_actual,
        transaction.transaction_date_reported
    )
    
    # Layer 1 — Platform Assessment
    layer1_inputs = build_layer1_inputs(
        transaction, company_profile, price_cache
    )
    layer1_output, layer1_tokens = run_layer_agent(
        layer=1,
        inputs=layer1_inputs,
        tools=LAYER1_TOOLS,
        model="claude-haiku-4-5-20251001"
    )
    
    # Layer 2 — Director Purchase Analysis
    layer2_inputs = build_layer2_inputs(transaction)
    
    # Determine Layer 2 model based on conditional branch
    # Use Sonnet if credibility research likely needed
    layer2_model = (
        "claude-sonnet-4-20250514"
        if should_trigger_credibility_research(transaction)
        else "claude-haiku-4-5-20251001"
    )
    layer2_output, layer2_tokens = run_layer_agent(
        layer=2,
        inputs=layer2_inputs,
        tools=LAYER2_TOOLS,
        model=layer2_model
    )
    
    # Layer 3 — Signal Freshness
    layer3_inputs = build_layer3_inputs(
        transaction, article, price_cache
    )
    layer3_output, layer3_tokens = run_layer_agent(
        layer=3,
        inputs=layer3_inputs,
        tools=LAYER3_TOOLS,
        model="claude-sonnet-4-20250514"
    )
    
    # Synthesis
    synthesis_output, synthesis_tokens = run_synthesis(
        transaction,
        layer1_output,
        layer2_output,
        layer3_output,
        simple_lens_output
    )
    
    # Aggregate token usage
    total_tokens = aggregate_tokens(
        layer1_tokens, layer2_tokens, 
        layer3_tokens, synthesis_tokens
    )
    
    # Persist to Firestore
    persist_agentic_results(
        transaction.rns_article_id,
        layer1_output,
        layer2_output,
        layer3_output,
        synthesis_output,
        total_tokens
    )
    
    return synthesis_output
```

### Acceptance criteria — sequential

```
□ Layer 1 agent runs successfully with real tool outputs:
    get_company_profile called and result used
    get_price_history called and result used
    get_volatility_metrics called and result used
    get_index_snapshot called and result used
    get_relative_performance called and result used
    JSON output returned and parseable
□ Layer 2 agent runs successfully with real tool outputs:
    get_director_transaction_history called
    get_company_insider_activity called
    get_company_ch_filings called
    conditional branch correctly triggered or skipped
    JSON output returned and parseable
□ Layer 3 agent runs successfully with real tool outputs:
    get_company_news_history called
    price_cache used (no redundant yfinance call)
    get_index_context_for_freshness called
    JSON output returned and parseable
□ All three layers complete without error for 
  at least one real PDMR transaction
□ Token usage captured per layer
□ agentic_layer1_output, agentic_layer2_output, 
  agentic_layer3_output written to Firestore signals document
□ Total elapsed time logged (expected: 30-120 seconds 
  in sequential mode)
```

---

## Stage 4: Agentic Orchestrator — Parallel

### What it does

Converts the sequential layer execution to parallel using 
`concurrent.futures.ThreadPoolExecutor`. All three layers 
run simultaneously. The orchestrator waits for all three 
to complete before calling the Synthesis agent.

### Implementation

```python
import concurrent.futures

def run_agentic_investigation_parallel(
    article: Announcement,
    transaction: PdmrTransaction,
    company_profile: CompanyProfile,
    simple_lens_output: dict
) -> dict:
    """
    Runs three layer agents in parallel.
    Production implementation.
    """
    
    update_signal_status(transaction.rns_article_id, "running")
    
    # Pre-fetch shared price data (sequential — must complete 
    # before layers start)
    price_cache = prefetch_price_data(
        transaction.ticker,
        transaction.transaction_date_actual,
        transaction.transaction_date_reported
    )
    
    # Define layer tasks
    layer1_fn = lambda: run_layer_agent(
        layer=1,
        inputs=build_layer1_inputs(
            transaction, company_profile, price_cache
        ),
        tools=LAYER1_TOOLS,
        model="claude-haiku-4-5-20251001"
    )
    
    layer2_model = (
        "claude-sonnet-4-20250514"
        if should_trigger_credibility_research(transaction)
        else "claude-haiku-4-5-20251001"
    )
    layer2_fn = lambda: run_layer_agent(
        layer=2,
        inputs=build_layer2_inputs(transaction),
        tools=LAYER2_TOOLS,
        model=layer2_model
    )
    
    layer3_fn = lambda: run_layer_agent(
        layer=3,
        inputs=build_layer3_inputs(
            transaction, article, price_cache
        ),
        tools=LAYER3_TOOLS,
        model="claude-sonnet-4-20250514"
    )
    
    # Run all three layers in parallel
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=3
    ) as executor:
        future_l1 = executor.submit(layer1_fn)
        future_l2 = executor.submit(layer2_fn)
        future_l3 = executor.submit(layer3_fn)
        
        # Wait for all three with timeout
        done, not_done = concurrent.futures.wait(
            [future_l1, future_l2, future_l3],
            timeout=300  # 5 minute timeout per investigation
        )
        
        # Handle timeouts
        if not_done:
            for future in not_done:
                future.cancel()
            handle_layer_timeout(not_done, transaction)
        
        # Retrieve results
        layer1_output, layer1_tokens = (
            future_l1.result() if future_l1 in done 
            else (None, None)
        )
        layer2_output, layer2_tokens = (
            future_l2.result() if future_l2 in done 
            else (None, None)
        )
        layer3_output, layer3_tokens = (
            future_l3.result() if future_l3 in done 
            else (None, None)
        )
    
    # Synthesis (sequential — depends on all three layers)
    synthesis_output, synthesis_tokens = run_synthesis(
        transaction,
        layer1_output,
        layer2_output,
        layer3_output,
        simple_lens_output
    )
    
    total_tokens = aggregate_tokens(
        layer1_tokens, layer2_tokens,
        layer3_tokens, synthesis_tokens
    )
    
    persist_agentic_results(
        transaction.rns_article_id,
        layer1_output,
        layer2_output,
        layer3_output,
        synthesis_output,
        total_tokens
    )
    
    return synthesis_output
```

### Handling partial failures

If one layer fails or times out, the Synthesis agent should 
still run with whatever layers completed:

```python
def handle_layer_timeout(timed_out_futures, transaction):
    """
    Called when one or more layers time out.
    Logs the failure and sets a placeholder output.
    """
    for future in timed_out_futures:
        layer_num = get_layer_number(future)
        log_layer_failure(layer_num, transaction, "timeout")
        # Synthesis agent receives None for failed layers
        # and must handle this gracefully
```

The Synthesis agent prompt instructs it to surface failed 
layers explicitly in the briefing rather than silently 
omitting them.

### Acceptance criteria — parallel

```
□ All three layers confirmed running concurrently:
    log timestamps show overlapping execution
□ Total elapsed time materially less than sequential:
    expect 40-60% reduction in wall clock time
□ Results identical to sequential run for same transaction
□ Timeout handling tested:
    artificially delay one layer → other two complete
    synthesis runs with partial results
    failed layer noted in synthesis limitations
□ Thread safety confirmed:
    no shared mutable state between layer threads
    Firestore writes do not conflict
□ 5-minute timeout fires correctly for genuinely 
  stuck layer calls
□ agentic_status = "complete" set on success
□ agentic_status = "failed" set on total failure
```

---

## Stage 5: Synthesis Agent Call

### What it does

Calls the Synthesis agent with all three layer outputs and 
the simple lens output. Returns the final investor briefing.

```python
def run_synthesis(
    transaction: PdmrTransaction,
    layer1_output: dict | None,
    layer2_output: dict | None,
    layer3_output: dict | None,
    simple_lens_output: dict
) -> tuple[dict, dict]:
    """
    Calls Synthesis agent.
    Handles None layer outputs gracefully.
    Returns (synthesis_output, token_usage).
    """
    
    # Replace None outputs with explicit failure markers
    l1 = layer1_output or {"error": "Layer 1 failed or timed out"}
    l2 = layer2_output or {"error": "Layer 2 failed or timed out"}
    l3 = layer3_output or {"error": "Layer 3 failed or timed out"}
    
    prompt = build_synthesis_prompt(
        transaction, l1, l2, l3, simple_lens_output
    )
    
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    synthesis_output = json.loads(
        response.content[0].text
    )
    token_usage = {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens
    }
    
    return synthesis_output, token_usage
```

### Acceptance criteria

```
□ Synthesis agent called with outputs from all three layers
□ synthesis_output JSON correctly parsed
□ Synthesis handles None layer inputs gracefully:
    failed layer noted in limitations
    recommendation still produced where possible
□ agentic_recommendation written to signals document
□ agentic_justification written to signals document
□ agentic_limitations written to signals document
□ agentic_token_usage written to signals document:
    all six layer token counts present
    total_input and total_output correct
□ agentic_completed_at timestamp written
□ agentic_status = "complete" confirmed
□ Test with all three layers present
□ Test with one layer missing (None)
```

---

## Stage 6: Streamlit Display Extension

### What it does

Extends the existing Streamlit frontend to display both the 
simple lens output and the agentic briefing for the same 
signal, with clear visual differentiation.

### Display principles

From the design sessions:
- Simple and agentic outputs displayed side by side or 
  in clearly labelled tabs
- `fact_summary` and `inference_summary` displayed 
  separately with clear labels
- `data_quality_summary.thin_data_flags` displayed 
  prominently — never hidden
- `vs_simple_lens` delta section visible
- Token usage displayed for PoC cost monitoring
- `limitations` displayed in full — never collapsed
- Visual distinction between simple and agentic signals 
  must be immediately apparent

### Deduplication in display

A single RNS article must appear once in the signal list, 
not twice. The signals document contains both simple and 
agentic outputs — the display renders both from this 
single document.

```python
# Correct: one signal card per signals document
for signal in signals_collection:
    render_signal_card(
        simple_output=signal.simple_fields,
        agentic_output=signal.agentic_fields,
        show_agentic=(signal.agentic_status == "complete")
    )

# Incorrect: separate cards for simple and agentic
# This would violate the deduplication constraint
```

### Acceptance criteria

```
□ Signal list shows one entry per signals document
□ Simple lens output visible for all signals
□ Agentic briefing visible when agentic_status = "complete"
□ "Agentic analysis pending" shown when status = "running"
□ Clear visual differentiation between simple and agentic
□ fact_summary and inference_summary labelled distinctly
□ thin_data_flags visible and not hidden
□ vs_simple_lens section rendered
□ Token usage displayed: per layer and total
□ limitations section displayed in full
□ agentic_recommendation displayed prominently
□ No duplicate signal entries in the list
□ Existing lens_catalyst signals unaffected
□ Test: same transaction shows one entry with both outputs
```

---

## Error Handling — Overall

```
Classification fails:
  → extraction_status = "failed" on announcements document
  → article not routed to any lens
  → logged for manual review

Simple lens fails:
  → simple_* fields absent from signals document
  → agentic investigation still proceeds
  → Streamlit shows "Simple lens failed" for this signal

Layer agent fails:
  → layer output = None passed to Synthesis
  → Synthesis surfaces failure in limitations
  → agentic_status = "partial" if some layers completed
  → agentic_status = "failed" if all layers failed

Synthesis fails:
  → agentic_status = "failed"
  → layer outputs already persisted (recoverable)
  → logged — synthesis can be retried manually

Firestore write fails:
  → log and retry once
  → if retry fails: log to local file for manual recovery
  → do not block pipeline for subsequent articles
```

---

## Token Usage Instrumentation

Token usage is recorded in the signals document for every 
investigation. This is the primary PoC cost measurement 
instrument.

```
agentic_token_usage:
  layer1_input:     integer
  layer1_output:    integer
  layer2_input:     integer
  layer2_output:    integer
  layer3_input:     integer
  layer3_output:    integer
  synthesis_input:  integer
  synthesis_output: integer
  total_input:      integer
  total_output:     integer
  estimated_cost_gbp: float  ← calculated from Anthropic 
                                pricing at time of call
                                store model pricing constants
                                in config, not hardcoded
```

Review token usage periodically during PoC to identify:
- Which layer consumes the most tokens
- Whether credibility research branch materially increases cost
- Whether the agentic investigation cost is justified by 
  output quality delta vs simple lens

---

## Configuration

The following should be in a config file, not hardcoded:

```python
ORCHESTRATOR_CONFIG = {
    # Models
    "classification_model": "claude-sonnet-4-20250514",
    "simple_lens_model": "claude-haiku-4-5-20251001",
    "layer1_model": "claude-haiku-4-5-20251001",
    "layer2_model_standard": "claude-haiku-4-5-20251001",
    "layer2_model_credibility": "claude-sonnet-4-20250514",
    "layer3_model": "claude-sonnet-4-20250514",
    "synthesis_model": "claude-sonnet-4-20250514",
    
    # Timeouts
    "layer_timeout_seconds": 300,
    "tool_call_timeout_seconds": 30,
    
    # Rate limiting
    "yfinance_delay_seconds": 2,
    "ch_api_delay_seconds": 1,
    
    # Thresholds
    "credibility_holding_pct_threshold": 0.5,
    "cluster_detection_window_days": 30,
    "insider_activity_lookback_days": 90,
    
    # Pricing (GBP, update if Anthropic changes pricing)
    "haiku_input_cost_per_1m_tokens": 0.63,
    "haiku_output_cost_per_1m_tokens": 1.26,
    "sonnet_input_cost_per_1m_tokens": 2.36,
    "sonnet_output_cost_per_1m_tokens": 11.81
}
```

---

## Files

```
orchestrator/
  __init__.py
  orchestrator.py          ← main entry point
  classification.py        ← Stage 1: classification call
  simple_lens.py           ← Stage 2: lens_director_simple
  layer_runner.py          ← Stage 3/4: layer agent execution
  synthesis.py             ← Stage 5: synthesis agent call
  persistence.py           ← Firestore read/write operations
  token_tracker.py         ← token usage logging
  config.py                ← configuration constants
  
tests/
  test_orchestrator.py
  test_classification.py
  test_simple_lens.py
  test_layer_runner.py
  test_synthesis.py
```
