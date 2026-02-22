# Docker Deployment Guide

## Directory Structure

```
/app                  - Application code (read-only)
  ├── watermeter/     - Python package
  │   ├── routes/     - FastAPI route modules
  │   ├── templates/  - Jinja2 HTML templates
  │   └── static/     - CSS/JS assets
  └── scripts/        - Utility scripts

/config               - User configuration (volume mount)
  └── config.yaml     - Active configuration

/config_default       - Default configuration templates
  └── config.yaml     - Default config (copied to /config if empty)

/data                 - Persistent data (volume mount)
  └── state.json      - Application state

/app/models           - Trained models (volume mount)
  ├── digits/         - Digit recognition models
  └── arrows/         - Arrow/dial recognition models

/training             - Training data (volume mount)
  ├── digits/         - Digit training images
  │   ├── input/      - Low-confidence captures
  │   └── ground_truth/ - Labeled training data
  └── arrows/         - Arrow training images
      ├── input/      - Low-confidence captures
      └── ground_truth/ - Labeled training data
```

## Quick Start

### Option 1: Using docker compose (Recommended)

```bash
# Start the service
docker compose up -d

# View logs
docker compose logs -f

# Stop the service
docker compose down
```

### Option 2: Using debug.sh (Development)

```bash
# Build and run in interactive mode with local volumes
./debug.sh

# Build and run detached (maps to port 8002)
./debug.sh --detach
```

This creates local `./config_debug`, `./data_debug`, and `./models_debug` directories and
maps training data from `./digits/` and `./arrows/`.

### Option 3: Manual Docker run

```bash
# Build
docker build -t watermeter-dashboard .

# Create directories
mkdir -p ./config ./data ./models ./training/digits ./training/arrows

# Run (CPU only)
docker run -d \
  --name watermeter \
  -p 8001:8001 \
  -v $(pwd)/config:/config \
  -v $(pwd)/data:/data \
  -v $(pwd)/models:/app/models \
  -v $(pwd)/training/digits:/training/digits \
  -v $(pwd)/training/arrows:/training/arrows \
  watermeter-dashboard

# Optional: add Intel GPU acceleration
#   --device /dev/dri/renderD128:/dev/dri/renderD128 \
#   --group-add=$(stat -c "%g" /dev/dri/renderD128) \
```

## Web UI Pages

- `/` — Dashboard (live meter reading)
- `/training` — Model training, benchmarking, and model management
- `/label` — Interactive labeling of training data
- `/roi-config` — Visual ROI editor
- `/config-editor` — Configuration editor
- `/docs` — Swagger API documentation

## Configuration

On first start, the default configuration is automatically copied to `./config/config.yaml`.

Edit settings via the web UI at `/config-editor` — changes take effect immediately without
restarting the container.

If you edit `./config/config.yaml` directly on the host, restart the container to apply
the changes:

```bash
docker compose restart
```

Configurable settings include:
- MQTT broker settings
- AI-on-the-edge device IP
- Confidence thresholds
- Home Assistant integration
- Active model selection (also manageable through the `/training` UI)

Models are managed through the training UI at `/training`. The container ships a default
model that is installed automatically on first run if no models exist. Additional models
can be trained, imported, or switched via the training page.

## Data Persistence

Persistent data is stored in separate volume mounts:

**`./data/` directory:**
- `state.json` - Last reading and application state

**`./models/` directory:**
- `digits/` - Trained digit recognition models
- `arrows/` - Trained arrow recognition models

**`./training/` directory:**
- `digits/input/` - Auto-saved low-confidence digit captures
- `digits/ground_truth/` - Labeled digit training data
- `arrows/input/` - Auto-saved low-confidence arrow captures
- `arrows/ground_truth/` - Labeled arrow training data

**`hf_cache` named volume:**
- Hugging Face model weight cache (`/app/.cache/huggingface` inside container)
- Persists downloaded pretrained weights across container restarts
- Note: `docker compose down -v` will delete this volume — weights will need to be re-downloaded

## GPU Support (Optional)

By default, the container runs on **CPU only**. To enable Intel iGPU acceleration:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

**Prerequisites:**
- Intel integrated GPU with `/dev/dri/renderD128`
- Set `RENDER_GROUP` in `.env` if your render group differs from 44:
  ```bash
  # Find your render group
  stat -c "%g" /dev/dri/renderD128
  ```

## Environment Variables

Optional environment variables (set in `.env` or `docker-compose.yml`):

- `PUID` / `PGID` - Host user UID/GID for volume permission mapping (default: 1000)
- `LOG_LEVEL` - Logging verbosity: DEBUG, INFO, WARNING, ERROR (default: INFO)
- `HF_TOKEN` - Hugging Face API token (optional; required to download pretrained model
  weights from Hugging Face Hub during training). Get a token at
  https://huggingface.co/settings/tokens and set it in `.env`.

## Health Check

The service includes a health endpoint:
```bash
curl http://localhost:8001/health
```

## Ports

- `8001` - Web dashboard and API

## Updating Configuration

**Via web UI (recommended):** Open `/config-editor` in the browser. Changes are applied
immediately — no restart needed.

**Via direct file edit:** Edit `./config/config.yaml` on the host, then restart:

```bash
docker compose restart
```

## Troubleshooting

**Container fails to start:**
- Check GPU access: `ls -l /dev/dri/renderD128`
- Verify config syntax: `docker compose config`

**Config not persisting:**
- Ensure `./config` directory has correct permissions
- Check volume mounts: `docker inspect watermeter`

**Models not found:**
- Use the `/training` page to train or import a model
- Check mounted model directory: `docker exec watermeter ls /app/models/digits/`
