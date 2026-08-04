"""
Mistral AI client using direct HTTP requests with retry logic and error handling.
"""
import os
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# Load .env from project root if present
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)


DEFAULT_MODEL = "mistral-large-latest"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"


class MistralClientError(Exception):
    """Custom exception for Mistral client failures."""
    pass


class MistralTruncationError(MistralClientError):
    """Raised when the AI response is cut off due to token limits."""
    pass


class MistralClient:
    """Client for interacting with the Mistral AI API via HTTP requests."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY")
        if not self.api_key:
            raise MistralClientError(
                "Mistral API key is required. Set MISTRAL_API_KEY environment variable."
            )

        self.model = model or os.getenv("MISTRAL_MODEL", DEFAULT_MODEL)
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    def send_prompt(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 8192,
    ) -> str:
        """
        Send a prompt to Mistral AI with retry logic.

        Args:
            system_prompt: The system-level instructions.
            user_prompt: The user query / prompt.
            temperature: Sampling temperature (lower = more deterministic).
            max_tokens: Maximum tokens in the response.

        Returns:
            Raw text response from the AI.

        Raises:
            MistralClientError: If all retries are exhausted.
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._session.post(
                    MISTRAL_API_URL,
                    json=payload,
                    timeout=60,
                )
                response.raise_for_status()
                data = response.json()

                choices = data.get("choices", [])
                if not choices:
                    raise MistralClientError("Empty response choices from Mistral API.")

                choice = choices[0]
                finish_reason = choice.get("finish_reason")
                content = choice.get("message", {}).get("content")

                if finish_reason == "length":
                    raise MistralTruncationError(
                        "AI response was truncated due to token limits. "
                        "Try reducing prompt complexity or increasing max_tokens."
                    )

                if content is None:
                    raise MistralClientError("Mistral returned None content.")

                return content.strip()

            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS * attempt)
                continue

            except (KeyError, ValueError) as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS * attempt)
                continue

        raise MistralClientError(
            f"Failed to get response from Mistral after {MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        )
