---
last_mapped_commit: 721bd40e2f58d1951323f3b24679dc2436db1362
last_mapped_at: 2026-10-11
---
# Codebase Structure

**Analysis Date:** 2026-10-11

## Directory Layout

```
watermeter-inference/
├── watermeter/              # Application package (FastAPI service, all runtime code)
│   ├── routes/              # One APIRouter per feature area
│   ├── templates/           # Jinja2 pages and HTMX fragments
│   └── static/              # Per-page vanilla JS and style.css
├── tests/                   # pytest suites: unit, regression, export, integration
│   ├── unit/                # Fast, default suite (~670 tests)
│   ├── integration/         # Needs running container or builds image (marker: integration)
│   ├── regression/          # Pinned bug regressions
│   ├── export/              # OpenVINO export tests
│   └── fixtures/            # Sample images (e.g. meter_snapshot.jpg)
├── scripts/                 # Offline replay, calibration, and tuning scripts
├── docs/                    # Decisions, design plans, task notes, reviews
│   ├── plans/               # Dated design docs: YYYY-MM-DD-name.md
│   ├── tasks/               # Historical backlog task write-ups
│   └── reviews/             # Review reports
├── pretrained/              # Shipped default OpenVINO models (digits/, arrows/)
├── digits/                  # Digit training notebook, ground truth, reference crops
├── arrows/                  # Arrow training notebook, ground truth, reference crops
├── .github/workflows/       # CI (ci.yml) and Docker publish (docker.yml)
├── .gitea/workflows/        # Mirror of CI for the local Gitea remote
├── config.yaml              # Shipped default configuration (copied to /config on first run)
├── Dockerfile               # Image build; copies watermeter/, pretrained/, config.yaml
├── docker-compose.yml       # Production-style service definition
├── docker-compose.gpu.yml   # GPU override
├── docker-entrypoint.sh     # Container entrypoint (PUID/PGID, model install, privilege drop)
├── debug.sh                 # Debug container on port 8002
├── debug_clean.sh           # Ephemeral first-boot test on port 8003
├── oneshot_test.sh          # One-shot CLI test against a stub camera
├── run.sh, setup.sh         # Local run helpers (setup.sh is stale per CLAUDE.md)
├── send_models.sh           # Model transfer helper
├── train_digits.py          # Legacy standalone training script
├── train_arrows.py          # Legacy standalone training script
├── benchmark_digits.py      # Legacy standalone benchmark script
├── benchmark_arrows.py      # Legacy standalone benchmark script
├── pyproject.toml           # Project metadata and tool config (ruff, black, pytest)
├── pytest.ini               # Default pytest options (excludes integration marker)
├── requirements*.txt        # Runtime, dev, docker, and arrows-specific dependencies
├── backlog.md               # Active backlog (BL-{id} items)
├── backlog_archiv.md        # Archived backlog items
├── plan_failure_tracking.md # Failure tracking plan notes
├── review_*.md              # Earlier review notes (python, structure)
├── DATA_PROVENANCE.md       # Training data provenance notes
├── Integrated_training.md   # Training workflow notes
└── README.md, DOCKER.md     # User documentation
```

Directories created at runtime and not tracked: `config/`, `data/`, `models/`, `training/` inside the container (`/config`, `/data`, `/app/models`, `/training`). Locally, `config_debug/`, `data_debug/`, `models_debug/` are created by `debug.sh`. Use `.gitignore` as the source of truth for what is excluded.

## Directory Purposes

**`watermeter/`:**
- Purpose: The whole runtime package. Imported as `watermeter.*`.
- Contains: Service orchestration, image pipeline, inference, arrow backends, plausibility and confirmation logic, MQTT publisher, persistence, training, model management, config utilities, CLI, and FastAPI app.
- Key files: `watermeter/app.py`, `watermeter/watermeter_service.py`, `watermeter/inference.py`, `watermeter/image_pipeline.py`, `watermeter/config_utils.py`.

