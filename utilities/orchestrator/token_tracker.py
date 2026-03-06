"""
Token usage tracking and cost calculation for the agentic investigation pipeline.

Each layer agent call returns {"input": int, "output": int, "model": str}.
aggregate_token_usage() combines all layer records into the agentic_token_usage
schema used in the signals Firestore document and the synthesis prompt.
"""

from typing import Optional
from utilities.orchestrator.config import ORCHESTRATOR_CONFIG

# Models classified as Haiku for cost calculation
_HAIKU_MODELS = {
    "claude-haiku-4-5-20251001",
    "claude-haiku-4-5",
}

# Models classified as Sonnet for cost calculation
_SONNET_MODELS = {
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-sonnet-4-20250514",
}


def _safe_tokens(usage: Optional[dict], key: str) -> int:
    """Return token count from a usage dict, defaulting to 0 if absent."""
    if not usage:
        return 0
    return usage.get(key, 0) or 0


def _layer_cost_gbp(usage: Optional[dict], config: dict) -> float:
    """Calculate GBP cost for a single layer usage record."""
    if not usage:
        return 0.0
    model = usage.get("model", "")
    inp = _safe_tokens(usage, "input")
    out = _safe_tokens(usage, "output")

    if any(m in model for m in _HAIKU_MODELS) or "haiku" in model.lower():
        inp_rate = config.get("haiku_input_cost_per_1m_tokens", 0.63)
        out_rate = config.get("haiku_output_cost_per_1m_tokens", 1.26)
    else:
        # Default to Sonnet pricing for unknown models (conservative)
        inp_rate = config.get("sonnet_input_cost_per_1m_tokens", 2.36)
        out_rate = config.get("sonnet_output_cost_per_1m_tokens", 11.81)

    return (inp * inp_rate + out * out_rate) / 1_000_000


def aggregate_token_usage(
    layer1_usage: Optional[dict] = None,
    layer2_usage: Optional[dict] = None,
    layer3_usage: Optional[dict] = None,
    synthesis_usage: Optional[dict] = None,
    config: Optional[dict] = None,
) -> dict:
    """
    Aggregate per-layer token usage into the agentic_token_usage schema.

    Parameters
    ----------
    layer1_usage, layer2_usage, layer3_usage, synthesis_usage : dict | None
        Per-layer usage dicts with keys: "input", "output", "model".
        Pass None for any layer that failed or has not yet run.
    config : dict, optional
        Orchestrator config for pricing constants. Uses defaults if None.

    Returns
    -------
    dict
        Keys: layer1_input, layer1_output, layer2_input, layer2_output,
        layer3_input, layer3_output, synthesis_input, synthesis_output,
        total_input, total_output, estimated_cost_gbp.
    """
    cfg = config or ORCHESTRATOR_CONFIG

    l1i = _safe_tokens(layer1_usage, "input")
    l1o = _safe_tokens(layer1_usage, "output")
    l2i = _safe_tokens(layer2_usage, "input")
    l2o = _safe_tokens(layer2_usage, "output")
    l3i = _safe_tokens(layer3_usage, "input")
    l3o = _safe_tokens(layer3_usage, "output")
    si = _safe_tokens(synthesis_usage, "input")
    so = _safe_tokens(synthesis_usage, "output")

    total_input = l1i + l2i + l3i + si
    total_output = l1o + l2o + l3o + so

    estimated_cost = sum(
        _layer_cost_gbp(u, cfg)
        for u in [layer1_usage, layer2_usage, layer3_usage, synthesis_usage]
    )

    return {
        "layer1_input": l1i,
        "layer1_output": l1o,
        "layer2_input": l2i,
        "layer2_output": l2o,
        "layer3_input": l3i,
        "layer3_output": l3o,
        "synthesis_input": si,
        "synthesis_output": so,
        "total_input": total_input,
        "total_output": total_output,
        "estimated_cost_gbp": round(estimated_cost, 4),
    }
