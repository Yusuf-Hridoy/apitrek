"""
GitHub Models AI client using direct HTTP requests with retry logic and error handling.

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


DEFAULT_MODEL = "gpt-4o"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2
GITHUB_MODELS_API_URL = "https://models.inference.ai.azure.com/chat/completions"


class GitHubModelsError(Exception):
    """Custom exception for GitHub Models client failures."""
    pass


class GitHubModelsTruncationError(GitHubModelsError):
    """Raised when the AI response is cut off due to token limits."""
    pass


class GitHubModelsClient:
    """Client for interacting with the GitHub Models inference API via HTTP requests."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GITHUB_MODELS_API_KEY")
        if not self.api_key:
            raise GitHubModelsError(
                "GitHub Models API key is required. "
                "Set GITHUB_MODELS_API_KEY environment variable."
            )

        self.model = model or os.getenv("GITHUB_MODEL", DEFAULT_MODEL)
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
        Send a prompt to GitHub Models with retry logic and exponential backoff.

        Args:
            system_prompt: The system-level instructions.
            user_prompt: The user query / prompt.
            temperature: Sampling temperature (lower = more deterministic).
            max_tokens: Maximum tokens in the response.

        Returns:
            Raw text response from the AI.

        Raises:
            GitHubModelsError: If all retries are exhausted.
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
                    GITHUB_MODELS_API_URL,
                    json=payload,
                    timeout=60,
                )
                response.raise_for_status()
                data = response.json()

                choices = data.get("choices", [])
                if not choices:
                    raise GitHubModelsError("Empty response choices from GitHub Models API.")

                choice = choices[0]
                finish_reason = choice.get("finish_reason")
                content = choice.get("message", {}).get("content")

                if finish_reason == "length":
                    raise GitHubModelsTruncationError(
                        "AI response was truncated due to token limits. "
                        "Try reducing prompt complexity or increasing max_tokens."
                    )

                if content is None:
                    raise GitHubModelsError("GitHub Models returned None content.")

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

        raise GitHubModelsError(
            f"Failed to get response from GitHub Models after {MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        )
