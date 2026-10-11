---
last_mapped_commit: 721bd40e2f58d1951323f3b24679dc2436db1362
last_mapped_at: 2026-10-11
---
# Technology Stack

**Analysis Date:** 2026-10-11

## Languages

**Primary:**
- Python 3.10+ (code targets `py310` in `pyproject.toml`; CI and the local `.venv` use 3.12) - all application code in `watermeter/`, tests in `tests/`, and standalone scripts (`train_digits.py`, `train_arrows.py`, `benchmark_*.py`, `synthetic_generator.py`)

**Secondary:**
- HTML / Jinja2 templates - `watermeter/templates/`
- Vanilla JavaScript and CSS - `watermeter/static/` (no framework, no build step)
- YAML - runtime config `config.yaml`, `docker-compose.yml`, `docker-compose.gpu.yml`, CI workflows in `.github/workflows/` and `.gitea/workflows/`
- Shell - `docker-entrypoint.sh`, `debug.sh`, `debug_clean.sh`, `oneshot_test.sh`, `setup.sh` (stale, per CLAUDE.md), `run.sh`, `send_models.sh`
- Markdown - documentation under `docs/`, `README.md`, `DOCKER.md`, backlog in `backlog.md`

## Runtime

**Environment:**
- Production/runtime container: base image `openvino/ubuntu22_runtime:2025.0.0` (defined in `Dockerfile`). Ships OpenVINO runtime and a system Python 3 on Ubuntu 22.04.
- Local development: uv-managed virtualenv at `.venv/` (CPython 3.12, per `.venv/pyvenv.cfg`).
- CI (`.github/workflows/ci.yml`): `actions/setup-python` with Python `3.12`.
- Container runs as non-root user `watermeter` (UID/GID 1000) after the entrypoint drops privileges via `gosu`.

**Package Manager:**
- `uv` is the package manager for local work (`uv pip install -r requirements.txt -r requirements-dev.txt`). CLAUDE.md states no pip/poetry for local use.
- Docker image installs packages with `pip install` (see `Dockerfile`).
- Lockfile: Not detected. Dependencies are pinned by range only (`requirements*.txt`, `pyproject.toml` has no `[project]` dependency list).

## Frameworks

**Core:**
- FastAPI `>=0.100.0,<1.0.0` - HTTP API, dashboard pages, and routers in `watermeter/routes/` (entry point `watermeter.app:app`, `watermeter/app.py`)
- Uvicorn `>=0.23.0,<1.0.0` (with `standard` extras) - ASGI server; the container command is `python3 -m uvicorn watermeter.app:app --host 0.0.0.0 --port 8001` (`Dockerfile`)
- Pydantic (transitive via FastAPI; imported directly in `watermeter/routes/*.py`) - request models and validators
- Jinja2 `>=3.1.0,<4.0.0` - server-rendered templates in `watermeter/templates/`
- python-multipart `>=0.0.9,<1.0.0` - form parsing for FastAPI

**Inference:**
- OpenVINO `>=2025.0.0,<2026.0.0` (pip package locally and in CI; provided by the base image in Docker) - model loading and inference via `ov.Core().compile_model()` in `watermeter/inference.py` and `watermeter/training_manager.py`. Device selected by `inference.device` (`AUTO`, `CPU`, `GPU`).
- OpenCV headless `>=4.8.0,<5.0.0` (`opencv-python-headless`) - image alignment, feature matching, ROI cropping (`watermeter/image_pipeline.py`, `watermeter/opencv_arrows.py`, `watermeter/feature_alignment.py`)
- NumPy `>=1.24.0,<3.0.0` and SciPy `>=1.11.0,<2.0.0` - numerics
- Pillow `>=10.0.0,<13.0.0` - image I/O

**Training (in-process, daemon threads):**
- PyTorch `>=2.1.0,<3.0.0` and torchvision `>=0.16.0,<1.0.0` - CPU-only wheels from `https://download.pytorch.org/whl/cpu` (installed before other deps in `Dockerfile`)
- timm `>=1.0.0,<2.0.0` - model architectures; `timm.create_model(..., pretrained=True)` in `watermeter/training_manager.py`
- ONNX `>=1.15.0,<2.0.0` and onnxscript `>=0.1.0,<1.0.0` - PyTorch -> ONNX export path (`torch.onnx.export` in `watermeter/training_core.py`); OpenVINO IR conversion via `ov.convert_model()`
- scikit-learn `>=1.3.0,<2.0.0` - `compute_class_weight` in `watermeter/training_core.py`
- matplotlib `>=3.7.0,<4.0.0` - training plots, Agg backend (`watermeter/training_manager.py`)

**Testing:**
- pytest `>=7.0.0,<9.0.0` - config in `pytest.ini` (`testpaths = tests`, default run excludes the `integration` marker)
- pytest-asyncio `>=0.21.0,<1.0.0` - async tests
- httpx `>=0.24.0,<1.0.0` - FastAPI TestClient backend and integration tests
- Integration tests target a running server via `--base-url` (default: `http://localhost:8002`)

**Build/Dev:**
- Docker / Docker Compose - image build (`Dockerfile`), deployment (`docker-compose.yml`), GPU overlay (`docker-compose.gpu.yml`)
- Ruff (`uvx ruff`, `select = E, F, W, I`, line length 120) - lint, configured in `pyproject.toml`
- Black (`uvx black`, line length 120, target `py310`) - formatting, configured in `pyproject.toml`
- GitHub Actions (`.github/workflows/ci.yml`, `docker.yml`) and Gitea Actions mirror (`.gitea/workflows/`)
- Dependency-free repository config: `pyproject.toml` contains only tool sections (no `[project]` or `[build-system]`)

## Key Dependencies

