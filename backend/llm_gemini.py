from google import genai
from abstractions import LLMProviderBase
from dotenv import load_dotenv
import os

load_dotenv()

class GeminiProvider(LLMProviderBase):
    """LLM Provider implementation using Google Gemini API.
    Implements LLMProviderBase so this can be swapped for Claude
    or any other provider without changing the rest of the system."""

    MODEL = "gemini-2.0-flash"

    def __init__(self):
        api_key = os.getenv("GEMINI_KEY")
        if not api_key:
            raise ValueError("GEMINI_KEY not found in environment.")
        self.client = genai.Client(api_key=api_key)

    def analyse(self, prompt: str) -> str:
        try:
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=prompt
            )
            return response.text
        except Exception as e:
            print(f"Gemini API error: {e}")
            return ""


if __name__ == "__main__":
    provider = GeminiProvider()
    test = provider.analyse("In one sentence, confirm you are working correctly.")
    print(f"Gemini response: {test}")
