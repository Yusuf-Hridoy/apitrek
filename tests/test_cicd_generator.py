"""
Unit tests for the exports.cicd_generator module.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from exports.cicd_generator import (
    generate_github_actions_yaml,
    generate_gitlab_ci_yaml,
    generate_azure_pipelines_yaml,
)

yaml = pytest.importorskip("yaml")

ENDPOINT = "https://fakestoreapi.com/products/1"
METHOD = "GET"
TEST_DATA = {
    "positive_test_cases": [
        {
            "id": "POS-001",
            "title": "Valid product fetch",
            "description": "Fetch a product with a valid ID",
            "request": {"method": "GET"},
            "expected": {"status_code": 200},
        }
    ],
    "negative_test_cases": [],
    "edge_cases": [],
    "assertions": [],
}


def test_github_actions_yaml_is_valid():
    content = generate_github_actions_yaml(ENDPOINT, METHOD, TEST_DATA)
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict)
    assert "jobs" in parsed
    assert "api-tests" in parsed["jobs"]


def test_github_actions_yaml_contents():
    content = generate_github_actions_yaml(ENDPOINT, METHOD, TEST_DATA)
    assert "actions/checkout@v4" in content
    assert "actions/setup-python@v5" in content
    assert 'python-version: "3.11"' in content
    assert "pip install pytest requests" in content
    assert "pytest test_api.py -v" in content
    assert "actions/upload-artifact@v4" in content
    assert "secrets.API_ENDPOINT" in content
    assert "secrets.AUTH_TOKEN" in content


def test_github_actions_yaml_embeds_test_file():
    content = generate_github_actions_yaml(ENDPOINT, METHOD, TEST_DATA)
    # The generated pytest script is written into the workflow via heredoc
    assert "cat > test_api.py << 'EOF'" in content
    assert "def test_" in content


def test_gitlab_ci_yaml_is_valid():
    content = generate_gitlab_ci_yaml(ENDPOINT, METHOD, TEST_DATA)
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict)
    assert parsed["stages"] == ["test", "report"]
    assert "api-tests" in parsed
    assert "test-report" in parsed


def test_gitlab_ci_yaml_contents():
    content = generate_gitlab_ci_yaml(ENDPOINT, METHOD, TEST_DATA)
    assert "pip install pytest requests" in content
    assert "pytest test_api.py -v" in content
    assert "junit: test-results.xml" in content
    assert "coverage:" in content
    assert "expire_in: 1 week" in content


def test_azure_pipelines_yaml_is_valid():
    content = generate_azure_pipelines_yaml(ENDPOINT, METHOD, TEST_DATA)
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict)
    assert "steps" in parsed
    assert isinstance(parsed["steps"], list)


def test_azure_pipelines_yaml_contents():
    content = generate_azure_pipelines_yaml(ENDPOINT, METHOD, TEST_DATA)
    assert "UsePythonVersion@0" in content
    assert "pip install pytest requests" in content
    assert "pytest test_api.py -v" in content
    assert "PublishTestResults@2" in content
    assert "PublishBuildArtifacts@1" in content
    assert "API_ENDPOINT: $(API_ENDPOINT)" in content
    assert "AUTH_TOKEN: $(AUTH_TOKEN)" in content


def test_all_generators_return_str():
    for generator in (
        generate_github_actions_yaml,
        generate_gitlab_ci_yaml,
        generate_azure_pipelines_yaml,
    ):
        result = generator(ENDPOINT, METHOD, TEST_DATA)
        assert isinstance(result, str)
        assert len(result) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