**`watermeter/routes/`:**
- Purpose: HTTP surface. One router per feature, all included in `watermeter/app.py`.
- Contains: `pages.py` (HTML pages), `service.py` (status, trigger, reset, set value, health), `config.py` (config editor API), `roi.py` (ROI and marker setup, largest route file at ~876 lines), `label.py` (manual labeling), `training.py` (training jobs), `models.py` (model management and benchmarks, ~908 lines), `synthetic.py` (synthetic data), `mqtt.py` (MQTT settings), `metrics.py` (metrics), `calibration.py` (arrow calibration tab).
- Key files: `watermeter/routes/service.py` (reading control endpoints), `watermeter/routes/pages.py` (page routes).

**`watermeter/templates/`:**
- Purpose: Jinja2 templates. Full pages plus HTMX fragments.
- Contains: One page template per feature (`dashboard.html`, `training.html`, `explore.html`, `label.html`, `roi_config.html`, `config_editor.html`, `calibration.html`), shared partials (`_nav.html`, `_theme.html`), and `status_fragment.html` (polled by HTMX).
- Key files: `watermeter/templates/_theme.html` (theme variables), `watermeter/templates/_nav.html` (navigation).

**`watermeter/static/`:**
- Purpose: Browser assets, served at `/static`.
- Contains: One JS file per page (`dashboard.js`, `training.js`, `explore.js`, `label.js`, `roi-config.js`, `config-editor.js`, `mqtt-config.js`, `calibration.js`) and `style.css` (CSS variables for light and dark themes).
- Key files: `watermeter/static/style.css`.

**`tests/unit/`:**
- Purpose: Default test suite. Covers pure functions, service logic, routes, and training helpers.
- Contains: `test_*.py` modules named after the feature they test, plus `conftest.py`, `synthetic_dial.py` (test helper for rendered dials).
- Key files: `tests/unit/conftest.py`, `tests/unit/test_calculate_total.py`, `tests/unit/test_plausibility.py`, `tests/unit/test_api_routes.py`.

**`tests/integration/`:**
- Purpose: End-to-end checks against a running container (`--base-url`) or one built by the fixture.
- Contains: `conftest.py` (container lifecycle), `test_smoke.py`, `test_oneshot.py`, `test_training.py`, and others.

**`tests/regression/`:**
- Purpose: Locks in specific past bugs (e.g. NAN class label, config comment preservation).
- Key files: `tests/regression/test_nan_class_label.py`, `tests/regression/test_config_comments.py`.

**`scripts/`:**
- Purpose: Offline utilities that import the `watermeter` package. Not part of the server.
- Contains: `replay_cascade.py`, `replay_plausibility.py`, `replay_alignment_compare.py`, `replay_marker_confidence.py`, `calibrate_arrows.py`, `tune_synthetic.py`, `backfill_training_samples.py`, `fetch_upstream_data.sh`.

**`docs/`:**
- Purpose: Project knowledge base.
- Contains: `decisions.md` (accepted decisions AD-01 and AD-02, dated entries), `plans/` (design and implementation plans, `YYYY-MM-DD-<name>.md`, with `-design` and `-plan` suffixes for pairs), `tasks/` (task notes named `YYYY_MM_DD_BLxx_<Name>.md`), `reviews/` (review reports).

**`pretrained/`:**
- Purpose: Default models shipped in the image. `Dockerfile` copies `pretrained/digits/` to `/app/digits/selected/` and `pretrained/arrows/` to `/app/arrows/selected/`. The entrypoint installs them into `/app/models` when that is empty.
- Contains: `digits/` and `arrows/`, each with an OpenVINO `.xml`/`.bin` pair and `metadata.json`.
- Generated: No (committed binaries). Committed: Yes.

**`digits/` and `arrows/` (repo root):**
- Purpose: Training notebook (`Digits.ipynb`, `Arrows.ipynb`), `requirements.txt` (arrows), `reference/` crops by class, and `ground_truth/` (gitignored, local training labels). Not imported at runtime.
- Generated: `ground_truth/` contents are local data. Committed: only `reference/` and notebooks.

**`.planning/` (repo root):**
- Purpose: GSD planning artifacts. `.planning/codebase/` holds these analysis documents.
- Generated: Yes (by mapping). Committed: depends on project workflow.

