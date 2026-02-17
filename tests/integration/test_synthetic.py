"""Integration tests for synthetic data generation."""

import time

import pytest

pytestmark = pytest.mark.integration


def test_synthetic_status_idle(api):
    """Status endpoint returns idle when nothing is running."""
    response = api.get("/api/synthetic/status")
    assert response.status_code == 200
    data = response.json()
    assert "running" in data
    assert "message" in data


def test_synthetic_generation_digits(api):
    """Generate a small number of digit images and verify completion."""
    # Delete any existing synthetic data first
    api.delete("/api/synthetic/digits")

    # Start generation with minimal count
    response = api.post("/api/synthetic/generate", json={
        "type": "digits",
        "count_per_class": 2,
        "seed": 42,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "job_id" in data

    # Poll until done (max 120s — fisheye transforms are slow)
    for _ in range(120):
        status = api.get("/api/synthetic/status").json()
        if not status["running"]:
            break
        time.sleep(1)
    else:
        pytest.fail("Generation did not complete in 120s")

    assert "Complete" in status["message"]

    # Clean up
    response = api.delete("/api/synthetic/digits")
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == 20  # 10 classes * 2 images


def test_synthetic_cannot_run_twice(api):
    """Cannot start two generations simultaneously."""
    # Use a large enough count so generation is still running when we send
    # the second request. Digits are fast (no fisheye), so use 500 per class
    # (5000 total images) to ensure the job takes a few seconds.
    response1 = api.post("/api/synthetic/generate", json={
        "type": "digits",
        "count_per_class": 500,
        "seed": 42,
    })
    assert response1.status_code == 200

    # Verify the job is actually running before attempting the second request
    status = api.get("/api/synthetic/status").json()
    if not status["running"]:
        # Job finished instantly — skip this test as we cannot reliably test
        # the concurrency guard when generation is too fast
        api.delete("/api/synthetic/digits")
        pytest.skip("Generation completed too quickly to test concurrency guard")

    # Try second immediately — should be rejected
    response2 = api.post("/api/synthetic/generate", json={
        "type": "digits",
        "count_per_class": 1,
        "seed": 42,
    })
    assert response2.status_code == 409

    # Wait for first to finish before next test
    for _ in range(120):
        status = api.get("/api/synthetic/status").json()
        if not status["running"]:
            break
        time.sleep(1)

    # Clean up
    api.delete("/api/synthetic/digits")


def test_synthetic_delete_nonexistent(api):
    """Deleting when no synthetic data exists returns 0."""
    # First ensure nothing exists
    api.delete("/api/synthetic/digits")
    # Delete again
    response = api.delete("/api/synthetic/digits")
    assert response.status_code == 200
    assert response.json()["deleted"] == 0


def test_synthetic_invalid_type_on_delete(api):
    """Invalid type on delete returns 400."""
    response = api.delete("/api/synthetic/invalid_type")
    assert response.status_code == 400
