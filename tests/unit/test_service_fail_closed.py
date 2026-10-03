"""Service-level tests: alignment failure must short-circuit inference + persist a record.

This file follows the same sys.modules dance as test_archive_raw_image.py so we can
import the real WatermeterService class instead of the conftest mock.  cv2/openvino/
paho/inference are still mocked at sys.modules so the module-level imports succeed.
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
        # The service early-returns if no models are loaded; force models_loaded=True
        # so we exercise the alignment short-circuit path instead.
        _svc = MagicMock()
        _svc.models_loaded = True
        _inf_mock.get_inference_service = MagicMock(return_value=_svc)
        sys.modules[_inf_name] = _inf_mock

# cv2 may be mocked by conftest; that is fine for these tests because we mock
# the whole image_pipeline. But the watermeter_service module imports cv2 at
# the top, so the mock must be present and look like a module.
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
from watermeter.image_pipeline import AlignmentResult  # noqa: E402, I001
from watermeter.meter_state import MeterState  # noqa: E402, I001


def _make_service_with_alignment_failure(alignment_result):
    """Build a WatermeterService whose pipeline always returns the given alignment failure."""
    svc = object.__new__(WatermeterService)
    # Minimal config — process_separate=False routes us through the whole-image path.
    svc.config = {
        "images": {"process_separate": False},
        "alignment": {},
        "inference": {"confidence_threshold": 0.6},
        "low_confidence": {"save_path": "/tmp/lc"},
        "plausibility": {},
        "homeassistant": {"enabled": False},
        "detection": {"digits": {"rois": [{"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.1}]}},
    }

    # MeterState container — use the real init so all fields are populated.
    svc._state = MeterState(ha_publish_enabled=False)

    # _pending_confirmation is a property forwarded to ConfirmationManager
    svc._confirmation_manager = MagicMock()
    svc._confirmation_manager._pending_confirmation = None
    svc.processing_lock = asyncio.Lock()
    svc._failure_store = MagicMock()  # FailureStore is mocked here
    svc._metrics = MagicMock()  # PipelineMetrics mocked (Task 5)
    svc.consecutive_alignment_failures = 0  # __init__ sets this; we bypass __init__ here

    # Pipeline returns (None, AlignmentResult(success=False, ...))
    svc._image_pipeline = MagicMock()
    svc._image_pipeline.process_whole_image = MagicMock(return_value=(None, alignment_result))

    # Inference shouldn't be reached; if it is, the test fails loudly
    svc.run_inference = AsyncMock(side_effect=AssertionError("inference must not run on alignment failure"))
    svc.fetch_whole_image = AsyncMock(return_value=b"fake jpeg bytes")
    svc.publish_to_mqtt = AsyncMock()
    svc._archive_raw_image = MagicMock()  # archive hook is fire-and-forget
    return svc


def test_alignment_failure_short_circuits_inference():
    align = AlignmentResult(
        success=False,
        error_reason="low_confidence",
        failed_marker=2,
        marker_confidences=[0.82, 0.31],
    )
    svc = _make_service_with_alignment_failure(align)

    asyncio.run(svc.process_reading())

    # No inference, no MQTT publish of a value
    svc.run_inference.assert_not_called()
    # State reflects ALIGNMENT_FAILED
    assert svc.current_state.get("status") == "alignment_failed"
    # Failure was persisted with all expected kwargs
    svc._failure_store.record_failure.assert_called_once()
    kwargs = svc._failure_store.record_failure.call_args.kwargs
    assert kwargs.get("reason") == "low_confidence"
    assert kwargs.get("stage") == "alignment"
    assert kwargs.get("failed_marker") == 2
    assert kwargs.get("marker_confidences") == [0.82, 0.31]


def test_alignment_failure_increments_consecutive_failures():
    align = AlignmentResult(success=False, error_reason="insufficient_markers", marker_confidences=[])
    svc = _make_service_with_alignment_failure(align)
    svc.consecutive_alignment_failures = 0

    asyncio.run(svc.process_reading())
    assert svc.consecutive_alignment_failures == 1

    asyncio.run(svc.process_reading())
    assert svc.consecutive_alignment_failures == 2


# ---------------------------------------------------------------------------
# Integration tests for the inference_failed metric path (Task 5 / review fix).
# These verify the production process_reading flow records the metric correctly:
#   - run_inference itself raises  → status="inference_failed" recorded
#   - downstream code raises       → status="inference_failed" NOT recorded
# ---------------------------------------------------------------------------


def _make_service_with_alignment_success():
    """Build a service whose alignment succeeds, so process_reading reaches inference."""
    svc = object.__new__(WatermeterService)
    svc.config = {
        "images": {"process_separate": False},
        "alignment": {},
        "inference": {"confidence_threshold": 0.6},
        "low_confidence": {"save_path": "/tmp/lc"},
        "plausibility": {},
        "homeassistant": {"enabled": False},
        "detection": {"digits": {"rois": [{"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.1}]}},
    }
    svc._state = MeterState(ha_publish_enabled=False)
    svc._confirmation_manager = MagicMock()
    svc._confirmation_manager._pending_confirmation = None
    svc.processing_lock = asyncio.Lock()
    svc._failure_store = MagicMock()
    svc._metrics = MagicMock()
    svc.consecutive_alignment_failures = 0
    svc._stale_notified = False
    svc._data_collector = None
    svc._rate_tracker = MagicMock()
    svc._rate_tracker.add = MagicMock()
    svc._state_store = None

    align_success = AlignmentResult(
        success=True,
        image=MagicMock(),
        marker_confidences=[0.9, 0.92],
    )
    fake_images = {"d1": (b"...", "digits")}

    svc._image_pipeline = MagicMock()
    svc._image_pipeline.process_whole_image = MagicMock(return_value=(fake_images, align_success))

    svc.fetch_whole_image = AsyncMock(return_value=b"fake jpeg bytes")
    svc.publish_to_mqtt = AsyncMock()
    svc._archive_raw_image = MagicMock()
    svc._notify_stale = MagicMock()
    return svc


def test_inference_exception_records_inference_failed():
    """If run_inference raises, the metric must record status='inference_failed'."""
    svc = _make_service_with_alignment_success()

    # The pivotal mock: run_inference raises
    svc.run_inference = AsyncMock(side_effect=RuntimeError("openvino exploded"))

    # Downstream methods that should NOT be called
    svc.calculate_total = MagicMock()
    svc.correct_predictions = MagicMock()
    svc.check_consistency = MagicMock()
    svc.validate_plausibility = MagicMock()

    asyncio.run(svc.process_reading())

    # inference_failed was recorded exactly once
    inference_failed_calls = [
        c for c in svc._metrics.record_reading.call_args_list if c.kwargs.get("status") == "inference_failed"
    ]
    assert (
        len(inference_failed_calls) == 1
    ), f"Expected exactly one inference_failed call, got: {svc._metrics.record_reading.call_args_list}"
    # No "ok" or "alignment_failed" recorded for this reading
    ok_calls = [c for c in svc._metrics.record_reading.call_args_list if c.kwargs.get("status") == "ok"]
    assert len(ok_calls) == 0
    # State reflects the failure
    assert svc.current_state["status"] == "inference_failed"
    assert "openvino exploded" in svc.current_state.get("last_inference_error", "")
    # Downstream pipeline steps must not run
    svc.calculate_total.assert_not_called()


def test_post_inference_exception_does_not_record_inference_failed():
    """If a downstream operation (e.g. calculate_total) raises, that's NOT inference_failed."""
    svc = _make_service_with_alignment_success()

    # run_inference returns successfully
    svc.run_inference = AsyncMock(
        return_value={
            "d1": {
                "id": "d1",
                "class": 7,
                "confidence": 0.99,
                "model": "digits",
                "image_bytes": b"",
            }
        }
    )
    # Pivotal mock: calculate_total raises (post-inference downstream failure)
    svc.calculate_total = MagicMock(side_effect=RuntimeError("downstream boom"))
    svc.correct_predictions = MagicMock()
    svc.check_consistency = MagicMock()
    svc.validate_plausibility = MagicMock()

    asyncio.run(svc.process_reading())

    # No inference_failed metric recorded — that label is reserved for actual
    # run_inference exceptions.
    inference_failed_calls = [
        c for c in svc._metrics.record_reading.call_args_list if c.kwargs.get("status") == "inference_failed"
    ]
    assert (
        len(inference_failed_calls) == 0
    ), f"Downstream exception must NOT record inference_failed, got: {svc._metrics.record_reading.call_args_list}"
    # Status should be "error" (the catch-all generic), not "inference_failed"
    assert svc.current_state["status"] == "error"


