"""
Synthesis agent for the director buying lens orchestrator — Stage 5.

Receives the three layer outputs plus the simple lens output and
produces the final structured investor briefing.

No tool calls — pure reasoning from structured inputs.
Model: Claude Sonnet (synthesis_model from config).
"""

import json
from typing import Optional

from utilities.orchestrator.config import ORCHESTRATOR_CONFIG
from utilities.orchestrator.token_tracker import aggregate_token_usage


def build_synthesis_prompt(
    transaction: dict,
    layer1_output: Optional[dict],
    layer2_output: Optional[dict],
    layer3_output: Optional[dict],
    simple_lens_output: Optional[dict],
) -> str:
    """
    Build the synthesis agent prompt.

    Parameters
    ----------
    transaction : dict
        Transaction fields: company_name, ticker, director_name,
        transaction_category, total_consideration_gbp, published_at.
    layer1_output, layer2_output, layer3_output : dict | None
        Parsed JSON outputs from each layer agent.
        None means the layer failed or timed out.
    simple_lens_output : dict | None
        Parsed simple lens output for comparison context.

    Returns
    -------
    str
        Formatted prompt ready to send to the Anthropic API.
    """
    def _fmt(obj: Optional[dict]) -> str:
        if obj is None:
            return "UNAVAILABLE — layer failed or timed out"
        return json.dumps(obj, indent=2, default=str)

    consideration = transaction.get("total_consideration_gbp") or 0.0
    published_at = transaction.get("published_at", "")

    return f"""\
You are an investment research assistant producing a structured briefing for a retail investor operating in LSE small-cap and AIM markets.

The investor makes their own final buy/no-buy decisions. Your role is to synthesise three independent investigation reports into a clear, honest, and well-evidenced briefing that makes their decision faster and better informed.

THE INVESTOR:
- Operates with retail position sizes of £1,000-£4,000 within an ISA framework
- Focuses on LSE small-caps and AIM where informed parties have expressed confidence in upcoming value increase
- Values transparency above all — needs to know what is fact, what is inference, and where data was thin or unavailable
- Makes final decisions based on factors that may be outside your knowledge: current cash position, portfolio composition, sector preferences, ethical stance
- Has explicitly requested that you do NOT make the decision for them — you recommend, they decide

SIGNAL BEING ASSESSED:
Company: {transaction.get("company_name", "")} ({transaction.get("ticker", "")})
Director: {transaction.get("director_name", "")}
Transaction: {transaction.get("transaction_category", "")}
Consideration: £{consideration:,.0f}
Announced: {published_at}

THREE LAYER ASSESSMENTS:
You have received three independent investigation reports.
Each was produced without knowledge of the others.

Layer 1 — Platform Assessment:
{_fmt(layer1_output)}

Layer 2 — Director Purchase Analysis:
{_fmt(layer2_output)}

Layer 3 — Signal Freshness:
{_fmt(layer3_output)}

For comparison, the simple one-shot assessment:
{_fmt(simple_lens_output)}

SYNTHESIS INSTRUCTIONS:

Step 1 — Review all three layer outputs
Read each layer output fully before writing anything.
Note where layers agree, where they diverge, and where data was thin or unavailable.

Step 2 — Identify the key findings
For each layer, identify the single most important finding:
- Layer 1: what is the platform risk rating and what drove it?
- Layer 2: what is the signal strength and what drove it?
- Layer 3: is the signal likely fresh or priced in?

Step 3 — Assess coherence across layers
Do the three assessments tell a coherent story, or do they point in different directions?

Coherent example: low platform risk + strong director signal + likely fresh = strong case to investigate further
Divergent example: high platform risk + strong director signal + insufficient freshness data = genuine tension — surface this honestly, do not resolve it artificially

Step 4 — Produce the recommendation
Assign ONE of:
- "Investigate further": signal is meaningful, platform is acceptable, signal appears fresh. Warrants active attention.
- "Monitor": some positive indicators but meaningful uncertainty in one or more layers. Watch for corroborating signals.
- "Ignore": weak signal, high platform risk, or signal likely already priced in. Not worth active attention at this time.

The recommendation must follow from the evidence. Do not default to "Monitor" to avoid taking a position. If the evidence points clearly in one direction, say so.

Step 5 — Produce structured output
Respond ONLY with the following JSON structure.
No preamble. No explanation outside the JSON.

{{
  "briefing": {{
    "company": "",
    "ticker": "",
    "director": "",
    "transaction": "",
    "consideration_gbp": 0.0,
    "announced": ""
  }},

  "layer_summaries": {{
    "platform": {{
      "risk_rating": "",
      "key_finding": "",
      "data_quality": ""
    }},
    "director_signal": {{
      "signal_strength": 0,
      "signal_direction": "",
      "key_finding": "",
      "data_quality": ""
    }},
    "freshness": {{
      "assessment": "",
      "key_finding": "",
      "data_quality": ""
    }}
  }},

  "cross_layer_assessment": {{
    "coherence": "",
    "tensions": "",
    "amplifiers": "",
    "detractors": ""
  }},

  "fact_summary": "",

  "inference_summary": "",

  "data_quality_summary": {{
    "overall": "",
    "thin_data_flags": [],
    "unavailable_data": []
  }},

  "vs_simple_lens": {{
    "simple_recommendation": "",
    "simple_signal_strength": 0,
    "agentic_adds": "",
    "agentic_changes": ""
  }},

  "recommendation": "",

  "recommendation_justification": "",

  "limitations": "",

  "token_usage": {{
    "layer1_input": 0,
    "layer1_output": 0,
    "layer2_input": 0,
    "layer2_output": 0,
    "layer3_input": 0,
    "layer3_output": 0,
    "synthesis_input": 0,
    "synthesis_output": 0,
    "total_input": 0,
    "total_output": 0
  }}
}}

FIELD GUIDANCE:

fact_summary: 3-5 sentences of facts only — what the data directly shows across all three layers. No interpretation.

inference_summary: 2-4 sentences of clearly labelled inference. Must begin with "INFERENCE:".

cross_layer_assessment:
  coherence: "coherent" | "mixed" | "divergent"
  tensions: describe any layer findings that point in opposite directions. If none, state "None identified."
  amplifiers: findings that strengthen the case across layers.
  detractors: findings that weaken the case.

vs_simple_lens:
  agentic_adds: what did the agentic investigation reveal that the simple lens could not?
  agentic_changes: did the agentic investigation change the recommendation relative to the simple lens?

recommendation_justification: 3-5 sentences. Direct and specific. Reference the most important findings from each layer. State clearly what drove the recommendation. Do not hedge unnecessarily.

limitations: comprehensive list of what could not be assessed. Aggregate limitations from all three layers. Be specific.

token_usage: populate from actual token counts returned by the Anthropic API for each layer call. If a layer used multiple model calls, aggregate all calls for that layer.\
"""


