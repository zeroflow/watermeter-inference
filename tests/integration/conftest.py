"""Integration test fixtures.

Starts a fresh, isolated watermeter container for testing.
- Auto-builds the image from the project Dockerfile if needed
- No persistent volumes — config and models come from the image defaults
- Ground truth data is copied in via `docker cp` for training/benchmark tests
- Container is removed after the test session
"""

import os
import subprocess
import sys
import time
import uuid

import httpx
import pytest

CONTAINER_PORT = 8001
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def _find_free_port():
    """Find a free TCP port on the host."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _image_exists(image):
    """Check whether a Docker image is available locally."""
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
    )
    return result.returncode == 0


def _docker_build(image, build_dir):
    """Build the Docker image. Streams output to stderr for visibility."""
    print(f"\n>>> Building Docker image '{image}' from {build_dir} ...", file=sys.stderr)
    result = subprocess.run(
        ["docker", "build", "-t", image, build_dir],
        stdout=sys.stderr,  # show build output live
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker build failed (exit {result.returncode})")
    print(f">>> Image '{image}' built successfully.\n", file=sys.stderr)


def _docker_run(image, host_port):
    """Start a fresh container and return its name."""
    name = f"watermeter-test-{uuid.uuid4().hex[:8]}"
    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "-p", f"{host_port}:{CONTAINER_PORT}",
        # GPU device for OpenVINO (optional, don't fail if missing)
        *(["--device", "/dev/dri/renderD128"] if os.path.exists("/dev/dri/renderD128") else []),
        image,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"docker run failed (exit {result.returncode}):\n{result.stderr.strip()}"
        )
    return name


def _docker_cp(container, src, dst):
    """Copy local files into the container."""
    subprocess.run(
        ["docker", "cp", src, f"{container}:{dst}"],
        check=True, capture_output=True,
    )


def _docker_rm(container):
    """Stop and remove the container."""
    subprocess.run(
        ["docker", "rm", "-f", container],
        capture_output=True,
    )


def _wait_healthy(url, timeout=90):
    """Poll /health until 200 or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{url}/health", timeout=5)
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


def _copy_ground_truth(container):
    """Copy local ground truth into the test container (if available)."""
    for subdir in ("digits", "arrows"):
        local_gt = os.path.join(PROJECT_ROOT, subdir, "ground_truth")
        if os.path.isdir(local_gt):
            # docker cp copies the dir itself, so target is the parent
            # e.g. digits/ground_truth → /training/digits/  (creates /training/digits/ground_truth/)
            _docker_cp(container, local_gt, f"/training/{subdir}/")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def base_url(request):
    """
    Start a fresh container or use an existing service.

    - Auto-builds the image unless --no-build is passed
    - Use --base-url to test against an already-running service
    """
    explicit_url = (
        request.config.getoption("--base-url")
        or os.environ.get("WATERMETER_TEST_URL")
    )

    if explicit_url:
        url = explicit_url.rstrip("/")
        if not _wait_healthy(url, timeout=30):
            pytest.skip(f"Service not reachable at {url}")
        yield url
        return

    image = request.config.getoption("--image")
    no_build = request.config.getoption("--no-build")

    # Auto-build if image doesn't exist (or always rebuild for freshness)
    if not no_build:
        _docker_build(image, PROJECT_ROOT)
    elif not _image_exists(image):
        pytest.skip(
            f"Docker image '{image}' not found and --no-build was set. "
            f"Build it first: docker build -t {image} ."
        )

    port = _find_free_port()
    url = f"http://localhost:{port}"
    container = _docker_run(image, port)

    try:
        if not _wait_healthy(url, timeout=90):
            logs = subprocess.run(
                ["docker", "logs", "--tail", "50", container],
                capture_output=True, text=True,
            )
            _docker_rm(container)
            pytest.fail(
                f"Container {container} did not become healthy within 90s.\n"
                f"Logs:\n{logs.stdout}\n{logs.stderr}"
            )

        _copy_ground_truth(container)

        yield url
    finally:
        _docker_rm(container)


@pytest.fixture(scope="session")
def api(base_url):
    """httpx client with base_url pre-configured."""
    with httpx.Client(base_url=base_url, timeout=30) as client:
        yield client


@pytest.fixture
def unique_id():
    """Generate a unique ID for test isolation."""
    return f"test_{uuid.uuid4().hex[:8]}"
