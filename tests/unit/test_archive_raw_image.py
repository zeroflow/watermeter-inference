"""Unit tests for WatermeterService._archive_raw_image (best-effort archive helper).

This file follows the same sys.modules dance as test_marker_alignment.py so we can
import the real WatermeterService class instead of the conftest mock. The archive
helper itself does not use cv2/openvino/paho — those are still mocked via
sys.modules so the module-level imports succeed.
"""

import sys
from datetime import datetime, timedelta
from types import ModuleType
from unittest.mock import MagicMock, patch

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
        _inf_mock.get_inference_service = MagicMock(return_value=MagicMock())
        sys.modules[_inf_name] = _inf_mock

# cv2 may be mocked by conftest; that is fine for these tests because the
# archive helper does not call cv2. But the watermeter_service module imports
# cv2 at the top, so the mock must be present and look like a module.
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

# Restore the conftest mocks so that other test files are not affected.
import watermeter as _wm_pkg  # noqa: E402, I001

for _ws_name, _ws_mock in _saved_ws_mocks.items():
    sys.modules[_ws_name] = _ws_mock
    _attr = _ws_name.split(".")[-1]
    if hasattr(_wm_pkg, _attr):
        setattr(_wm_pkg, _attr, _ws_mock)


def _make_service(config: dict):
    """Construct a bare WatermeterService instance with only the fields _archive_raw_image needs."""
    svc = object.__new__(WatermeterService)
    svc.config = config
    svc._last_sweep_date = None
    return svc


def test_writes_jpeg_to_dated_subdir(tmp_path):
    svc = _make_service({"alignment": {"archive_dir": str(tmp_path)}})
    svc._archive_raw_image(b"fake-jpeg-bytes")
    files = list(tmp_path.rglob("*.jpg"))
    assert len(files) == 1
    # Path shape: <tmp>/<YYYY-MM-DD>/<HHMMSS_microseconds>.jpg
    assert files[0].parent.name == datetime.now().strftime("%Y-%m-%d")
    assert files[0].read_bytes() == b"fake-jpeg-bytes"


def test_filename_includes_microseconds_to_avoid_collision(tmp_path):
    svc = _make_service({"alignment": {"archive_dir": str(tmp_path)}})
    # Two writes in rapid succession should produce two distinct files
    svc._archive_raw_image(b"a")
    svc._archive_raw_image(b"b")
    files = sorted(tmp_path.rglob("*.jpg"))
    assert len(files) == 2
    # Filenames have at least one underscore (HHMMSS_microseconds.jpg)
    assert all("_" in f.stem for f in files)


def test_swallows_io_errors(tmp_path, caplog):
    svc = _make_service({"alignment": {"archive_dir": str(tmp_path)}})
    with patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")):
        # MUST NOT raise
        svc._archive_raw_image(b"data")
    # Warning logged
    assert any("Failed to archive raw image" in r.message for r in caplog.records)


def test_max_age_sweep_deletes_old_day_dirs(tmp_path):
    svc = _make_service({"alignment": {"archive_dir": str(tmp_path), "archive_max_age_days": 7}})
    # Create ten dated subdirs spanning 14 days back
    today = datetime.now().date()
    for days_ago in range(0, 14):
        d = today - timedelta(days=days_ago)
        sub = tmp_path / d.strftime("%Y-%m-%d")
        sub.mkdir()
        (sub / "stub.jpg").write_bytes(b"x")
    # Trigger archive (which triggers the sweep on the first write of the day)
    svc._archive_raw_image(b"new")
    surviving = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    # Anything older than 7 days should be gone; today + last 7 should remain
    cutoff = today - timedelta(days=7)
    for name in surviving:
        d = datetime.strptime(name, "%Y-%m-%d").date()
        assert d >= cutoff


def test_sweep_runs_at_most_once_per_day(tmp_path):
    """Repeated archive writes within the same day should not re-walk the directory."""
    svc = _make_service({"alignment": {"archive_dir": str(tmp_path)}})
    svc._archive_raw_image(b"first")
    initial_sweep_date = svc._last_sweep_date
    assert initial_sweep_date is not None
    svc._archive_raw_image(b"second")
    svc._archive_raw_image(b"third")
    # _last_sweep_date should not have advanced (still today)
    assert svc._last_sweep_date == initial_sweep_date


def test_sweep_errors_do_not_break_archive(tmp_path, caplog):
    """If the sweep blows up, the archive write itself must still succeed."""
    svc = _make_service({"alignment": {"archive_dir": str(tmp_path)}})
    # Patch the sweep helper to raise; the archive write must still succeed.
    with patch.object(svc, "_sweep_old_archive_dirs", side_effect=RuntimeError("boom")):
        svc._archive_raw_image(b"data")
    # Write succeeded
    files = list(tmp_path.rglob("*.jpg"))
    assert len(files) == 1
