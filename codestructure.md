# Code Structure — Smart API Testing Assistant

> Context document for AI assistants. Describes the full architecture, module APIs,
> data flow, and conventions of this project.

## Purpose

**API Sentinel** — an AI-powered API testing suite. Given an API endpoint (+ HTTP
method, and either a manually pasted sample JSON response or a live-fetched one), it
calls an LLM to generate structured QA intelligence:

- `positive_test_cases`
- `negative_test_cases`
- `edge_cases`
- `assertions`

On top of generation it also provides: **live test execution**, **OWASP API Top-10
security scanning**, **OpenAPI import + contract testing**, **session history**
(SQLite), and export to **pytest script**, **Postman Collection v2.1**, and
**CI/CD pipelines** (GitHub Actions / GitLab CI / Azure Pipelines).

## Tech Stack

- Python 3.14 (note: `mistralai` SDK and Jinja2 were deliberately **avoided** due to
  Py3.14 wheel/bug issues — LLMs are called via raw `requests`, HTML served via
  `FileResponse`)
- FastAPI + uvicorn (web), vanilla JS frontend (no framework, no build step)
- Multi-provider LLM via `llm/ai_router.py`: Mistral (primary,
  `mistral-large-latest`) → Groq → GitHub Models, with automatic failover; only
  providers with a configured API key are used
- SQLite (`sqlite3`) for session/test/result history
- Dependencies (`requirements.txt`): `requests`, `python-dotenv`, `fastapi`,
  `uvicorn[standard]`, `python-multipart`, `PyYAML` (pytest used but unpinned)

## Directory Tree

```
api-testing-assistant/
├── cli/
│   └── app.py                  # argparse CLI entry point (functional generation)
├── core/
│   ├── analyzer.py             # endpoint/response metadata extraction (no LLM)
│   ├── generator.py            # orchestrator: analyze → prompt → LLM → parse/validate
│   ├── live_fetcher.py         # live HTTP fetch of API responses
│   ├── test_executor.py        # executes test cases against the real API
│   ├── security_scanner.py     # OWASP API Top-10 probes + risk scoring
│   ├── openapi_parser.py       # OpenAPI 3.0/3.1 spec parsing (JSON/YAML)
│   ├── contract_tester.py      # response-vs-schema contract validation
│   └── database.py             # SQLite persistence (sessions/cases/results)
├── llm/
│   ├── ai_router.py            # multi-provider router with failover
│   ├── mistral_client.py       # Mistral API client (raw requests, retries)
│   ├── groq_client.py          # Groq fallback client
│   ├── github_models_client.py # GitHub Models fallback client
│   └── prompt_templates.py     # system prompt + user prompt builder
├── exports/                    # all deterministic, no LLM
│   ├── python_test_generator.py    # pytest script generation
│   ├── postman_generator.py        # Postman Collection v2.1
│   ├── cicd_generator.py           # GitHub/GitLab/Azure pipeline YAML
│   └── security_report_generator.py# Markdown/HTML security reports
├── web/
│   ├── app.py                  # FastAPI app: serves index.html, mounts /static, CORS,
│   │                           #   lifespan runs init_db(); /api-prefixed routers
│   ├── routes/
│   │   ├── generate.py         # POST /generate-tests
│   │   ├── execute.py          # POST /api/execute-single, /api/execute-tests
│   │   ├── security.py         # GET/POST /api/security/* (categories, scan, report)
│   │   ├── openapi.py          # POST /api/openapi/parse, /validate-response
│   │   ├── cicd.py             # POST /api/export/cicd
│   │   ├── export.py           # POST /export/python, POST /export/postman
│   │   └── history.py          # /api/history/sessions (list/read/rerun/delete)
│   ├── static/
│   │   ├── app.js              # single-page UI logic (fetch calls to the routes)
│   │   ├── style.css
│   │   └── darkmode.js         # theme toggle
│   └── templates/
│       └── index.html          # single-page UI (served via FileResponse, not Jinja2)
├── examples/
│   ├── example_input.json
│   └── example_run.py          # programmatic usage demo
├── data/                       # SQLite DB (created at runtime)
├── tests/                      # one test file per module, 175 tests total
├── .env                        # provider API keys (never commit)
├── requirements.txt
├── README.md                   # user docs
└── IMPLEMENTATION.md           # phase-by-phase build log
```

Note: `web/routes/*` are mounted under `/api` (except `generate.py` and `export.py`,
which are mounted at the root — hence `/generate-tests`, `/export/python`).

