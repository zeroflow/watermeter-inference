"""Smoke tests: verify the service is up and serving pages."""

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_health_endpoint(api):
    r = api.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


def test_status_endpoint(api):
    r = api.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    # Should have core state fields
    assert "processing" in data or "status" in data


def test_unauthenticated_api_is_refused(base_url):
    """Without a session cookie /api is refused with JSON, but /health stays open."""
    r = httpx.get(f"{base_url}/api/status", timeout=10)
    assert r.status_code == 401
    assert r.json()["success"] is False

    r = httpx.get(f"{base_url}/health", timeout=10)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_unauthenticated_page_redirects_to_login(base_url):
    """Without a session cookie a page sends the browser to the login page."""
    r = httpx.get(f"{base_url}/training", follow_redirects=False, timeout=10)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_dashboard_page(api):
    r = api.get("/", follow_redirects=False)
    if r.status_code == 303:
        # A container without a reference image starts the first-run wizard (not an auth redirect)
        location = r.headers["location"]
        assert not location.startswith(("/login", "/setup")), f"auth redirect: {location}"
        assert location.startswith("/roi-config")
        return
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_training_page(api):
    r = api.get("/training")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_training_status_endpoint(api):
    r = api.get("/api/training/status")
    assert r.status_code == 200
    data = r.json()
    assert "training" in data
    assert "benchmark" in data
    assert "queue" in data
