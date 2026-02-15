#!/bin/bash
set -e

# Debug build & run script for local development.
# Maps to host port 8002 (not 8001) so it can run alongside production.

# Parse flags
PURGE_MODELS=0
DETACH=0
for arg in "$@"; do
    case "$arg" in
        --purge-models) PURGE_MODELS=1 ;;
        --detach) DETACH=1 ;;
        *) echo "Unknown option: $arg"; echo "Usage: $0 [--detach] [--purge-models]"; exit 1 ;;
    esac
done

# Load environment variables (.env contains HF_TOKEN etc.)
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

echo "Building watermeter-dashboard image..."
docker build . -t watermeter-dashboard

echo "Creating local directories for volumes..."
mkdir -p ./config_debug ./data_debug ./models_debug

if [[ $PURGE_MODELS -eq 1 ]]; then
    echo "Purging models directory (--purge-models)..."
    rm -rf ./models_debug/*
fi

# rm -rf ./config_debug/*

CONTAINER_NAME="watermeter-dashboard-debug"

# Stop and remove existing container if it exists
if docker container inspect "$CONTAINER_NAME" &>/dev/null; then
  echo "Stopping and removing existing container '$CONTAINER_NAME'..."
  docker rm -f "$CONTAINER_NAME"
fi

echo "Starting container..."

# Check if GPU device exists
DRI_DEVICE="/dev/dri/renderD128"
DRI_FLAGS=""
if [[ -e "$DRI_DEVICE" ]]; then
  echo "GPU device found, enabling hardware acceleration..."
  DRI_FLAGS="--device $DRI_DEVICE:$DRI_DEVICE --group-add=$(stat -c "%g" $DRI_DEVICE)"
else
  echo "No GPU device found, running in CPU-only mode..."
fi

if [[ $DETACH -eq 1 ]]; then
  # Detached mode - run in background and wait for health
  docker run -d \
    --name "$CONTAINER_NAME" \
    $DRI_FLAGS \
    -p 8002:8001 \
    -v $(pwd)/config_debug:/config \
    -v $(pwd)/data_debug:/data \
    -v $(pwd)/models_debug:/app/models \
    -v ./arrows/:/training/arrows \
    -v ./digits/:/training/digits \
    -v /home/thomas/.cache/huggingface:/root/.cache/huggingface \
    ${HF_TOKEN:+-e HF_TOKEN="$HF_TOKEN"} \
    watermeter-dashboard

  echo "Waiting for container to become healthy..."
  for i in $(seq 1 60); do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "starting")
    if [ "$STATUS" = "healthy" ]; then
      echo "Container is healthy and ready on http://localhost:8002"
      exit 0
    fi
    sleep 2
  done
  echo "ERROR: Container did not become healthy within 120s"
  docker logs "$CONTAINER_NAME" --tail 20
  exit 1
else
  # Interactive mode - run in foreground
  docker run -it --rm \
    --name "$CONTAINER_NAME" \
    $DRI_FLAGS \
    -p 8002:8001 \
    -v $(pwd)/config_debug:/config \
    -v $(pwd)/data_debug:/data \
    -v $(pwd)/models_debug:/app/models \
    -v ./arrows/:/training/arrows \
    -v ./digits/:/training/digits \
    -v /home/thomas/.cache/huggingface:/root/.cache/huggingface \
    ${HF_TOKEN:+-e HF_TOKEN="$HF_TOKEN"} \
    watermeter-dashboard
fi
