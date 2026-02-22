"""Integration test for the full fisheye correction workflow."""

import pytest

pytestmark = pytest.mark.integration


def test_fisheye_save_appears_in_config(api):
    """Save fisheye, then verify it shows up in GET /api/roi/config."""
    # Save
    resp = api.post("/api/roi/fisheye", json={"fisheye_correction": 0.35})
    assert resp.status_code == 200
    assert resp.json()["success"]

    # Verify in config
    resp = api.get("/api/roi/config")
    assert resp.json()["fisheye_correction"] == 0.35

    # Delete
    resp = api.delete("/api/roi/fisheye")
    assert resp.status_code == 200

    # Verify gone
    resp = api.get("/api/roi/config")
    assert resp.json()["fisheye_correction"] is None


def test_fisheye_save_zero_clears_correction(api):
    """Saving 0.0 should set fisheye_correction to 0.0 (not remove it)."""
    resp = api.post("/api/roi/fisheye", json={"fisheye_correction": 0.0})
    assert resp.status_code == 200
    assert resp.json()["success"]

    resp = api.get("/api/roi/config")
    # 0.0 is a valid value — it disables fisheye but is still persisted
    assert resp.json()["fisheye_correction"] == 0.0

    # Clean up
    api.delete("/api/roi/fisheye")


def test_fisheye_delete_idempotent(api):
    """Deleting fisheye when not set should still return 200."""
    # Ensure there is no fisheye set first
    api.delete("/api/roi/fisheye")

    # Delete again — should not error
    resp = api.delete("/api/roi/fisheye")
    assert resp.status_code == 200
    assert resp.json()["success"]


def test_fisheye_value_persisted_after_save(api):
    """Saving a non-trivial value roundtrips correctly."""
    resp = api.post("/api/roi/fisheye", json={"fisheye_correction": -0.12})
    assert resp.status_code == 200

    resp = api.get("/api/roi/config")
    saved = resp.json()["fisheye_correction"]
    # Should be rounded to 4 decimal places: -0.12 → -0.12
    assert abs(saved - (-0.12)) < 1e-4

    # Clean up
    api.delete("/api/roi/fisheye")