Not a packaged project: no `pyproject.toml`/`setup.py`. Modules do
`sys.path.insert(0, PROJECT_ROOT)` and must be run from the project root.
There are no `__init__.py` files; imports are plain module paths (`core.generator`, ...).

## Module APIs

### `core/analyzer.py` — metadata extraction (pure, no LLM, no I/O)

- `infer_type(value) -> str` — maps a JSON value to a type label (`string`, `integer`,
  `email`, `iso_date`, `boolean`, `array`, `object`, `null`, ...)
- `analyze_fields(data, parent_path="") -> list[dict]` — recursive field walk producing
  `{path, type, example}` entries
- `analyze_sample_response(response) -> dict` — field analysis + summary of a sample body
- `analyze_endpoint(endpoint) -> dict` — URL parsing: path segments, path params with
  inferred types (uuid/numeric/slug), endpoint category (auth/commerce/file_media/general)
- `build_analysis_metadata(endpoint, method, sample_response) -> dict` — combined metadata
  dict fed into the prompt

### `core/live_fetcher.py` — live API fetching

- `fetch_api_response(url, method="GET", headers=None, body=None, ...) -> dict` —
  real HTTP request via `requests`; 10s timeout, 1 MB response cap, JSON-only;
  raises `LiveFetchError` with contextual messages for 401/403/404/500, timeouts,
  HTML/empty/oversized responses
- `LiveFetchError(Exception)`

### `llm/mistral_client.py` — Mistral API wrapper

- `MistralClient(api_key=None, model=None)` — key from arg or `MISTRAL_API_KEY` env
  (loads `.env` defensively itself); model from arg or `MISTRAL_MODEL` env,
  default `mistral-large-latest`
- `client.send_prompt(system_prompt, user_prompt, temperature=0.2, max_tokens=8192) -> str`
  — single chat completion, 3 retries with exponential backoff on API/network errors;
  raises `MistralTruncationError` when `finish_reason == "length"`
- `MistralClientError(Exception)`, `MistralTruncationError(MistralClientError)`

### `llm/prompt_templates.py`

- `SYSTEM_PROMPT` — "Staff QA Engineer" persona; forces JSON-only output matching the
  four-key schema
- `build_user_prompt(endpoint, method, sample_response, response_metadata) -> str`

### `core/generator.py` — orchestrator (the heart of the app)

- `generate_test_cases(endpoint, method="GET", sample_response=None, mistral_client=None) -> dict`
  — full pipeline. Never raises: every failure path returns a schema-shaped dict with an
  `_error` key (via `_safe_error_response`).
  - Missing-keys recovery: if the LLM omits any of the four top-level keys, the prompt is
    retried **once** with an appended nudge naming the missing keys; the retry is kept only
    if it is strictly more complete. Otherwise missing keys are filled with `[]` and a note
    is appended to `_error`.
- `_request_and_parse(client, user_prompt) -> (parsed | None, error | None)` — one LLM
  call + extraction/parsing; exactly one of the two return values is None
- `_extract_json(text)` — strips markdown fences, brace-matches outermost `{...}`
- `_repair_truncated_json(text)` — repairs cut-off JSON (unterminated strings, trailing
  commas, unbalanced braces; falls back to truncating at the last complete element)
- `_validate_structure(data)`, `_safe_error_response(msg)`
- `EXPECTED_TOP_KEYS = {"positive_test_cases", "negative_test_cases", "edge_cases", "assertions"}`

### `exports/python_test_generator.py` — pytest export (no LLM)

- `generate_pytest_script(endpoint, method, test_data) -> str` — complete runnable pytest
  file: request helper, one `test_*` function per case/assertion, sanitized unique names

### `exports/postman_generator.py` — Postman export (no LLM)

- `generate_postman_collection(endpoint, method, test_data, headers=None, body=None) -> dict`
  — Postman Collection v2.1 JSON; one item per case with `pm.test(...)` scripts, parsed
  URL (host/path/query), global headers/body applied

### `exports/cicd_generator.py` — CI/CD export (no LLM)

- `generate_cicd_config(fmt, endpoint, method, test_data) -> (filename, yaml)` —
  pipeline YAML for `github` (Actions), `gitlab` (CI), or `azure` (Pipelines)

### `exports/security_report_generator.py` — security report export (no LLM)

- Renders a scan result into a downloadable Markdown or HTML report

### `llm/ai_router.py` — multi-provider router

- `AIRouter(primary="mistral", fallback_order=["groq","github"])` — exposes
  `send_prompt(...)` (duck-type compatible with the individual clients) and
  `generate_with_fallback(...)`; tries each provider that has a key, records the
  winner in `last_provider`, and raises `AllProvidersFailedError` if all fail
