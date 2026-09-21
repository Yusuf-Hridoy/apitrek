# AGENTS.md — Apitrek

Authoritative context for any coding agent (Kimi, Claude Code, etc.) working in this repo.
Read this first. Where this file and `codestructure.md` disagree, **this file wins** —
`codestructure.md` is older and has stale facts (see "Known-stale docs" below).

---

## What this project is

**Apitrek** — an AI-powered API testing tool. You give it an API endpoint (+ HTTP method,
optional headers/body, and either a pasted sample JSON response or a live-fetched one). It:

1. Calls an LLM to generate structured tests: `positive_test_cases`, `negative_test_cases`,
   `edge_cases`, and `assertions`.
2. **Grounds** every assertion against the real fetched response and labels it **verified**
   (the field exists in the response) or **unverified** (a model guess). This is the
   product's core differentiator — "honest AI test generation."
3. Runs tests live against the API, runs an **OWASP API Top 10 security scan**, and exports
   **runnable** pytest / Postman v2.1 / CI-CD (GitHub Actions, GitLab, Azure) suites.

The guiding principle everywhere: **the tool never fakes certainty.** Assertions it can't
verify are marked unverified; security findings it can't confirm are "Needs Review"; exports
skip what they can't check instead of emitting a fake pass. Preserve this principle in any change.

## Tech stack (current — trust this over codestructure.md)

- **Python 3.11+** (the codebase must stay 3.11-clean — a past backslash-in-f-string bug
  broke 3.11 import; don't reintroduce py3.12-only syntax). The generated CI pins 3.11.
- **FastAPI + uvicorn** backend. Entry: `web/app.py` (run `python -m uvicorn web.app:app`).
- **Vanilla JS frontend** — no framework, no build step. `web/static/app.js`,
  `web/static/style.css`, `web/static/darkmode.js`. HTML served via `FileResponse` /
  `HTMLResponse` (no Jinja2). `web/templates/index.html`.
- **Multi-provider LLM** via `llm/ai_router.py`: primary → Groq → GitHub Models, automatic
  failover; only providers with a configured API key are used. If all fail, a **deterministic
  floor** (`core/deterministic_generator.py`) still produces baseline tests, labeled degraded.
  Do NOT name specific providers/models in user-facing UI or README (owner keeps the stack private).
- **SQLite** for session/test/result history.
- Deps in `requirements.txt`: `requests`, `python-dotenv`, `fastapi`, `uvicorn[standard]`,
  `python-multipart`, `PyYAML`, `slowapi`. Tests use `pytest` + `httpx`.

## Directory map

```
core/        generation + logic (no web).  generator.py (LLM orchestration + grounding),
             deterministic_generator.py (no-LLM floor), security_scanner.py (OWASP),
             test_executor.py (live run), url_guard.py (SSRF guard), database.py (SQLite)
llm/         ai_router.py (failover) + one client per provider + prompt_templates.py
exports/     python_test_generator.py (pytest), postman_generator.py, cicd_generator.py,
             security_report_generator.py, assertion_translator.py (rule -> real assert)
web/         app.py (FastAPI), routes/ (generate, execute, security, cicd, export, openapi),
             static/ (app.js, style.css, darkmode.js), templates/index.html
cli/         argparse CLI entry
tests/       pytest, one file per module (~288 tests). Keep them green.
```

## How to run + test

```bash
pip install -r requirements.txt pytest httpx --break-system-packages
python -m pytest -q                 # full suite — must stay green
python -m uvicorn web.app:app --reload --port 8000
```

## Non-negotiable conventions (do not break these)

1. **Outbound requests to user-supplied URLs go through `core/url_guard.py` (`safe_request`).**
   Never call `requests.*` directly against a target in `core/` — SSRF guard must stay in the path.
2. **Semantic color system** (Palette B, "Night Teal + Gold"), used consistently:
   - green = verified / pass / secure, amber/orange = unverified / needs-review,
     red = failed / vulnerable, **gold = primary actions ONLY**, cool-teal (`--interactive`)
     = links / active nav / focus. Never let gold leak into status or general chrome.
   - Use CSS tokens (`--ok`, `--warn`, `--bad`, `--brand`, `--interactive`, `--text-*`,
     `--bg-*`), never hardcoded hex on theme-adaptive elements (hardcoded light colors caused
     invisible-in-dark-mode bugs). Verify BOTH light and dark after any CSS change.
3. **Cache-busting:** static assets are served with a `?v=<hash>` version string injected in
   `web/app.py`'s index route; `index.html` is `no-store`. Don't remove this — it's what makes
   deploys actually reflect on the live site.
4. **Grounding / honesty:** never emit an assertion or verdict that claims more certainty than
   the evidence supports. Unverifiable → mark it, don't fake it.
5. **Exports must be runnable:** pytest must `ast.parse`, Postman must be valid v2.1 JSON, CI
   YAML must `yaml.safe_load`. Negative/edge tests must send the case's real request (URL/
   headers/body from `case["request"]`), not the baseline. Escape dynamic text safely
   (`assertion_translator._py_str_literal` / docstring helpers) — quotes in rules must not break output.
6. **Deploy target:** Render (free tier) + UptimeRobot to prevent sleep. Frontend changes need
   a redeploy + hard refresh to appear; if a fix "doesn't work," suspect a stale deploy/cache first.

## Working style expected here

- **Phase-gated.** Implement exactly the current brief's scope, nothing extra. Don't add
  features not asked for.
- **Verify behavior, not just syntax.** For export/generation changes, generate real output
  and check it behaves (e.g. the negative test actually differs from the positive), then run it.
- **Keep `pytest` green** and add tests for new logic.
- Commit messages: conventional style (`feat(...)`, `fix(...)`, `docs(...)`), specific.

## Known-stale docs (ignore these claims in codestructure.md / README if seen)

- "Python 3.14" → it's **3.11+**.
- "API Sentinel" → the product is now **Apitrek**.
- "mistral-large-latest" and any named model/provider → do not surface provider/model names.
- "OpenAPI import + contract testing" as an active feature → the OpenAPI import UI is
  **hidden** (button commented out in `index.html`); backend + tests remain but it's not a
  user-facing feature. Don't re-expose it unless a brief says so.