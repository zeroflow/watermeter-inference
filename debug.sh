#!/bin/bash
set -e

CONFIG_FILE="config.yaml"

echo "Preparing selected models from config.yaml..."
rm -rf digits/selected arrows/selected
mkdir -p digits/selected arrows/selected

# Extract model paths from config.yaml
DIGITS_MODEL=$(grep 'digits_model:' "$CONFIG_FILE" | sed 's/.*\/app\/models\/digits\///' | tr -d '"' | tr -d "'" | xargs)
ARROWS_MODEL=$(grep 'arrows_model:' "$CONFIG_FILE" | sed 's/.*\/app\/models\/arrows\///' | tr -d '"' | tr -d "'" | xargs)

DIGITS_BASE="${DIGITS_MODEL%.xml}"
ARROWS_BASE="${ARROWS_MODEL%.xml}"

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
rm -rf ./config
mkdir -p ./config ./data

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
  --name wmi_full \
  $DRI_FLAGS \
  -p 8001:8001 \
  -v $(pwd)/config:/config \
  -v $(pwd)/data:/data \
  -v ./arrows/:/training/arrows \
  -v ./digits/:/training/digits \
  wmi_full

