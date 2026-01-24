# Docker Deployment Guide

## Directory Structure

```
/app              - Application code (read-only)
  ├── models/     - All available OpenVINO models
  │   ├── digits/ - Digit recognition models
  │   └── arrows/ - Arrow/dial recognition models
  ├── templates/  - HTML templates
  └── static/     - CSS/JS assets

/config           - User configuration (volume mount)
  └── config.yaml - Active configuration

/config_default   - Default configuration templates
  └── config.yaml - Default config (copied to /config if empty)

/data             - Persistent data (volume mount)
  └── state.json  - Application state

/training         - Training images (volume mount, shared with Label Studio)
  ├── digits/     - Low-confidence digit images
  └── arrows/     - Low-confidence arrow images
```

## Quick Start

### Option 1: Using docker-compose (Recommended)

```bash
# Start the service
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the service
docker-compose down
```

### Option 2: Using debug.sh (Development)

```bash
# Build and run in interactive mode with local volumes
./debug.sh
```

This creates local `./config`, `./data`, and `./training` directories.

### Option 3: Manual Docker run

```bash
# Build
docker build -t watermeter-dashboard .

# Create directories
mkdir -p ./config ./data ./training

# Run
docker run -d \
  --name watermeter \
  -p 8001:8001 \
  -v $(pwd)/config:/config \
  -v $(pwd)/data:/data \
  -v $(pwd)/training:/training \
  --device /dev/dri/renderD128:/dev/dri/renderD128 \
  --group-add=$(stat -c "%g" /dev/dri/renderD128) \
  watermeter-dashboard
```

## Configuration

On first start, the default configuration is automatically copied to `./config/config.yaml`.

Edit `./config/config.yaml` to customize:
- MQTT broker settings
- AI-on-the-edge device IP
- Model selection (choose from available models in `/app/models/`)
- Confidence thresholds
- Home Assistant integration

### Available Models

**Digits:**
- `model_mobilenetv3_small_100_c11_r144.xml` (default)

**Arrows:**
- `model_mobilenetv3_small_100_c10_r144.xml`
- `model_mobilenetv3_small_100_c20_r144.xml` (default)
- `model_mobilenetv3_small_100_c50_r144.xml`
- `model_efficientnet_lite0_c50_r144.xml`

Change models by editing `inference.digits_model` and `inference.arrows_model` in config.yaml.

## Data Persistence

Persistent data is stored in separate volume mounts:

**`./data/` directory:**
- `state.json` - Last reading and application state

**`./training/` directory:**
- `digits/` - Low-confidence digit images for retraining
- `arrows/` - Low-confidence arrow images for retraining
- Can be shared with Label Studio for annotation (see Label Studio Integration below)

## Label Studio Integration

To share training images with Label Studio, mount the same directory:

**docker-compose.yml example:**
```yaml
services:
  watermeter:
    volumes:
      - /var/ml/label-studio-data/import:/training

  label-studio:
    volumes:
      - /var/ml/label-studio-data/import:/label-studio/data/import
```

This allows automatic import of low-confidence images into Label Studio for manual annotation.

## GPU Support

The container requires Intel GPU access for OpenVINO acceleration:
- Device: `/dev/dri/renderD128`
- Ensure the user has access to the render group

To find your render group:
```bash
stat -c "%g" /dev/dri/renderD128
```

## Environment Variables

Optional environment variables (set in docker-compose.yml):
- `LOG_LEVEL` - Logging level (DEBUG, INFO, WARNING, ERROR)

## Health Check

The service includes a health endpoint:
```bash
curl http://localhost:8001/health
```

## Ports

- `8001` - Web dashboard and API

## Updating Configuration

1. Edit `./config/config.yaml`
2. Restart the container: `docker-compose restart`

The configuration is read on startup.

## Troubleshooting

**Container fails to start:**
- Check GPU access: `ls -l /dev/dri/renderD128`
- Verify config syntax: `docker-compose config`

**Config not persisting:**
- Ensure `./config` directory has correct permissions
- Check volume mounts: `docker inspect watermeter`

**Models not found:**
- Verify model paths in config.yaml start with `/app/models/`
- Check available models: `docker exec watermeter ls /app/models/digits/`
