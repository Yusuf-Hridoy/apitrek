# Apitrek

**Trustworthy AI-powered API testing** — generates test cases from any endpoint,
labels every assertion by whether it's grounded in the real API response, runs an
OWASP security scan, and exports runnable pytest/Postman suites.

🔗 **Live demo:** https://apitrek-production.up.railway.app

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)

## What makes it different: grounded assertions

Most tools that generate API tests with an LLM will happily assert on fields that
don't exist — the model hallucinates a field, and the "test" either false-passes
or errors on nonsense. Apitrek checks every generated assertion against the
real fetched response and labels it:

- **✓ verified** — the field exists in the actual API response
- **⚠ unverified** — a model guess you should confirm before trusting

Exports only assert on fields that really exist, so the pytest or Postman suite
you download runs clean instead of KeyError-ing on a hallucinated field.

<!-- TODO: screenshot of grounding badges (✓ verified / ⚠ unverified in the UI) -->

## Features

- **Grounded test generation** — positive / negative / edge cases + assertions,
  each labeled verified or unverified against the real response (see above).
- **Multi-provider AI with automatic failover** — one provider outage doesn't
  kill a run; only the providers you configure keys for are used.
- **Deterministic floor** — if no AI provider is available, it still produces
  baseline tests deterministically, clearly labeled. The tool never hard-fails.
- **Live execution** — run one test or the whole suite against the real API;
  see PASS/FAIL, assertion detail, and response previews.
- **OWASP API security scanning** — probe the OWASP API Top 10 (2023) with a
  risk score and exportable Markdown / HTML report.
- **OpenAPI import & contract testing** — paste a 3.0/3.1 spec, load an endpoint,
  validate live responses against the documented schema.
- **Exports** — runnable **pytest** script, **Postman** collection (v2.1), and
  **CI/CD** pipelines for GitHub Actions, GitLab CI, and Azure Pipelines.
- **History** — every generation and run persists to SQLite; reload, rerun, delete.
- **Web UI + CLI + library** — single-page app (no build step, light/dark mode),
  a CLI, and importable Python functions.

## How it works

1. **Analyze** the endpoint and (optionally auto-fetched) response locally — no
   LLM call is needed to understand the shape of the API.
2. **Prompt** the AI layer with that analysis; a router tries multiple providers
   in order, so a single provider's blip doesn't degrade the run.
3. **Validate & repair** the returned JSON against the expected schema before
   anything is trusted.
4. **Ground** every assertion against the real response — the verified/unverified
   labels come from this step.
5. **Execute, scan, export** — run live, scan for OWASP issues, or export a suite.

If every AI provider is down, step 2 falls back to a deterministic generator
that still emits baseline tests — labeled as such.

## Quick start

```bash
git clone https://github.com/Yusuf-Hridoy/apitrek.git
cd apitrek
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add at least one provider key (see table below)
python -m uvicorn web.app:app --reload --port 8000
```

Open http://localhost:8000 and click **Try a sample API** — one click fills a
safe public endpoint and runs the full pipeline.

## Usage

### Web UI

- Enter an **API Endpoint**, pick a method, tick **Auto-fetch sample response**
  (or paste one) and click **Generate Test Cases**.
- Run tests individually or **Run All Tests**; export pytest / Postman / CI-CD.
- Switch to **Security Scanning** for the OWASP scan, **Import from OpenAPI**
  for contract testing, **History** to reload past sessions.

### CLI

```bash
# Basic generation
./venv/bin/python cli/app.py --endpoint https://fakestoreapi.com/products/1 --method GET

# With a sample response file
./venv/bin/python cli/app.py --endpoint https://fakestoreapi.com/products/1 \
    --sample-response examples/example_input.json

# With inline sample JSON
./venv/bin/python cli/app.py --endpoint https://fakestoreapi.com/products/1 \
    --sample-json '{"id":1,"title":"Test"}'

# Save output to a file (add --compact for minified JSON)
./venv/bin/python cli/app.py --endpoint https://fakestoreapi.com/products/1 \
    --output results.json
```

### Programmatic

```python
from core.generator import generate_test_cases

result = generate_test_cases(
    endpoint="https://fakestoreapi.com/products/1",
    method="GET",
    sample_response={"id": 1, "title": "Test", "price": 109.95},
)
# -> {positive_test_cases, negative_test_cases, edge_cases, assertions}
```

By default this routes through the multi-provider AI layer with automatic
failover. Pass your own client instance to plug in any LLM client that exposes
a `send_prompt` method.

### HTTP API

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
| `POST /api/openapi/contract-tests` | Generate contract tests from a spec |
| `POST /api/openapi/validate-response` | Validate a response against a schema |
| `GET/POST/DELETE /api/history/sessions...` | List / read / rerun / delete history |

Interactive docs are available at `/docs` while the server is running.

## Configuration

The AI layer uses multiple providers with automatic failover, plus a
deterministic fallback that generates baseline tests if no provider is
available. Configure at least one provider key in `.env` to enable AI
generation — only the providers you configure are ever called.

| Variable | Required | Purpose |
|----------|----------|---------|
| `MISTRAL_API_KEY` | Yes* | Primary AI provider key |
| `GROQ_API_KEY` | No | Secondary provider key (automatic fallback) |
| `GITHUB_MODELS_API_KEY` | No | Tertiary provider key (automatic fallback) |
| `DATABASE_PATH` | No | SQLite history location (default `data/api_sentinel.db`) |
| `LLM_TIMEOUT_SECONDS` | No | Per-provider request timeout (default `30`) |
| `RATE_LIMIT_*` | No | Per-IP rate limits (see `.env.example`) |

\*At least one provider key is required for AI generation; without any, the
deterministic floor still produces baseline tests.

## Project structure

```
apitrek/
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
│   ├── mistral_client.py      # Primary provider client (retries + backoff)
│   ├── groq_client.py         # Fallback provider client
│   ├── github_models_client.py# Fallback provider client
│   └── prompt_templates.py    # System prompt + user prompt builder
├── exports/
│   ├── python_test_generator.py     # Schema-driven pytest script generation
│   ├── postman_generator.py         # Postman collection v2.1
│   ├── cicd_generator.py            # GitHub/GitLab/Azure pipelines
│   └── security_report_generator.py # Markdown/HTML security reports
├── web/
│   ├── app.py                 # FastAPI entry point (serves UI, mounts routers)
│   ├── routes/                # generate, execute, security, openapi, cicd,
│   │                          #   export, history
│   ├── templates/index.html   # Single-page UI
│   └── static/                # style.css, app.js, darkmode.js (cache-busted)
├── cli/app.py                 # CLI (functional generation)
├── examples/                  # example_input.json, example_run.py
├── tests/                     # One test file per module
├── data/                      # SQLite DB (created at runtime)
├── requirements.txt
└── LICENSE                    # MIT
```

## Testing

```bash
./venv/bin/python -m pytest tests/ -v
```

261 tests, one file per module — covering the generator, grounding, exporters,
security scanner, LLM failover/retry, rate limiting, and every HTTP route.

## Notes

- Targets **Python 3.11+** (the exported CI pipelines pin 3.11, and the codebase
  is kept 3.11-clean). The LLM is called via raw `requests` rather than an SDK,
  and the UI is served as static files — no build step, minimal dependencies.
- **Security scanning is for authorized targets only** — scan APIs you own or
  have explicit written permission to test.

## License

[MIT](LICENSE)
