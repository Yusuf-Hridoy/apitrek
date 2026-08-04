#!/usr/bin/env python3
"""
Simple CLI for the Smart API Testing Assistant.
Usage:
    python cli/app.py --endpoint https://fakestoreapi.com/products/1 --method GET
"""
import argparse
import json
import sys
from pathlib import Path

# Ensure core and llm are importable when running from repo root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from core.generator import generate_test_cases


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smart API Testing Assistant — Generate AI-powered test cases."
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="API endpoint URL (e.g., https://api.example.com/items/1)",
    )
    parser.add_argument(
        "--method",
        default="GET",
        choices=["GET", "POST", "PUT", "DELETE", "PATCH"],
        help="HTTP method (default: GET)",
    )
    parser.add_argument(
        "--sample-response",
        dest="sample_response",
        help="Path to a JSON file containing a sample response body",
    )
    parser.add_argument(
        "--sample-json",
        dest="sample_json",
        help="Inline JSON string for sample response body",
    )
    parser.add_argument(
        "--output",
        dest="output_file",
        help="Optional file path to write JSON output",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Pretty-print JSON output (default: true)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact JSON instead of pretty-printed",
    )
    return parser.parse_args()


def load_sample_response(args: argparse.Namespace) -> dict:
    """Load sample response from file, inline JSON, or return None."""
    if args.sample_response:
        try:
            with open(args.sample_response, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            print(json.dumps({"_error": f"Sample response file not found: {args.sample_response}"}))
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(json.dumps({"_error": f"Invalid JSON in sample response file: {e}"}))
            sys.exit(1)

    if args.sample_json:
        try:
            return json.loads(args.sample_json)
        except json.JSONDecodeError as e:
            print(json.dumps({"_error": f"Invalid inline JSON: {e}"}))
            sys.exit(1)

    return None


def main() -> int:
    args = parse_args()
    sample_response = load_sample_response(args)

    result = generate_test_cases(
        endpoint=args.endpoint,
        method=args.method,
        sample_response=sample_response,
    )

    indent = 2 if (args.pretty and not args.compact) else None
    output = json.dumps(result, indent=indent, ensure_ascii=False)

    if args.output_file:
        try:
            with open(args.output_file, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"Output written to: {args.output_file}")
        except OSError as e:
            print(json.dumps({"_error": f"Failed to write output file: {e}"}))
            return 1
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
