# Smart API Testing Assistant — Implementation Log

## Quick Start

```bash
cd api-testing-assistant
source venv/bin/activate
python -m uvicorn web.app:app --reload --host 0.0.0.0 --port 8000
```
Open http://localhost:8000

---

## Project Overview

**API Sentinel** — an AI-powered API testing suite that generates test cases, edge
cases, and validation rules from API endpoints, then lets you execute, secure, and
export them. The project was built in phases:

- **Phase 1:** Core CLI AI engine
- **Phase 2:** Lightweight web UI on top of the engine
- **Phase 4:** Automatic live API response fetching
- **Phase 5:** Pytest automation script export
- **Phase 6+ (see below):** Live test execution, OWASP API security scanning,
  OpenAPI import & contract testing, session history (SQLite), Postman + CI/CD
  export, and a multi-provider AI router with automatic failover.

---

## Technology Stack

| Layer | Technology | Reason |
|-------|-----------|--------|
| AI Provider | Mistral (primary) + Groq + GitHub Models | Failover across providers; Mistral default (`mistral-large-latest`) |
| HTTP Client | `requests` (direct REST API) | Avoided SDK due to Python 3.14 compatibility issues |
| Backend | FastAPI | Lightweight, async, automatic OpenAPI docs |
| Frontend | Vanilla HTML + CSS + JS | No build step, fast MVP, easy to maintain |
| Server | Uvicorn | ASGI server for FastAPI |
| Persistence | SQLite (`sqlite3`) | Zero-config session/test/result history |
| Spec parsing | `PyYAML` | Parse OpenAPI specs supplied as YAML |
| Config | `python-dotenv` | Secure API key handling via `.env` |
| Testing | `pytest` | Unit tests for all core modules (175 tests) |

---

## Phase 1 — Core AI Engine

### Goal
Transform raw API input into structured QA test intelligence via AI.

### Architecture

```
Input (JSON) → Analyzer → Prompt Builder → Mistral Client → JSON Parser → Output (JSON)
```

### Modules Built

#### 1. `llm/mistral_client.py`
- Connects to Mistral Chat Completions API via direct HTTP (`requests`)
- Reads API key from `MISTRAL_API_KEY` environment variable
- Implements retry logic: 3 attempts with exponential backoff
- Returns raw AI text response
- Handles network errors, timeouts, and empty responses gracefully

**Why `requests` instead of the official `mistralai` SDK?**
The SDK did not have a pre-built wheel for Python 3.14, causing `pip install` to fail. Using direct HTTP calls keeps the dependency tree minimal and avoids compatibility issues.

#### 2. `llm/prompt_templates.py`
- Contains structured system and user prompts
- System prompt enforces strict JSON-only output with QA engineer context
- User prompt dynamically injects endpoint, HTTP method, sample response, and analyzer metadata
- Explicitly asks for specific numbers of test cases (3+ positive, 4+ negative, 4+ edge, 5+ assertions)

#### 3. `core/analyzer.py`
- Analyzes sample responses to extract field types, nested structures, and patterns
- Detects patterns like ISO dates, emails, URLs, numeric strings
- Analyzes endpoint URLs to infer path parameters and suggested HTTP methods
- Feeds metadata into prompts to improve AI test generation quality

