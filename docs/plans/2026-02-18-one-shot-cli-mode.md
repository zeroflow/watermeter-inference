# One-Shot CLI Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `--one-shot` CLI flag that boots the app, loads config and models, reads a meter once from a (stubbed or real) sidecar, prints the result, and exits with code 0 on success or 1 on failure.

**Architecture:** Add argparse to `watermeter/__main__.py` with `--one-shot` and `--config` flags. Create `watermeter/oneshot.py` that reuses the production `WatermeterService` and `InferenceService` to execute a single `process_reading()` call — testing the real code path without the web server, MQTT loop, or background tasks. Provide `oneshot_test.sh` for Docker-based integration testing with an nginx sidecar stub (same pattern as `debug_clean.sh`).

**Tech Stack:** Python 3.12, argparse, asyncio, existing watermeter modules

---

## Task 1: CLI Argument Parser

**Files:**
- Modify: `watermeter/__main__.py`

**Step 1: Read the current `__main__.py`**

Read `watermeter/__main__.py` — it's ~3 lines. Understand what it does today.

**Step 2: Write test for CLI dispatch**

Create `tests/unit/test_cli.py`:

```python
"""Tests for CLI argument parsing."""
import subprocess
import sys


def test_one_shot_flag_recognized():
    """--one-shot flag is parsed without error (with --help to avoid actual run)."""
    result = subprocess.run(
        [sys.executable, "-m", "watermeter", "--help"],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0
    assert "--one-shot" in result.stdout
    assert "--config" in result.stdout
```

**Step 3: Run test, verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_cli.py -v`
Expected: FAIL (no `--one-shot` in current help output, or no help output at all)

**Step 4: Implement argparse in `__main__.py`**

Replace the content of `watermeter/__main__.py` with:

```python
"""Watermeter Inference — CLI entry point."""
import argparse
import sys


