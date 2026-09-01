"""
Lightweight checks on static UI copy that are hard to verify manually.
"""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_degraded_banner_does_not_advertise_failure():
    app_js = PROJECT_ROOT / "web" / "static" / "app.js"
    text = app_js.read_text()

    # Extract the innerHTML assignment block for the degraded banner.
    match = re.search(
        r"renderDegradedBanner\(data\)\s*\{.*?if\s*\(data\._degraded\)\s*\{(.*?)\}\s*else\s*\{",
        text,
        re.S,
    )
    assert match, "Could not locate degraded banner rendering block"
    banner_block = match.group(1)

    assert "_degraded" in banner_block or "innerHTML" in banner_block
    assert "unavailable" not in banner_block.lower()
    assert "failed" not in banner_block.lower()
