# Code Structure — Smart API Testing Assistant

> Context document for AI assistants. Describes the full architecture, module APIs,
> data flow, and conventions of this project.

## Purpose

AI-powered API test case generator. Given an API endpoint (+ HTTP method, and either a
manually pasted sample JSON response or a live-fetched one), it calls the Mistral AI
chat API to generate structured QA intelligence:

- `positive_test_cases`
- `negative_test_cases`
- `edge_cases`
- `assertions`

Results can be exported as a runnable **pytest script** or a **Postman Collection v2.1**.

## Tech Stack

- Python 3.14 (note: `mistralai` SDK and Jinja2 were deliberately **avoided** due to
  Py3.14 wheel/bug issues — Mistral is called via raw `requests`, HTML served via
  `FileResponse`)
- FastAPI + uvicorn (web), vanilla JS frontend (no framework, no build step)
- Mistral AI chat completions (`https://api.mistral.ai/v1/chat/completions`),
  default model `mistral-large-latest`
- Dependencies (`requirements.txt`): `requests`, `python-dotenv`, `fastapi`,
  `uvicorn[standard]`, `python-multipart` (pytest used but unpinned)

## Directory Tree

```
api-testing-assistant/
├── cli/
│   └── app.py                  # argparse CLI entry point
├── core/
│   ├── analyzer.py             # endpoint/response metadata extraction (no LLM)
│   ├── generator.py            # orchestrator: analyze → prompt → LLM → parse/validate
│   └── live_fetcher.py         # live HTTP fetch of API responses
├── llm/
│   ├── mistral_client.py       # Mistral API client (raw requests, retries)
│   └── prompt_templates.py     # system prompt + user prompt builder
├── exports/
│   ├── python_test_generator.py  # pytest script generation (deterministic)
│   └── postman_generator.py      # Postman Collection v2.1 generation (deterministic)
├── web/
│   ├── app.py                  # FastAPI app: serves index.html, mounts /static, CORS
│   ├── routes/
│   │   ├── generate.py         # POST /generate-tests
│   │   └── export.py           # POST /export/python, POST /export/postman
│   ├── static/
│   │   ├── app.js              # single-page UI logic (fetch calls to the routes)
│   │   └── style.css
│   └── templates/
│       └── index.html          # single-page UI (served via FileResponse, not Jinja2)
├── examples/
│   ├── example_input.json
│   └── example_run.py          # programmatic usage demo
├── tests/                      # one test file per module, ~75 tests total
├── .env                        # MISTRAL_API_KEY (never commit)
├── requirements.txt
├── README.md                   # user docs (structure section is stale)
└── IMPLEMENTATION.md           # phase-by-phase build log (documents through Phase 5)
```

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

### `cli/app.py`

- `python cli/app.py --endpoint URL [--method GET] [--sample-response JSON_STRING |
  --sample-json FILE] [--output FILE]`
- Prints the generated JSON (or writes it to `--output`)

## Data Flow

```
User (web UI / CLI / library call)
  └─> core.generator.generate_test_cases(endpoint, method, sample_response?)
        ├─> core.analyzer.build_analysis_metadata(...)        # deterministic metadata
        ├─> llm.prompt_templates.build_user_prompt(...)       # prompt assembly
        ├─> llm.mistral_client.MistralClient.send_prompt(...) # Mistral API call
        │     (retry once with nudge if top-level keys are missing)
        └─> JSON extraction / repair / validation
              └─> dict {positive_test_cases, negative_test_cases, edge_cases,
                        assertions, _error?}

Export (deterministic, no LLM):
  test_data ─> exports.python_test_generator ─> .py pytest file (download)
           └─> exports.postman_generator     ─> .json Postman collection (download)

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

| Env var           | Required | Default                 | Purpose              |
|-------------------|----------|-------------------------|----------------------|
| `MISTRAL_API_KEY` | yes      | —                       | Mistral API auth     |
| `MISTRAL_MODEL`   | no       | `mistral-large-latest`  | model override       |

`.env` is loaded via `python-dotenv` at entry points and inside `mistral_client.py`.

## Run Commands

```bash
# Web UI (http://localhost:8000)
./venv/bin/python -m uvicorn web.app:app --reload --host 0.0.0.0 --port 8000

# CLI
./venv/bin/python cli/app.py --endpoint https://fakestoreapi.com/products/1 --method GET

# Tests (75 tests, one file per module)
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

- `README.md` project-structure section is stale (missing `live_fetcher.py`, `exports/`,
  `web/routes/export.py`).
- `IMPLEMENTATION.md` documents through Phase 5; the Postman export phase is implemented
  but undocumented there.
- Python 3.14 constraint: do not add the `mistralai` SDK or Jinja2 without verifying
  3.14 compatibility first.
- The project directory is not yet tracked in git.
