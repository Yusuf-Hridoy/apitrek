# API Sentinel — Smart API Testing Assistant

AI-powered API testing suite. Point it at an endpoint and it generates positive,
negative, and edge-case tests plus assertions — which you can **execute live**,
**export** (pytest / Postman / CI-CD), **scan for OWASP API security issues**, and
**validate against an OpenAPI contract**. Every run is saved to a local history.

## Features

- **AI test generation** — positive / negative / edge cases + assertions from an
  endpoint (and an optional or auto-fetched sample response).
- **Multi-provider AI with automatic failover** — Mistral (primary), then Groq and
  GitHub Models if a provider is unavailable. Only the providers you configure keys
  for are used.
- **Live fetching** — tick *Auto-fetch* to call the API and generate tests from the
  real response.
- **Live execution** — run a single test or the whole suite against the real API;
  see PASS/FAIL, assertion detail, and response previews.
- **OWASP API security scanning** — probe the endpoint against the OWASP API
  Top-10 (2023) with a risk score and exportable Markdown / HTML report.
- **OpenAPI import & contract testing** — paste a 3.0/3.1 spec, load an endpoint,
  and validate live responses against the documented schema.
- **Exports** — runnable **pytest** script, **Postman** collection (v2.1), and
  **CI/CD** pipelines for GitHub Actions, GitLab CI, and Azure Pipelines.
- **History** — every generation and run is persisted to SQLite; reload, rerun, or
  delete past sessions.
- **Web UI** — single-page app (no build step) with light/dark mode, keyboard
  shortcuts, and toasts. Also usable via CLI and as a library.

## Setup

1. Create a virtualenv and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. Configure API keys:
   ```bash
   cp .env.example .env
   # Edit .env and add at least MISTRAL_API_KEY
   ```

   Only `MISTRAL_API_KEY` is required. `GROQ_API_KEY` and `GITHUB_MODELS_API_KEY`
   are optional fallbacks used automatically if the primary provider fails.

## Web UI

Start the FastAPI server:

```bash
./venv/bin/python -m uvicorn web.app:app --reload --host 0.0.0.0 --port 8000
```

