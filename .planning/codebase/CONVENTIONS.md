---
last_mapped_commit: 721bd40e2f58d1951323f3b24679dc2436db1362
last_mapped_at: 2026-10-11
---
# Coding Conventions

**Analysis Date:** 2026-10-11

## Naming Patterns

**Files:**
- Python modules are `snake_case.py` under the `watermeter/` package, named for the concern they own: `image_pipeline.py`, `plausibility.py`, `rate_tracker.py`, `calibrated_arrows.py`, `config_utils.py`.
- Routes are one file per feature area in `watermeter/routes/` (`service.py`, `config.py`, `calibration.py`, `training.py`, `models.py`, `mqtt.py`, `metrics.py`, `roi.py`, `label.py`, `pages.py`, `synthetic.py`).
- Tests are `tests/unit/test_<module_or_feature>.py`. Helper modules that are not tests are plain names, e.g. `tests/unit/synthetic_dial.py`.
- Standalone legacy scripts at the repo root (`train_digits.py`, `train_arrows.py`, `benchmark_*.py`) are not part of the package and should not be imported from `watermeter/`.
- Markdown design docs go in `docs/plans/YYYY-MM-DD-name.md`; backlog items live in `backlog.md` as `BL-{id}`.

**Functions:**
- `snake_case` everywhere (108 module-level `def` names under `watermeter/`).
- Module-private helpers use a leading underscore (31 module-level `_name` functions), e.g. `_round_half_up` in `watermeter/position_utils.py`, `_validate_model_id` as a `@staticmethod` on `ModelManager` in `watermeter/model_manager.py`.
- Async service entry points are `async def` (106 occurrences), e.g. `WatermeterService.process_reading` in `watermeter/watermeter_service.py`.
- Route handlers are plain `def`/`async def` functions decorated with `@router.get` / `@router.post` (see `watermeter/routes/service.py`).

**Variables and constants:**
- Module constants are `UPPER_SNAKE_CASE` at module top: `CALIBRATION_VERSION`, `MAX_PIVOT_CONDITION`, `MIN_TICKS`, `ROLL_WINDOW` (`watermeter/arrow_calibration.py`, `watermeter/position_utils.py`), `IMAGENET_MEAN` (`watermeter/training_core.py`).
- Position IDs are strings built by convention: `digit_1`, `digit_2`, ... and `analog_1`, ... (`get_position_ids` in `watermeter/position_utils.py`). Do not invent other formats.
- Class labels are strings. Digit classes are `"0"`–`"9"` plus the literal `"NAN"` (never `"N"`); arrow classes are numeric strings like `"0.0"`, `"0.1"`. See `tests/regression/test_nan_class_label.py`.
- Single-leading-underscore attributes hold internal state (`self._state`, `self._background_tasks`).

**Types and classes:**
- Classes are `PascalCase`: `WatermeterService`, `PlausibilityChecker`, `RateTracker`, `StateStore`, `FailureStore`, `CalibrationSession`.
- Exception classes end in `Error` or describe the condition: `CalibrationError` (`watermeter/arrow_calibration.py`), `SessionError`, `SessionBusy` (`watermeter/calibration_session.py`).
- Enums use `PascalCase` with members in upper case: `JobStatus` (`watermeter/training_manager.py`).
- Result objects are small `@dataclass`es (8 occurrences), e.g. `PlausibilityResult` in `watermeter/plausibility.py`, `AlignmentResult` in `watermeter/image_pipeline.py`. Prefer a dataclass for new multi-field results instead of tuples or dicts.
- Pydantic `BaseModel` request bodies sit next to their routes (7 modules, e.g. `watermeter/routes/service.py`) and use `field_validator` (8 uses) for input checks.

## Type Annotations

Annotations are used on public functions but the style is mixed. Match the file you are editing:
- `typing` generics: `Dict`, `List`, `Optional`, `Tuple`, `Union` (about 44 `Dict[` uses, 112 `Optional[` uses; e.g. `watermeter/position_utils.py`, `watermeter/watermeter_service.py`, `watermeter/config_utils.py`).
- PEP 604 / builtin generics: `X | None`, `dict[...]`, `tuple[...]` (e.g. `watermeter/inference.py` `arrow_bin_width(...) -> float | None`, `watermeter/persistence.py` `tuple[Optional[float], Optional[datetime]]`).
- New code targets Python 3.10 (`target-version = "py310"`): `X | None` and `list[str]` are valid. Prefer the builtin/PEP 604 form in new modules.
- `from __future__ import annotations` is not used (7 files only touched for other reasons); do not rely on forward references without quoting.

## Code Style

**Formatting:**
- Black, line length **120**, target `py310` (`[tool.black]` in `pyproject.toml`). Run with `uvx black watermeter/ tests/`.
- Quote style: package modules use double quotes (e.g. `watermeter/plausibility.py`, `watermeter/config_utils.py`); many test files use single quotes in dict fixtures (e.g. `tests/unit/test_plausibility.py`). Keep the existing quote style of the file you touch; do not reformat unrelated lines.
- Trailing commas in multi-line calls and literals are the norm.