def test_plausibility_rejection_reports_rejected_not_failed():
    """A plausibility rejection must surface as status 'rejected' / pipeline REJECTED, not FAILED."""
    svc = _make_service_with_alignment_success()
    svc.run_inference = AsyncMock(
        return_value={
            "d1": {"id": "d1", "class": 7, "confidence": 0.99, "model": "digits", "image_bytes": b""},
        }
    )
    svc.calculate_total = MagicMock(return_value=(56.3624, {}))
    svc.correct_predictions = MagicMock(return_value=[])
    svc.check_consistency = MagicMock(return_value=[])
    svc.validate_plausibility = MagicMock(return_value=(False, ["Reverse detected: 56.3682 → 56.3624"]))
    svc._check_sustained_consumption = MagicMock(return_value=None)
    svc._data_collector = None
    svc.previous_value = 56.3682
    svc._compute_raw_total = MagicMock(return_value=56.40679)

    asyncio.run(svc.process_reading())

    assert svc.current_state["status"] == "rejected"
    assert svc.current_state["pipeline_status"] == "REJECTED"
    assert svc.consecutive_rejections == 1


# ---------------------------------------------------------------------------
# Restore conftest mocks so subsequent test files in the same session see them.
# (Done at module bottom so failures during imports above leave the mocks
# unrestored only for this file, not the whole run.)
# ---------------------------------------------------------------------------
import watermeter as _wm_pkg  # noqa: E402

for _ws_name, _ws_mock in _saved_ws_mocks.items():
    sys.modules[_ws_name] = _ws_mock
    _attr = _ws_name.split(".")[-1]
    if hasattr(_wm_pkg, _attr):
        setattr(_wm_pkg, _attr, _ws_mock)
