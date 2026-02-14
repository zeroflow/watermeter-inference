"""Benchmark E2E integration tests."""

import time

import pytest

pytestmark = pytest.mark.integration

BENCHMARK_TIMEOUT = 120  # 2 minutes max


def _find_benchmarkable_model(api, model_type="digits"):
    """Find a model that can be benchmarked (has .xml file)."""
    r = api.get("/api/models", params={"model_type": model_type})
    if r.status_code != 200:
        return None
    models = r.json().get("models", [])
    for m in models:
        if m.get("files_exist") or m.get("status") in ("active", "ready", None):
            return m.get("id") or m.get("model_id")
    return None


def _poll_benchmark_status(api, timeout=BENCHMARK_TIMEOUT):
    """Poll until benchmark is no longer running. Returns final status dict."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = api.get("/api/training/status")
        assert r.status_code == 200
        status = r.json()
        benchmark = status.get("benchmark")
        if not benchmark or benchmark.get("status") not in ("running", "starting"):
            return status
        time.sleep(2)
    pytest.fail(f"Benchmark did not complete within {timeout}s")


def test_benchmark_e2e(api):
    """Run benchmark on an existing model and verify results."""
    model_id = _find_benchmarkable_model(api)
    if not model_id:
        pytest.skip("No benchmarkable digits model found")

    # Check ground truth exists
    stats = api.get("/api/training-data/stats").json()
    gt = stats.get("ground_truth", {}).get("digits", {})
    total = sum(gt.values()) if isinstance(gt, dict) else 0
    if total < 10:
        pytest.skip(f"Not enough ground truth data for benchmark ({total} images)")

    # Start benchmark
    r = api.post(f"/api/models/digits/{model_id}/benchmark")
    if r.status_code == 409:
        pytest.skip("Another benchmark is already running")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "job_id" in data

    # Poll until done
    final = _poll_benchmark_status(api)
    benchmark = final.get("benchmark")
    if benchmark:
        assert benchmark["status"] in ("completed", "idle"), (
            f"Benchmark ended with: {benchmark['status']}"
        )
        # If completed, check for accuracy results
        if benchmark["status"] == "completed":
            result = benchmark.get("result", {})
            assert "accuracy" in result or "overall_accuracy" in result or result


def test_benchmark_cancel(api):
    """Start a benchmark and cancel it."""
    model_id = _find_benchmarkable_model(api)
    if not model_id:
        pytest.skip("No benchmarkable digits model found")

    stats = api.get("/api/training-data/stats").json()
    gt = stats.get("ground_truth", {}).get("digits", {})
    total = sum(gt.values()) if isinstance(gt, dict) else 0
    if total < 10:
        pytest.skip("Not enough ground truth data")

    r = api.post(f"/api/models/digits/{model_id}/benchmark")
    if r.status_code == 409:
        pytest.skip("Another benchmark is already running")
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    time.sleep(1)

    r = api.post("/api/benchmark/cancel", params={"job_id": job_id})
    assert r.status_code == 200
    assert r.json()["success"] is True

    time.sleep(2)
    r = api.get("/api/training/status")
    benchmark = r.json().get("benchmark")
    if benchmark:
        assert benchmark["status"] in ("cancelled", "idle", "completed")
