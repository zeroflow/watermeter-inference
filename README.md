# AI Water Meter

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL%203.0-blue.svg)](LICENSE)

AI-powered water meter reader with live dashboard, model training UI, and Home Assistant integration. Uses OpenVINO for fast inference on CPU/GPU.

![Dashboard Screenshot](docs/screenshot.png)

## Features

- **AI-Powered Reading**: Digit and analog arrow recognition using OpenVINO inference
- **Live Dashboard**: Real-time meter readings with confidence scores and validation
- **Model Training**: Built-in training UI with PyTorch/timm — train custom models on your own meter
- **Smart Validation**: Temporal and spatial context rules detect and correct misreads
- **MQTT Integration**: Publish readings to Home Assistant or other MQTT consumers
- **Benchmarking**: Compare model accuracy on ground truth datasets
- **Interactive Labeling**: Web UI for annotating training data
- **ROI Configuration**: Visual editor for defining digit/arrow regions
- **Leak Detection**: Continuous consumption monitoring with configurable alerts
- **User Confirmation**: Route uncertain readings through Home Assistant for manual verification

## Quick Start

```bash
# Clone the repository
git clone <repository-url>
cd watermeter

# Create environment file
cp .env.example .env
# Edit .env to add your Hugging Face token (optional, only needed for training)

# Start the application
docker compose up -d

# Visit the dashboard
open http://localhost:8001
```

The application will:
- Create default config at `./config/config.yaml` on first run
- Install default pre-trained models to `./models/`
- Create data directories for readings, training images, and state

## Configuration

Edit configuration through the web UI at [http://localhost:8001/config](http://localhost:8001/config) or directly in `./config/config.yaml`.

Key settings:
- **Camera**: URL, authentication, capture interval
- **ROI**: Digit/arrow regions (configure via `/roi-config` UI)
- **MQTT**: Broker host, topic prefix, credentials
- **Plausibility**: Validation thresholds for rejected readings
- **Training**: Default architectures, image resolution, preprocessing

## API Documentation

Interactive API documentation is available when the application is running:
- **Swagger UI**: [http://localhost:8001/docs](http://localhost:8001/docs)
- **ReDoc**: [http://localhost:8001/redoc](http://localhost:8001/redoc)

Key endpoints:
- `GET /api/status` — Current reading and system status
- `GET /api/dashboard` — Dashboard data (readings, history, warnings)
- `POST /api/training/start` — Start model training job
- `POST /api/models/{type}/{id}/activate` — Switch active model
- `POST /api/set-value` — Manual meter reading input

## Development

### Run Debug Container

The debug container mounts source code and runs on port 8002:

```bash
bash debug.sh --detach
```

This allows live code changes without rebuilding. Access at [http://localhost:8002](http://localhost:8002).

### Run Tests

```bash
# Unit tests
pytest tests/unit/

# Integration tests (requires running container)
pytest tests/integration/

# Browser tests with Playwright (requires debug container on port 8002)
pytest tests/browser/
```

### Linting

```bash
# Check code style
ruff check watermeter/

# Auto-fix issues
ruff check --fix watermeter/
```

## Architecture

- **Backend**: FastAPI with async support
- **Inference**: OpenVINO Runtime (CPU/GPU)
- **Training**: PyTorch + timm pretrained models
- **Frontend**: HTMX + vanilla JavaScript, Jinja2 templates
- **Storage**: File-based (JSON state, YAML config, trained models)
- **Messaging**: MQTT for Home Assistant integration
- **Deployment**: Docker Compose

### Directory Structure

```
watermeter/
├── app.py                   # FastAPI application
├── inference.py             # OpenVINO inference engine
├── training_manager.py      # Training job orchestration
├── model_manager.py         # Model metadata and lifecycle
├── config/                  # Configuration (config.yaml)
├── data/                    # State, readings history
├── models/                  # Trained models (digits/, arrows/)
├── training/                # Training data (input/, ground_truth/)
├── templates/               # Jinja2 HTML templates
└── static/                  # CSS, JavaScript, images
```

## Docker Volumes

- `./config` — Configuration files (auto-created on first run)
- `./data` — Persistent state and readings history
- `./models` — Trained model binaries and metadata
- `./training/digits` — Digit training data (input/ and ground_truth/)
- `./training/arrows` — Arrow training data (input/ and ground_truth/)
- `hf_cache` — Hugging Face model cache (named volume)

## Optional: Intel GPU Acceleration

To enable GPU inference:

1. Check GPU device exists: `ls -l /dev/dri/renderD128`
2. Get render group ID: `stat -c "%g" /dev/dri/renderD128`
3. Uncomment `devices` and `group_add` sections in `docker-compose.yml`
4. Update group ID in `docker-compose.yml`
5. Restart container: `docker compose up -d`

## Optional: Local MQTT Broker

If you don't have an existing MQTT broker, uncomment the `mosquitto` service in `docker-compose.yml` to run Eclipse Mosquitto locally.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

## Acknowledgments

Inspired by [jomjol/AI-on-the-edge-device](https://github.com/jomjol/AI-on-the-edge-device) — analog water/gas meter reading with ESP32-CAM. This project reimplements the core concept with modern ML tools and a web-based training workflow.
