#!/bin/bash
set -e

# Parse flags
PURGE_MODELS=0
for arg in "$@"; do
    case "$arg" in
        --purge-models) PURGE_MODELS=1 ;;
        *) echo "Unknown option: $arg"; echo "Usage: $0 [--purge-models]"; exit 1 ;;
    esac
done

# Load environment variables (.env contains HF_TOKEN etc.)
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

CONFIG_FILE="config.yaml"

echo "Preparing selected models from config.yaml..."
rm -rf digits/selected arrows/selected
mkdir -p digits/selected arrows/selected

# Extract model basenames from config.yaml (handles both flat and subdirectory paths)
DIGITS_PATH=$(grep 'digits_model:' "$CONFIG_FILE" | tr -d '"' | tr -d "'" | xargs)
DIGITS_BASE=$(basename "${DIGITS_PATH}" .xml)

ARROWS_PATH=$(grep 'arrows_model:' "$CONFIG_FILE" | tr -d '"' | tr -d "'" | xargs)
ARROWS_BASE=$(basename "${ARROWS_PATH}" .xml)

echo "  Digits: $DIGITS_BASE"
echo "  Arrows: $ARROWS_BASE"

# Validate model files exist
MISSING=0
for f in "digits/ov_model/${DIGITS_BASE}.xml" "digits/ov_model/${DIGITS_BASE}.bin" \
         "arrows/ov_model/${ARROWS_BASE}.xml" "arrows/ov_model/${ARROWS_BASE}.bin"; do
    if [[ ! -f "$f" ]]; then
        echo "ERROR: Missing model file: $f"
        MISSING=1
    fi
done
if [[ $MISSING -eq 1 ]]; then
    echo "Aborting. Check config.yaml model paths."
    exit 1
fi

cp "digits/ov_model/${DIGITS_BASE}.xml" digits/selected/
cp "digits/ov_model/${DIGITS_BASE}.bin" digits/selected/
cp "arrows/ov_model/${ARROWS_BASE}.xml" arrows/selected/
cp "arrows/ov_model/${ARROWS_BASE}.bin" arrows/selected/

ls -lh digits/selected/ arrows/selected/

echo ""
echo "Building watermeter-dashboard..."
docker build . -t wmi_full

echo "Creating local directories for volumes..."
mkdir -p ./config_debug ./data_debug ./models_debug

if [[ $PURGE_MODELS -eq 1 ]]; then
    echo "Purging models directory (--purge-models)..."
    rm -rf ./models_debug/*
fi

rm -rf ./config_debug/*

CONTAINER_NAME="wmi_full"

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
  wmi_full