def parse_synthesis_response(text: str) -> dict:
    """
    Parse JSON from synthesis agent response, stripping markdown fences.

    Returns
    -------
    dict
        Parsed JSON, or {"_error": ..., "_raw_text": ...} on parse failure.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        stripped = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        return {"_error": f"JSON parse failed: {exc}", "_raw_text": text}


def run_synthesis(
    rns_article_id: str,
    transaction: dict,
    investigation_result: dict,
    simple_lens_output: Optional[dict],
    client,
    db=None,
    config=None,
) -> dict:
    """
    Run the synthesis agent and persist the result to Firestore.

    Parameters
    ----------
    rns_article_id : str
        The signals document_id.
    transaction : dict
        Transaction fields used to populate the prompt header.
        Keys: company_name, ticker, director_name, transaction_category,
        total_consideration_gbp, published_at.
    investigation_result : dict
        Output from run_agentic_investigation_sequential/parallel.
        Shape: {"layer1": {"output": dict|None, "tokens": dict}, ...}
    simple_lens_output : dict | None
        Parsed simple lens output for comparison context.
    client : anthropic.Anthropic
        Anthropic API client.
    db : Firestore client, optional
        Injected for testing. Uses singleton when None.
    config : dict, optional
        Overrides ORCHESTRATOR_CONFIG for model selection and pricing.

    Returns
    -------
    dict
        Parsed synthesis JSON with token_usage populated from actual API
        counts. If the synthesis API call fails, the exception propagates
        after a best-effort agentic_status="failed" write to Firestore.
    """
    from utilities.orchestrator.persistence import (
        persist_synthesis_result,
        update_agentic_status,
    )

    cfg = config or ORCHESTRATOR_CONFIG

    layer1_result = investigation_result.get("layer1", {})
    layer2_result = investigation_result.get("layer2", {})
    layer3_result = investigation_result.get("layer3", {})

    layer1_output = layer1_result.get("output")
    layer2_output = layer2_result.get("output")
    layer3_output = layer3_result.get("output")

    layer1_tokens = layer1_result.get("tokens")
    layer2_tokens = layer2_result.get("tokens")
    layer3_tokens = layer3_result.get("tokens")

    prompt = build_synthesis_prompt(
        transaction, layer1_output, layer2_output, layer3_output, simple_lens_output
    )
    model = cfg.get("synthesis_model", "claude-sonnet-4-6")

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        try:
            update_agentic_status(rns_article_id, "failed", db=db)
        except Exception:
            pass
        raise

    synthesis_tokens = {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
        "model": model,
    }

    text = response.content[0].text if response.content else ""
    result = parse_synthesis_response(text)

    token_summary = aggregate_token_usage(
        layer1_usage=layer1_tokens,
        layer2_usage=layer2_tokens,
        layer3_usage=layer3_tokens,
        synthesis_usage=synthesis_tokens,
        config=cfg,
    )

    # Inject actual token counts, overwriting the LLM's placeholder zeros
    if "_error" not in result:
        result["token_usage"] = {
            "layer1_input": token_summary["layer1_input"],
            "layer1_output": token_summary["layer1_output"],
            "layer2_input": token_summary["layer2_input"],
            "layer2_output": token_summary["layer2_output"],
            "layer3_input": token_summary["layer3_input"],
            "layer3_output": token_summary["layer3_output"],
            "synthesis_input": token_summary["synthesis_input"],
            "synthesis_output": token_summary["synthesis_output"],
            "total_input": token_summary["total_input"],
            "total_output": token_summary["total_output"],
        }

    persist_synthesis_result(rns_article_id, result, token_summary, db=db)
    return result
