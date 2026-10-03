"""Config API integration tests."""

import pytest
import yaml

pytestmark = pytest.mark.integration


def test_get_config(api):
    r = api.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "content" in data
    # Should be valid YAML containing key sections
    assert "mqtt" in data["content"] or "images" in data["content"]


def test_get_config_schema(api):
    r = api.get("/api/config/schema.json")
    assert r.status_code == 200
    schema = r.json()
    assert "properties" in schema


def test_save_invalid_yaml(api):
    r = api.post("/api/config/save", json={
        "content": "invalid: yaml: [missing bracket",
        "save_option": "saveonly",
    })
    assert r.status_code == 400
    data = r.json()
    assert data["success"] is False


def test_config_roundtrip(api):
    """Read config, save it back, verify no semantic data loss."""
    r = api.get("/api/config")
    assert r.status_code == 200
    original = r.json()["content"]

    r = api.post("/api/config/save", json={
        "content": original,
        "save_option": "saveonly",
    })
    assert r.status_code == 200
    assert r.json()["success"] is True

    # Verify content is semantically unchanged
    # (ruamel.yaml may normalize indentation and null representation)
    r = api.get("/api/config")
    saved = r.json()["content"]

    original_parsed = yaml.safe_load(original)
    saved_parsed = yaml.safe_load(saved)
    assert original_parsed == saved_parsed
