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
