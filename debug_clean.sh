#!/bin/bash
set -e

# Ephemeral "clean install" container for testing first-boot experience.
# No persistent volumes — everything lives inside the container and vanishes on stop.
# Serves a committed meter snapshot via nginx sidecar.
# Maps to host port 8003 (separate from debug=8002 and prod=8001).

CONTAINER_NAME="watermeter-dashboard-clean"
STUB_CONTAINER="watermeter-stub"
NETWORK_NAME="watermeter-clean-net"
PORT=8003
DETACH=0

for arg in "$@"; do
    case "$arg" in
        --detach) DETACH=1 ;;
        *) echo "Unknown option: $arg"; echo "Usage: $0 [--detach]"; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUB_IMAGE="$SCRIPT_DIR/tests/fixtures/meter_snapshot.jpg"

if [[ ! -f "$STUB_IMAGE" ]]; then
    echo "ERROR: Meter snapshot not found at $STUB_IMAGE"
    echo "Fetch one with: curl -o tests/fixtures/meter_snapshot.jpg http://<device-ip>/img_tmp/alg.jpg"
    exit 1
fi

# Create temp directory for patched config (cleaned up on exit)
STUB_DIR=$(mktemp -d)

cleanup() {
    echo "Cleaning up..."
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    docker rm -f "$STUB_CONTAINER" 2>/dev/null || true
    docker network rm "$NETWORK_NAME" 2>/dev/null || true
    rm -rf "$STUB_DIR" 2>/dev/null || true
}
trap cleanup EXIT

# --- Build the app image ---
echo "Building watermeter-dashboard image..."
docker build . -t watermeter-dashboard

# Stop existing containers if present
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
docker rm -f "$STUB_CONTAINER" 2>/dev/null || true

# Check for GPU
DRI_DEVICE="/dev/dri/renderD128"
DRI_FLAGS=""
if [[ -e "$DRI_DEVICE" ]]; then
    echo "GPU device found, enabling hardware acceleration..."
    DRI_FLAGS="--device $DRI_DEVICE:$DRI_DEVICE --group-add=$(stat -c "%g" $DRI_DEVICE)"
fi

# --- Create stub config pointing at sidecar ---
# Copy debug config as base, override image source and disable MQTT/HA
cp config_debug/config.yaml "$STUB_DIR/config.yaml"

# Point image source at the stub server
sed -i 's|^\(\s*src:\).*|  src: "http://watermeter-stub:80/meter.jpg"|' "$STUB_DIR/config.yaml"

# Switch to cyclic trigger (no MQTT broker in clean env)
sed -i 's|^\(\s*mode:\).*|  mode: "cyclic"|' "$STUB_DIR/config.yaml"

# Disable Home Assistant publishing
sed -i '/^homeassistant:/,/^[a-z]/ s|^\(\s*enabled:\).*|  enabled: false|' "$STUB_DIR/config.yaml"

# Disable low-confidence saving (no training volumes mounted)
sed -i '/^low_confidence:/,/^[a-z]/ s|^\(\s*save_enabled:\).*|  save_enabled: false|' "$STUB_DIR/config.yaml"

# --- Set up Docker network + nginx sidecar ---
docker network rm "$NETWORK_NAME" 2>/dev/null || true
docker network create "$NETWORK_NAME"

docker run -d \
    --name "$STUB_CONTAINER" \
    --network "$NETWORK_NAME" \
    -v "$STUB_IMAGE:/usr/share/nginx/html/meter.jpg:ro" \
    nginx:alpine

echo "Stub server running — serving meter snapshot"

# --- Run the ephemeral container ---
echo "Starting ephemeral clean-install container on port $PORT..."

if [[ $DETACH -eq 1 ]]; then
    docker run -d \
        --name "$CONTAINER_NAME" \
        $DRI_FLAGS \
        --network "$NETWORK_NAME" \
        -v "$STUB_DIR:/config" \
        -p ${PORT}:8001 \
        watermeter-dashboard

    echo "Waiting for container to become ready..."
    for i in $(seq 1 60); do
        if ! docker ps -q -f name="$CONTAINER_NAME" | grep -q .; then
            echo "ERROR: Container exited unexpectedly"
            docker logs "$CONTAINER_NAME" --tail 20
            exit 1
        fi
        if curl -sf http://localhost:${PORT}/ >/dev/null 2>&1; then
            echo ""
            echo "Clean-install container ready on http://localhost:${PORT}"
            # Don't cleanup on success in detach mode — user will stop manually
            trap - EXIT
            echo ""
            echo "To stop:"
            echo "  docker rm -f $CONTAINER_NAME $STUB_CONTAINER"
            echo "  docker network rm $NETWORK_NAME"
            echo "  rm -rf $STUB_DIR"
            exit 0
        fi
        sleep 2
    done
    echo "ERROR: Container did not become ready within 120s"
    docker logs "$CONTAINER_NAME" --tail 20
    exit 1
else
    docker run -it --rm \
        --name "$CONTAINER_NAME" \
        $DRI_FLAGS \
        --network "$NETWORK_NAME" \
        -v "$STUB_DIR:/config" \
        -p ${PORT}:8001 \
        watermeter-dashboard
fi
