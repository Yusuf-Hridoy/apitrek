# Smart API Testing Assistant

AI-powered API test case generator using Mistral AI.

## Phase 1 — Core AI Engine

Transforms raw API input into structured QA test intelligence:
- Positive test cases
- Negative test cases
- Edge cases
- Assertions & validation rules

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure your Mistral API key:
```bash
cp .env.example .env
# Edit .env and add your MISTRAL_API_KEY
```

## Web UI Usage (Phase 2)

Start the FastAPI server:

```bash
./venv/bin/uvicorn web.app:app --reload --host 0.0.0.0 --port 8000
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

Enter an API endpoint, select the HTTP method, optionally paste a sample JSON response, and click **Generate Test Cases**.

## CLI Usage (Phase 1)

```bash
python cli/app.py --endpoint https://fakestoreapi.com/products/1 --method GET

# With a sample response file
python cli/app.py --endpoint https://fakestoreapi.com/products/1 --sample-response examples/example_input.json

# With inline sample JSON
python cli/app.py --endpoint https://fakestoreapi.com/products/1 --sample-json '{"id":1,"title":"Test"}'

# Save output to file
python cli/app.py --endpoint https://fakestoreapi.com/products/1 --output results.json
```

## Programmatic Usage

```python
from core.generator import generate_test_cases

result = generate_test_cases(
    endpoint="https://fakestoreapi.com/products/1",
    method="GET",
    sample_response={"id": 1, "title": "Test", "price": 109.95},
)
print(result)
```

## Running Tests

```bash
pytest tests/ -v
```

## Project Structure

```
api-testing-assistant/
├── core/
│   ├── generator.py      # Orchestrates prompt building + LLM call + JSON validation
│   ├── analyzer.py       # Extracts metadata from endpoints and sample responses
├── llm/
│   ├── mistral_client.py # Mistral API client with retries
│   ├── prompt_templates.py # Structured prompts enforcing strict JSON output
├── cli/
│   ├── app.py            # Simple CLI interface
├── web/
│   ├── app.py            # FastAPI entry point
│   ├── routes/
│   │   └── generate.py   # POST /generate-tests endpoint
│   ├── templates/
│   │   └── index.html    # Web UI
│   ├── static/
│   │   ├── style.css     # UI styles
│   │   └── app.js        # Frontend logic
├── examples/
│   ├── example_input.json
│   ├── example_run.py
├── tests/
│   ├── test_generator.py
│   ├── test_analyzer.py
│   ├── test_mistral_client.py
├── requirements.txt
└── README.md
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MISTRAL_API_KEY` | Your Mistral AI API key | Required |
| `MISTRAL_MODEL` | Model to use | `mistral-large-latest` |
