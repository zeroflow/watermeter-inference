"""Service-level tests: published high-water mark and reading recovery.

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
from watermeter.image_pipeline import AlignmentResult  # noqa: E402, I001, F401
from watermeter.meter_state import MeterState  # noqa: E402, I001


from watermeter.persistence import StateStore  # noqa: E402, I001
from watermeter.plausibility import PlausibilityChecker  # noqa: E402, I001, F401
from watermeter.rate_tracker import RateTracker  # noqa: E402, I001, F401

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


def test_meter_state_reset_clears_high_water():
    state = MeterState()
    state.published_high_water = 1.0
    state.reset()
    assert state.published_high_water is None


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
