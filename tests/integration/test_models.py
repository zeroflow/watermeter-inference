"""Model management integration tests."""

import pytest

pytestmark = pytest.mark.integration


def test_list_architectures(api):
    """Search architectures — requires q param with at least 2 chars."""
    r = api.get("/api/models/architectures", params={"q": "resnet"})
    assert r.status_code == 200
    archs = r.json()
    assert isinstance(archs, list)
    assert len(archs) > 0
    assert all(isinstance(a, str) for a in archs)


def test_list_architectures_short_query_returns_empty(api):
    """Query too short (<2 chars) returns empty list."""
    r = api.get("/api/models/architectures", params={"q": "r"})
    assert r.status_code == 200
    assert r.json() == []


def test_list_digits_models(api):
    r = api.get("/api/models", params={"model_type": "digits"})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "models" in data


def test_list_arrows_models(api):
    r = api.get("/api/models", params={"model_type": "arrows"})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "models" in data


def test_training_data_stats(api):
    r = api.get("/api/training-data/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "ground_truth" in data


def test_model_not_found(api):
    r = api.get("/api/models/digits/nonexistent_model_xyz_999")
    assert r.status_code == 404
