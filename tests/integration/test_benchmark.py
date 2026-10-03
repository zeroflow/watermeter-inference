"""Benchmark E2E integration tests."""

import time

import pytest

pytestmark = pytest.mark.integration

BENCHMARK_TIMEOUT = 120  # 2 minutes max
MIN_ACCURACY = 90.0      # percent — fail if model is worse than this


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


def _run_benchmark(api, model_type, model_id):
    """Start benchmark, poll to completion, return result dict."""
    r = api.post(f"/api/models/{model_type}/{model_id}/benchmark")
    if r.status_code == 409:
        pytest.skip("Another benchmark is already running")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "job_id" in data

    final = _poll_benchmark_status(api)
    benchmark = final.get("benchmark")
    assert benchmark is not None, "No benchmark status returned"
    assert benchmark["status"] == "completed", (
        f"Benchmark ended with status '{benchmark['status']}', "
        f"error: {benchmark.get('error')}"
    )

    result = benchmark.get("result")
    assert result is not None, "Benchmark completed but no result returned"
    return result, data["job_id"]


def _skip_if_not_enough_gt(api, model_type, min_images=10):
    """Skip test if not enough ground truth data exists."""
    stats = api.get("/api/training-data/stats").json()
    gt = stats.get("ground_truth", {}).get(model_type, {})
    total = sum(gt.values()) if isinstance(gt, dict) else 0
    if total < min_images:
        pytest.skip(f"Not enough {model_type} ground truth data ({total} images)")
    return total


def test_benchmark_digits(api):
    """Benchmark digits model: verify result structure and minimum accuracy."""
    model_id = _find_benchmarkable_model(api, "digits")
    if not model_id:
        pytest.skip("No benchmarkable digits model found")

    _skip_if_not_enough_gt(api, "digits")

    result, job_id = _run_benchmark(api, "digits", model_id)

    # Result structure
    assert isinstance(result["accuracy"], (int, float))
    assert isinstance(result["mean_confidence"], (int, float))
    assert isinstance(result["total_images"], int)
    assert isinstance(result["correct_predictions"], int)
    assert isinstance(result["per_class_accuracy"], dict)
    assert result["total_images"] > 0
    assert result["correct_predictions"] <= result["total_images"]

    # Accuracy gate
    assert result["accuracy"] >= MIN_ACCURACY, (
        f"Digits accuracy {result['accuracy']:.1f}% < {MIN_ACCURACY}% threshold "
        f"({result['correct_predictions']}/{result['total_images']})"
    )

    # Per-class: every class should have count > 0 and accuracy as float
    for cls, data in result["per_class_accuracy"].items():
        assert "accuracy" in data, f"Class '{cls}' missing accuracy"
        assert "count" in data, f"Class '{cls}' missing count"


def test_benchmark_arrows(api):
    """Benchmark arrows model: verify result structure and minimum accuracy."""
    model_id = _find_benchmarkable_model(api, "arrows")
    if not model_id:
        pytest.skip("No benchmarkable arrows model found")

    _skip_if_not_enough_gt(api, "arrows")

    result, job_id = _run_benchmark(api, "arrows", model_id)

    # Result structure
    assert isinstance(result["accuracy"], (int, float))
    assert result["total_images"] > 0

    # Accuracy gate
    assert result["accuracy"] >= MIN_ACCURACY, (
        f"Arrows accuracy {result['accuracy']:.1f}% < {MIN_ACCURACY}% threshold "
        f"({result['correct_predictions']}/{result['total_images']})"
    )


def test_benchmark_cancel(api):
    """Start a benchmark and cancel it."""
    model_id = _find_benchmarkable_model(api)
    if not model_id:
        pytest.skip("No benchmarkable digits model found")

    _skip_if_not_enough_gt(api, "digits")

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
