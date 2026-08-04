"""
Unit tests for the llm.ai_router module.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from llm.ai_router import AIRouter, AllProvidersFailedError


ALL_KEYS = {
    "MISTRAL_API_KEY": "mistral-key",
    "GROQ_API_KEY": "groq-key",
    "GITHUB_MODELS_API_KEY": "github-key",
}


def _make_client_class(result=None, error=None):
    """Build a mock provider client class with the standard interface."""
    instance = MagicMock()
    if error is not None:
        instance.send_prompt.side_effect = error
    else:
        instance.send_prompt.return_value = result
    return MagicMock(return_value=instance)


def _patched_providers(mistral=None, groq=None, github=None):
    return {
        "mistral": mistral or _make_client_class(result="mistral response"),
        "groq": groq or _make_client_class(result="groq response"),
        "github": github or _make_client_class(result="github response"),
    }


def test_unknown_primary_provider():
    with pytest.raises(ValueError, match="Unknown primary provider"):
        AIRouter(primary="bogus")


def test_load_env_keys():
    with patch.dict("os.environ", ALL_KEYS, clear=True):
        router = AIRouter()
        keys = router.load_env_keys()
        assert keys == {
            "mistral": "mistral-key",
            "groq": "groq-key",
            "github": "github-key",
        }


def test_get_available_providers_all():
    with patch.dict("os.environ", ALL_KEYS, clear=True):
        router = AIRouter()
        assert router.get_available_providers() == ["mistral", "groq", "github"]


def test_get_available_providers_mistral_only():
    with patch.dict("os.environ", {"MISTRAL_API_KEY": "mistral-key"}, clear=True):
        router = AIRouter()
        assert router.get_available_providers() == ["mistral"]


def test_primary_success_no_fallback():
    providers = _patched_providers()
    with patch.dict("os.environ", ALL_KEYS, clear=True):
        with patch("llm.ai_router.PROVIDER_CLIENTS", providers):
            router = AIRouter()
            result = router.generate_with_fallback("system", "user")

    assert result == "mistral response"
    assert router.last_provider == "mistral"
    providers["groq"].assert_not_called()
    providers["github"].assert_not_called()


def test_fallback_to_groq_on_primary_failure():
    providers = _patched_providers(
        mistral=_make_client_class(error=Exception("Mistral is down")),
    )
    with patch.dict("os.environ", ALL_KEYS, clear=True):
        with patch("llm.ai_router.PROVIDER_CLIENTS", providers):
            router = AIRouter()
            result = router.generate_with_fallback("system", "user")

    assert result == "groq response"
    assert router.last_provider == "groq"
    assert "mistral" in router.last_failures


def test_fallback_chain_to_github():
    providers = _patched_providers(
        mistral=_make_client_class(error=Exception("Mistral is down")),
        groq=_make_client_class(error=Exception("Groq rate limited")),
    )
    with patch.dict("os.environ", ALL_KEYS, clear=True):
        with patch("llm.ai_router.PROVIDER_CLIENTS", providers):
            router = AIRouter()
            result = router.generate_with_fallback("system", "user")

    assert result == "github response"
    assert router.last_provider == "github"


def test_all_providers_fail_raises_with_details():
    providers = _patched_providers(
        mistral=_make_client_class(error=Exception("Mistral timeout")),
        groq=_make_client_class(error=Exception("Groq 500")),
        github=_make_client_class(error=Exception("GitHub 401")),
    )
    with patch.dict("os.environ", ALL_KEYS, clear=True):
        with patch("llm.ai_router.PROVIDER_CLIENTS", providers):
            router = AIRouter()
            with pytest.raises(AllProvidersFailedError) as exc_info:
                router.generate_with_fallback("system", "user")

    err = exc_info.value
    assert "Mistral timeout" in err.failures["mistral"]
    assert "Groq 500" in err.failures["groq"]
    assert "GitHub 401" in err.failures["github"]
    assert "mistral" in str(err) and "groq" in str(err)


def test_providers_without_keys_are_skipped():
    groq_cls = _make_client_class(result="groq response")
    providers = _patched_providers(groq=groq_cls)
    # Only Groq key configured: mistral and github skipped, Groq used
    with patch.dict("os.environ", {"GROQ_API_KEY": "groq-key"}, clear=True):
        with patch("llm.ai_router.PROVIDER_CLIENTS", providers):
            router = AIRouter()
            result = router.generate_with_fallback("system", "user")

    assert result == "groq response"
    assert router.last_provider == "groq"
    providers["mistral"].assert_not_called()
    providers["github"].assert_not_called()


def test_no_keys_at_all_raises():
    with patch.dict("os.environ", {}, clear=True):
        router = AIRouter()
        with pytest.raises(AllProvidersFailedError) as exc_info:
            router.generate_with_fallback("system", "user")

    assert "No API key configured" in str(exc_info.value)


def test_custom_fallback_order():
    providers = _patched_providers(
        mistral=_make_client_class(error=Exception("down")),
    )
    with patch.dict("os.environ", ALL_KEYS, clear=True):
        with patch("llm.ai_router.PROVIDER_CLIENTS", providers):
            router = AIRouter(primary="mistral", fallback_order=["github", "groq"])
            result = router.generate_with_fallback("system", "user")

    assert result == "github response"
    assert router.last_provider == "github"


def test_send_prompt_alias_matches_client_interface():
    providers = _patched_providers()
    with patch.dict("os.environ", ALL_KEYS, clear=True):
        with patch("llm.ai_router.PROVIDER_CLIENTS", providers):
            router = AIRouter()
            result = router.send_prompt(
                system_prompt="system",
                user_prompt="user",
                temperature=0.5,
                max_tokens=100,
            )

    assert result == "mistral response"
    instance = providers["mistral"].return_value
    instance.send_prompt.assert_called_once_with(
        system_prompt="system",
        user_prompt="user",
        temperature=0.5,
        max_tokens=100,
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
