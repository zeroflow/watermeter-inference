"""Smoke tests: verify the service is up and serving pages."""

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


def test_dashboard_page(api):
    r = api.get("/")
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
