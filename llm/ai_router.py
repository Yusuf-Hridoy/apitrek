"""
Multi-provider AI router with automatic failover.

Tries the primary provider first (Mistral by default) and falls back to
secondary providers (Groq, GitHub Models) on any failure. Providers without
a configured API key are skipped, so the tool works exactly as before when
only MISTRAL_API_KEY is set.
"""
import sys
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from llm.mistral_client import MistralClient, MistralClientError
from llm.groq_client import GroqClient, GroqClientError
from llm.github_models_client import GitHubModelsClient, GitHubModelsError

# Load .env from project root if present
_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    load_dotenv(_env_path)


PROVIDER_KEY_ENV = {
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "github": "GITHUB_MODELS_API_KEY",
}

PROVIDER_CLIENTS = {
    "mistral": MistralClient,
    "groq": GroqClient,
    "github": GitHubModelsClient,
}

class AllProvidersFailedError(Exception):
    """Raised when every configured AI provider fails."""

    def __init__(self, failures: Dict[str, str]):
        self.failures = failures
        details = "; ".join(f"{name}: {err}" for name, err in failures.items())
        super().__init__(f"All AI providers failed. {details}")


class AIRouter:
    """Routes LLM prompts across providers with automatic failover."""

    def __init__(
        self,
        primary: str = "mistral",
        fallback_order: Optional[List[str]] = None,
    ):
        if primary not in PROVIDER_CLIENTS:
            raise ValueError(f"Unknown primary provider: {primary}")
        self.primary = primary
        self.fallback_order = list(fallback_order) if fallback_order else ["groq", "github"]
        self.last_provider: Optional[str] = None
        self.last_failures: Dict[str, str] = {}

    def load_env_keys(self) -> Dict[str, Optional[str]]:
        """Read provider API keys from the environment / .env file."""
        import os

        return {name: os.getenv(env_var) for name, env_var in PROVIDER_KEY_ENV.items()}

    def get_available_providers(self) -> List[str]:
        """Return the names of providers that have an API key configured."""
        keys = self.load_env_keys()
        return [name for name in self._provider_sequence() if keys.get(name)]

    def _provider_sequence(self) -> List[str]:
        """Primary first, then fallbacks, without duplicates."""
        sequence = [self.primary]
        for name in self.fallback_order:
            if name not in sequence:
                sequence.append(name)
        return sequence

    def generate_with_fallback(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 8192,
    ) -> str:
        """
        Send a prompt to the primary provider, falling back on any failure.

        Returns the response from the first successful provider and records
        it in ``self.last_provider``. Raises AllProvidersFailedError with
        per-provider failure details if every provider fails.
        """
        keys = self.load_env_keys()
        failures: Dict[str, str] = {}
        self.last_provider = None

        for provider in self._provider_sequence():
            client_class = PROVIDER_CLIENTS.get(provider)
            if client_class is None:
                failures[provider] = "Unknown provider."
                continue

            if not keys.get(provider):
                failures[provider] = "No API key configured."
                continue

            try:
                client = client_class(api_key=keys[provider])
                response = client.send_prompt(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self.last_provider = provider
                self.last_failures = failures
                return response
            except Exception as e:
                failures[provider] = str(e)
                continue

        self.last_failures = failures
        raise AllProvidersFailedError(failures)

    # Alias so AIRouter is duck-type compatible with the individual clients
    # (anything accepting a client with .send_prompt also accepts a router).
    def send_prompt(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 8192,
    ) -> str:
        return self.generate_with_fallback(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
