#!/usr/bin/env python3
"""
Example script showing how to use the generator programmatically.
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from core.generator import generate_test_cases


def main():
    endpoint = "https://fakestoreapi.com/products/1"
    method = "GET"
    sample_response = {
        "id": 1,
        "title": "test product",
        "price": 109.95,
        "category": "electronics",
    }

    print(f"Generating test cases for: {endpoint}")
    result = generate_test_cases(
        endpoint=endpoint,
        method=method,
        sample_response=sample_response,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
