#!/bin/bash
set -e

echo "Building watermeter-dashboard..."
docker build . -t wmi_full

echo "Creating local directories for volumes..."
rm -rf ./config
mkdir -p ./config ./data

echo "Starting container..."
docker run -it --rm \
  --name wmi_full \
  --device /dev/dri/renderD128:/dev/dri/renderD128 \
  --group-add=$(stat -c "%g" /dev/dri/renderD128) \
  -p 8001:8001 \
  -v $(pwd)/config:/config \
  -v $(pwd)/data:/data \
  -v ./arrows/:/training/arrows \
  -v ./digits/:/training/digits \
  wmi_full