**Linting:**
- Ruff, configured in `pyproject.toml` under `[tool.ruff]` with line length 120, rule sets `E`, `F`, `W`, `I` (isort).
- Ignored: `E501` (line length left to Black) and `E402` (module-level imports after `sys.path` / mock setup is required in test files and `tests/unit/conftest.py`).
- `known-first-party = ["watermeter"]`: the package is its own import section.
- CI runs `ruff check watermeter/ tests/` (`.github/workflows/ci.yml`). Run it locally as `uvx ruff check watermeter/ tests/` before committing.
- Type checking is not enforced in CI (no mypy config found in `pyproject.toml`).

## Import Organization

**Order (as enforced by isort via ruff):**
1. Standard library (`asyncio`, `logging`, `json`, `pathlib`, `datetime`, ...)
2. Third-party (`yaml`, `fastapi`, `pydantic`, `ruamel.yaml`, `numpy`, `cv2`, `paho.mqtt`)
3. First-party (`watermeter`), or relative imports inside the package

**Inside the package:** relative imports are the majority (73 `from .x import` lines, e.g. `watermeter/watermeter_service.py`, `watermeter/routes/service.py`). Absolute `from watermeter.x import` appears in tests and a few modules. Within one file, be consistent.

**Path aliases:** none. There is no `paths` alias configuration.

**Heavy imports:** `cv2` is imported at module top in most image modules (`watermeter/image_pipeline.py`, `watermeter/feature_alignment.py`, `watermeter/arrow_calibration.py`), `openvino` in `watermeter/inference.py`, and `torch` in `watermeter/training_core.py`. Unit tests run without these on the host because `tests/unit/conftest.py` replaces them in `sys.modules`. A new module imported by the unit suite should either follow that stub pattern or keep its heavy import out of the import path that tests exercise.

**`__all__`:** not used.

## Error Handling

**Core rules observed:**
- The service never raises into the reading loop for expected failures. Alignment failures, inference failures and unresolvable readings are recorded and the previous value is kept (see `process_reading` in `watermeter/watermeter_service.py`). Preserve this when adding steps: return a result or log and skip rather than letting an exception escape the loop.
- Broad handlers are common (`except Exception as e:`, 126 occurrences) in persistence, MQTT and routes, typically logging and falling back to a safe default. Example, `StateStore.load` in `watermeter/persistence.py` logs and returns `(None, None)`.
- A bare `except:` is not used (0 occurrences). Do not add one.
- Input validation raises `ValueError` with the offending value in the message: `ModelManager._validate_model_id` and `ModelManager._get_model_types_dir` in `watermeter/model_manager.py`, `safe_subpath` in `watermeter/app.py`.
- Path inputs from users must go through `safe_subpath(base, *parts)` (`watermeter/app.py`), which resolves and checks `is_relative_to`. Model IDs must go through `_validate_model_id()`.
- HTTP errors in routes return JSON with `success` and `message` keys and an explicit status, e.g. `resp.json()['success'] is False` with status 400 for bad config (`tests/unit/test_api_routes.py` documents the contract). `HTTPException` is used in `watermeter/routes/training.py` for upload validation.
- Domain errors use small custom exception classes (`CalibrationError`, `SessionError`, `SessionBusy`) which routes map to responses.

**Patterns:**

```python
try:
    with open(metadata_file, "r") as f:
        metadata = json.load(f)
except Exception as e:
    logger.error(f"Failed to load metadata for {model_id}: {e}")
    return None
```

**Do not** let a background task die silently. Fire-and-forget tasks must be stored in the module-level `_background_tasks` set in `watermeter/app.py` (via `_create_background_task`) so they are not garbage-collected.

## Logging

**Framework:** standard library `logging`. Each module creates `logger = logging.getLogger(__name__)` (38 modules).

**Patterns:**
- Use f-strings in log calls. This is the dominant style (about 240 `logger.<level>(f"...")` calls). Only 8 call sites use `%s` lazy formatting. Either is acceptable, but do not mix within one function.
- Levels: `logger.error` for failures that the code recovers from (persist failed, load failed); `logger.warning` for deprecated/ignored config (for example the legacy `aiote:` section in `watermeter/config_utils.py`) and degraded states; `logger.info` for lifecycle events; `logger.exception` only inside `except` blocks where the traceback matters (2 uses).
- Log file rotation is configured from `config.yaml` `logging` section in `WatermeterService.__init__`.
- `print()` appears only in the one-shot CLI output path (`watermeter/oneshot.py`). Do not use `print` in the service.

## Comments

**When to comment:**
- Explain *why* for non-obvious numerics and thresholds. Examples: the `ROLL_WINDOW` comment in `watermeter/position_utils.py` explains the 0.1-dial wrap, `MAX_PIVOT_CONDITION` in `watermeter/arrow_calibration.py` explains the fallback.
- Reference a backlog item or regression when a workaround exists (`# Populate last_published from persisted state (BL-14)` in `watermeter/watermeter_service.py`).
- Mark mock setup in tests with a comment explaining why a module is stubbed (`tests/unit/conftest.py`).

