# `debug_clean.sh` — Ephemeral "Clean Install" Container

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a script that spins up a throwaway container simulating a fresh install — no persistent volumes, port 8003, with a committed meter snapshot served by a stub server.

**Architecture:** Single bash script following `debug.sh` conventions. A real meter snapshot is committed to the repo at `tests/fixtures/meter_snapshot.jpg`. The script serves it via a nginx sidecar on a Docker network. The app container gets a config (copied from debug, patched) pointing at the sidecar. No external volume mounts — everything ephemeral.

**Tech Stack:** Bash, Docker, nginx:alpine (sidecar)

---

## Context & Analysis

### What the app needs to run (first boot)

| Resource | Source | Behavior when empty |
|----------|--------|-------------------|
| `/config/config.yaml` | Entrypoint copies from `/config_default/` | Auto-created from defaults |
| `/data/state.json` | Created by app on first reading | App starts without it |
| `/app/models/` | Entrypoint copies bootstrap models from `/app/digits/selected/` and `/app/arrows/selected/` | Auto-populated; inference works with bootstrap models |
| `/training/arrows/`, `/training/digits/` | Training-only | Not needed for startup |
| HuggingFace cache | Training-only | Not needed for startup |

**Conclusion:** The app starts cleanly with **zero external mounts**. We only mount a config file to point the image source at our stub server.

### Image source

- Real meter snapshot committed at `tests/fixtures/meter_snapshot.jpg`
- Fetched once from `http://<camera-ip>/img_tmp/alg.jpg` and committed to git
- Script serves it statically via nginx sidecar

---

## Task 1: Fetch and commit meter snapshot

**Files:**
- Create: `tests/fixtures/meter_snapshot.jpg`

**Step 1: Fetch the image from the real device**

Run: `curl -sf -o tests/fixtures/meter_snapshot.jpg "http://<camera-ip>/img_tmp/alg.jpg"`
Expected: File created, valid JPEG

**Step 2: Verify**

Run: `file tests/fixtures/meter_snapshot.jpg`
Expected: `JPEG image data`

**Step 3: Commit**

```bash
git add tests/fixtures/meter_snapshot.jpg
git commit -m "claude: add real meter snapshot for clean-install stub server"
```

---

## Task 2: Create `debug_clean.sh`

**Files:**
- Create: `debug_clean.sh`

**Step 1: Write the script**

```bash
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
        -v "$STUB_DIR/config.yaml:/config/config.yaml" \
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
        -v "$STUB_DIR/config.yaml:/config/config.yaml" \
        -p ${PORT}:8001 \
        watermeter-dashboard
fi
```

**Step 2: Make it executable**

Run: `chmod +x debug_clean.sh`

**Step 3: Commit**

```bash
git add debug_clean.sh
git commit -m "claude: add debug_clean.sh for ephemeral clean-install testing"
```

---

## Task 3: Smoke test

**Step 1: Run the clean container in detach mode**

Run: `./debug_clean.sh --detach`
Expected:
- "Stub server running" message
- "Clean-install container ready on http://localhost:8003"

**Step 2: Verify dashboard loads**

Run: `curl -sf http://localhost:8003/ | head -c 200`
Expected: HTML response (dashboard renders)

**Step 3: Verify stub server is serving the real image**

Run: `docker logs watermeter-stub 2>&1 | grep meter.jpg`
Expected: GET request(s) for `/meter.jpg` with 200 status

**Step 4: Stop and verify cleanup**

Run: `docker rm -f watermeter-dashboard-clean watermeter-stub && docker network rm watermeter-clean-net`
Run: `docker ps -a | grep watermeter-dashboard-clean`
Expected: No output (everything gone)

---

## Design Decisions

### Why commit the meter snapshot to git?
A real meter snapshot makes the clean-install test realistic — bootstrap models run inference on real data. Committing it means no runtime dependency on the device being reachable. Re-fetch manually if a fresh image is needed.

### Why copy debug config as base instead of hardcoding?
The debug config already has correct ROIs, detection settings, model references, etc. Copying it and patching just the image source + disabled features avoids duplicating 100+ lines of config that would drift over time.

### Why nginx sidecar instead of host python server?
Self-contained in Docker — no dependency on host processes. Docker network gives clean hostname resolution (`watermeter-stub`). nginx:alpine is ~5MB, instant startup.

### Why not docker-compose?
The project uses plain `docker run` scripts (`debug.sh`). Keeping the same pattern avoids introducing a new tool.

### Why cyclic trigger mode?
MQTT broker doesn't exist in this test environment. Cyclic mode polls on its own without external triggers.

### Why `save_enabled: false`?
No training volumes are mounted. Saving low-confidence images would fail.

### Entrypoint and config interaction
The entrypoint runs `sed` to update model paths in `/config/config.yaml`. Since we mount the stub config as a writable file (no `:ro`), the entrypoint can patch model paths into it — the bootstrap models get configured automatically.
