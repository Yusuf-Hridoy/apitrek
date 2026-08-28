"""Tests for the deterministic fallback floor inside generate_test_cases."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from llm.ai_router import AllProvidersFailedError
from core.generator import generate_test_cases


ENDPOINT = "https://api.example.com/users/1"


def test_falls_back_when_all_providers_fail():
    router = MagicMock()
    router.generate_with_fallback.side_effect = AllProvidersFailedError(
        {"mistral": "timeout", "groq": "decommissioned"}
    )

    result = generate_test_cases(
        endpoint=ENDPOINT,
        method="GET",
        mistral_client=router,
    )

    assert result["_degraded"] is True
    assert "baseline cases" in result["_degraded_reason"].lower()
    assert "_error" not in result
    assert len(result["positive_test_cases"]) > 0
    assert len(result["negative_test_cases"]) > 0
    assert len(result["edge_cases"]) > 0
    assert len(result["assertions"]) > 0


def test_falls_back_when_router_cannot_be_constructed():
    with patch("core.generator.AIRouter", side_effect=RuntimeError("no providers")):
        result = generate_test_cases(endpoint=ENDPOINT, method="GET")

    assert result["_degraded"] is True
    assert "_error" not in result
    assert len(result["positive_test_cases"]) > 0


def test_successful_llm_path_is_not_degraded():
    router = MagicMock()
    router.send_prompt.return_value = """
    {
        "positive_test_cases": [{"id": "TC-POS-01", "title": "Valid request"}],
        "negative_test_cases": [],
        "edge_cases": [],
        "assertions": []
    }
    """
    router.last_provider = "mistral"

    result = generate_test_cases(
        endpoint=ENDPOINT,
        method="GET",
        mistral_client=router,
    )

    assert result.get("_degraded") is not True
    assert result["_provider"] == "mistral"


def test_groq_default_model_is_not_decommissioned():
    from llm.groq_client import GroqClient, DEFAULT_MODEL

    assert DEFAULT_MODEL != "llama-3.3-70b-versatile"
    assert "gpt-oss" in DEFAULT_MODEL or DEFAULT_MODEL != ""


def test_groq_model_env_override():
    from llm.groq_client import GroqClient

    with patch.dict("os.environ", {"GROQ_API_KEY": "test-key", "GROQ_MODEL": "custom-model"}, clear=True):
        client = GroqClient()
        assert client.model == "custom-model"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