**JSDoc/TSDoc:** not applicable. For Python use docstrings (below).

**Docstrings:**
- Module docstrings describe the module's role in one or two sentences (`watermeter/position_utils.py`, `watermeter/config_utils.py`).
- Public functions and methods use triple-quoted docstrings with an `Args:` / `Returns:` section for non-trivial logic (`get_position_ids` in `watermeter/position_utils.py`). One-line docstrings are fine for simple methods.
- Test functions carry a one-line docstring stating the expectation (e.g. `"""Half/upper mismatch -> warning."""` in `tests/unit/test_plausibility.py`).

## Function Design

**Size:** Keep orchestration in `WatermeterService` thin: it delegates to focused modules (`image_pipeline`, `plausibility`, `confirmation`, `correction`, `rate_tracker`, `leak_detector`, `mqtt_publisher`, `persistence`, `metrics`, etc.). New behaviour should go in one of these modules rather than growing `watermeter_service.py`.

**Parameters:**
- Prefer keyword arguments for optional or multi-field calls, e.g. `PlausibilityChecker.validate_plausibility(new_value=..., previous_value=..., last_update_time=...)`.
- Configuration is passed as the parsed config dict (`config: Dict`) or as a sub-section. Modules read their own section keys (`config["plausibility"]`, `config["inference"]`). The dict is the contract; add new keys to the schema (below).
- Constructor injection is used for collaborators: `PlausibilityChecker(config=..., rate_tracker=...)`.

**Return values:**
- Predicates and validators return tuples `(is_valid, warnings)` or a dataclass (`PlausibilityResult`). Keep that shape for new validators.
- Functions that can legitimately have no answer return `Optional[...]` / `X | None` and the caller checks for `None`.

## Configuration

- All reads and writes of `config.yaml` go through `watermeter/config_utils.py` (ruamel.yaml, comment-preserving; `preserve_quotes=True`, mapping indent 2, sequence indent 4 with offset 2). Do not call `yaml.safe_load` on the user config in new code. `WatermeterService.__init__` currently uses PyYAML for the runtime read; keep the write path on `config_utils`.
- Every new config field must be added to the validation schema in `config_utils.py`. The JSON schema is served at `/api/config/schema.json` and is checked by `tests/unit/test_api_routes.py`.
- Env-var substitution is supported in config values; see `tests/unit/test_env_var_substitution.py` for the accepted syntax before adding new substitutions.
- Secrets come from environment variables (`MQTT_USERNAME`, `MQTT_PASSWORD`), never from committed config. The repo `config.yaml` uses placeholders.

## Module Design

**Exports:** no `__all__`; modules expose their public names directly. Import the name you need from the defining module (`from watermeter.plausibility import PlausibilityChecker`).

**Barrel files:** `watermeter/__init__.py` and `watermeter/routes/__init__.py` are empty (0 bytes) and do not re-export the public API. Import from the concrete module.

**Singletons:** service and model manager are module-level singletons accessed through getters: `get_inference_service()` (`watermeter/inference.py`), `get_service` / `watermeter_service` (`watermeter/routes/service.py`), `_model_manager` (`watermeter/model_manager.py`). Tests reset or patch them (see `tests/regression/test_nan_class_label.py`).

**Concurrency:**
- `process_reading` is serialized by an `asyncio.Lock`. Use `asyncio.Lock` for async-only state.
- Use `threading.Lock` for state touched from daemon threads (training jobs in `watermeter/training_manager.py`, file writers). 5 `threading.Lock`/`RLock` usages exist.
- Blocking work called from async code goes through `asyncio.to_thread` or `run_in_executor` (7 call sites).

## Model/Data Conventions

- Model directories: `/app/models/{digits,arrows}/<id>/` with `<id>.xml`/`.bin` plus `metadata.json`. Read metadata through `ModelManager`, not by hand.
- Persisted runtime data is JSON under `/data` (state, failures, metrics), written through `StateStore`, `FailureStore`, `PipelineMetrics`. Do not add a second writer for the same file.
- Calibration files are versioned (`CALIBRATION_VERSION`); bump the version when the on-disk shape changes.

## Frontend Conventions (Jinja2 + HTMX)

- Templates in `watermeter/templates/`, static JS/CSS in `watermeter/static/`. Vanilla JS per page; no framework.
- Theming uses CSS variables in `watermeter/static/style.css` (light and dark). Avoid inline `style=` attributes; add a class and a variable instead.

## Git and Commits

- Commit messages start with `claude: ` (for example `claude: calibration tab review fixes`). Keep the subject in the imperative and short.
- Never amend or force-push without explicit approval. Do not commit real IPs, hostnames, credentials or home paths; the repo is public.

---

*Convention analysis: 2026-10-11*
