"""Service-level tests: published high-water mark and reading recovery.

This file follows the same sys.modules dance as test_archive_raw_image.py so we can
import the real WatermeterService class instead of the conftest mock.  cv2/openvino/
paho/inference are still mocked at sys.modules so the module-level imports succeed.
"""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

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


from watermeter.persistence import StateStore  # noqa: E402, I001
from watermeter.plausibility import PlausibilityChecker  # noqa: E402, I001
from watermeter.rate_tracker import RateTracker  # noqa: E402, I001

PLAUSIBILITY = {
    "enable_reverse_detection": True,
    "enable_rate_limit": True,
    "max_rate_per_reading": 0.15,
    "max_rate_per_hour": 1.5,
    "enable_consistency_check": False,
    "reverse_tolerance": 0.002,
    "reanchor_after": 6,
    "reanchor_max_spread": 0.01,
}


def _make_publish_service(tmp_path=None):
    svc = object.__new__(WatermeterService)
    svc._state = MeterState(ha_publish_enabled=True)
    svc._mqtt = MagicMock()
    svc._mqtt.publish_to_mqtt = AsyncMock()
    svc._state_store = StateStore(str(tmp_path / "state.json")) if tmp_path else None
    return svc


def _published(svc):
    return svc._mqtt.publish_to_mqtt.call_args.args[0]


def test_publish_never_decreases(tmp_path):
    svc = _make_publish_service(tmp_path)
    asyncio.run(svc.publish_to_mqtt(56.5999, [], {}))
    asyncio.run(svc.publish_to_mqtt(56.5003, [], {}))
    assert _published(svc) == 56.5999


def test_publish_increases_past_high_water(tmp_path):
    svc = _make_publish_service(tmp_path)
    asyncio.run(svc.publish_to_mqtt(56.5999, [], {}))
    asyncio.run(svc.publish_to_mqtt(56.6010, [], {}))
    assert _published(svc) == 56.6010


def test_allow_decrease_overrides_high_water(tmp_path):
    svc = _make_publish_service(tmp_path)
    asyncio.run(svc.publish_to_mqtt(56.5999, [], {}))
    asyncio.run(svc.publish_to_mqtt(40.0, [], {}, allow_decrease=True))
    assert _published(svc) == 40.0
    assert svc._state.published_high_water == 40.0


def test_high_water_survives_restart(tmp_path):
    """Review focus 5: after a restart the first publish is still >= the old peak."""
    svc = _make_publish_service(tmp_path)
    asyncio.run(svc.publish_to_mqtt(56.5999, [], {}))
    restarted = _make_publish_service(tmp_path)
    restarted._state.published_high_water = restarted._state_store.load_published_value()
    asyncio.run(restarted.publish_to_mqtt(56.5003, [], {}))
    assert _published(restarted) == 56.5999


def _init_mark(tmp_path, content=None):
    svc = _make_publish_service(tmp_path)
    if content is not None:
        (tmp_path / "state.json").write_text(content)
    svc._state.previous_value, svc._state.last_update_time = svc._state_store.load()
    svc._init_published_high_water()
    return svc._state.published_high_water


def test_init_high_water_old_state_file_falls_back_to_previous_value(tmp_path):
    assert _init_mark(tmp_path, '{"previous_value": 56.5999, "last_update_time": null}') == 56.5999


def test_init_high_water_uses_persisted_value(tmp_path):
    content = '{"previous_value": 56.5999, "last_update_time": null, "published_value": 57.0}'
    assert _init_mark(tmp_path, content) == 57.0


def test_init_high_water_no_file(tmp_path):
    assert _init_mark(tmp_path) is None


def _make_manual_service(tmp_path):
    svc = _make_publish_service(tmp_path)
    svc._mqtt.mqtt_client = None  # MQTT not connected -> publish_to_mqtt never called
    svc._rate_tracker = MagicMock()
    svc._cancel_confirmation_timer = MagicMock()
    svc._confirmation_manager = MagicMock()
    return svc


def test_manual_set_lowers_high_water_without_mqtt(tmp_path):
    svc = _make_manual_service(tmp_path)
    asyncio.run(svc.publish_to_mqtt(56.5999, [], {}))
    assert svc._state.published_high_water == 56.5999
    asyncio.run(svc.set_manual_value(40.0))
    assert svc._state.published_high_water == 40.0
    assert svc._state_store.load_published_value() == 40.0


def test_reset_previous_value_clears_high_water(tmp_path):
    svc = _make_manual_service(tmp_path)
    asyncio.run(svc.publish_to_mqtt(56.5999, [], {}))
    svc.reset_previous_value()
    assert svc._state.published_high_water is None
    assert svc._state_store.load_published_value() is None


def test_meter_state_reset_clears_high_water():
    state = MeterState()
    state.published_high_water = 1.0
    state.reset()
    assert state.published_high_water is None


