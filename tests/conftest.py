"""Shared test fixtures for watermeter tests."""

import sys
from pathlib import Path

import pytest

# Add project root to path so we can import modules
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# CLI options for integration tests (must live in root conftest for early loading)
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--base-url",
        default=None,
        help="Use an already-running service instead of starting a fresh container",
    )
    parser.addoption(
        "--image",
        default="watermeter-dashboard:latest",
        help="Docker image to use for integration tests",
    )
    parser.addoption(
        "--no-build",
        action="store_true",
        default=False,
        help="Skip auto-building the Docker image (use existing image)",
    )


@pytest.fixture
def sample_config_yaml():
    """Minimal valid YAML config string."""
    return """\
# AI-on-the-edge device settings
aiote:
  host: "192.168.1.100"

images:
  process_separate: false
  src: "http://192.168.1.100/img_tmp/raw.jpg"
  digits:
    - digit_1
    - digit_2
  arrows:
    - analog_1

mqtt:
  broker: "192.168.1.50"
  port: 1883
  client_id: "watermeter"

inference:
  confidence_threshold: 0.6
  device: CPU
  digits_model: "/app/models/digits/model_abc/model_abc.xml"
  digits_classes: ["0","1","2","3","4","5","6","7","8","9","NAN"]
  digits_resolution: 32
  arrows_model: "/app/models/arrows/model_xyz/model_xyz.xml"
  arrows_classes: ["0.0","0.1","0.2","0.3","0.4","0.5","0.6","0.7","0.8","0.9"]
  arrows_resolution: 32

plausibility:
  enable_reverse_detection: true
  enable_rate_limit: true
  max_rate_per_hour: 0.5
  max_rate_per_reading: 1.0
  rate_history_size: 10
  enable_consistency_check: true

persistence:
  enabled: true
  state_file: "/data/state.json"
"""


@pytest.fixture
def sample_config_dict():
    """Config as a plain dict (for modules that take dict config)."""
    return {
        'images': {
            'process_separate': False,
            'src': 'http://192.168.1.100/img_tmp/raw.jpg',
            'digits': ['digit_1', 'digit_2'],
            'arrows': ['analog_1'],
        },
        'detection': {
            'digits': {'count': 2, 'rois': []},
            'analogs': {'count': 1, 'rois': []},
        },
        'mqtt': {
            'broker': '192.168.1.50',
            'port': 1883,
        },
        'inference': {
            'confidence_threshold': 0.6,
            'digits_model': '/app/models/digits/model_abc/model_abc.xml',
            'arrows_model': '/app/models/arrows/model_xyz/model_xyz.xml',
        },
        'plausibility': {
            'enable_reverse_detection': True,
            'enable_rate_limit': True,
            'max_rate_per_hour': 0.5,
            'max_rate_per_reading': 1.0,
            'rate_history_size': 10,
            'enable_consistency_check': True,
        },
    }