## Key File Locations

**Entry Points:**
- `watermeter/app.py`: ASGI app `app`, lifespan, router wiring, `main()`.
- `watermeter/__main__.py`: `python -m watermeter`, with `--one-shot` and `--config`.
- `watermeter/oneshot.py`: One-shot reading used by the CLI.
- `docker-entrypoint.sh`: Container start.

**Configuration:**
- `config.yaml`: Shipped default. Sections: `images`, `detection`, `alignment`, `trigger`, `mqtt`, `homeassistant`, `inference`, `plausibility`, `correction`, `confirmation`, `low_confidence`, `persistence`, `dashboard`, `logging`.
- `watermeter/config_utils.py`: All config reads and writes. New fields must be added to its validation schema.
- `pyproject.toml`: Ruff, Black (line length 120, py310), pytest settings.
- `pytest.ini`: Default pytest markers and options.
- `.env.example`: Template for environment variables (copy to `.env`, which is gitignored).

**Core Logic:**
- `watermeter/watermeter_service.py`: `WatermeterService`, `process_reading`.
- `watermeter/image_pipeline.py`: Fetch, geometry, alignment, ROI crop.
- `watermeter/feature_alignment.py`: Feature-based alignment fallback.
- `watermeter/inference.py`: Classifiers, regressor, `InferenceService`.
- `watermeter/position_utils.py`: `calculate_total`, carry logic.
- `watermeter/plausibility.py`, `watermeter/reanchor.py`, `watermeter/rate_tracker.py`, `watermeter/leak_detector.py`: Validation.
- `watermeter/confirmation.py`, `watermeter/correction.py`: Optional flows.
- `watermeter/mqtt_publisher.py`: Home Assistant MQTT and discovery.
- `watermeter/persistence.py`: `/data` JSON state and failure history.
- `watermeter/training_manager.py`, `watermeter/training_core.py`: Training.
- `watermeter/model_manager.py`: Model directories and activation.
- `watermeter/arrow_calibration.py`, `watermeter/calibrated_arrows.py`, `watermeter/calibration_session.py`, `watermeter/opencv_arrows.py`: Arrow readers and calibration.

**Testing:**
- `tests/unit/`: Default suite. Run `.venv/bin/python -m pytest tests/unit tests/regression -q`.
- `tests/integration/`: Run with `--base-url=http://localhost:8002`.
- `tests/conftest.py`: Shared fixtures.

## Naming Conventions

**Files:**
- Python modules: `snake_case.py`, named by responsibility (`position_utils.py`, `rate_tracker.py`). Route modules are named after the feature area (`routes/training.py`).
- Tests: `tests/unit/test_<module_or_feature>.py`. Example: `tests/unit/test_plausibility.py` tests `watermeter/plausibility.py`.
- Static JS: `kebab-case.js` for multi-word page names (`roi-config.js`, `config-editor.js`), `lowercase.js` otherwise.
- Templates: `snake_case.html` matching the page (`roi_config.html`). Partials and fragments are prefixed with `_` (`_nav.html`) or suffixed `_fragment` (`status_fragment.html`).
- Design docs: `docs/plans/YYYY-MM-DD-<slug>.md`, with `-design` and `-plan` suffixes for paired files.
- Task notes: `docs/tasks/YYYY_MM_DD_BL<nn>_<PascalName>.md`.
- Models: `/app/models/{digits,arrows}/<id>/<id>.xml` plus `metadata.json`. IDs are validated by `_validate_model_id()`.

**Directories:**
- Lowercase, single word (`routes`, `templates`, `static`, `scripts`, `docs`, `tests`).
- Model and training directories are keyed by type: `digits` and `arrows`.

**Symbols:**
- Functions and variables: `snake_case`. Classes: `PascalCase`. Module-level singletons use a leading underscore (`_service`, `_inference_service`).
- Class label for "no digit": `NAN`, never `N`.

## Where to Add New Code