Then open [http://localhost:8000](http://localhost:8000).

- Enter an **API Endpoint** and pick an HTTP method.
- Tick **Auto-fetch sample response** to call the API and use its real response, or
  expand **Add headers, body & sample response** to paste one manually.
- Click **Generate Test Cases**, then run tests individually or **Run All Tests**.
- Use **Export** for a pytest script, Postman collection, or copy the JSON; expand
  **Export CI/CD Pipeline** for GitHub / GitLab / Azure configs.
- Switch to **Security Scanning** to probe the OWASP API Top-10 (authorized targets
  only). Use **Import from OpenAPI** for contract testing. Open **History** to
  reload, rerun, or delete past sessions.

## CLI

The CLI covers functional test generation:

```bash
# Basic
./venv/bin/python cli/app.py --endpoint https://fakestoreapi.com/products/1 --method GET

# With a sample response file
./venv/bin/python cli/app.py --endpoint https://fakestoreapi.com/products/1 --sample-response examples/example_input.json

# With inline sample JSON
./venv/bin/python cli/app.py --endpoint https://fakestoreapi.com/products/1 --sample-json '{"id":1,"title":"Test"}'

# Save output to a file (add --compact for minified JSON)
./venv/bin/python cli/app.py --endpoint https://fakestoreapi.com/products/1 --output results.json
```

## Programmatic Usage

```python
from core.generator import generate_test_cases

result = generate_test_cases(
    endpoint="https://fakestoreapi.com/products/1",
    method="GET",
    sample_response={"id": 1, "title": "Test", "price": 109.95},
)
print(result)  # {positive_test_cases, negative_test_cases, edge_cases, assertions}
```

By default this uses the multi-provider `AIRouter` (Mistral → Groq → GitHub Models).
Pass your own client via `mistral_client=` to override.

## HTTP API

| Method & path | Purpose |
|---------------|---------|
| `POST /generate-tests` | Generate test cases (optionally auto-fetch the response) |
| `POST /export/python` | Download a runnable pytest script |
| `POST /export/postman` | Download a Postman v2.1 collection |
| `POST /api/export/cicd` | GitHub / GitLab / Azure pipeline YAML |
| `POST /api/execute-single`, `POST /api/execute-tests` | Execute test(s) live |
| `GET /api/security/owasp-categories` | List scannable OWASP categories |
| `POST /api/security/scan` | Run an OWASP API security scan |
| `POST /api/security/report` | Export a Markdown / HTML security report |
| `POST /api/openapi/parse` | Parse an OpenAPI 3.0/3.1 spec |
| `POST /api/openapi/validate-response` | Validate a response against a schema |
| `GET/POST/DELETE /api/history/sessions...` | List / read / rerun / delete history |

Interactive docs are available at `/docs` while the server is running.

## Running Tests

```bash
./venv/bin/python -m pytest tests/ -v
```

## Project Structure

```
api-testing-assistant/
├── core/
│   ├── analyzer.py            # Endpoint/response metadata extraction (no LLM)
│   ├── generator.py           # Orchestrator: analyze → prompt → LLM → validate
│   ├── live_fetcher.py        # Live HTTP fetch of API responses
│   ├── test_executor.py       # Executes test cases against the real API
│   ├── security_scanner.py    # OWASP API Top-10 probes + risk scoring
│   ├── openapi_parser.py      # OpenAPI 3.0/3.1 spec parsing
│   ├── contract_tester.py     # Response-vs-schema contract validation
│   └── database.py            # SQLite persistence (sessions/cases/results)
├── llm/
│   ├── ai_router.py           # Multi-provider router with failover
│   ├── mistral_client.py      # Mistral client (raw requests, retries)
│   ├── groq_client.py         # Groq fallback client
│   ├── github_models_client.py# GitHub Models fallback client
│   └── prompt_templates.py    # System prompt + user prompt builder
├── exports/
│   ├── python_test_generator.py    # pytest script generation
│   ├── postman_generator.py        # Postman collection v2.1
│   ├── cicd_generator.py           # GitHub/GitLab/Azure pipelines
│   └── security_report_generator.py# Markdown/HTML security reports
├── web/
│   ├── app.py                 # FastAPI entry point (serves UI, mounts routers)
│   ├── routes/                # generate, execute, security, openapi, cicd,
│   │                          #   export, history
│   ├── templates/index.html   # Single-page UI (served via FileResponse)
│   └── static/                # style.css, app.js, darkmode.js
├── cli/app.py                 # CLI (functional generation)
├── examples/                  # example_input.json, example_run.py
├── tests/                     # One test file per module
├── data/                      # SQLite DB (created at runtime)
├── requirements.txt
├── README.md
├── IMPLEMENTATION.md          # Phase-by-phase build log
└── codestructure.md           # Architecture/context reference
```

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `MISTRAL_API_KEY` | Yes | — | Primary AI provider |
| `MISTRAL_MODEL` | No | `mistral-large-latest` | Primary model override |
| `GROQ_API_KEY` | No | — | Fallback provider (enables Groq) |
| `GROQ_MODEL` | No | `llama-3.3-70b-versatile` | Groq model override |
| `GITHUB_MODELS_API_KEY` | No | — | Fallback provider (enables GitHub Models) |
| `GITHUB_MODEL` | No | `gpt-4o` | GitHub Models model override |
| `DATABASE_PATH` | No | `data/api_sentinel.db` | SQLite history location |

## Notes

- Built for Python 3.14. The `mistralai` SDK and Jinja2 are intentionally avoided
  (3.14 wheel/bug issues): the LLM is called via raw `requests`, and the UI is
  served as a static file.
- **Security scanning is for authorized targets only** — scan APIs you own or have
  explicit written permission to test.
