import logging
import os
import time

from google import genai
from abstractions import LLMProviderBase
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

logger = logging.getLogger(__name__)

_MAX_RETRIES = 4
_INITIAL_DELAY_S = 2.0  # doubles on each retry: 2, 4, 8, 16


class GeminiProvider(LLMProviderBase):
    """LLM Provider implementation using Google Gemini API.
    Implements LLMProviderBase so this can be swapped for any other
    provider without changing the rest of the system."""

    MODEL = "gemini-2.5-flash"

    def __init__(self):
        api_key = os.getenv("GEMINI_KEY")
        if not api_key:
            raise ValueError("GEMINI_KEY not found in environment.")
        self.client = genai.Client(api_key=api_key)

    def analyse(self, prompt: str) -> str:
        delay = _INITIAL_DELAY_S
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=self.MODEL,
                    contents=prompt,
                )
                return response.text or ""
            except Exception as e:
                last_exc = e
                err_str = str(e)
                is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                if is_rate_limit and attempt < _MAX_RETRIES - 1:
                    logger.warning(
                        "Gemini rate limited (attempt %d/%d), retrying in %.0fs: %s",
                        attempt + 1, _MAX_RETRIES, delay, e,
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue
                logger.error("Gemini API error: %s", e)
                return ""
        logger.error("Gemini API error after %d retries: %s", _MAX_RETRIES, last_exc)
        return ""


if __name__ == "__main__":
    provider = GeminiProvider()
    test = provider.analyse("In one sentence, confirm you are working correctly.")
    print(f"Gemini response: {test}")
