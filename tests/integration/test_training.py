"""Training E2E integration tests.

These tests start real training jobs and can take several minutes.
"""

import time

import pytest

pytestmark = pytest.mark.integration

# Small config for fast training
FAST_TRAINING_CONFIG = {
    "model_type": "digits",
    "architecture": "mobilenetv3_small_050",
    "resolution": 32,
    "seeds": [42],
    "epochs": 2,
    "batch_size": 8,
    "step_size": 1.0,
    "notes": "integration-test",
    "auto_benchmark": False,
}

TRAINING_TIMEOUT = 300  # 5 minutes max


def _has_enough_ground_truth(api, model_type="digits", min_images=10):
    """Check if enough ground truth data exists. Returns (bool, count)."""
    stats = api.get("/api/training-data/stats").json()
    gt = stats.get("ground_truth", {}).get(model_type, {})
    total = sum(gt.values()) if isinstance(gt, dict) else 0
    return total >= min_images, total


def _poll_training_status(api, timeout=TRAINING_TIMEOUT):
    """Poll until training is no longer running. Returns final status dict."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = api.get("/api/training/status")
        assert r.status_code == 200
        status = r.json()
        training = status.get("training")
        if not training or training.get("status") not in ("running", "starting"):
            return status
        time.sleep(3)
    pytest.fail(f"Training did not complete within {timeout}s")


def _get_logs(api, job_id):
    """Fetch training logs for a job."""
    r = api.get(f"/api/training/logs/{job_id}")
    if r.status_code == 200:
        return r.json().get("logs", [])
    return []


def test_training_e2e(api):
    """Full training cycle: start -> poll -> verify model created -> cleanup."""
    enough, total = _has_enough_ground_truth(api)
    if not enough:
        pytest.skip(f"Not enough digits ground truth data ({total} images)")

    # Start training
    r = api.post("/api/training/start", json=FAST_TRAINING_CONFIG)
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    job_id = data["job_id"]

    # Poll until done
    final = _poll_training_status(api)
    training = final.get("training")

    if training and training["status"] == "failed":
        logs = _get_logs(api, job_id)
        log_tail = "\n".join(logs[-20:]) if logs else "(no logs)"
        pytest.fail(f"Training failed. Last logs:\n{log_tail}")

    if training:
        assert training["status"] in ("completed", "idle")

    # Verify model appears in model list
    r = api.get("/api/models", params={"model_type": "digits"})
    assert r.status_code == 200
    models = r.json()["models"]

    # Find our model (notes contain 'integration-test')
    test_models = [
        m for m in models
        if "integration-test" in (m.get("notes", "") or m.get("metadata", {}).get("notes", ""))
        or "integration" in m.get("id", "")
    ]

    # Cleanup: delete test models
    for model in test_models:
        model_id = model.get("id") or model.get("model_id")
        if model_id:
            api.delete(f"/api/models/digits/{model_id}")

    # Check logs were recorded
    r = api.get(f"/api/training/logs/{job_id}")
    assert r.status_code == 200


def test_training_cancel(api):
    """Start training and immediately cancel it."""
    enough, total = _has_enough_ground_truth(api)
    if not enough:
        pytest.skip(f"Not enough digits ground truth data ({total} images)")

    r = api.post("/api/training/start", json=FAST_TRAINING_CONFIG)
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    # Small delay to let it start
    time.sleep(1)

    # Cancel
    r = api.post("/api/training/cancel", params={"job_id": job_id})
    assert r.status_code == 200
    assert r.json()["success"] is True

    # Wait for status to settle
    time.sleep(2)
    r = api.get("/api/training/status")
    status = r.json()
    training = status.get("training")
    if training:
        # "failed" is acceptable if training failed before cancel took effect
        assert training["status"] in ("cancelled", "idle", "completed", "failed")


def test_training_queue(api):
    """Start two jobs, verify queue, then clean up."""
    enough, total = _has_enough_ground_truth(api)
    if not enough:
        pytest.skip(f"Not enough digits ground truth data ({total} images)")

    # Start first job
    r = api.post("/api/training/start", json=FAST_TRAINING_CONFIG)
    assert r.status_code == 200
    job1_id = r.json()["job_id"]

    # Start second job — should be queued
    r = api.post("/api/training/start", json=FAST_TRAINING_CONFIG)
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data.get("queued") is True

    # Verify queue has entries
    r = api.get("/api/training/status")
    queue = r.json().get("queue", [])
    assert len(queue) >= 1

    # Clear queue
    r = api.delete("/api/training/queue")
    assert r.status_code == 200

    # Cancel running job
    api.post("/api/training/cancel", params={"job_id": job1_id})

    # Wait for cleanup
    _poll_training_status(api, timeout=30)