def _make_reading_service(previous_value):
    """Service with real calculate_total + PlausibilityChecker; fetch/inference/publish mocked."""
    svc = object.__new__(WatermeterService)
    svc.config = {
        "images": {"process_separate": False},
        "alignment": {},
        "inference": {"confidence_threshold": 0.6},
        "low_confidence": {"save_path": "/tmp/lc"},
        "plausibility": dict(PLAUSIBILITY),
        "homeassistant": {"enabled": False},
        "detection": {
            "digits": {"count": 3, "rois": [{"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.1}]},
            "analogs": {"count": 4},
        },
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
    svc._rate_tracker = RateTracker(max_size=25)
    svc._plausibility_checker = PlausibilityChecker(config=svc.config, rate_tracker=svc._rate_tracker)
    svc._state_store = None
    align_ok = AlignmentResult(success=True, image=MagicMock(), marker_confidences=[0.9])
    svc._image_pipeline = MagicMock()
    svc._image_pipeline.process_whole_image = MagicMock(return_value=({"x": (b"", "digits")}, align_ok))
    svc.fetch_whole_image = AsyncMock(return_value=b"jpeg")
    svc.publish_to_mqtt = AsyncMock()
    svc._archive_raw_image = MagicMock()
    svc._notify_stale = MagicMock()
    svc.correct_predictions = MagicMock(return_value=[])
    svc.check_consistency = MagicMock(return_value=[])
    svc._check_sustained_consumption = MagicMock(return_value=None)
    svc._should_request_confirmation = MagicMock(return_value=None)
    svc.previous_value = previous_value
    return svc


def _live_predictions(digits=("0", "5", "6"), arrows=("5.2", "9.7", "0.3", "3.2")):
    p = {}
    for i, d in enumerate(digits):
        p[f"digit_{i + 1}"] = {
            "id": f"digit_{i + 1}",
            "class": d,
            "confidence": 0.99,
            "model": "digits",
            "image_bytes": b"",
        }
    for i, a in enumerate(arrows):
        p[f"analog_{i + 1}"] = {
            "id": f"analog_{i + 1}",
            "class": a,
            "confidence": 0.9,
            "model": "arrows",
            "image_bytes": b"",
        }
    return p


def test_live_stuck_sequence_reanchors_after_six_readings():
    """2026-10-04: baseline 56.5999 wrong, true 56.5003 -> 5 rejections, re-anchor on the 6th."""
    svc = _make_reading_service(previous_value=56.5999)
    svc.run_inference = AsyncMock(return_value=_live_predictions())
    statuses = []
    for _ in range(6):
        asyncio.run(svc.process_reading())
        statuses.append(svc.current_state["status"])
    assert statuses[:5] == ["rejected"] * 5
    assert statuses[5] == "warning"
    assert svc.previous_value == pytest.approx(56.5003)
    assert svc.consecutive_rejections == 0
    assert svc.publish_to_mqtt.call_args.args[0] == pytest.approx(56.5003)


def test_jitter_holds_previous_value():
    svc = _make_reading_service(previous_value=56.5004)
    svc.run_inference = AsyncMock(return_value=_live_predictions())  # reads 56.5003
    asyncio.run(svc.process_reading())
    assert svc.current_state["status"] == "warning"
    assert svc.previous_value == pytest.approx(56.5004)


def test_unresolvable_reading_is_rejected_without_crash():
    """Review focus 4: NAN that can't be resolved -> rejected; STUCK message must not crash on None."""
    svc = _make_reading_service(previous_value=56.5000)
    svc.consecutive_rejections = 10  # beyond max -> STUCK message path
    svc.run_inference = AsyncMock(
        return_value=_live_predictions(digits=("0", "8", "NAN"), arrows=("1.0", "0.0", "0.0", "0.0"))
    )
    asyncio.run(svc.process_reading())
    assert svc.current_state["status"] == "rejected"
    assert any("inconsistent" in w for w in svc.current_state["last_rejected_reasons"])
    assert any("STUCK" in w for w in svc.current_state["warnings"])


def test_reset_clears_reanchor_candidates():
    svc = _make_reading_service(previous_value=56.5999)
    svc.run_inference = AsyncMock(return_value=_live_predictions())
    for _ in range(5):  # one short of re-anchoring
        asyncio.run(svc.process_reading())
    svc._plausibility_checker.reset_reanchor()
    svc.previous_value = 56.5999
    asyncio.run(svc.process_reading())
    assert svc.current_state["status"] == "rejected"


def test_unresolvable_rejection_keeps_displayed_total():
    """Fix round 1: a None total must not blank current_state["total_value"] (dashboard 'No data')."""
    svc = _make_reading_service(previous_value=56.5)
    svc.current_state["total_value"] = 56.5
    svc.run_inference = AsyncMock(
        return_value=_live_predictions(digits=("0", "8", "NAN"), arrows=("1.0", "0.0", "0.0", "0.0"))
    )
    asyncio.run(svc.process_reading())
    assert svc.current_state["status"] == "rejected"
    assert svc.current_state["total_value"] == 56.5
    assert svc.current_state["last_rejected_value"] is None
    assert svc.current_state["last_rejected_reasons"]


def test_unresolvable_reading_resets_reanchor_candidates():
    """Spec A2: any non-reverse rejection (incl. unresolvable) clears re-anchor candidates."""
    svc = _make_reading_service(previous_value=56.5999)
    svc.run_inference = AsyncMock(return_value=_live_predictions())
    for _ in range(5):
        asyncio.run(svc.process_reading())
    svc.run_inference = AsyncMock(
        return_value=_live_predictions(digits=("0", "8", "NAN"), arrows=("1.0", "0.0", "0.0", "0.0"))
    )
    asyncio.run(svc.process_reading())
    assert svc.current_state["status"] == "rejected"
    svc.run_inference = AsyncMock(return_value=_live_predictions())
    asyncio.run(svc.process_reading())
    assert svc.current_state["status"] == "rejected"
    assert svc.previous_value == pytest.approx(56.5999)


def test_set_manual_value_resets_reanchor_candidates():
    """The real set_manual_value must clear re-anchor candidates."""
    svc = _make_reading_service(previous_value=56.5999)
    svc._cancel_confirmation_timer = MagicMock()
    svc.run_inference = AsyncMock(return_value=_live_predictions())
    for _ in range(5):
        asyncio.run(svc.process_reading())
    assert svc.consecutive_rejections == 5
    asyncio.run(svc.set_manual_value(56.5999))
    asyncio.run(svc.process_reading())
    assert svc.current_state["status"] == "rejected"
    assert svc.previous_value == pytest.approx(56.5999)


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