#### 4. `core/generator.py`
- Orchestration layer: takes API input → builds prompt → calls Mistral → parses JSON
- Defensive JSON extraction: handles markdown fences (` ```json `), extra text, and plain JSON
- Validates output structure: ensures all 4 required top-level keys exist
- Returns error-safe JSON (`_error` field) instead of crashing on any failure

#### 5. `cli/app.py`
- `argparse`-based CLI
- Supports `--endpoint`, `--method`, `--sample-response` (file), `--sample-json` (inline), `--output`
- Loads `.env` before importing core modules

### Error Handling Strategy
Every layer returns structured error JSON rather than raising unhandled exceptions:

| Failure | Behavior |
|---------|----------|
| Missing API key | Clear error message in `_error` field |
| Malformed AI JSON | Attempts extraction; falls back to error JSON |
| Missing required keys in AI output | Reports missing keys in `_error` |
| Network timeout / API down | Retries 3×, then returns error JSON |
| Invalid user input (empty endpoint) | Returns error JSON immediately |

### Testing
24 unit tests covering:
- Analyzer field/type inference
- Endpoint path parsing
- JSON extraction from markdown/plain text
- Generator validation logic
- Mistral client retries and error paths
- Mocked AI responses (success, malformed, missing keys)

---

## Phase 2 — Web UI + Backend Integration

### Goal
Transform the CLI utility into a usable browser-based application without rewriting core logic.

### Architecture

```
Browser → FastAPI → Reuses core/generator.py → Mistral AI → Structured JSON → Browser
```

### Backend Built

#### `web/app.py`
- FastAPI entry point
- Serves `index.html` via `FileResponse`
- Mounts `/static` for CSS and JS
- Includes `/generate-tests` router
- Adds CORS middleware for local development

**Why `FileResponse` instead of Jinja2 templates?**
Jinja2 3.1.6 has a cache-key hashing bug with Python 3.14 that causes `TypeError: cannot use 'tuple' as a dict key`. Since the UI is a single-page app with no server-side rendering needs, serving a static HTML file eliminates the dependency entirely.

#### `web/routes/generate.py`
- Exposes `POST /generate-tests`
- Request body validated with Pydantic:
  ```json
  {
    "endpoint": "https://api.example.com/items/1",
    "method": "GET",
    "sample_response": { ... }
  }
  ```
- Reuses `generate_test_cases()` from Phase 1 directly — zero logic duplication
- Returns 422 for empty endpoints, 502 for AI failures

### Frontend Built

#### `web/templates/index.html`
- Single-page layout with semantic sections
- Input form: endpoint URL, method dropdown, sample JSON textarea
- Action button with loading spinner
- Output grid: 4 cards (Positive, Negative, Edge, Assertions)
- Error banner and "Copy JSON" button

#### `web/static/style.css`
- CSS custom properties for consistent theming
- Card-based layout with clean spacing
- Color-coded sections:
  - Green for positive cases
  - Red for negative cases
  - Amber for edge cases
  - Purple for assertions
- Responsive grid (1 column mobile, 2 columns desktop)
- Smooth hover states and focus rings

#### `web/static/app.js`
- Handles form submission with `fetch()`
- Validates sample JSON client-side before sending
- Toggles loading spinner and disables button during generation
- Renders each category dynamically into cards with:
  - Test case ID badge
  - Title and description
  - Expected status code tags
  - Validation rules as lists
  - Severity/category tags for assertions
- Copies full JSON response to clipboard
- Displays readable error messages for network and backend failures

### Integration Points
The backend intentionally does **not** duplicate any Phase 1 logic:

| Web Layer | Reuses Phase 1 Module |
|-----------|----------------------|
| `web/routes/generate.py` | `core.generator.generate_test_cases()` |
| AI prompting | `llm.prompt_templates.build_user_prompt()` |
| Response analysis | `core.analyzer.build_analysis_metadata()` |
| Mistral API calls | `llm.mistral_client.MistralClient` |
| JSON validation | `core.generator._validate_structure()` |

---

## Challenges & Solutions

### 1. Python 3.14 Compatibility
**Problem:** `mistralai` SDK and `jinja2` both had compatibility issues with Python 3.14.
**Solution:** Replaced SDK with direct `requests` HTTP calls; replaced Jinja2 templates with static `FileResponse`.

### 2. `python` vs `python3` Mismatch
**Problem:** User shell had `python` aliased to system Python 3.12, but venv was created with Python 3.14.
**Solution:** Documented using `./venv/bin/python` explicitly or `python3` instead of `python`.

### 3. Strict JSON Output Enforcement
**Problem:** LLMs sometimes wrap JSON in markdown fences or add explanatory text.
**Solution:** `_extract_json()` function strips ` ```json ` fences and finds the outermost `{}` pair using brace matching.

### 4. `.env` Not Loading
**Problem:** API key stored in `.env.example` was not read by the application.
**Solution:** Added `load_dotenv()` to entry points (`cli/app.py`, `web/app.py`) and defensive loading inside `llm/mistral_client.py`.

---

## Phase 4 — Automatic Live API Response Fetching

### Goal
Transform the tool from a manual "paste JSON" assistant into a live AI-powered API testing analyzer that can fetch real API responses automatically.

### Architecture

```
User → endpoint + method (+ optional headers/body)
        ↓
POST /generate-tests
        ↓
[auto_fetch?] → core/live_fetcher.py → Real HTTP request
        ↓
Parsed JSON response → core/generator.py → Mistral AI
        ↓
Structured test cases → Browser
```

### Modules Built

#### 1. `core/live_fetcher.py`
- Sends real HTTP requests via `requests`
- Supports GET, POST (with headers and JSON body), and other methods
- Validates URL format before sending
- Enforces 10-second timeout and 1 MB response size limit
- Detects non-JSON responses (HTML, plain text) and returns readable errors
- Handles specific HTTP status codes with contextual messages:
  - 401 → "Authentication may be required"
  - 403 → "You may not have permission"
  - 404 → "Please check the endpoint URL"
  - 500+ → "Server error... temporarily unavailable"
- Returns parsed JSON dict on success
- Raises `LiveFetchError` with human-readable messages on any failure

#### 2. Updated `web/routes/generate.py`
- Extended `GenerateRequest` Pydantic model with new fields:
  - `auto_fetch` (bool)
  - `headers` (dict, optional)
  - `request_body` (dict, optional)
  - `sample_response` (dict, optional — manual fallback)
- When `auto_fetch=true`, calls `live_fetcher.fetch_api_response()` before AI generation
- Coerces header values to strings for safe HTTP transmission
- On fetch failure, returns 502 with a readable error message
- On success, passes fetched response into existing `generate_test_cases()`
- Manual `sample_response` flow remains fully functional when `auto_fetch=false`

#### 3. Updated Frontend
- **Checkbox:** "Auto-fetch sample response"
  - Toggles visibility of the manual Sample Response textarea
- **Headers textarea:** Optional JSON object for request headers
- **Request Body textarea:** Optional JSON object for POST/PUT/PATCH
- **Loading state:** Button text changes to "Fetching API response..." when auto-fetch is active
- Footer updated to "Phase 4 Live API Analyzer"

### Integration Points
Phase 4 intentionally reuses all existing core logic:

| Phase 4 Layer | Reuses Existing Module |
|---------------|------------------------|
| `web/routes/generate.py` | `core.generator.generate_test_cases()` |
| Live response analysis | `core.analyzer.build_analysis_metadata()` |
| AI prompting | `llm.prompt_templates.build_user_prompt()` |
| Mistral API calls | `llm.mistral_client.MistralClient` |
| JSON validation | `core.generator._validate_structure()` |

### Error Handling Strategy
Fetcher errors are surfaced as structured HTTP 502 responses with readable messages:

| Failure | Behavior |
|---------|----------|
| Invalid URL | "Invalid URL format..." |
| Timeout (>10s) | "Request timed out..." |
| Connection/DNS failure | "Could not connect to the API..." |
| HTML instead of JSON | "The API returned HTML instead of JSON..." |
| Empty response | "The API returned an empty response." |
| Response >1 MB | "Response too large..." |
| 401/403/404/500 | Contextual message with status code |
| Non-JSON body | "The API response is not valid JSON..." |

### Testing
15 new unit tests covering:
- URL validation
- Unsupported method rejection
- Successful JSON fetch
- Timeout, connection, and redirect errors
- HTML response detection
- Empty and non-JSON responses
- 401/500 status code handling
- POST with headers and body
- Oversized response rejection
- JSON array rejection (objects only)

Total test count: **42** (24 Phase 1 + 15 Phase 4 + existing)

---

## Phase 5 — Pytest Automation Script Generator

### Goal
Transform the tool from an "AI test suggestion tool" into an "AI-assisted API automation generator" by exporting generated test cases as runnable pytest Python scripts.

### Architecture

```
User → Generate test cases → Receive JSON payload
        ↓
Click "Download Pytest Script"
        ↓
POST /export/python (sends JSON payload)
        ↓
exports/python_test_generator.py → Python script string
        ↓
Browser downloads test_api.py
```

### Modules Built

#### 1. `exports/python_test_generator.py`
- Transforms structured AI-generated test data into a pytest-compatible `.py` file
- Generates clean, readable test functions with descriptive docstrings
- Includes a reusable `_make_request()` helper supporting GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS
- Generates status-code assertions from `expected.status_code` fields
- Attempts lightweight type assertions from validation rules (e.g., "id should be integer" → `assert isinstance(data["id"], int)`)
- Handles duplicate test titles by appending numeric suffixes
- Falls back to a minimal smoke test when no AI-generated cases are available
- Output is valid Python syntax guaranteed (verified via AST parsing in tests)

#### 2. `web/routes/export.py`
- Exposes `POST /export/python`
- Accepts `ExportRequest` Pydantic model:
  ```json
  {
    "endpoint": "https://api.example.com/items/1",
    "method": "GET",
    "test_data": { ... }
  }
  ```
- Returns a downloadable `test_api.py` file with `Content-Disposition: attachment`
- Media type: `text/x-python`
- On generation failure, returns a structured 500 error instead of crashing

#### 3. Updated Frontend
- **New button:** "Download Pytest Script" in the results header (next to "Copy JSON")
- Sends the currently displayed `lastResult` JSON to `/export/python`
- Triggers a browser file download via Blob + temporary anchor element
- If no test cases have been generated yet, shows a friendly error message

### Integration Points
Phase 5 consumes the existing output of Phase 1 without modifying any generation logic:

| Phase 5 Layer | Reuses Existing Output |
|---------------|------------------------|
| `exports/python_test_generator.py` | `positive_test_cases`, `negative_test_cases`, `edge_cases`, `assertions` |
| `web/routes/export.py` | `GenerateRequest.endpoint` and `GenerateRequest.method` |
| Frontend | `lastResult` state from Phase 2/4 |

### Error Handling Strategy

| Failure | Behavior |
|---------|----------|
| No test data generated yet | Frontend: "Please generate test cases first before exporting." |
| Missing endpoint in export request | Backend 422: "Endpoint is required." |
| Script generation crashes | Backend 500: "Failed to generate script: ..." |
| Malformed assertions in test data | Generator skips/defaults safely, still produces valid Python |
| Empty test data | Generator produces a minimal smoke test |

### Testing
11 new unit tests covering:
- Minimal fallback script generation
- Positive, negative, and edge case script generation
- Assertion rule conversion
- Validation rule → type assertion parsing (int, str, bool, number, presence)
- POST method and request body embedding
- Duplicate title handling (unique function names)
- Valid Python syntax via AST parsing
- Malformed/empty assertion fallback
- Missing endpoint fallback

Total test count: **53** (42 prior + 11 Phase 5)

---

## Phase 6+ — Execution, Security, Contracts, History & More

After Phase 5 the tool grew from an "AI test suggestion + export" utility into a
full testing suite. Each capability reuses the Phase 1 core and follows the same
rules (never crash the caller, deterministic exporters, defensive parsing).

### Live Test Execution
- `core/test_executor.py` — executes generated cases against the real API,
  mutating requests by category (negative: break auth / drop a field / wrong
  Content-Type; edge: boundary values) and checking status codes + validation
  rules. Returns structured per-test results; never raises.
- `web/routes/execute.py` — `POST /api/execute-single` and `POST /api/execute-tests`
  (suite run with a pass/fail summary). The UI runs tests individually or via
  **Run All Tests** with a live progress bar.

### OWASP API Security Scanning
- `core/security_scanner.py` — probes the OWASP API Top-10 (2023): API1, API2,
  API5, API6, API7, API8, API10. Produces findings (severity, payload used,
  remediation) and a 0–100 risk score.
- `web/routes/security.py` — `GET /api/security/owasp-categories`,
  `POST /api/security/scan`, `POST /api/security/report`.
- `exports/security_report_generator.py` — Markdown / HTML report export.
- UI: a dedicated **Security Scanning** mode with a category picker, an
  "authorized testing only" warning, a risk gauge, and a filterable findings table.

### OpenAPI Import & Contract Testing
- `core/openapi_parser.py` — parses OpenAPI 3.0/3.1 (JSON or YAML) into endpoints,
  base URL, request/response schemas.
- `core/contract_tester.py` — validates a live response against the documented
  schema and reports violations by JSON path.
- `web/routes/openapi.py` — `POST /api/openapi/parse`,
  `POST /api/openapi/validate-response`. UI: import modal + endpoint explorer;
  contract validation runs automatically after a test executes.

### Session History (SQLite)
- `core/database.py` — sessions, test cases, and execution results in SQLite.
  Includes a `case_ref` column storing the original LLM case id so stored
  execution results can be matched back to their cases on reload; `init_db()`
  runs an idempotent migration to add it to existing databases.
- `web/routes/history.py` — list / read / rerun / delete sessions.
- UI: a **History** view (functional vs. security tabs). **Load** restores the
  full session — cases *and* prior PASS/FAIL badges — without another LLM call;
  **Rerun** re-executes a stored suite live. Persistence is best-effort and never
  blocks generation.

### Postman & CI/CD Export
- `exports/postman_generator.py` — Postman Collection v2.1 with `pm.test(...)`
  scripts (`POST /export/postman`).
- `exports/cicd_generator.py` — GitHub Actions, GitLab CI, and Azure Pipelines
  configs (`POST /api/export/cicd`).

### Multi-Provider AI Router
- `llm/ai_router.py` — tries the primary provider (Mistral) and falls back to Groq
  then GitHub Models on any failure; providers without a configured key are
  skipped. `generate_test_cases()` uses it by default and records the winning
  provider in `_provider` for the UI badge.
- `llm/groq_client.py`, `llm/github_models_client.py` — fallback clients with the
  same `send_prompt(...)` interface as the Mistral client.

**Current test count: 175** (one test file per module).

---

## File Structure

```
api-testing-assistant/
├── core/
│   ├── analyzer.py           # Response/endpoint metadata extraction
│   ├── generator.py          # Main orchestrator: prompt → AI → JSON
│   ├── live_fetcher.py       # Live API HTTP request fetcher
│   ├── test_executor.py      # Executes test cases against the real API
│   ├── security_scanner.py   # OWASP API Top-10 probes + risk scoring
│   ├── openapi_parser.py     # OpenAPI 3.0/3.1 spec parsing
│   ├── contract_tester.py    # Response-vs-schema contract validation
│   └── database.py           # SQLite persistence (sessions/cases/results)
├── llm/
│   ├── ai_router.py          # Multi-provider router with failover
│   ├── mistral_client.py     # Mistral client (raw requests, retries)
│   ├── groq_client.py        # Groq fallback client
│   ├── github_models_client.py  # GitHub Models fallback client
│   └── prompt_templates.py   # Structured system + user prompts
├── cli/
│   └── app.py                # argparse CLI interface (functional generation)
├── exports/
│   ├── python_test_generator.py     # Pytest script generator
│   ├── postman_generator.py         # Postman collection v2.1
│   ├── cicd_generator.py            # GitHub/GitLab/Azure pipelines
│   └── security_report_generator.py # Markdown/HTML security reports
├── web/
│   ├── app.py                # FastAPI entry point (lifespan runs init_db)
│   ├── routes/
│   │   ├── generate.py       # POST /generate-tests
│   │   ├── execute.py        # POST /api/execute-single, /api/execute-tests
│   │   ├── security.py       # /api/security/* (scan, categories, report)
│   │   ├── openapi.py        # /api/openapi/* (parse, validate-response)
│   │   ├── cicd.py           # POST /api/export/cicd
│   │   ├── export.py         # POST /export/python, /export/postman
│   │   └── history.py        # /api/history/sessions (list/read/rerun/delete)
│   ├── templates/
│   │   └── index.html        # Single-page Web UI (served via FileResponse)
│   └── static/
│       ├── style.css         # Responsive styles
│       ├── app.js            # Frontend fetch + render logic
│       └── darkmode.js       # Theme toggle
├── examples/
│   ├── example_input.json
│   └── example_run.py
├── data/                     # SQLite DB (created at runtime)
├── tests/                    # One test file per module (175 tests)
├── .env.example
├── requirements.txt
├── README.md
├── IMPLEMENTATION.md         # This file
└── codestructure.md          # Architecture/context reference
```

---

## How to Run

### Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your MISTRAL_API_KEY (Groq / GitHub Models keys optional)
```

### Web UI
```bash
./venv/bin/python -m uvicorn web.app:app --reload --host 0.0.0.0 --port 8000
```
Open http://localhost:8000

### CLI (Phase 1)
```bash
./venv/bin/python cli/app.py --endpoint https://fakestoreapi.com/products/1 --method GET
```

### Tests
```bash
./venv/bin/python -m pytest tests/ -v
```

---

## Success Criteria Met

- [x] User can run CLI command with API endpoint
- [x] System returns structured API test cases
- [x] Mistral AI is successfully integrated
- [x] Output is valid JSON only
- [x] No UI or unnecessary components in Phase 1
- [x] User can access app in browser
- [x] Submit API endpoint via web form
- [x] Receive generated API tests dynamically
- [x] Backend integrates with existing AI engine
- [x] No unnecessary complexity added
- [x] **User can provide only endpoint URL (Phase 4)**
- [x] **System fetches live API response automatically (Phase 4)**
- [x] **AI generates tests from real response (Phase 4)**
- [x] **Fallback manual JSON still works (Phase 4)**
- [x] **App remains stable and clean (Phase 4)**
- [x] **User can export pytest-ready scripts (Phase 5)**
- [x] **Scripts run successfully with pytest (Phase 5)**
- [x] **Generated assertions are usable (Phase 5)**
- [x] **Export works directly from UI (Phase 5)**
- [x] **User can execute generated tests live against the real API (Phase 6+)**
- [x] **User can scan an endpoint against the OWASP API Top-10 (Phase 6+)**
- [x] **User can import an OpenAPI spec and contract-test responses (Phase 6+)**
- [x] **Sessions are saved to history and can be reloaded / rerun (Phase 6+)**
- [x] **User can export Postman collections and CI/CD pipelines (Phase 6+)**
- [x] **AI provider fails over automatically (Mistral → Groq → GitHub) (Phase 6+)**
