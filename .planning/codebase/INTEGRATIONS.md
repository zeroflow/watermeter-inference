---
last_mapped_commit: 721bd40e2f58d1951323f3b24679dc2436db1362
last_mapped_at: 2026-10-11
---
# External Integrations

**Analysis Date:** 2026-10-11

## APIs & External Services

**Camera image source (required):**
- AI-on-the-edge ESP32-CAM style HTTP endpoint serving a JPEG (default path `img_tmp/alg.jpg`)
  - Configured in: `images.src` in `config.yaml` (placeholder `http://<camera-host>/img_tmp/alg.jpg`)
  - Client: `httpx.AsyncClient` in `watermeter/image_pipeline.py` (~lines 154, 186); ROI preview fetches in `watermeter/routes/roi.py` (~lines 117, 163)
  - Auth: None (no credentials sent)
  - With `images.process_separate: true`, one image is fetched per ROI instead of a single whole-image fetch.

**Hugging Face Hub (training only):**
- Downloads pretrained timm weights via `timm.create_model(..., pretrained=True)` in `watermeter/training_manager.py` (~lines 662, 665)
  - SDK/Client: `timm` / `huggingface_hub` (transitive)
  - Auth: `HF_TOKEN` environment variable (see `.env.example`); required only for training with downloads
  - Cache: `HF_HOME=/app/.cache/huggingface`, mounted as the named volume `hf_cache` in `docker-compose.yml`
  - Not used for inference; the shipped `pretrained/` models are bundled in the image.

**PyTorch wheel index (build time only):**
- `https://download.pytorch.org/whl/cpu` - CPU-only torch/torchvision install in `Dockerfile` and in `.github/workflows/ci.yml`

**Container registry:**
- GitHub Container Registry `ghcr.io/zeroflow/watermeter-inference` - images published by `.github/workflows/docker.yml` using `GITHUB_TOKEN` (`packages: write`)
- Pulled by `docker-compose.yml` via `WATERMETER_TAG` (default `latest`)

**Base image registry:**
- Docker Hub `openvino/ubuntu22_runtime:2025.0.0` (`Dockerfile`)

**Apt repositories:** standard Ubuntu 22.04 mirrors for `gosu` and `libxcb*` packages in `Dockerfile`.

**Source hosting / CI mirrors:**
- GitHub: `origin` (public primary repo); GitHub Actions in `.github/workflows/`
- Gitea: `gitea` mirror on a local network host; Gitea Actions mirror in `.gitea/workflows/`. Local Gitea hosts are not documented here (see CLAUDE.md).

## Data Storage

**Databases:**
- None. No SQL or NoSQL database client is present.

**File storage (local filesystem, JSON and binary):**
- State, failure history, metrics: JSON files under `/data` (container path; bind mount `./data`). Examples: `state.json`, readings history, `arrow_calibration.json` (`inference.calibrated_arrows.calibration_file` in `config.yaml`, default `/data/arrow_calibration.json`).
- Raw image archive (optional): `alignment.archive_dir` (default `/data/raw_archive`), retention `archive_max_age_days` (default 7), enabled by `archive_raw_images`.
- Models: `/app/models/{digits,arrows}/<id>/` containing `<id>.xml`, `<id>.bin` (OpenVINO IR) and `metadata.json`. Bind mount `./models`. Bootstrap models in the image at `/app/digits/selected/` and `/app/arrows/selected/` (copied from `pretrained/`) and installed by the entrypoint when `/app/models` is empty.
- Training data: `/training/{digits,arrows}/input` and `/training/{digits,arrows}/ground_truth/<class>` (bind mounts `./training/digits`, `./training/arrows`). Ground truth is gitignored.
- Low-confidence captures: `low_confidence.save_path` (default `/training`), rate-limited by `save_rate_limit` (seconds).
- Config: `/config/config.yaml` (bind mount `./config`), round-trip edited via `watermeter/config_utils.py`.

**Caching:**
- None beyond the Hugging Face model cache volume (`hf_cache`) and in-process state. No Redis or memcached.

## Authentication & Identity

**Auth Provider:**
- None for the HTTP API or dashboard. This is a deliberate decision documented in `docs/decisions.md`; do not add auth without user direction.
- SSRF protection: none, deliberately (`docs/decisions.md`).

**MQTT credentials:**
- Optional username/password. Sources, in order: `MQTT_USERNAME` / `MQTT_PASSWORD` environment variables, then `mqtt.username` / `mqtt.password` in config (`watermeter/mqtt_publisher.py` ~lines 603-604).
- No TLS configuration is present in `watermeter/mqtt_publisher.py` (plain TCP on `mqtt.port`, default 1883).

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry or similar).

**Logs:**
- Python `logging` to stdout/stderr; viewed with `docker logs`. Log level from `LOG_LEVEL` env var or config.
- Health endpoint: `GET /health` on port 8001 (used by `docker-compose.yml` healthcheck and the CI smoke test in `.github/workflows/docker.yml`).
- Metrics: JSON persistence through `watermeter/metrics.py`, exposed in the dashboard and `watermeter/routes/metrics.py`.
- Pipeline status notifications: MQTT notification on STALE (see Webhooks & Callbacks below).

