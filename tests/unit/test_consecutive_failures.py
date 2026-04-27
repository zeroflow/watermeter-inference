"""Tests for consecutive-failure tracking and STALE pipeline notification (Task 4).

Covers the threshold-crossing edge trigger of `_notify_stale`, the latch that
prevents re-firing on every subsequent failure, and the recovery reset on the
first successful alignment.

Follows the same sys.modules dance as `test_service_fail_closed.py` and
`test_derive_pipeline_status.py` so we can import the real `WatermeterService`
class instead of the conftest mock.
"""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

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

from watermeter.image_pipeline import AlignmentResult  # noqa: E402, I001
from watermeter.meter_state import MeterState  # noqa: E402, I001
from watermeter.watermeter_service import WatermeterService  # noqa: E402, I001


def make_failing_service(*, max_failures=10):
    """Construct a bare WatermeterService whose alignment always fails.

    Bypasses __init__ to avoid all the heavy wiring; only the fields required by
    `process_reading`'s alignment-failure branch are populated.
    """
    svc = object.__new__(WatermeterService)
    svc.config = {
        "images": {"process_separate": False},
        "alignment": {"max_consecutive_failures": max_failures},
        "inference": {"confidence_threshold": 0.6},
        "low_confidence": {"save_path": "/tmp/lc"},
        "plausibility": {},
        "homeassistant": {"enabled": False, "publish_topic": "watermeter/state"},
        "detection": {"digits": {"rois": [{"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.1}]}},
    }

    # MeterState container — use the real init so all fields are populated
    svc._state = MeterState(ha_publish_enabled=False)

    # _pending_confirmation forwards to ConfirmationManager; mock it
    svc._confirmation_manager = MagicMock()
    svc._confirmation_manager._pending_confirmation = None

    svc.processing_lock = asyncio.Lock()
    svc._failure_store = MagicMock()
    svc._metrics = MagicMock()  # PipelineMetrics mocked (Task 5)
    svc.consecutive_alignment_failures = 0
    svc._stale_notified = False

    # Pipeline returns (None, failing AlignmentResult)
    fail_align = AlignmentResult(
        success=False,
        error_reason="low_confidence",
        failed_marker=1,
        marker_confidences=[0.31, 0.85],
    )
    svc._image_pipeline = MagicMock()
    svc._image_pipeline.process_whole_image = MagicMock(return_value=(None, fail_align))

    # I/O hooks (alignment failure short-circuits before inference)
    svc.fetch_whole_image = AsyncMock(return_value=b"fake jpeg bytes")
    svc.publish_to_mqtt = AsyncMock()
    svc.run_inference = AsyncMock(side_effect=AssertionError("inference must not run on alignment failure"))
    svc._archive_raw_image = MagicMock()

    # MQTT client placeholder — _notify_stale's no-MQTT branch will short-circuit
    # on its own. Tests that need to assert against the publish call set this.
    svc._mqtt = MagicMock()
    svc._mqtt.mqtt_client = None
    return svc


def test_consecutive_failures_counter_increments():
    """Three consecutive alignment failures advance the counter and produce FAILED."""
    svc = make_failing_service()
    asyncio.run(svc.process_reading())
    asyncio.run(svc.process_reading())
    asyncio.run(svc.process_reading())
    assert svc.consecutive_alignment_failures == 3
    # 3 < default max_failures=10 → not STALE; alignment_failed → FAILED.
    assert svc.current_state["pipeline_status"] == "FAILED"


def test_state_transitions_to_stale_at_threshold():
    """When the counter reaches the configured threshold, status becomes STALE."""
    svc = make_failing_service(max_failures=5)
    for _ in range(5):
        asyncio.run(svc.process_reading())
    assert svc.consecutive_alignment_failures == 5
    assert svc.current_state["pipeline_status"] == "STALE"


def test_stale_triggers_mqtt_notification_once():
    """Notification fires exactly once on the boundary, not on every subsequent failure."""
    svc = make_failing_service(max_failures=3)
    svc._notify_stale = MagicMock()
    for _ in range(5):
        asyncio.run(svc.process_reading())
    # 5 failures, threshold=3 → notification fires once when counter hits 3.
    assert svc._notify_stale.call_count == 1


def test_first_success_resets_counter_and_clears_stale():
    """A successful reading resets the counter and clears the STALE latch."""
    svc = make_failing_service(max_failures=3)
    # 4 failures → STALE (and notification fired once internally)
    for _ in range(4):
        asyncio.run(svc.process_reading())
    assert svc.current_state["pipeline_status"] == "STALE"
    assert svc._stale_notified is True

    # Flip the pipeline to succeed
    success_align = AlignmentResult(
        success=True,
        image=MagicMock(),
        marker_confidences=[0.95, 0.91],
    )
    fake_images = {"d1": (b"...", "digits")}
    svc._image_pipeline.process_whole_image = MagicMock(return_value=(fake_images, success_align))
    svc.run_inference = AsyncMock(
        return_value={"d1": {"id": "d1", "class": "7", "confidence": 0.99, "model": "digits", "image_bytes": b"..."}}
    )
    svc.calculate_total = MagicMock(return_value=(1.234, {"digits": [7], "arrows": []}))
    svc.correct_predictions = MagicMock(return_value=[])
    svc.check_consistency = MagicMock(return_value=[])
    svc.validate_plausibility = MagicMock(return_value=(True, []))
    svc._check_sustained_consumption = MagicMock(return_value=None)
    svc.save_low_confidence = AsyncMock()
    svc._compute_raw_total = MagicMock(return_value=1.234)
    svc._should_request_confirmation = MagicMock(return_value=None)
    # No data collector / state store / arrow ids needed for the path; stub them out.
    svc._data_collector = None
    svc._state_store = None
    svc._rate_tracker = MagicMock()

    asyncio.run(svc.process_reading())
    assert svc.consecutive_alignment_failures == 0
    assert svc._stale_notified is False
    assert svc.current_state["pipeline_status"] in ("OK", "DEGRADED")


def test_threshold_raised_mid_incident_re_arms_latch():
    """If the threshold is raised while the latch is set, a future STALE crossing must re-notify."""
    svc = make_failing_service(max_failures=3)
    svc._notify_stale = MagicMock()

    # Cross threshold at 3, latch fires
    asyncio.run(svc.process_reading())
    asyncio.run(svc.process_reading())
    asyncio.run(svc.process_reading())
    assert svc._stale_notified is True
    assert svc._notify_stale.call_count == 1

    # Operator raises threshold to 10 mid-incident
    svc.config["alignment"]["max_consecutive_failures"] = 10

    # Next failure: counter=4, status no longer STALE → latch should clear
    asyncio.run(svc.process_reading())
    # Latch was cleared by _derive_pipeline_status because pipeline_status != "STALE"
    assert svc._stale_notified is False

    # Continue failing until the new threshold is crossed
    for _ in range(6):  # 4 + 6 = 10
        asyncio.run(svc.process_reading())
    # New STALE crossing: notification fires again
    assert svc._notify_stale.call_count == 2
    assert svc._stale_notified is True


# ---------------------------------------------------------------------------
# Restore conftest mocks so subsequent test files in the same session see them.
# ---------------------------------------------------------------------------
import watermeter as _wm_pkg  # noqa: E402

for _ws_name, _ws_mock in _saved_ws_mocks.items():
    sys.modules[_ws_name] = _ws_mock
    _attr = _ws_name.split(".")[-1]
    if hasattr(_wm_pkg, _attr):
        setattr(_wm_pkg, _attr, _ws_mock)
