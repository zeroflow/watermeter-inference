"""Docker-based integration test for one-shot CLI mode.

Replicates what oneshot_test.sh does in Python/pytest:
- Creates an ephemeral Docker network
- Starts an nginx:alpine sidecar serving tests/fixtures/meter_snapshot.jpg
- Patches the fixture config so the image src URL points at the sidecar container
- Runs the app container in --one-shot mode mounted with the patched config
- Asserts exit code == 0

Requires Docker and a built image. Run with:
    .venv/bin/python -m pytest tests/integration/test_oneshot.py -m integration
"""

import os
import shutil
import subprocess
import sys
import tempfile
import uuid

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXTURES_DIR = os.path.join(PROJECT_ROOT, "tests", "fixtures")
METER_SNAPSHOT = os.path.join(FIXTURES_DIR, "meter_snapshot.jpg")
ONESHOT_CONFIG = os.path.join(FIXTURES_DIR, "oneshot_config.yaml")

# Placeholder hostname used in oneshot_config.yaml — gets replaced with the
# actual nginx sidecar container name before the test runs.
_STUB_HOSTNAME_PLACEHOLDER = "watermeter-stub"

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Docker helpers (local to this module — no shared state with conftest.py)
# ---------------------------------------------------------------------------


def _docker_network_create(name: str) -> None:
    """Create a Docker network; silently remove any stale network first."""
    subprocess.run(["docker", "network", "rm", name], capture_output=True)
    result = subprocess.run(
        ["docker", "network", "create", name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker network create '{name}' failed:\n{result.stderr.strip()}"
        )


def _docker_network_rm(name: str) -> None:
    """Remove a Docker network, ignoring errors."""
    subprocess.run(["docker", "network", "rm", name], capture_output=True)


def _docker_rm(name: str) -> None:
    """Force-remove a container, ignoring errors."""
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _start_nginx_sidecar(container_name: str, network: str, image_path: str) -> None:
    """Start an nginx:alpine container serving *image_path* as /meter.jpg."""
    cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "--network", network,
        "-v", f"{image_path}:/usr/share/nginx/html/meter.jpg:ro",
        "nginx:alpine",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to start nginx sidecar:\n{result.stderr.strip()}"
        )


def _dri_flags() -> list[str]:
    """Return --device flags for GPU acceleration if /dev/dri/renderD128 exists."""
    dri = "/dev/dri/renderD128"
    if os.path.exists(dri):
        stat_result = subprocess.run(
            ["stat", "-c", "%g", dri], capture_output=True, text=True
        )
        gid = stat_result.stdout.strip()
        flags = ["--device", f"{dri}:{dri}"]
        if gid:
            flags += ["--group-add", gid]
        return flags
    return []


# ---------------------------------------------------------------------------
# Fixture: ephemeral network + nginx sidecar + patched config
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def oneshot_env(request):
    """
    Create the full Docker environment for a one-shot test run.

    Yields a dict with:
        config_dir   — host path to the temp dir containing patched config.yaml
        network      — Docker network name
        stub_name    — nginx sidecar container name
    """
    suffix = uuid.uuid4().hex[:8]
    network_name = f"watermeter-oneshot-net-{suffix}"
    stub_name = f"watermeter-stub-{suffix}"
    tmp_dir = tempfile.mkdtemp(prefix="watermeter-oneshot-")

    # Verify fixtures exist before spending time on Docker setup
    if not os.path.isfile(METER_SNAPSHOT):
        pytest.skip(
            f"Meter snapshot not found at {METER_SNAPSHOT}. "
            "Fetch with: curl -o tests/fixtures/meter_snapshot.jpg http://<device-ip>/img_tmp/alg.jpg"
        )
    if not os.path.isfile(ONESHOT_CONFIG):
        pytest.skip(f"Fixture config not found at {ONESHOT_CONFIG}")

    # Write patched config — replace placeholder hostname with actual sidecar name
    with open(ONESHOT_CONFIG) as f:
        config_text = f.read()
    config_text = config_text.replace(_STUB_HOSTNAME_PLACEHOLDER, stub_name)
    patched_config_path = os.path.join(tmp_dir, "config.yaml")
    with open(patched_config_path, "w") as f:
        f.write(config_text)

    # Create network and start sidecar
    try:
        _docker_network_create(network_name)
        _start_nginx_sidecar(stub_name, network_name, METER_SNAPSHOT)
    except Exception as exc:
        # Clean up partially-created resources before re-raising
        _docker_rm(stub_name)
        _docker_network_rm(network_name)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        pytest.fail(f"Failed to set up Docker environment: {exc}")

    env_info = {
        "config_dir": tmp_dir,
        "network": network_name,
        "stub_name": stub_name,
    }

    try:
        yield env_info
    finally:
        _docker_rm(stub_name)
        _docker_network_rm(network_name)
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_oneshot_docker_e2e(request, oneshot_env):
    """
    Full end-to-end one-shot test: real container, real models, real inference.

    Mirrors oneshot_test.sh:
    1. nginx sidecar serves the fixture meter image
    2. App container runs --one-shot with patched config
    3. Assert exit code == 0
    """
    image = request.config.getoption("--image")
    no_build = request.config.getoption("--no-build")

    # Build or verify image
    if not no_build:
        print(f"\n>>> Building Docker image '{image}' ...", file=sys.stderr)
        build_result = subprocess.run(
            ["docker", "build", PROJECT_ROOT, "-t", image],
            stdout=sys.stderr,
            stderr=subprocess.STDOUT,
        )
        if build_result.returncode != 0:
            pytest.fail(f"docker build failed (exit {build_result.returncode})")
    else:
        # Verify the image exists when --no-build is set
        check = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
        )
        if check.returncode != 0:
            pytest.skip(
                f"Docker image '{image}' not found and --no-build was set. "
                f"Build it first: docker build -t {image} ."
            )

    container_name = f"watermeter-oneshot-{uuid.uuid4().hex[:8]}"
    config_dir = oneshot_env["config_dir"]
    network = oneshot_env["network"]

    cmd = [
        "docker", "run", "--rm",
        "--name", container_name,
        *_dri_flags(),
        "--network", network,
        "-v", f"{config_dir}:/config",
        image,
        "python3", "-m", "watermeter",
        "--one-shot",
        "--config", "/config/config.yaml",
    ]

    print(f"\n>>> Running one-shot container: {' '.join(cmd)}", file=sys.stderr)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,  # 5 minutes — model loading can be slow on first run
    )

    # Always print output for debugging (visible with pytest -s or on failure)
    combined_output = result.stdout + result.stderr
    if combined_output.strip():
        print("\n--- One-shot container output ---", file=sys.stderr)
        print(combined_output, file=sys.stderr)
        print("--- End of container output ---\n", file=sys.stderr)

    if result.returncode != 0:
        pytest.fail(
            f"One-shot container exited with code {result.returncode}.\n"
            f"Output:\n{combined_output}"
        )

    assert result.returncode == 0, (
        f"Expected exit code 0, got {result.returncode}.\nOutput:\n{combined_output}"
    )
