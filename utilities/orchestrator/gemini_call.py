"""
Shared Gemini API call helper with exponential backoff on 429/RESOURCE_EXHAUSTED.

All Gemini Flash call sites in this orchestrator import from here so that
rate-limit retry behaviour is defined once.

Retry schedule (default): 2s → 4s → 8s → 16s (4 attempts total).
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES = 4
_DEFAULT_INITIAL_DELAY_S: float = 2.0  # doubles on each retry: 2, 4, 8, 16


def call_gemini(
    prompt: str,
    model: str,
    config=None,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    initial_delay: float = _DEFAULT_INITIAL_DELAY_S,
) -> tuple[str, dict]:
    """
    Call Gemini and return (text, token_usage).

    Parameters
    ----------
    prompt : str
        Text prompt to send.
    model : str
        Gemini model ID (e.g. "gemini-2.0-flash").
    config : types.GenerateContentConfig | None
        Optional generation config (temperature, max_output_tokens, tools, …).
        Pass None for default behaviour.
    max_retries : int
        Maximum number of attempts including the first.
    initial_delay : float
        Seconds to wait before the first retry; doubles on each subsequent retry.

    Returns
    -------
    (text, token_usage) : tuple[str, dict]
        text — raw response text.
        token_usage — {"input": int, "output": int, "model": str}.

    Raises
    ------
    ValueError
        If GEMINI_KEY is not set.
    Exception
        Re-raises the last exception when all retries are exhausted, or
        immediately for non-rate-limit errors.
    """
    from google import genai

    api_key = os.environ.get("GEMINI_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_KEY not set in environment")

    client = genai.Client(api_key=api_key)

    delay = initial_delay
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            kwargs: dict = {"model": model, "contents": prompt}
            if config is not None:
                kwargs["config"] = config
            response = client.models.generate_content(**kwargs)
            text = response.text or ""
            token_usage = _extract_token_usage(response, model)
            return text, token_usage

        except Exception as exc:
            last_exc = exc
            err_str = str(exc)
            is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str

            if is_rate_limit and attempt < max_retries - 1:
                logger.warning(
                    "Gemini rate limited (attempt %d/%d), retrying in %.0fs: %s",
                    attempt + 1,
                    max_retries,
                    delay,
                    exc,
                )
                time.sleep(delay)
                delay *= 2
                continue

            raise

    raise last_exc  # type: ignore[misc]


def _extract_token_usage(response, model: str) -> dict:
    """Extract token counts from a Gemini response object."""
    try:
        usage = response.usage_metadata
        return {
            "input": getattr(usage, "prompt_token_count", 0) or 0,
            "output": getattr(usage, "candidates_token_count", 0) or 0,
            "model": model,
        }
    except Exception:
        return {"input": 0, "output": 0, "model": model}
