"""Unit test conftest - mocks heavy dependencies so app.py can be imported without Docker."""

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---- Mock heavy modules BEFORE importing app ----
# These are not available on the host (only in Docker), or have module-level
# side effects (inference.py reads config.yaml at import time).

_MOCK_MODULES = [
    'cv2',
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

    # Note: numpy is NOT mocked - it's available in the venv and torch/timm need it at import time


_install_mock_modules()

# Mock inference module (has module-level init that reads config.yaml + loads OpenVINO models)
for _inf_name in ['inference', 'watermeter.inference']:
    if _inf_name not in sys.modules:
        _inference_mock = MagicMock(spec=ModuleType)
        _inference_mock.__name__ = _inf_name
        _inference_mock.get_inference_service = MagicMock(return_value=MagicMock())
        sys.modules[_inf_name] = _inference_mock

# Mock watermeter_service module (imports inference, cv2, paho.mqtt, etc.)
for _ws_name in ['watermeter_service', 'watermeter.watermeter_service']:
    if _ws_name not in sys.modules:
        _ws_mock = MagicMock(spec=ModuleType)
        _ws_mock.__name__ = _ws_name
        _ws_mock.get_service = MagicMock()
        sys.modules[_ws_name] = _ws_mock


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

    # Create minimal templates and static dirs for test
    templates_dir = tmp_path / "templates"
    static_dir = tmp_path / "static"
    templates_dir.mkdir()
    static_dir.mkdir()

    # Create minimal template stubs
    for tmpl in ["dashboard.html", "status_fragment.html", "label.html",
                 "roi_config.html", "config_editor.html", "training.html"]:
        (templates_dir / tmpl).write_text("<html>{{ request.url }}</html>")

    # Create a config.yaml
    config_yaml = (
        "images:\n  digits: [digit_1]\n  arrows: [analog_1]\n"
        "mqtt:\n  broker: localhost\n  port: 1883\n"
        "inference:\n  confidence_threshold: 0.6\n"
    )
    (tmp_path / "config.yaml").write_text(config_yaml)

    # Wire the mock service into the watermeter_service mock module
    for _ws_name in ['watermeter_service', 'watermeter.watermeter_service']:
        if _ws_name in sys.modules:
            sys.modules[_ws_name].get_service = MagicMock(return_value=mock_service)
    for _inf_name in ['inference', 'watermeter.inference']:
        if _inf_name in sys.modules:
            sys.modules[_inf_name].get_inference_service = MagicMock(return_value=MagicMock())

    # Import app after mocking (deferred to avoid import errors at collection time)
    import watermeter.app as app_module
    import watermeter.routes.pages as pages_module

    # Override template/static dirs to use test stubs
    monkeypatch.setattr(pages_module, "templates", Jinja2Templates(directory=str(templates_dir)))
    app_module.app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    from fastapi.testclient import TestClient

    # Use TestClient without lifespan to avoid MQTT startup
    client = TestClient(app_module.app, raise_server_exceptions=False)
    return client