**New reading-pipeline logic (validation, correction, value computation):**
- Implementation: a new focused module in `watermeter/` (for example `watermeter/<feature>.py`), then a thin delegating method on `WatermeterService` in `watermeter/watermeter_service.py`.
- Tests: `tests/unit/test_<feature>.py`.
- Do not add more methods to the facade body when a focused module can own the logic.

**New HTTP endpoint or page:**
- Route: add to the matching `watermeter/routes/<area>.py`, or create `watermeter/routes/<area>.py` with `router = APIRouter(...)` and register it in `watermeter/app.py` with `app.include_router(...)` and a `tags_metadata` entry if it is a new area.
- Template: `watermeter/templates/<page>.html` (add a link in `_nav.html`).
- JS: `watermeter/static/<page>.js`. Styles go in `watermeter/static/style.css` using CSS variables, not inline styles.
- Tests: `tests/unit/test_<area>_routes.py` or extend `tests/unit/test_api_routes.py`.
- Any user-supplied filesystem path must go through `safe_subpath()` (defined in `watermeter/app.py`). Model IDs go through `_validate_model_id()`.

**New arrow or digit backend:**
- Implementation: a new module next to `watermeter/opencv_arrows.py` and `watermeter/calibrated_arrows.py`. Wire it into `_create_arrows_backend` in `watermeter/inference.py`. Add its config keys to `config.yaml` and the validation schema in `watermeter/config_utils.py`.
- Tests: `tests/unit/test_<backend>.py`.

**New config field:**
- Default: `config.yaml` (with a comment).
- Schema: `watermeter/config_utils.py`.
- Regression test: `tests/regression/test_config_comments.py` pattern, to ensure comments survive writes.

**New training or model feature:**
- Implementation: `watermeter/training_core.py` (PyTorch helpers), `watermeter/training_manager.py` (job orchestration), `watermeter/model_manager.py` (files and activation).
- Routes: `watermeter/routes/training.py` or `watermeter/routes/models.py`.
- Tests: `tests/unit/test_training_*.py`, `tests/unit/test_model_manager.py`.

**New offline analysis tool:**
- Implementation: `scripts/<name>.py`. It may import `watermeter` modules but should not start the server.

**New design or backlog item:**
- Backlog: `backlog.md`, with `BL-{id}` items and status `idea → planned → in-progress → done`. Bump "Next ID" when adding.
- Design doc: `docs/plans/YYYY-MM-DD-<slug>.md`.

**Utilities:**
- Shared path and ID helpers: `watermeter/app.py` (`safe_subpath`), `watermeter/model_manager.py` (`_validate_model_id`). Image helpers: `watermeter/photo_master.py`, `watermeter/image_hash.py`.
- Do not import from `watermeter/app.py` in new route modules (see Anti-Patterns in ARCHITECTURE.md).

## Special Directories

**`pretrained/`:**
- Purpose: Shipped default models.
- Generated: No. Committed: Yes.

**`digits/ground_truth/` and `arrows/ground_truth/`:**
- Purpose: Local labeled training crops.
- Generated: By the labeling UI or training data collection. Committed: No (gitignored by `**/ground_truth/`).

**`/training/{digits,arrows}/{input,ground_truth/<class>}` (container path):**
- Purpose: Training data written by low-confidence capture and labeling.
- Generated: Yes. Committed: No.

**`/data` (container path):**
- Purpose: `state.json`, failure history, metrics JSON.
- Generated: Yes. Committed: No.

**`/config` (container path):**
- Purpose: Live copy of `config.yaml`, created by the entrypoint from `/config_default`.
- Generated: Yes. Committed: No.

**`/app/models` (container path):**
- Purpose: Active and archived model directories.
- Generated: Yes (trained or installed from `pretrained/`). Committed: No.

**`config_debug/`, `data_debug/`, `models_debug/` (repo root, local):**
- Purpose: State for the debug container started by `debug.sh`.
- Generated: Yes. Committed: No.

**`.planning/`:**
- Purpose: GSD planning and codebase analysis.
- Generated: Yes. Committed: depends on workflow.

---

*Structure analysis: 2026-10-11*
