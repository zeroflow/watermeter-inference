# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

AI water meter reader. A FastAPI service fetches camera images (AI-on-the-edge ESP32-CAM). It aligns them via marker templates, crops digit and arrow ROIs, and runs OpenVINO classifiers/regressors on the crops. It then validates the reading for plausibility and publishes it via MQTT to Home Assistant. The same container also hosts a dashboard, a labeling UI, and in-process PyTorch training.

## Hard rules

- **Port 8001 / container `watermeter-dashboard-prod` is production: never stop, restart, rebuild, or exec into it.** Use the debug container instead (port 8002).
- Never force-push or rewrite history without explicit user approval.

## Commands

```bash
# Environment: uv-managed venv in .venv (setup.sh is stale, don't use it)
.venv/bin/python -m pytest                          # default: excludes `integration` marker (pytest.ini)
.venv/bin/python -m pytest tests/unit/test_foo.py::test_bar
.venv/bin/python -m pytest tests/unit tests/regression -q   # what to run after changes
.venv/bin/python -m pytest tests/integration --base-url=http://localhost:8002
#   without --base-url (or WATERMETER_TEST_URL) the integration fixture builds the image and starts its own container

uvx ruff check watermeter/ tests/                   # CI runs this
uvx black watermeter/ tests/                        # line length 120, py310
```

CI (`.github/workflows/ci.yml`, mirrored in `.gitea/`) runs ruff plus `pytest tests/unit` on push/PR to `main`.

Running the app:
- `./debug.sh --detach [--purge-models]`: builds the image and runs `watermeter-dashboard-debug` on **:8002**. It persists state in `config_debug/`, `data_debug/`, and `models_debug/`, and mounts `./digits` and `./arrows` as `/training/*`. Logs: `docker logs watermeter-dashboard-debug`.
- `./debug_clean.sh`: an ephemeral first-boot test on **:8003**. An nginx sidecar serves `tests/fixtures/meter_snapshot.jpg` as the camera, and the container has no persistent volumes.
- `./oneshot_test.sh`: tests the one-shot CLI (`python -m watermeter --one-shot --config <cfg>`, exit 0 = success) against the same stub.
- Inside the container the app always listens on 8001 (`uvicorn watermeter.app:app`).

## Architecture

**Startup** (`watermeter/app.py` lifespan): `WatermeterService` singleton → `InferenceService.initialize` (tolerates missing models) → MQTT → stats loop → cyclic trigger loop (if `trigger.mode` is cyclic/both) → initial reading. Routers live in `watermeter/routes/`, one per feature area.

**`WatermeterService`** (`watermeter_service.py`) is a facade over focused modules:
- `image_pipeline`
- `meter_state`
- `plausibility`
- `confirmation`
- `correction`
- `rate_tracker`
- `leak_detector`
- `mqtt_publisher`
- `low_confidence_capture`
- `data_collector`
- `persistence`
- `metrics`

**Reading pipeline** (`process_reading`, serialized by an asyncio lock):
1. Trigger: MQTT `trigger_topic`, the cyclic loop, or `POST /api/trigger`.
2. Fetch from `images.src`: the whole image, or one image per ROI if `images.process_separate`.
3. `image_pipeline`: fisheye/rotation, then marker alignment, then ROI crop. Alignment **fails closed**. Consecutive failures move the pipeline status through OK → DEGRADED → FAILED → STALE, with an MQTT notification on STALE.
4. `inference.py`:
   - Digits: an 11-class classifier (`0`–`9` plus `NAN`; it is `NAN`, never `N`).
   - Arrows: a classifier or a sin/cos regressor. The mode comes from the model's `metadata.json` `training_mode`. Alternatively, `inference.arrows_mode: opencv` uses a classical detector.
5. Total → cross-arrow consistency → optional correction → plausibility (max change, reverse flow, leak) → optional MQTT confirmation flow → persist → publish to Home Assistant.
6. Rejections keep the previous value. `POST /api/reset` clears the baseline, e.g. after long downtime.

**Config:**
- All config reads and writes go through `config_utils.py`, which uses ruamel.yaml so comments survive. New config fields must be added to its validation schema.
- The repo-root `config.yaml` is the shipped default. The Dockerfile copies it to `/config_default`, and `docker-entrypoint.sh` copies it to `/config` on first run.
- The entrypoint also:
  - remaps the user to `PUID`/`PGID`;
  - installs `pretrained/` models when `/app/models` is empty;
  - drops privileges.
- Credentials can come from the `MQTT_USERNAME`/`MQTT_PASSWORD` env vars.
- The legacy `aiote:` section is ignored with a warning.

**Storage:**
- State, failure history, and metrics are JSON files under `/data`.
- Models live in `/app/models/{digits,arrows}/<id>/`, each as `<id>.xml`/`.bin` (OpenVINO IR) plus `metadata.json`. The active model is the `.xml` path in `inference.{digits,arrows}_model`, and hot-reload happens via `reload_models`.
- Training data is at `/training/{digits,arrows}/{input,ground_truth/<class>}`; ground truth is gitignored.

**Training** (`training_manager.py`, `training_core.py`):
- Jobs run in daemon threads inside the service process: timm/PyTorch → ONNX → OpenVINO IR.
- `HF_TOKEN` is needed only to download pretrained weights.
- `train_*.py`, `benchmark_*.py`, and the notebooks are legacy standalone scripts.

**Frontend:** Jinja2 templates plus HTMX polling plus vanilla JS per page (`watermeter/static/`), with no framework. Theming uses CSS variables in `style.css` (light/dark); avoid inline styles.

## Conventions

- Build any filesystem path from user input via `safe_subpath()` (`watermeter/app.py`), and validate model IDs with `_validate_model_id()` (`model_manager.py`).
- No API auth and no SSRF protection are deliberate, documented decisions (`docs/decisions.md`). Don't "fix" them unasked.
- Backlog: `backlog.md`, with items `BL-{id}` and status `idea → planned → in-progress → done`. Bump "Next ID" when adding items. Design docs for larger work go in `docs/plans/YYYY-MM-DD-name.md`.

## Git

- Remotes: `origin` = GitHub `zeroflow/watermeter-inference` (public, primary), `gitea` = local Gitea mirror. Always push to both.
- Commit and push directly to `main` (user preference); no `claude/*` branches or PRs unless asked. Every push to `main` runs CI and publishes `ghcr.io/zeroflow/watermeter-inference:dev`.
- Releases: tag `vX.Y.Z` on `main` and push the tag. The Docker workflow then publishes `:X.Y.Z`, `:X.Y` and `:latest`.
- Commit messages start with `claude: `. The committer identity is set repo-locally to `zeroflow`.
- The repo is public, so never commit real IPs, hostnames, credentials, or home paths. Configs use placeholders, and secrets come from `.env` (gitignored).