def cli():
    parser = argparse.ArgumentParser(description="Watermeter Inference")
    parser.add_argument(
        "--one-shot",
        action="store_true",
        help="Run a single meter reading and exit (exit 0 = success)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    args = parser.parse_args()

    if args.one_shot:
        from watermeter.oneshot import main as oneshot_main

        sys.exit(oneshot_main(args.config))
    else:
        from watermeter.app import main

        main()


if __name__ == "__main__":
    cli()
```

**Step 5: Run test, verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_cli.py -v`
Expected: PASS

**Step 6: Verify existing app boot is not broken**

Run: `.venv/bin/python -m pytest tests/unit/ -v --timeout=10` (full unit suite)
Expected: All existing tests still pass. The `cli()` wrapper should not affect tests that import `watermeter.app` directly.

**Step 7: Commit**

```bash
git add watermeter/__main__.py tests/unit/test_cli.py
git commit -m "claude: add --one-shot and --config CLI flags to __main__.py"
```

---

## Task 2: One-Shot Runner — Failing Tests

**Files:**
- Create: `tests/unit/test_oneshot.py`

**Step 1: Check if `pytest-asyncio` is available**

Run: `.venv/bin/python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`

If not installed: `uv add --dev pytest-asyncio` (or check `pyproject.toml` for existing async test deps).

**Step 2: Write failing tests for `oneshot.py`**

Create `tests/unit/test_oneshot.py`:

```python
"""Tests for one-shot meter reading mode."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, mock_open


@pytest.fixture
def mock_config():
    """Minimal config dict for one-shot mode."""
    return {
        "images": {"src": "http://stub:80/meter.jpg", "process_separate": False},
        "detection": {
            "enabled": True,
            "rotation": 0,
            "digits": {"rois": []},
            "analogs": {"rois": []},
        },
        "inference": {
            "digits_model": "/models/digits/model.xml",
            "digits_classes": ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "NAN"],
            "digits_resolution": 128,
            "arrows_model": "/models/arrows/model.xml",
            "arrows_classes": ["0.0", "1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0", "9.0"],
            "arrows_resolution": 128,
            "device": "CPU",
            "confidence_threshold": 0.8,
        },
        "homeassistant": {"enabled": False},
        "trigger": {"mode": "cyclic"},
        "persistence": {"enabled": False},
    }


@pytest.mark.asyncio
async def test_one_shot_config_load_failure():
    """Exit 1 when config file doesn't exist."""
    from watermeter.oneshot import run_one_shot

    result = await run_one_shot("/nonexistent/config.yaml")
    assert result == 1


@pytest.mark.asyncio
async def test_one_shot_model_load_failure(mock_config, tmp_path):
    """Exit 1 when models fail to load."""
    import yaml

    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(mock_config))

    mock_inference = MagicMock()
    mock_inference.models_loaded = False

    with patch("watermeter.oneshot.get_inference_service", return_value=mock_inference):
        from watermeter.oneshot import run_one_shot

        result = await run_one_shot(str(config_file))
        assert result == 1


@pytest.mark.asyncio
async def test_one_shot_image_fetch_failure(mock_config, tmp_path):
    """Exit 1 when image fetch fails."""
    import yaml

    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(mock_config))

    mock_inference = MagicMock()
    mock_inference.models_loaded = True

    mock_pipeline = MagicMock()
    mock_pipeline.fetch_whole_image = AsyncMock(side_effect=Exception("Connection refused"))

    with patch("watermeter.oneshot.get_inference_service", return_value=mock_inference), \
         patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline):
        from watermeter.oneshot import run_one_shot

        result = await run_one_shot(str(config_file))
        assert result == 1


@pytest.mark.asyncio
async def test_one_shot_no_rois_extracted(mock_config, tmp_path):
    """Exit 1 when image processing yields no ROIs."""
    import yaml

    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(mock_config))

    mock_inference = MagicMock()
    mock_inference.models_loaded = True

    mock_pipeline = MagicMock()
    mock_pipeline.fetch_whole_image = AsyncMock(return_value=b"fake_jpeg")
    mock_pipeline.process_whole_image.return_value = {}  # no ROIs

    with patch("watermeter.oneshot.get_inference_service", return_value=mock_inference), \
         patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline):
        from watermeter.oneshot import run_one_shot

        result = await run_one_shot(str(config_file))
        assert result == 1


@pytest.mark.asyncio
async def test_one_shot_success(mock_config, tmp_path):
    """Exit 0 when reading completes successfully with a valid total."""
    import yaml

    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(mock_config))

    mock_inference = MagicMock()
    mock_inference.models_loaded = True
    mock_inference.predict.return_value = {"class": "5", "confidence": 0.95}

    mock_pipeline = MagicMock()
    mock_pipeline.fetch_whole_image = AsyncMock(return_value=b"fake_jpeg")
    mock_pipeline.process_whole_image.return_value = {
        "digit1": (b"roi_bytes", "digits"),
    }

    with patch("watermeter.oneshot.get_inference_service", return_value=mock_inference), \
         patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline):
        from watermeter.oneshot import run_one_shot

        result = await run_one_shot(str(config_file))
        assert result == 0
```

**NOTE for dev agent:** These tests may need adjustment once you read the actual signatures of `ImagePipeline`, `process_whole_image`, and `InferenceService.predict`. Read `docs/codebase_map.md` and the source code, then adjust mocks to match real return types. The test structure and assertions should remain the same.

**Step 3: Run tests, verify they all fail**

Run: `.venv/bin/python -m pytest tests/unit/test_oneshot.py -v`
Expected: All FAIL with `ModuleNotFoundError: No module named 'watermeter.oneshot'`

**Step 4: Commit failing tests**

```bash
git add tests/unit/test_oneshot.py
git commit -m "claude: add failing tests for one-shot mode (TDD red phase)"
```

---

## Task 3: One-Shot Runner — Implementation

**Files:**
- Create: `watermeter/oneshot.py`

**Step 1: Read the codebase map and key source files**

Read `docs/codebase_map.md` first. Then read these files to understand exact signatures:

1. `watermeter/image_pipeline.py` — `ImagePipeline.__init__`, `fetch_whole_image()`, `process_whole_image()` — understand what args they take and what they return
2. `watermeter/inference.py` — `get_inference_service()`, `InferenceService.initialize()`, `predict()` — understand the predict API (does it take a file path? bytes? what does it return?)
3. `watermeter/watermeter_service.py:284` — `run_inference()` — understand how ROI images are fed to the inference service (temp files? bytes?)
4. `watermeter/watermeter_service.py:355` — `calculate_total()` — understand input/output format
5. `watermeter/watermeter_service.py:45` — `__init__()` — understand what gets created on init and whether it's safe to construct without a broker

**Step 2: Decide on approach**

Based on reading the code, choose ONE approach:

**Option A — Compose pipeline directly** (preferred if `calculate_total` is simple or extractable):
- Use `ImagePipeline` for image fetch + ROI extraction
- Use `InferenceService` for inference
- Inline or extract `calculate_total` logic
- Skip WatermeterService entirely

**Option B — Use WatermeterService** (preferred if the service __init__ is safe without MQTT):
- Construct WatermeterService with a config that has `homeassistant.enabled: false`
- Initialize InferenceService
- Call `service.process_reading()` directly
- Read `service.current_state` for result

**Option A is preferred** because it avoids MQTT/scheduling complexity. Only use Option B if the pipeline logic is too tightly coupled to WatermeterService to extract cleanly.

**Step 3: Implement `watermeter/oneshot.py`**

Skeleton (adjust based on Step 1 findings):

```python
"""One-shot meter reading mode: boot, read once, exit."""
import asyncio
import logging
import yaml

from watermeter.image_pipeline import ImagePipeline
from watermeter.inference import get_inference_service

logger = logging.getLogger(__name__)


async def run_one_shot(config_path: str) -> int:
    """Execute a single meter reading.

    Returns:
        0 on successful reading, 1 on any failure.
    """
    # 1. Load config
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        logger.info("Config loaded from %s", config_path)
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        return 1

    # 2. Initialize inference (loads OpenVINO models)
    inference = get_inference_service()
    inference.initialize(config)
    if not inference.models_loaded:
        logger.error("Models failed to load")
        return 1
    logger.info("Models loaded successfully")

    # 3. Fetch image from camera/sidecar
    pipeline = ImagePipeline(config)
    try:
        image_bytes = await pipeline.fetch_whole_image()
    except Exception as e:
        logger.error("Image fetch failed: %s", e)
        return 1
    logger.info("Image fetched (%d bytes)", len(image_bytes))

    # 4. Process image → extract ROIs
    try:
        images = pipeline.process_whole_image(image_bytes)
    except Exception as e:
        logger.error("Image processing failed: %s", e)
        return 1

    if not images:
        logger.error("No ROIs extracted from image")
        return 1
    logger.info("Extracted %d ROIs", len(images))

    # 5. Run inference on each ROI
    #    IMPORTANT: Check how run_inference() in watermeter_service.py:284
    #    feeds images to the inference service. Replicate that pattern here.
    #    Likely: write ROI bytes to tmpfile, call inference.predict(model_type, path)
    predictions = {}
    try:
        for image_id, (img_bytes, model_type) in images.items():
            # ... predict (see watermeter_service.py:284 for exact pattern)
            result = ...  # inference.predict(model_type, tmpfile)
            predictions[image_id] = result
    except Exception as e:
        logger.error("Inference failed: %s", e)
        return 1

    # 6. Calculate total from predictions
    #    IMPORTANT: Read calculate_total() at watermeter_service.py:355.
    #    Either call it (if extractable) or replicate the logic.
    total = ...  # calculate_total(predictions, config)
    logger.info("Reading result: %s", total)
    print(total)
    return 0


def main(config_path: str = "config.yaml") -> int:
    """Sync entry point for one-shot mode."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return asyncio.run(run_one_shot(config_path))
```

**IMPORTANT notes for the dev agent:**
- The `# ...` sections (steps 5 and 6) MUST be filled in by reading the actual source code.
- Check `watermeter_service.py:284` for how `run_inference` feeds images to predict (temp files? bytes? paths?).
- Check `watermeter_service.py:355` for `calculate_total` — if it's a pure function of predictions + config, consider extracting it to a utility. If it depends on service state, you may need to replicate the core logic.
- If `ImagePipeline.__init__` takes different args than just `config`, adjust accordingly.
- If `process_whole_image` requires additional args (like marker templates), handle that.

**Step 4: Run tests, verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_oneshot.py -v`
Expected: All PASS

**Step 5: Run full unit test suite to check for regressions**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: All existing tests still pass

**Step 6: Commit**

```bash
git add watermeter/oneshot.py
git commit -m "claude: implement one-shot meter reading mode"
```

---

## Task 4: Dockerfile Compatibility Check

**Files:**
- Possibly modify: `Dockerfile`

**Step 1: Check the Dockerfile**

Read the `Dockerfile`. Look for `CMD` and `ENTRYPOINT` directives.

- If `ENTRYPOINT ["python", "-m", "watermeter"]` → args are appended, `--one-shot` works via `docker run image --one-shot`. No change needed.
- If `CMD ["python", "-m", "watermeter"]` → args REPLACE the CMD. Need to change to ENTRYPOINT or use `docker run image python -m watermeter --one-shot` syntax.

**Step 2: Adjust if needed**

If using CMD, change to ENTRYPOINT so CLI args pass through:

```dockerfile
ENTRYPOINT ["python", "-m", "watermeter"]
```

**Step 3: Commit (only if changed)**

```bash
git add Dockerfile
git commit -m "claude: use ENTRYPOINT for CLI arg passthrough"
```

---

## Task 5: Integration Test Script

**Files:**
- Create: `oneshot_test.sh`

**Step 1: Read `debug_clean.sh` for reference**

Read `debug_clean.sh` to understand the stub pattern: Docker network creation, nginx sidecar, config patching, app container launch.

**Step 2: Write `oneshot_test.sh`**

This script is modeled on `debug_clean.sh` but runs `--one-shot` instead of starting the web server.

```bash
#!/usr/bin/env bash
# oneshot_test.sh — Integration test: one-shot reading with stubbed sidecar
#
# Usage: ./oneshot_test.sh [--image IMAGE_TAG]
#
# Boots the app in one-shot mode with an nginx sidecar serving a static
# meter image. Exits 0 if the reading succeeds, 1 if it fails.
set -euo pipefail

IMAGE="${1:-watermeter-dashboard:latest}"
NETWORK="oneshot-test-$$"
STUB_NAME="watermeter-stub-$$"
APP_NAME="watermeter-oneshot-$$"
FIXTURE="tests/fixtures/meter_snapshot.jpg"

cleanup() {
    echo "Cleaning up..."
    docker rm -f "$APP_NAME" "$STUB_NAME" 2>/dev/null || true
    docker network rm "$NETWORK" 2>/dev/null || true
    rm -rf "$TMPDIR"
}
trap cleanup EXIT

# 1. Create isolated network
docker network create "$NETWORK"

# 2. Start nginx sidecar serving static meter image
TMPDIR=$(mktemp -d)
mkdir -p "$TMPDIR/html"
cp "$FIXTURE" "$TMPDIR/html/meter.jpg"

docker run -d --name "$STUB_NAME" \
    --network "$NETWORK" \
    -v "$TMPDIR/html:/usr/share/nginx/html:ro" \
    nginx:alpine

# 3. Create patched config
cp config.yaml "$TMPDIR/config.yaml"
sed -i "s|src:.*|src: http://$STUB_NAME:80/meter.jpg|" "$TMPDIR/config.yaml"
sed -i "s|mode:.*|mode: \"cyclic\"|" "$TMPDIR/config.yaml"
sed -i "s|enabled:.*# homeassistant|enabled: false  # homeassistant|" "$TMPDIR/config.yaml"

# NOTE: The sed commands above are approximate. The dev agent should adjust
# them based on the actual config.yaml structure, matching the pattern used
# in debug_clean.sh.

# 4. Run one-shot
echo "Running one-shot meter reading..."
docker run --name "$APP_NAME" \
    --network "$NETWORK" \
    -v "$TMPDIR/config.yaml:/config/config.yaml:ro" \
    "$IMAGE" \
    --one-shot --config /config/config.yaml

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "SUCCESS: One-shot reading completed"
else
    echo "FAILURE: One-shot reading failed (exit code $EXIT_CODE)"
    docker logs "$APP_NAME" 2>&1 | tail -20
fi

exit $EXIT_CODE
```

**Step 3: Test the script**

Run: `./oneshot_test.sh`
Expected: Prints the meter reading and exits 0.

If it fails: check docker logs, debug, fix either the script or the oneshot.py module.

**Step 4: Commit**

```bash
chmod +x oneshot_test.sh
git add oneshot_test.sh
git commit -m "claude: add one-shot integration test script with stubbed sidecar"
```

---

## Task 6: Update Codebase Map

**Files:**
- Modify: `docs/codebase_map.md`

**Step 1: Add entries for new files**

Add to `docs/codebase_map.md`:
- `watermeter/oneshot.py` — `run_one_shot()`, `main()`
- `watermeter/__main__.py` — `cli()` (updated)
- `tests/unit/test_oneshot.py` — all test functions
- `tests/unit/test_cli.py` — CLI test
- `oneshot_test.sh` — integration test script

**Step 2: Commit**

```bash
git add docs/codebase_map.md
git commit -m "claude: update codebase map with one-shot mode files"
```

---

## Summary

| Task | Agent | Files | Depends on |
|------|-------|-------|------------|
| 1. CLI args | dev | `__main__.py`, `test_cli.py` | — |
| 2. Failing tests | dev | `test_oneshot.py` | Task 1 |
| 3. Implementation | dev | `oneshot.py` | Task 2 |
| 4. Dockerfile check | dev | `Dockerfile` | Task 3 |
| 5. Integration script | dev | `oneshot_test.sh` | Task 3, 4 |
| 6. Codebase map | dev | `codebase_map.md` | Task 5 |

**Key risk:** Steps 5–6 of `run_one_shot()` (inference call pattern + total calculation) depend on understanding the exact signatures of `run_inference` and `calculate_total` in `watermeter_service.py`. The dev agent MUST read those methods before implementing. If `calculate_total` is tightly coupled to service state, it may need to be extracted into a standalone function — this is an acceptable refactor within scope.

**Exit criteria:**
- `.venv/bin/python -m pytest tests/unit/test_oneshot.py tests/unit/test_cli.py -v` — all pass
- `.venv/bin/python -m watermeter --one-shot --config config.yaml` — exits 0 with real config + accessible camera
- `./oneshot_test.sh` — exits 0 with stubbed sidecar
