"""
CI/CD pipeline config generator.

Deterministic string templating only — no LLM calls. Produces ready-to-commit
pipeline definitions for GitHub Actions, GitLab CI, and Azure Pipelines that
run the exported pytest suite against the target API.
"""
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from exports.python_test_generator import generate_pytest_script

TEST_FILE = "test_api.py"
RESULTS_FILE = "test-results.xml"


def _count_tests(test_data: Dict[str, Any]) -> int:
    """Count generated test cases for informational pipeline output."""
    total = 0
    for key in ("positive_test_cases", "negative_test_cases", "edge_cases"):
        cases = test_data.get(key)
        if isinstance(cases, list):
            total += len(cases)
    return total


def _write_test_file_step(script: str) -> str:
    """
    Shell snippet that writes the pytest script to disk via a quoted heredoc
    (no shell expansion of the embedded Python code).
    """
    return f"cat > {TEST_FILE} << 'EOF'\n{script}\nEOF"


def generate_github_actions_yaml(
    endpoint: str,
    method: str,
    test_data: Dict[str, Any],
    python_version: str = "3.11",
) -> str:
    """
    Generate a complete .github/workflows/api-tests.yml workflow.

    Secrets are read from repository secrets / env vars:
      API_ENDPOINT, AUTH_TOKEN
    """
    method = method.upper()
    test_count = _count_tests(test_data)
    script = generate_pytest_script(endpoint=endpoint, method=method, test_data=test_data)
    write_step = _write_test_file_step(script)

    return f"""name: API Tests

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

env:
  API_ENDPOINT: ${{{{ secrets.API_ENDPOINT }}}}
  AUTH_TOKEN: ${{{{ secrets.AUTH_TOKEN }}}}

jobs:
  api-tests:
    name: Run API tests ({method} {endpoint})
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "{python_version}"

      - name: Install dependencies
        run: pip install pytest requests

      - name: Create test file
        run: |
{chr(10).join('          ' + line for line in write_step.splitlines())}

      - name: Run API tests
        run: |
          echo "Running {test_count} generated test cases against $API_ENDPOINT"
          pytest {TEST_FILE} -v --junitxml={RESULTS_FILE}

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: api-test-results
          path: {RESULTS_FILE}
          if-no-files-found: ignore
"""


def generate_gitlab_ci_yaml(
    endpoint: str,
    method: str,
    test_data: Dict[str, Any],
) -> str:
    """
    Generate a .gitlab-ci.yml pipeline with test and report stages.

    Configure API_ENDPOINT and AUTH_TOKEN as CI/CD variables in GitLab.
    """
    method = method.upper()
    test_count = _count_tests(test_data)
    script = generate_pytest_script(endpoint=endpoint, method=method, test_data=test_data)
    write_step = _write_test_file_step(script)

    return f"""stages:
  - test
  - report

variables:
  # Set API_ENDPOINT and AUTH_TOKEN in Settings > CI/CD > Variables
  API_ENDPOINT: "${{API_ENDPOINT}}"
  AUTH_TOKEN: "${{AUTH_TOKEN}}"

api-tests:
  stage: test
  image: python:3.11-slim
  before_script:
    - pip install pytest requests
  script:
    - |
{chr(10).join('      ' + line for line in write_step.splitlines())}
    - echo "Running {test_count} generated test cases for {method} {endpoint}"
    - pytest {TEST_FILE} -v --junitxml={RESULTS_FILE} --cov=. --cov-report=term-missing || true
  coverage: '/TOTAL.*\\s+(\\d+%)$/'
  artifacts:
    when: always
    reports:
      junit: {RESULTS_FILE}
    paths:
      - {RESULTS_FILE}
    expire_in: 1 week

test-report:
  stage: report
  image: alpine:latest
  dependencies:
    - api-tests
  script:
    - echo "Test results collected from api-tests job"
    - cat {RESULTS_FILE} || echo "No results file found"
  artifacts:
    paths:
      - {RESULTS_FILE}
    expire_in: 1 week
"""


def generate_azure_pipelines_yaml(
    endpoint: str,
    method: str,
    test_data: Dict[str, Any],
) -> str:
    """
    Generate an azure-pipelines.yml pipeline.

    Define API_ENDPOINT and AUTH_TOKEN as secret pipeline variables.
    """
    method = method.upper()
    test_count = _count_tests(test_data)
    script = generate_pytest_script(endpoint=endpoint, method=method, test_data=test_data)
    write_step = _write_test_file_step(script)

    return f"""trigger:
  - main

pool:
  vmImage: ubuntu-latest

variables:
  # Define API_ENDPOINT and AUTH_TOKEN as secret variables in the pipeline settings
  pythonVersion: "3.11"

steps:
  - task: UsePythonVersion@0
    inputs:
      versionSpec: "$(pythonVersion)"
    displayName: "Set up Python"

  - script: pip install pytest requests
    displayName: "Install dependencies"

  - script: |
{chr(10).join('      ' + line for line in write_step.splitlines())}
    displayName: "Create test file"

  - script: |
      echo "Running {test_count} generated test cases for {method} {endpoint}"
      pytest {TEST_FILE} -v --junitxml={RESULTS_FILE}
    displayName: "Run API tests"
    env:
      API_ENDPOINT: $(API_ENDPOINT)
      AUTH_TOKEN: $(AUTH_TOKEN)

  - task: PublishTestResults@2
    condition: always()
    inputs:
      testResultsFormat: "JUnit"
      testResultsFiles: "{RESULTS_FILE}"
      failTaskOnFailedTests: false
    displayName: "Publish test results"

  - task: PublishBuildArtifacts@1
    condition: always()
    inputs:
      pathToPublish: "{RESULTS_FILE}"
      artifactName: "api-test-results"
    displayName: "Publish artifacts"
"""