## MQTT Broker

**Role:** Central event and state bus. Used for triggering readings, publishing results to Home Assistant, reset commands, and optional confirmation flow.

**Client:** `paho-mqtt` `>=2.1.0,<3.0.0` (`paho.mqtt.client`), class `MqttPublisher` in `watermeter/mqtt_publisher.py`. Created with paho v2 callback API; reconnect backoff configured by paho.

**Configuration** (`config.yaml`, section `mqtt`):
- `broker` (placeholder `<broker-host>`), `port` (1883), `client_id` (`watermeter-ai-service-debug` in the shipped default; use a distinct id per instance), `keepalive` (60)
- Enabled/disabled by the presence of a reachable broker; the service keeps running without MQTT and paho retries in the background.

**Topics:**

| Direction | Topic (config key) | Default | Purpose |
|-----------|--------------------|---------|---------|
| Subscribe | `mqtt.trigger_topic` | `watermeter/status` | Trigger a reading when payload equals `mqtt.trigger_payload` (`Flow finished`) |
| Subscribe | `mqtt.reset_topic` | `watermeter/reset` | Reset the baseline (same effect as `POST /api/reset`) |
| Subscribe | `homeassistant/status` (hardcoded, QoS 1) | - | Home Assistant birth/LWT messages; triggers discovery republish |
| Subscribe | `confirmation.response_topic` | `watermeter/confirmation_response` | Operator answer when confirmation flow is enabled (`confirmation.enabled`, default false) |
| Publish | `homeassistant.publish_topic` | `homeassistant/sensor/watermeter/state` | Reading state |
| Publish | `confirmation.request_topic` | `watermeter/confirmation_request` | Confirmation request when a reading is flagged |
| Publish | discovery topics under `homeassistant.discovery_prefix` (default `homeassistant`) | - | Home Assistant MQTT auto-discovery config |
| Publish | STALE notification | - | Sent when the image pipeline reaches STALE after consecutive alignment failures |

**Home Assistant integration:**
- MQTT discovery under `homeassistant.discovery_prefix` (default `homeassistant`), device name from `homeassistant.device.name`.
- Gated by `homeassistant.enabled` (default `false` in the shipped config).
- Discovery and state logic: `watermeter/mqtt_publisher.py` (`ha_config = self.config["homeassistant"]`, ~lines 324, 384, 421).

## CI/CD & Deployment

**Hosting:**
- Self-hosted Docker host (Proxmox PVE environment per CLAUDE.md). Single compose service `watermeter`.
- Public image on GHCR.

**CI Pipelines:**
- GitHub Actions: `.github/workflows/ci.yml` (ruff on `watermeter/` and `tests/`, then `pytest tests/unit/ -x -q` on Python 3.12, with CPU torch and `openvino` 2025.x installed via pip)
- GitHub Actions: `.github/workflows/docker.yml` (buildx build, GHA layer cache, smoke test, push to GHCR on `main` and `v*` tags)
- Gitea mirror workflows: `.gitea/workflows/`

**Dependency update automation:** Not detected (no Dependabot or Renovate config found in the scanned paths).

## Environment Configuration

**Required env vars:**
- None strictly required for default operation. A camera URL must be set in `config.yaml` (`images.src`) before readings can happen.

**Optional env vars:**
- `HF_TOKEN` - training with downloaded pretrained weights only
- `MQTT_USERNAME`, `MQTT_PASSWORD` - broker auth
- `LOG_LEVEL` - log verbosity
- `PUID`, `PGID` - container user mapping (defaults 1000)
- `RENDER_GROUP` - Intel GPU render group id (default 44), `docker-compose.gpu.yml`
- `WATERMETER_TAG` - image tag (default `latest`)
- `HF_HOME` - set in image to `/app/.cache/huggingface`

**Secrets location:**
- `.env` at repo root (gitignored; never committed). Values are not recorded in planning docs.
- MQTT credentials may alternatively live in `config.yaml` under `mqtt` (use placeholders in the public repo).

## Webhooks & Callbacks

**Incoming:**
- HTTP API on port 8001 (FastAPI). Routers in `watermeter/routes/`, including `POST /api/trigger`, `POST /api/reset`, `POST /api/arrows/calibrate`, config, training, labelling, calibration, synthetic, and metrics endpoints. No authentication (deliberate).
- MQTT subscriptions listed above (trigger, reset, HA status, confirmation response).

**Outgoing:**
- MQTT publishes listed above (state, discovery, confirmation request, STALE notification).
- HTTP GET to the camera endpoint `images.src` (and per-ROI sources when `process_separate` is true).
- HTTP GET to Hugging Face Hub during training (timm pretrained weights), when `HF_TOKEN` is set or public weights are fetched.
- No outgoing webhooks to third-party SaaS are detected.

---

*Integration audit: 2026-10-11*