- `llm/groq_client.py`, `llm/github_models_client.py` — same `send_prompt(...)`
  interface as `MistralClient`; keys from `GROQ_API_KEY` / `GITHUB_MODELS_API_KEY`

### `core/test_executor.py` — live execution (no LLM)

- `execute_test_case(test_case, endpoint, method, headers=None, body=None) -> dict` —
  runs one case against the real API, mutating the request by category
  (negative: break auth / drop a field / wrong Content-Type; edge: boundary values),
  checks status + validation rules; never raises (errors captured in the result)
- `execute_test_suite(test_cases, ...) -> list[dict]` — sequential run of many cases

### `core/security_scanner.py` — OWASP scanning (no LLM)

- Probes the OWASP API Top-10 (2023): API1, API2, API5, API6, API7, API8, API10;
  returns findings (severity, payload used, remediation) and a 0–100 risk score

### `core/openapi_parser.py` / `core/contract_tester.py`

- `openapi_parser` — parses OpenAPI 3.0/3.1 (JSON or YAML) into endpoints, base URL,
  and request/response schemas
- `contract_tester` — validates an actual response against a schema, returning
  violations by JSON path

### `core/database.py` — SQLite persistence

- Tables: `sessions`, `test_cases`, `execution_results`. `test_cases.case_ref`
  stores the original LLM case id so stored results can be matched back to cases on
  history reload; `init_db()` runs an idempotent migration to add it to older DBs
- `save_session`, `save_test_cases`, `save_execution_results`, `get_recent_sessions`,
  `get_session`, `delete_session`; DB path via `DATABASE_PATH` (default
  `data/api_sentinel.db`)
- `redact_headers(headers)` / `redact_body(value)` / `redact_url(url)` / `REDACTED` —
  mask credential-bearing header values, body/response fields (password, token,
  api_key, client_secret, ...), and secret URL query params before storage; all
  applied inside `save_session` (`redact_body` recurses through nested objects/arrays;
  `redact_url` leaves benign URLs byte-for-byte unchanged)

### `web/app.py`

- Creates the FastAPI app, permissive CORS, mounts `/static`, includes both routers,
  `GET /` serves `templates/index.html` via `FileResponse`

### `web/routes/generate.py`

- `GenerateRequest`: `{endpoint: str, method="GET", auto_fetch=False, headers=None,
  request_body=None, sample_response=None}`
- `POST /generate-tests` — if `auto_fetch`, calls `fetch_api_response` first (falls back
  to `sample_response` / errors as `LiveFetchError` → HTTP error), then
  `generate_test_cases`; returns the four-key JSON payload

### `web/routes/export.py`

- `ExportRequest`: `{endpoint: str, method="GET", test_data: dict}`
- `POST /export/python` — pytest script as a file download
- `POST /export/postman` — collection JSON as a file download

### `web/routes/execute.py` (mounted under `/api`)

- `POST /api/execute-single` — run one test case; returns a structured result
- `POST /api/execute-tests` — run a suite; returns `{results, summary}`. Both accept
  an optional `session_id` to persist execution results to history

### `web/routes/security.py` (mounted under `/api`)

- `GET /api/security/owasp-categories` — list scannable categories
- `POST /api/security/scan` — run an OWASP scan; returns findings + risk score
- `POST /api/security/report` — Markdown / HTML report as a file download

### `web/routes/openapi.py` (mounted under `/api`)

- `POST /api/openapi/parse` — parse a spec into endpoints/schemas
- `POST /api/openapi/validate-response` — validate a response against a schema

### `web/routes/cicd.py` (mounted under `/api`)

- `POST /api/export/cicd` — `{format, endpoint, method, test_data}` → `{filename,
  yaml_content}` for GitHub / GitLab / Azure

### `web/routes/history.py` (mounted under `/api`)

- `GET /api/history/sessions` — recent sessions with pass/fail aggregates
- `GET /api/history/sessions/{id}` — one session with its cases + results
- `POST /api/history/sessions/{id}/rerun` — re-execute a stored suite live
- `DELETE /api/history/sessions/{id}` — delete a session (cascades)

### `cli/app.py`

- `python cli/app.py --endpoint URL [--method GET] [--sample-response FILE |
  --sample-json JSON_STRING] [--output FILE] [--compact]`
  (`--sample-response` is a file path; `--sample-json` is an inline JSON string)
- Prints the generated JSON (or writes it to `--output`)

## Data Flow

