"""Unit tests for WatermeterService._derive_pipeline_status (Issue #2).

Covers the service-side derivation of pipeline_status, is_live, and the
last_valid_reading_age_seconds field — the exact code path that the template
tests in test_status_fragment.py bypass by passing values directly.

Follows the same sys.modules dance as test_service_fail_closed.py so we can
import the real WatermeterService class instead of the conftest mock. Heavy
modules (cv2/openvino/paho/inference) are mocked at sys.modules so the
module-level imports succeed.
"""

import sys
from datetime import datetime, timedelta
from types import ModuleType
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Save conftest mocks that we will temporarily replace, so we can restore them
# after importing the real watermeter_service.
# ---------------------------------------------------------------------------
_saved_ws_mocks = {}
for _ws_name in ["watermeter_service", "watermeter.watermeter_service", "watermeter.image_pipeline"]:
    if _ws_name in sys.modules:
        _saved_ws_mocks[_ws_name] = sys.modules[_ws_name]

# Mock heavy modules that watermeter_service imports at module level.
_modules_to_mock = [
    "paho",
    "paho.mqtt",
    "paho.mqtt.client",
    "openvino",
    "openvino.runtime",
]
for _mod in _modules_to_mock:
    if _mod not in sys.modules or isinstance(sys.modules[_mod], MagicMock):
        mock_mod = MagicMock()
        mock_mod.__name__ = _mod
        sys.modules[_mod] = mock_mod

# Mock watermeter.inference (module-level init loads OpenVINO models)
for _inf_name in ["inference", "watermeter.inference"]:
    if _inf_name not in sys.modules or isinstance(sys.modules[_inf_name], MagicMock):
        _inf_mock = MagicMock()
        _inf_mock.__name__ = _inf_name
        _svc = MagicMock()
        _svc.models_loaded = True
        _inf_mock.get_inference_service = MagicMock(return_value=_svc)
        sys.modules[_inf_name] = _inf_mock

if "cv2" not in sys.modules:
    cv2_mock = MagicMock(spec=ModuleType)
    cv2_mock.__name__ = "cv2"
    sys.modules["cv2"] = cv2_mock

# Remove conftest's full mocks of watermeter_service / image_pipeline so we can
# import the real ones.
for _ws_name in ["watermeter_service", "watermeter.watermeter_service", "watermeter.image_pipeline"]:
    if _ws_name in sys.modules:
        del sys.modules[_ws_name]

from watermeter.watermeter_service import WatermeterService  # noqa: E402, I001
from watermeter.meter_state import MeterState  # noqa: E402, I001


def _make_service(**overrides):
    """Construct a bare WatermeterService instance with only the fields needed
    for status derivation. Bypasses __init__ to avoid all the heavy wiring.
    """
    svc = object.__new__(WatermeterService)
    svc.config = {"alignment": {"max_consecutive_failures": 10}}
    svc.consecutive_alignment_failures = 0
    # current_state is a property forwarding to self._state.current_state, so
    # we must initialize _state with a real MeterState.
    svc._state = MeterState(ha_publish_enabled=False)
    svc.current_state["status"] = "idle"
    svc.current_state["warnings"] = []

    for k, v in overrides.items():
        if k == "current_state":
            svc.current_state.update(v)
        else:
            setattr(svc, k, v)
    return svc


def test_derive_status_ok_when_no_failures():
    svc = _make_service()
    svc._derive_pipeline_status()
    assert svc.current_state["pipeline_status"] == "OK"
    assert svc.current_state["is_live"] is True


def test_derive_status_failed_on_alignment_failed():
    svc = _make_service(current_state={"status": "alignment_failed"})
    svc._derive_pipeline_status()
    assert svc.current_state["pipeline_status"] == "FAILED"
    assert svc.current_state["is_live"] is False


def test_derive_status_failed_on_inference_failed():
    svc = _make_service(current_state={"status": "inference_failed"})
    svc._derive_pipeline_status()
    assert svc.current_state["pipeline_status"] == "FAILED"


def test_derive_status_failed_on_generic_error():
    """Catch-all exception path sets status='error' which must surface as FAILED."""
    svc = _make_service(current_state={"status": "error"})
    svc._derive_pipeline_status()
    assert svc.current_state["pipeline_status"] == "FAILED"
    assert svc.current_state["is_live"] is False


def test_derive_status_rejected_on_plausibility_rejection():
    """A plausibility rejection is not a pipeline failure: alignment and inference worked."""
    svc = _make_service(current_state={"status": "rejected"})
    svc._derive_pipeline_status()
    assert svc.current_state["pipeline_status"] == "REJECTED"
    assert svc.current_state["is_live"] is False


def test_derive_status_degraded_on_partial_failures():
    svc = _make_service(consecutive_alignment_failures=3, current_state={"status": "ok"})
    svc._derive_pipeline_status()
    assert svc.current_state["pipeline_status"] == "DEGRADED"
    assert svc.current_state["is_live"] is False


def test_derive_status_stale_at_threshold():
    svc = _make_service(consecutive_alignment_failures=10, current_state={"status": "alignment_failed"})
    svc._derive_pipeline_status()
    assert svc.current_state["pipeline_status"] == "STALE"
    assert svc.current_state["is_live"] is False


def test_age_seconds_computed_from_iso_timestamp():
    """The HIGH-priority spec gap fix: the helper must read last_published_iso (full ISO)."""
    five_min_ago = (datetime.now() - timedelta(minutes=5)).isoformat()
    svc = _make_service(current_state={"last_published_iso": five_min_ago, "status": "ok"})
    svc._derive_pipeline_status()
    age = svc.current_state["last_valid_reading_age_seconds"]
    assert age is not None
    assert 295 <= age <= 305  # ~300s, allow a few seconds slack


def test_age_seconds_none_when_no_published_iso():
    svc = _make_service()
    svc._derive_pipeline_status()
    assert svc.current_state["last_valid_reading_age_seconds"] is None


def test_age_seconds_handles_corrupt_iso_gracefully():
    """If last_published_iso is somehow malformed, fall back to None instead of raising."""
    svc = _make_service(current_state={"last_published_iso": "not-an-iso-timestamp"})
    svc._derive_pipeline_status()
    assert svc.current_state["last_valid_reading_age_seconds"] is None


def test_max_consecutive_failures_config_overrides_default():
    svc = _make_service(consecutive_alignment_failures=5)
    svc.config["alignment"]["max_consecutive_failures"] = 5
    svc.current_state["status"] = "alignment_failed"
    svc._derive_pipeline_status()
    assert svc.current_state["pipeline_status"] == "STALE"


def test_max_consecutive_failures_default_is_10():
    """When the config key is missing, default to 10 (Task 4 will set it explicitly)."""
    svc = _make_service(consecutive_alignment_failures=9, current_state={"status": "alignment_failed"})
    svc.config = {}  # No alignment.max_consecutive_failures
    svc._derive_pipeline_status()
    # 9 is below default 10 -> not STALE
    assert svc.current_state["pipeline_status"] == "FAILED"

    svc.consecutive_alignment_failures = 10
    svc._derive_pipeline_status()
    assert svc.current_state["pipeline_status"] == "STALE"


# ---------------------------------------------------------------------------
# Restore conftest mocks so subsequent test files in the same session see them.
# ---------------------------------------------------------------------------
import watermeter as _wm_pkg  # noqa: E402

for _ws_name, _ws_mock in _saved_ws_mocks.items():
    sys.modules[_ws_name] = _ws_mock
    _attr = _ws_name.split(".")[-1]
    if hasattr(_wm_pkg, _attr):
        setattr(_wm_pkg, _attr, _ws_mock)
