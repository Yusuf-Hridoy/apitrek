"""
Unit tests for the guarded static-file serving in web.app.

A zero-byte CSS/JS file (half-written deploy) must never be served: the
browser would cache an unstyled page. The guard answers 503 so the next
load retries instead.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.app import GuardedStaticFiles


def _app_with_static(tmp_path):
    (tmp_path / "style.css").write_text("")           # empty — mid-deploy state
    (tmp_path / "app.js").write_text("console.log(1)")  # healthy file
    app = FastAPI()
    app.mount("/static", GuardedStaticFiles(directory=str(tmp_path)))
    return TestClient(app)


def test_empty_static_file_returns_503_not_200(tmp_path):
    client = _app_with_static(tmp_path)
    res = client.get("/static/style.css")
    assert res.status_code == 503


def test_healthy_static_file_served_normally(tmp_path):
    client = _app_with_static(tmp_path)
    res = client.get("/static/app.js")
    assert res.status_code == 200
    assert "console.log" in res.text


def test_missing_file_still_404(tmp_path):
    client = _app_with_static(tmp_path)
    assert client.get("/static/nope.css").status_code == 404
