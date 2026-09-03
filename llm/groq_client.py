"""
Groq AI client using direct HTTP requests with retry logic and error handling.

Duck-typed to match llm.mistral_client.MistralClient so the two can be used
interchangeably by llm.ai_router.AIRouter.
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


# llama-3.3-70b-versatile was decommissioned by Groq on 2026-06-17.
# Use Groq's current recommended OpenAI OSS model; override via GROQ_MODEL env var.
DEFAULT_MODEL = "openai/gpt-oss-120b"
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 2
# Same transient set as llm.mistral_client — keep the two clients' retry
# behavior interchangeable for the router.
TRANSIENT_STATUSES = {429, 502, 503, 504}
_MAX_BACKOFF_SECONDS = 6
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))


def _retry_delay(attempt: int) -> float:
    """Exponential backoff: ~2s, 4s, 8s… capped at _MAX_BACKOFF_SECONDS."""
    return min(RETRY_DELAY_SECONDS * (2 ** attempt), _MAX_BACKOFF_SECONDS)


class GroqClientError(Exception):
    """Custom exception for Groq client failures."""
    pass


class GroqTruncationError(GroqClientError):
    """Raised when the AI response is cut off due to token limits."""
    pass


class GroqClient:
    """Client for interacting with the Groq API (OpenAI-compatible) via HTTP requests."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise GroqClientError(
                "Groq API key is required. Set GROQ_API_KEY environment variable."
            )

        self.model = model or os.getenv("GROQ_MODEL", DEFAULT_MODEL)
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
        Send a prompt to Groq with retry logic and exponential backoff.

        Args:
            system_prompt: The system-level instructions.
            user_prompt: The user query / prompt.
            temperature: Sampling temperature (lower = more deterministic).
            max_tokens: Maximum tokens in the response.

        Returns:
            Raw text response from the AI.

        Raises:
            GroqClientError: If all retries are exhausted.
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
                    GROQ_API_URL,
                    json=payload,
                    timeout=LLM_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()

                choices = data.get("choices", [])
                if not choices:
                    raise GroqClientError("Empty response choices from Groq API.")

                choice = choices[0]
                finish_reason = choice.get("finish_reason")
                content = choice.get("message", {}).get("content")

                if finish_reason == "length":
                    raise GroqTruncationError(
                        "AI response was truncated due to token limits. "
                        "Try reducing prompt complexity or increasing max_tokens."
                    )

                if content is None:
                    raise GroqClientError("Groq returned None content.")

                return content.strip()

            except requests.exceptions.HTTPError as e:
                last_error = e
                status = None
                if e.response is not None:
                    status = e.response.status_code
                # Fatal auth errors fail fast; non-transient statuses won't fix
                # themselves either. Only the transient set backs off and retries.
                if status in (401, 403):
                    raise GroqClientError(
                        f"Groq request failed with status {status}. "
                        "Check your API key and model tier."
                    )
                if status not in TRANSIENT_STATUSES:
                    raise GroqClientError(
                        f"Groq request failed with status {status}."
                    ) from e
                if attempt < MAX_RETRIES:
                    time.sleep(_retry_delay(attempt))
                continue

            except requests.exceptions.RequestException as e:
                # Timeouts / connection errors are transient — retry.
                last_error = e
                if attempt < MAX_RETRIES:
                    time.sleep(_retry_delay(attempt))
                continue

            except (KeyError, ValueError) as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    time.sleep(_retry_delay(attempt))
                continue

        raise GroqClientError(
            f"Failed to get response from Groq after {MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        )
