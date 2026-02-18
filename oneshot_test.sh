#!/bin/bash
set -e

# One-shot integration test with stubbed nginx sidecar.
# Runs a single meter reading cycle and exits 0 on success, 1 on failure.
# No persistent volumes — everything is ephemeral and cleaned up on exit.
# Mirrors debug_clean.sh patterns but uses --one-shot mode instead of the web server.

IMAGE="watermeter-dashboard"
CONTAINER_NAME="watermeter-oneshot-$$"
STUB_CONTAINER="watermeter-stub-$$"
NETWORK_NAME="watermeter-oneshot-net-$$"

for arg in "$@"; do
    case "$arg" in
        --image=*) IMAGE="${arg#--image=}" ;;
        --image) shift; IMAGE="$1" ;;
        *) echo "Unknown option: $arg"; echo "Usage: $0 [--image <tag>]"; exit 1 ;;
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
echo "Building $IMAGE image..."
docker build "$SCRIPT_DIR" -t "$IMAGE"

# Stop any leftover containers from a previous interrupted run
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
docker rm -f "$STUB_CONTAINER" 2>/dev/null || true

# Check for GPU
DRI_DEVICE="/dev/dri/renderD128"
DRI_FLAGS=""
if [[ -e "$DRI_DEVICE" ]]; then
    echo "GPU device found, enabling hardware acceleration..."
    DRI_FLAGS="--device $DRI_DEVICE:$DRI_DEVICE --group-add=$(stat -c "%g" $DRI_DEVICE)"
fi

# --- Build the stub config pointing at the sidecar ---
# Use the dedicated fixture config (ROIs, trigger mode, and disabled features are baked in)
cp "$SCRIPT_DIR/tests/fixtures/oneshot_config.yaml" "$STUB_DIR/config.yaml"

# Patch the stub container name into the image src URL
sed -i "s|watermeter-stub|$STUB_CONTAINER|" "$STUB_DIR/config.yaml"

# --- Set up Docker network + nginx sidecar ---
docker network rm "$NETWORK_NAME" 2>/dev/null || true
docker network create "$NETWORK_NAME"

docker run -d \
    --name "$STUB_CONTAINER" \
    --network "$NETWORK_NAME" \
    -v "$STUB_IMAGE:/usr/share/nginx/html/meter.jpg:ro" \
    nginx:alpine

echo "Stub server running — serving meter snapshot as /meter.jpg"

# --- Run one-shot and capture output + exit code ---
echo "Running one-shot meter reading..."

set +e
OUTPUT=$(docker run --rm \
    --name "$CONTAINER_NAME" \
    $DRI_FLAGS \
    --network "$NETWORK_NAME" \
    -v "$STUB_DIR:/config" \
    "$IMAGE" \
    python3 -m watermeter --one-shot --config /config/config.yaml 2>&1)
EXIT_CODE=$?
set -e

echo ""
echo "--- Output ---"
echo "$OUTPUT"
echo "--------------"
echo ""

if [[ $EXIT_CODE -eq 0 ]]; then
    echo "PASS: one-shot reading succeeded (exit $EXIT_CODE)"
else
    echo "FAIL: one-shot reading failed (exit $EXIT_CODE)"
fi

exit $EXIT_CODE