```
User (web UI / CLI / library call)
  └─> core.generator.generate_test_cases(endpoint, method, sample_response?)
        ├─> core.analyzer.build_analysis_metadata(...)        # deterministic metadata
        ├─> llm.prompt_templates.build_user_prompt(...)       # prompt assembly
        ├─> llm.ai_router.AIRouter.send_prompt(...)           # Mistral → Groq → GitHub
        │     (retry once with nudge if top-level keys are missing)
        └─> JSON extraction / repair / validation
              └─> dict {positive_test_cases, negative_test_cases, edge_cases,
                        assertions, _error?, _provider?}
      (web POST /generate-tests also persists the session via core.database)

Execute:  test_cases ─> core.test_executor ─> per-test PASS/FAIL results
Security: endpoint  ─> core.security_scanner ─> findings + risk score
Contract: response  ─> core.contract_tester (vs OpenAPI schema) ─> violations
History:  core.database (SQLite) ← save on generate/execute; read on load/rerun

Export (deterministic, no LLM):
  test_data ─> exports.python_test_generator      ─> .py pytest file
           ├─> exports.postman_generator          ─> .json Postman collection
           └─> exports.cicd_generator             ─> .yml pipeline (GH/GitLab/Azure)
  scan      ─> exports.security_report_generator  ─> .md / .html report

auto_fetch=true: core.live_fetcher.fetch_api_response runs first and its result
becomes sample_response.
```

## Output Schema (LLM payload)

```json
{
  "positive_test_cases": [{"id": "TC-POS-01", "title": "...", "...": "..."}],
  "negative_test_cases": [{"id": "TC-NEG-01", "title": "...", "...": "..."}],
  "edge_cases":          [{"id": "TC-EDGE-01", "title": "...", "...": "..."}],
  "assertions":          [{"category": "status_code", "rule": "200 OK", "severity": "critical"}],
  "_error": "only present on failure or partial recovery"
}
```

## Configuration

| Env var                 | Required | Default                    | Purpose                        |
|-------------------------|----------|----------------------------|--------------------------------|
| `MISTRAL_API_KEY`       | yes      | —                          | Primary provider auth          |
| `MISTRAL_MODEL`         | no       | `mistral-large-latest`     | primary model override         |
| `GROQ_API_KEY`          | no       | —                          | fallback provider (Groq)       |
| `GROQ_MODEL`            | no       | `llama-3.3-70b-versatile`  | Groq model override            |
| `GITHUB_MODELS_API_KEY` | no       | —                          | fallback provider (GitHub)     |
| `GITHUB_MODEL`          | no       | `gpt-4o`                   | GitHub Models model override   |
| `DATABASE_PATH`         | no       | `data/api_sentinel.db`     | SQLite history location        |

`.env` is loaded via `python-dotenv` at entry points and inside the LLM clients.

## Run Commands

```bash
# Web UI (http://localhost:8000)
./venv/bin/python -m uvicorn web.app:app --reload --host 0.0.0.0 --port 8000

# CLI
./venv/bin/python cli/app.py --endpoint https://fakestoreapi.com/products/1 --method GET

# Tests (175 tests, one file per module)
./venv/bin/python -m pytest tests/ -v

# Library
from core.generator import generate_test_cases
```

## Conventions & Design Rules

- **Never raise to the caller**: all generator/fetch failures become structured `_error`
  payloads (generator) or typed exceptions converted at the route layer (fetcher).
- **Defensive LLM parsing**: assume the model can return markdown fences, prose around
  JSON, truncated JSON, or missing keys — handle all of it.
- **Exporters are deterministic**: no LLM calls in `exports/`; same input → same output.
- Tests use `unittest.mock.MagicMock` for the Mistral client and `requests`; no network
  in tests. Test files insert `PROJECT_ROOT` into `sys.path` like the app modules.

## Known Gaps / Gotchas

- Python 3.14 constraint: do not add the `mistralai` SDK or Jinja2 without verifying
  3.14 compatibility first.
- The **Collections** UI view is a placeholder ("coming soon").
- No auth / multi-tenancy: one shared SQLite DB, permissive (localhost) CORS. Treat
  history as local-only data. Credentials are redacted before storage: header values
  (`redact_headers`), credential-like fields in the request body and sample response
  (`redact_body`), and secret query-string params in the endpoint URL (`redact_url`,
  e.g. `?api_key=...`, `?sig=...`) — all applied inside `core.database.save_session`.
- Because credentials are redacted, **rerun** against an auth-protected endpoint will
  fail until the token is re-entered in the form.
- Security scanning is for **authorized targets only**.
- The project directory is not yet tracked in git.
