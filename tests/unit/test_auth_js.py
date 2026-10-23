"""Runs the node behavior check of static/auth.js (skipped when node is not installed)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AUTH_JS = "watermeter/static/auth.js"
CHECK = "tests/unit/js/auth_shim_check.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_node(*args):
    """Run node from the repository root and return the completed process."""
    return subprocess.run(["node", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)


def test_auth_js_has_valid_syntax():
    """node --check accepts the script."""
    result = run_node("--check", AUTH_JS)
    assert result.returncode == 0, result.stderr


def test_auth_js_shim_behavior():
    """Fake window, fetch, XHR and location prove the header, the 401 redirect and the guard flag."""
    result = run_node(CHECK, AUTH_JS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "auth.js shim: all checks passed" in result.stdout
