"""Unit test conftest - mocks heavy dependencies so app.py can be imported without Docker."""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

# ---- Mock heavy modules BEFORE importing app ----
# These are not available on the host (only in Docker), or have module-level
# side effects (inference.py reads config.yaml at import time).

_MOCK_MODULES = [
    'cv2',
    'numpy',
    'paho',
    'paho.mqtt',
    'paho.mqtt.client',
    'openvino',
    'openvino.runtime',
]


def _install_mock_modules():
    """Install mock modules into sys.modules so imports don't fail."""
    for mod_name in _MOCK_MODULES:
        if mod_name not in sys.modules:
            mock_mod = MagicMock(spec=ModuleType)
            mock_mod.__name__ = mod_name
            sys.modules[mod_name] = mock_mod

    # numpy needs special attributes
    np_mock = sys.modules['numpy']
    np_mock.ndarray = MagicMock
    np_mock.float32 = float
    np_mock.uint8 = int


_install_mock_modules()

# Mock inference module (has module-level init that reads config.yaml + loads OpenVINO models)
if 'inference' not in sys.modules:
    _inference_mock = MagicMock(spec=ModuleType)
    _inference_mock.__name__ = 'inference'
    _inference_mock.get_inference_service = MagicMock(return_value=MagicMock())
    sys.modules['inference'] = _inference_mock

# Mock watermeter_service module (imports inference, cv2, paho.mqtt, etc.)
if 'watermeter_service' not in sys.modules:
    _ws_mock = MagicMock(spec=ModuleType)
    _ws_mock.__name__ = 'watermeter_service'
    _ws_mock.get_service = MagicMock()
    sys.modules['watermeter_service'] = _ws_mock


@pytest.fixture
def mock_service():
    """Mock WatermeterService for API tests."""
    service = MagicMock()
    service.current_state = {
        'processing': False,
        'last_update': '2025-06-01T12:00:00',
        'value': 123.456,
        'previous_value': 123.400,
    }
    service.config = {
        'low_confidence': {'save_path': '/training'},
        'images': {
            'process_separate': False,
            'digits': ['digit_1', 'digit_2'],
            'arrows': ['analog_1'],
        },
        'plausibility': {'enable_consistency_check': True},
    }
    service.mqtt_client = MagicMock()
    service.mqtt_client.is_connected.return_value = True
    return service


@pytest.fixture
def test_client(mock_service, tmp_path, monkeypatch):
    """FastAPI TestClient with mocked services.

    Provides a working test client where all heavy services are mocked.
    Uses tmp_path for config file operations.
    """
    # Change working directory to tmp_path so config.yaml operations work
    monkeypatch.chdir(tmp_path)

    # Create minimal templates and static dirs for app startup
    (tmp_path / "templates").mkdir()
    (tmp_path / "static").mkdir()

    # Create minimal template stubs
    for tmpl in ["dashboard.html", "status_fragment.html", "label.html",
                 "roi_config.html", "config_editor.html", "training.html"]:
        (tmp_path / "templates" / tmpl).write_text("<html>{{ request.url }}</html>")

    # Create a config.yaml
    config_yaml = (
        "images:\n  digits: [digit_1]\n  arrows: [analog_1]\n"
        "mqtt:\n  broker: localhost\n  port: 1883\n"
        "inference:\n  confidence_threshold: 0.6\n"
    )
    (tmp_path / "config.yaml").write_text(config_yaml)

    # Wire the mock service into the watermeter_service mock module
    sys.modules['watermeter_service'].get_service = MagicMock(return_value=mock_service)
    sys.modules['inference'].get_inference_service = MagicMock(return_value=MagicMock())

    # Import app after mocking (deferred to avoid import errors at collection time)
    import app as app_module

    # Patch the singletons at the app module level too
    monkeypatch.setattr(app_module, "get_service", lambda: mock_service)
    monkeypatch.setattr(app_module, "get_inference_service", lambda: MagicMock())

    from fastapi.testclient import TestClient

    # Use TestClient without lifespan to avoid MQTT startup
    client = TestClient(app_module.app, raise_server_exceptions=False)
    return client
