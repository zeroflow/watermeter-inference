---
last_mapped_commit: 721bd40e2f58d1951323f3b24679dc2436db1362
last_mapped_at: 2026-10-11
---
# Testing Patterns

**Analysis Date:** 2026-10-11

## Test Framework

**Runner:**
- pytest (`pytest>=7.0.0,<9.0.0`, `requirements-dev.txt`)
- pytest-asyncio (`>=0.21.0,<1.0.0` in `requirements-dev.txt`; 0.26.0 is installed in the local `.venv`, within the pin)
- pytest-cov is installed in CI only (`.github/workflows/ci.yml`); it is not listed in `requirements-dev.txt`.
- HTTP tests use FastAPI's `TestClient`, which needs `httpx` (`requirements-dev.txt`).
- Config: `pytest.ini` at repo root (not `pyproject.toml`; there is no `[tool.pytest]` section).

**Key `pytest.ini` settings:**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short -m "not integration"
markers =
    integration: Integration tests (require a running debug server, e.g. --base-url=http://localhost:8002)
    slow: Slow tests (download models, export operations)
```

- `asyncio_mode` is not set, so pytest-asyncio runs in **strict** mode: every async test needs `@pytest.mark.asyncio`.
- Default runs exclude the `integration` marker. The `slow` marker is not excluded by default.

**Assertion Library:**
- Plain `assert` statements with pytest introspection. No `pytest.approx` convention is enforced; floats are compared with `pytest.approx` or with explicit tolerances where needed.
- `pytest.raises` is used for error contracts (35 occurrences).

**Run Commands:**

```bash
.venv/bin/python -m pytest                                  # default: all non-integration tests (uses pytest.ini)
.venv/bin/python -m pytest tests/unit tests/regression -q   # the set to run after changes (per CLAUDE.md)
.venv/bin/python -m pytest tests/unit/test_foo.py::test_bar # single test
.venv/bin/python -m pytest tests/unit -x -q                 # stop on first failure (mirrors CI)
.venv/bin/python -m pytest tests/unit --cov=watermeter      # coverage (pytest-cov; not enforced)
.venv/bin/python -m pytest tests/integration --base-url=http://localhost:8002   # needs running debug container; do not run against prod (8001)
```

- Use `.venv/bin/python -m pytest`, not a global `pytest`. The venv is managed by `uv` (see `setup.sh` note in `CLAUDE.md`: it is stale).
- Local collection: `tests/unit` currently collects 922 tests.

## CI Test Job

Defined in `.github/workflows/ci.yml` (mirrored in `.gitea/`):
- Python 3.12. CPU-only torch is installed first, then `requirements-docker.txt` and `requirements-dev.txt`, then `openvino>=2025.0.0,<2026.0.0`.
- Runs `pytest tests/unit/ -x -q` only. `tests/regression`, `tests/export` and `tests/integration` are **not** run in CI; run them locally.
- Lint job runs `ruff check watermeter/ tests/` (see `CONVENTIONS.md`).

## Test File Organization

**Location:** all tests live under `tests/`, separate from source. No co-located tests.

**Layout:**

```
tests/
├── conftest.py              # root: sys.path setup, CLI options (--base-url, --image, --no-build), sample_config_yaml, sample_config_dict
├── unit/                    # the bulk: ~160 test classes, ~180 top-level test functions; no Docker needed
│   ├── conftest.py          # stubs heavy modules; mock_service and test_client fixtures
│   ├── synthetic_dial.py    # shared helper (not a test module)
│   └── test_<feature>.py
├── regression/              # one file per fixed bug; name describes the bug
│   ├── test_config_comments.py
│   └── test_nan_class_label.py
├── export/                  # @pytest.mark.slow; OpenVINO export; own conftest.py
└── integration/             # Docker-backed; own conftest.py builds/starts container on a free port
```

- `tests/__init__.py`, `tests/unit/__init__.py`, `tests/regression/__init__.py`, `tests/integration/__init__.py` exist; keep them so module names do not collide.

**Naming:**
- Files: `test_<area>.py` (e.g. `test_plausibility.py`, `test_calculate_total.py`, `test_service_fail_closed.py`).
- Classes: `Test<Subject>` grouping related cases (160 classes in `tests/unit`), e.g. `TestCheckConsistency`, `TestValidatePlausibility` in `tests/unit/test_plausibility.py`. Flat `def test_*` functions are equally common for small modules (e.g. `tests/unit/test_calculate_total.py`).
- Test functions: `test_<behaviour_in_snake_case>`; the docstring states the expectation in one line.

## Test Structure

**Suite organization** (from `tests/unit/test_plausibility.py`):

```python
"""Unit tests for PlausibilityChecker — consistency and plausibility validation.

Tests the extracted PlausibilityChecker class directly.
"""

from datetime import datetime, timedelta

import pytest

from watermeter.plausibility import PlausibilityChecker
from watermeter.rate_tracker import RateTracker

@pytest.fixture
def config():
    return {...}

@pytest.fixture
def checker(config, rate_tracker):
    return PlausibilityChecker(config=config, rate_tracker=rate_tracker)

class TestCheckConsistency:
    def test_no_warnings_consistent(self, checker):
        """All positions consistent -> empty warnings."""
        predictions = {...}
        warnings = checker.check_consistency(predictions)
        assert warnings == []
```

**Patterns:**
- Module docstring at the top describing what is under test.
- Section divider comments (`# ----...`) separate test classes in larger files.
- Fixtures are defined at module level near the top; shared fixtures go in `conftest.py`.
- Arrange → act → assert, one behaviour per test. Multiple asserts are fine when they describe one result.
- Parametrize with `@pytest.mark.parametrize` (10 uses) for tables of inputs, e.g. classes or thresholds.

**Setup/teardown:**
- Use fixtures with `yield` for cleanup; `tmp_path` handles temp files (711 uses across tests).
- Use `monkeypatch` for environment, working directory and module constants (134 uses), e.g.:

```python
monkeypatch.setattr(ca, "CALIBRATION_DIR", tmp_path)   # tests/unit/test_calibration_session.py
monkeypatch.chdir(tmp_path)                              # tests/unit/conftest.py test_client
```

## Mocking

**Framework:** `unittest.mock` (`MagicMock`, `AsyncMock`, `patch`). No `pytest-mock`.

**Heavy-dependency stubbing (critical pattern):**
Unit tests must run without OpenVINO, OpenCV's model runtime, or paho-mqtt installed on the host. `tests/unit/conftest.py` replaces these in `sys.modules` before any `watermeter` import:

```python
_MOCK_MODULES = ['cv2', 'paho', 'paho.mqtt', 'paho.mqtt.client', 'openvino', 'openvino.runtime']

def _install_mock_modules():
    for mod_name in _MOCK_MODULES:
        if mod_name not in sys.modules:
            mock_mod = MagicMock(spec=ModuleType)
            mock_mod.__name__ = mod_name
            sys.modules[mod_name] = mock_mod
```

- `watermeter.inference` is replaced with a mock because its module-level init reads `config.yaml` and loads models.
- `numpy` is **not** stubbed; it is available in the venv and needed by torch/timm.
- Tests that need the **real** `WatermeterService` or `ImagePipeline` must temporarily remove those mocks from `sys.modules`, import, then restore. See `tests/unit/test_service_fail_closed.py` (saves `_saved_ws_mocks`, deletes `watermeter_service` / `image_pipeline` entries, imports the real class). `tests/unit/test_archive_raw_image.py` follows the same approach. This is fragile: import order matters, so keep these tests self-contained and do not rely on another test having imported the module first.

**FastAPI route tests:**
- The `test_client` fixture in `tests/unit/conftest.py` builds a `TestClient` against the real `watermeter.app.app` with `mock_service` injected through the `watermeter_service` stub. It runs without the app lifespan, so MQTT and background loops do not start.
- `mock_service` is a `MagicMock` with `config`, `current_state` and `mqtt_client` pre-populated.
- Templates and static directories are replaced by stubs in `tmp_path` via `monkeypatch.setattr(pages_module, "templates", ...)`.
- Use `raise_server_exceptions=False`; assert on status codes and JSON keys (`resp.json()['success']`).

**Common patch patterns:**

```python

# Patch a name where it is looked up (module under test), not where it is defined

with patch('watermeter.model_manager._model_manager', None):
    ...

# Replace a method on the class for one test

with patch('watermeter.model_manager.ModelManager.__init__', lambda self, **kw: setattr(self, 'models_base_path', tmp_path)):
    tm = TrainingManager()
```

**Async mocks:**

```python
from unittest.mock import AsyncMock, MagicMock

svc.some_async_method = AsyncMock(return_value=...)
asyncio.run(svc.process_reading())    # used when no pytest-asyncio marker is wanted
```

- 35 `AsyncMock` uses; prefer `AsyncMock` for any awaited collaborator.
- Use `@pytest.mark.asyncio` on `async def` tests (25 uses). Five test files call `asyncio.run(...)` directly instead; both styles exist, prefer the marker for new tests.

**Bypassing `__init__` for service-level tests:**
`tests/unit/test_service_fail_closed.py` builds the service with `object.__new__(WatermeterService)` and sets only the attributes the code under test reads. Use this only when the constructor has side effects (file logging, state loading, MQTT); otherwise construct normally.

**What to Mock:**
- Hardware and native runtimes (`openvino`, `cv2` model-runtime calls, `paho.mqtt`).
- Network and HTTP fetches of camera images (`images.src`).
- Long-running loops and background tasks (trigger loop, stats loop).
- Filesystem paths the code reads from `/data`, `/app/models`, `/training`: redirect to `tmp_path` instead of mocking `open`.
- Heavy model objects: use `MagicMock` returning fixed class/confidence predictions in the `{'id', 'class', 'confidence', 'model'}` shape (see `tests/unit/test_plausibility.py`).

**What NOT to Mock:**
- Pure logic: `calculate_total`, `resolve_arrows`, `get_position_ids`, `RateTracker`, `PlausibilityChecker`. Test them with real inputs.
- Persistence in unit tests: `StateStore` and `FailureStore` work on real files in `tmp_path` (`tests/unit/test_persistence.py`, `tests/unit/test_persistence_failures.py`).
- The config round-trip: `config_utils` is tested against real YAML text (`tests/unit/test_config_utils.py`).

## Fixtures and Factories

**Shared fixtures (root `tests/conftest.py`):**
- `sample_config_yaml`: minimal valid YAML string containing `images`, `mqtt`, `inference`, `plausibility`, `persistence` sections.
- `sample_config_dict`: same config as a plain dict.

**Shared fixtures (`tests/unit/conftest.py`):**
- `mock_service`: MagicMock service for API tests.
- `test_client`: FastAPI TestClient over the real app with the mock service injected.

**Factories:** small module-level helpers named `_make_*` or `make_*` build inputs for one test file:
- `_make_predictions(**overrides)` in `tests/unit/test_confirmation.py`
- `make_config(method=..., markers=..., ...)` in `tests/unit/test_image_pipeline_features.py`
- `make_failing_service(*, max_failures=10)` in `tests/unit/test_consecutive_failures.py`
- `_make_service(**overrides)` in `tests/unit/test_derive_pipeline_status.py`
- `_make_fake_arrow()` in `tests/unit/test_photo_master.py`

Use keyword overrides with defaults so each test states only what it changes.

**Synthetic images:**
- `watermeter/synthetic_generator.py` and `tests/unit/synthetic_dial.py` render dial/digit images for tests instead of storing binary fixtures. Prefer generating an image over committing a new PNG.
- Fixtures under `tests/fixtures/` (e.g. `meter_snapshot.jpg`, used by the debug container) are real assets; do not overwrite them.

**Location:**
- Config fixtures: `tests/conftest.py`.
- Route fixtures: `tests/unit/conftest.py`.
- Per-file factories: inside the test module.

## Coverage

**Requirements:** None enforced. No `--cov-fail-under` in `pytest.ini` or CI.

**View Coverage:**

```bash
.venv/bin/python -m pytest tests/unit --cov=watermeter --cov-report=term-missing
```

- pytest-cov must be installed in the venv (`uv pip install pytest-cov`); it is not in `requirements-dev.txt`.

**Known gaps:**
- Hardware-facing code (camera fetch, MQTT over a real broker, OpenVINO inference on real models) is covered only by the Docker-backed integration tests, which CI does not run.
- Training runs (`watermeter/training_manager.py`, `watermeter/training_core.py`) are tested through config and augmentation unit tests, not end-to-end.

## Test Types

**Unit tests (`tests/unit/`):**
- Scope: one module or class, with collaborators replaced by mocks or real lightweight objects.
- Must pass without Docker, GPU, network or real models. This is the CI gate.
- Run after every change: `.venv/bin/python -m pytest tests/unit -q`.

**Regression tests (`tests/regression/`):**
- One file per bug. Name and docstring state the bug and the symptom it guards against (`tests/regression/test_nan_class_label.py`: digit class must be `NAN`, not `N`).
- Add a regression test whenever a bug is fixed in a model, label or config path.
- Run locally: `.venv/bin/python -m pytest tests/regression -q`.

**Export tests (`tests/export/`):**
- Marked `@pytest.mark.slow`; exercise OpenVINO IR export (`test_export_openvino.py`). Require the real runtime. Run manually.

**Integration tests (`tests/integration/`):**
- Marked `@pytest.mark.integration` and excluded by default.
- `tests/integration/conftest.py` either uses an already running service (`--base-url` / `WATERMETER_TEST_URL`) or builds the image and starts its own container on a free port.
- Test files: `test_smoke.py`, `test_config.py`, `test_models.py`, `test_training.py`, `test_synthetic.py`, `test_fisheye_workflow.py`, `test_benchmark.py`, `test_oneshot.py`.
- Run only against the debug container on port 8002 or a self-started container. Never target port 8001 (production).

**E2E Tests:** not used as a separate category. The integration tier is the closest equivalent.

## Common Patterns

**Async testing:**

```python
import pytest

@pytest.mark.asyncio
async def test_reading_rejected_keeps_previous_value(svc):
    svc.inference.predict = AsyncMock(return_value=bad_predictions)
    await svc.process_reading()
    assert svc.current_state["value"] == previous_value
```

**Error testing:**

```python
def test_save_invalid_yaml(self, test_client):
    resp = test_client.post("/api/config/save", json={"content": "{{invalid yaml", "save_option": "saveonly"})
    assert resp.status_code == 400
    assert resp.json()['success'] is False

# Pattern for validators that raise ValueError (illustrative; adapt the input to the rule under test)

with pytest.raises(ValueError, match="Invalid model_id"):
    ModelManager._validate_model_id("../etc")
```

**Filesystem and persistence testing:**

```python
def test_save_and_load(self, tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    store.save(value, timestamp)
    assert store.load() == (value, timestamp)
```

**Path-safety testing:** every user-supplied path or model ID gets a traversal test. Add one when touching `safe_subpath` or `_validate_model_id` callers.

**Regression testing:**

```python
"""Regression test: digit classes must include 'NAN' not 'N'. ..."""

def test_arrow_classes_do_not_contain_nan(tmp_path):
    ...
    assert 'NAN' not in classes
```

## Guidance for New Tests

1. Put the file in `tests/unit/` as `test_<area>.py` and keep it runnable without Docker.
2. Start with the real pure function; add a mock only for hardware, network or the filesystem outside `tmp_path`.
3. Use `tmp_path` and `monkeypatch`; never write to `/data`, `/training`, `/app/models` or the repo `config.yaml`.
4. For async code, add `@pytest.mark.asyncio` (strict mode).
5. For new config keys, add a test in `tests/unit/test_config_utils.py` that round-trips the key with comments preserved, and extend the schema check in `tests/unit/test_api_routes.py`.
6. For a bugfix, add a regression file under `tests/regression/` named after the bug.
7. Run `.venv/bin/python -m pytest tests/unit tests/regression -q` and `uvx ruff check watermeter/ tests/` before committing.

---

*Testing analysis: 2026-10-11*
