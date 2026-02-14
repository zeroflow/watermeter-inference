# Water Meter AI

Self-hosted service that reads water meters using OpenVINO inference and publishes readings to Home Assistant via MQTT.

## Features

- **Automated meter reading** -- receives MQTT triggers from an AI-on-the-edge ESP32-CAM, fetches images, runs inference, and publishes values to Home Assistant
- **Digit and analog dial recognition** -- MobileNetV3-based classifiers for digital counters (classes 0-9 + NAN) and analog pointer dials (10 to 100 decimal classes, e.g. 0.0, 0.5, 1.0 ... 9.5 for a 20-class model)
- **Web dashboard** -- live view of the current reading with per-image confidence scores, warnings, and manual trigger/reset controls
- **Built-in model training** -- train new classifiers from the web UI using PyTorch and timm, with automatic ONNX and OpenVINO export
- **Benchmarking** -- evaluate model accuracy against labeled ground truth data
- **Labeling tool** -- review and correct low-confidence images directly in the browser to build training datasets
- **ROI configuration** -- visually define digit and dial regions on the meter image
- **Plausibility checks** -- reverse detection, rate limiting, and consistency validation between adjacent dials

## Quick Start

```bash
git clone <repository-url>
cd watermeter

# Create config directory and copy defaults
mkdir -p config
cp config.yaml config/config.yaml
# Edit config/config.yaml: set aiote.host, mqtt.broker, and images

docker compose up -d
```

The dashboard is available at `http://localhost:8001`.

For Intel iGPU acceleration:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

See [DOCKER.md](DOCKER.md) for volume mounts, GPU setup, and manual Docker commands.

## Configuration

All settings are in `config.yaml`. The most important sections:

| Section | Key fields | Purpose |
|---------|-----------|---------|
| `aiote` | `host` | IP address of the AI-on-the-edge ESP32-CAM device |
| `images` | `src`, `digits`, `arrows` | Image source URL and region identifiers |
| `mqtt` | `broker`, `port`, `trigger_topic` | MQTT broker connection and trigger settings |
| `homeassistant` | `enabled`, `publish_topic` | Home Assistant MQTT discovery and state publishing |
| `inference` | `device`, `confidence_threshold`, `digits_model`, `arrows_model` | Model paths and OpenVINO device (CPU, GPU, or AUTO) |
| `plausibility` | `max_rate_per_hour`, `enable_reverse_detection` | Value validation thresholds |
| `low_confidence` | `save_enabled`, `save_path` | Automatic collection of uncertain images for retraining |

See [config.yaml](config.yaml) for the full reference with comments.

## Web Interface

The service provides several pages, all using HTMX for live updates without page reloads:

- **Dashboard** (`/`) -- current meter reading, per-image predictions with confidence scores (color-coded green/yellow/red), warnings, and manual trigger/reset buttons
- **Training** (`/training`) -- start training jobs, monitor progress, view training history, queue management, and benchmarking
- **Labeling** (`/label`) -- review low-confidence images, assign correct labels, and build ground truth datasets for retraining
- **ROI Config** (`/roi-config`) -- visually position digit and dial extraction regions on the meter image
- **Config Editor** (`/config-editor`) -- edit `config.yaml` in the browser with syntax highlighting

## API Endpoints

### Service

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/status` | Current reading, predictions, and warnings as JSON |
| `POST` | `/api/trigger` | Trigger a new meter reading |
| `POST` | `/api/reset` | Reset the previous value (for meter replacement) |
| `GET` | `/health` | Health check |

### Training and Models

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/training/status` | Training job and benchmark status |
| `POST` | `/api/training/start` | Start or queue a training job |
| `POST` | `/api/training/cancel` | Cancel the active training job |
| `GET` | `/api/models?type={digits\|arrows}` | List available models |
| `POST` | `/api/models/{type}/{id}/activate` | Set a model as the active model |
| `DELETE` | `/api/models/{type}/{id}` | Delete a model |
| `POST` | `/api/models/{type}/{id}/benchmark` | Run benchmark on a model |
| `GET` | `/api/training-data/stats` | Training data statistics |

### Configuration and Labeling

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/config` | Get current config as YAML |
| `POST` | `/api/config/save` | Save updated config |
| `GET` | `/api/label/next-image` | Get next image for labeling |
| `POST` | `/api/label/submit` | Submit a label correction |
| `GET` | `/api/roi/config` | Get current ROI definitions |
| `POST` | `/api/roi/digits` | Save digit ROI definitions |
| `POST` | `/api/roi/analogs` | Save analog dial ROI definitions |

## Development

### Local Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Start the service
uvicorn watermeter.app:app --host 0.0.0.0 --port 8001 --reload
```

### Running Tests

```bash
# Unit and regression tests (no Docker required, ~6 seconds)
python -m pytest tests/unit/ tests/regression/ --tb=short -q

# Integration tests (requires a running Docker container)
python -m pytest -m integration --no-build --base-url http://localhost:8001
```

### Project Structure

```
watermeter/
  app.py                 # FastAPI application and lifespan
  watermeter_service.py  # Core service: MQTT, image fetch, inference loop
  training_manager.py    # Training and benchmark job orchestration
  model_manager.py       # Model metadata, activation, deletion
  config_utils.py        # YAML config loading (ruamel.yaml)
  persistence.py         # State persistence (state.json)
  inference.py           # OpenVINO inference wrapper
  routes/                # API and page route handlers
  templates/             # Jinja2 + HTMX templates
  static/                # CSS and JavaScript
tests/
  unit/                  # Fast tests, no external dependencies
  regression/            # Regression tests for fixed bugs
  integration/           # End-to-end tests against Docker container
```

## Architecture

The service runs a continuous loop driven by MQTT messages. When the AI-on-the-edge ESP32-CAM finishes capturing a new image, it publishes a message to the configured MQTT topic. The service receives this trigger, fetches the meter image from the device over HTTP, and extracts digit and dial regions using the configured ROIs.

Each extracted region is classified by an OpenVINO model -- digits through an 11-class classifier (0-9 and NAN for unreadable), analog dials through a multi-class classifier with decimal values (e.g. 0.0, 0.5, 1.0 ... 9.5 for a 20-class model). The individual predictions are combined into a total meter value in cubic meters, then validated against the previous reading using reverse detection and rate limiting.

The final value and per-image confidence scores are published to Home Assistant via MQTT discovery and displayed on the web dashboard. Images with confidence below the threshold are automatically saved for later labeling and model retraining.

## Documentation

- [QUICKSTART.md](QUICKSTART.md) -- step-by-step setup and first reading
- [DOCKER.md](DOCKER.md) -- Docker deployment, volume mounts, GPU passthrough

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).