**Critical:**
- `openvino` 2025.x - the only inference runtime for production models; models are OpenVINO IR (`.xml` + `.bin`) under `/app/models/{digits,arrows}/<id>/`
- `opencv-python-headless` 4.8-4.x - alignment and arrow geometry; headless build avoids GUI libraries
- `fastapi` / `uvicorn` - all HTTP surface
- `paho-mqtt` `>=2.1.0,<3.0.0` - MQTT client (paho v2 callback API) in `watermeter/mqtt_publisher.py`; creates `mqtt.Client(...)` at line ~597 and uses paho's built-in reconnect backoff
- `ruamel.yaml` `>=0.18.0,<1.0.0` - round-trip YAML so comments in `config.yaml` survive edits (`watermeter/config_utils.py`)

**Infrastructure:**
- `httpx` `>=0.24.0,<1.0.0` - async HTTP client used to fetch camera images (`watermeter/image_pipeline.py`, lines ~154 and ~186), ROI preview fetches (`watermeter/routes/roi.py`), and tests
- `pyyaml` `>=6.0.0,<7.0.0` and `jsonschema` `>=4.0.0,<5.0.0` - config parsing and schema validation (`watermeter/config_utils.py` contains the JSON Schema, draft 2020-12)
- `timm`, `torch`, `torchvision`, `onnx`, `onnxscript`, `scikit-learn`, `matplotlib` - training stack only; not needed for inference-only operation
- `uv` - recommended local installer (not a runtime dependency)

**Other notable packages present in code but not pinned in `requirements.txt`:**
- `pydantic` (transitive via FastAPI)
- `gosu` (apt package in `Dockerfile`, used by `docker-entrypoint.sh` to drop privileges)
- `libxcb*` apt packages in `Dockerfile` (runtime libraries for OpenCV)

## Configuration

**Environment:**
- Runtime config file: `/config/config.yaml` inside the container, mapped from `./config` on the host. First run copies the shipped default from `config.yaml` (repo root) to `/config_default/config.yaml`, then `docker-entrypoint.sh` copies it to `/config`.
- Config values support `${VAR_NAME}` substitution from environment variables (`watermeter/config_utils.py`, `_substitute_env`); unset variables are left as literal text.
- `.env` file: present in the repo root (gitignored). Its contents are not documented here. `.env.example` lists the variable names used by compose: `HF_TOKEN`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `LOG_LEVEL`, `RENDER_GROUP`, `WATERMETER_TAG`, `PUID`, `PGID`.
- Environment variables read by application code:
  - `MQTT_USERNAME`, `MQTT_PASSWORD` - override `mqtt.username` / `mqtt.password` (`watermeter/mqtt_publisher.py` ~lines 603-604)
  - Config-substitution variables as referenced in `config.yaml`
  - `HF_HOME` - set to `/app/.cache/huggingface` in `Dockerfile`
  - `HF_TOKEN` - needed only to download pretrained weights for training (`.env.example`)
  - `PUID`, `PGID` - read by `docker-entrypoint.sh` to remap the `watermeter` user
  - `LOG_LEVEL` - overrides logging level (documented in `.env.example` and `docker-compose.yml`)
- Key config sections in `config.yaml` (repo root, shipped default): `images`, `detection`, `alignment`, `trigger`, `mqtt`, `homeassistant`, `inference`, `plausibility`, `correction`, `confirmation`, `low_confidence`
- Config changes are made via `config_utils.py` (round-trip YAML) only. New fields must be added to its validation schema.
- Legacy `aiote:` section is ignored with a warning (CLAUDE.md).

**Build:**
- `Dockerfile` - base `openvino/ubuntu22_runtime:2025.0.0`, CPU torch wheels, `requirements-docker.txt`, creates UID 1000 user `watermeter`, exposes port 8001
- `requirements.txt` - core runtime + training deps (local development)
- `requirements-docker.txt` - container deps (openvino excluded; supplied by base image)
- `requirements-dev.txt` - pytest, pytest-asyncio, httpx
- `pyproject.toml` - Black and Ruff settings only
- `pytest.ini` - pytest markers and options
- `.dockerignore` - build context exclusions
- `docker-compose.yml` - service `watermeter` (container `watermeter-dashboard`), image `ghcr.io/zeroflow/watermeter-inference:${WATERMETER_TAG:-latest}`, port `8001:8001`, healthcheck hitting `/health` every 30s
- `debug.sh` - builds and runs `watermeter-dashboard-debug` on port 8002 with `config_debug/`, `data_debug/`, `models_debug/`
- `debug_clean.sh` - ephemeral first-boot test on port 8003

**Image publishing:**
- `.github/workflows/docker.yml`: pushes to `main` publish `ghcr.io/<repo>:dev` and `sha-<short>`; tags `v*` publish `:<version>`, `:<major>.<minor>`, `:latest`. PRs build and smoke-test only (`curl /health` on port 8001).
- Actions are pinned by commit SHA.

## Platform Requirements

**Development:**
- Linux host (Proxmox PVE per CLAUDE.md); Python 3.12 via uv; Docker for debug container and image builds
- Optional Intel iGPU: `/dev/dri/renderD128` plus render group (`RENDER_GROUP`, default 44) for OpenVINO GPU (`docker-compose.gpu.yml`)
- Camera: AI-on-the-edge ESP32-CAM style HTTP image endpoint (`images.src` in `config.yaml`)

**Production:**
- Docker host running the `watermeter-dashboard` container (port 8001) with bind mounts for `/config`, `/data`, `/app/models`, `/training/*` and a named volume `hf_cache`
- CPU inference by default; Intel GPU optional
- Requires reachable MQTT broker (Home Assistant integration optional, `homeassistant.enabled`)

---

*Stack analysis: 2026-10-11*
