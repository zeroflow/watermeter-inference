"""
Config utilities with comment preservation using ruamel.yaml.

This module provides YAML handling that preserves comments and formatting
when loading and saving configuration files.
"""

import json
import logging
import os
import re
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Union

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

logger = logging.getLogger(__name__)

# Global YAML instance configured for comment preservation
_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.width = 120  # Line width before wrapping


def get_yaml() -> YAML:
    """Get the configured YAML instance."""
    return _yaml


def load_config(path: Union[str, Path]) -> CommentedMap:
    """
    Load a YAML config file, preserving comments.

    Args:
        path: Path to the config file

    Returns:
        CommentedMap with the config data (comments preserved)
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        config = _yaml.load(f)
    if isinstance(config, dict) and "aiote" in config:
        logger.warning(
            "Config section 'aiote' is deprecated and ignored; "
            "use images.src / images.timeout / images.fetch_delay instead"
        )
    return config


def resolve_env_vars(value: Any) -> Any:
    """Replace ${VAR_NAME} patterns with environment variable values.

    Unset variables keep their ${VAR_NAME} syntax intact.
    Works recursively on dicts and lists.
    """
    if isinstance(value, str):
        return re.sub(
            r"\$\{(\w+)\}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_vars(v) for v in value]
    return value


def save_config(config: CommentedMap, path: Union[str, Path]) -> None:
    """
    Save a config to a YAML file, preserving comments.

    Args:
        config: The config data (CommentedMap)
        path: Path to save to
    """
    path = Path(path)
    with open(path, "w", encoding="utf-8") as f:
        _yaml.dump(config, f)
    logger.info(f"Config saved to {path}")


def load_config_string(yaml_string: str) -> CommentedMap:
    """
    Load config from a YAML string, preserving comments.

    Args:
        yaml_string: YAML content as string

    Returns:
        CommentedMap with the config data
    """
    return _yaml.load(StringIO(yaml_string))


def dump_config_string(config: CommentedMap) -> str:
    """
    Dump config to a YAML string, preserving comments.

    Args:
        config: The config data (CommentedMap)

    Returns:
        YAML string with comments preserved
    """
    stream = StringIO()
    _yaml.dump(config, stream)
    return stream.getvalue()


def update_config(path: Union[str, Path], updater) -> "CommentedMap":
    """
    Load config, apply updater function, save, and return the updated config.

    The updater receives the CommentedMap and mutates it in-place.
    Comments and formatting are preserved.

    Args:
        path: Path to the config file
        updater: Callable that receives the config and mutates it

    Returns:
        The updated config (CommentedMap)
    """
    config = load_config(path)
    updater(config)
    save_config(config, path)
    return config


def validate_config(yaml_string: str) -> Dict[str, Any]:
    """
    Validate a YAML config string.

    Args:
        yaml_string: YAML content to validate

    Returns:
        Dict with 'valid' (bool) and 'error' (str or None)
    """
    try:
        config = load_config_string(yaml_string)

        # Basic structure validation
        required_sections = ["images", "mqtt", "inference"]
        missing = [s for s in required_sections if s not in config]

        if missing:
            return {"valid": False, "error": f"Missing required sections: {', '.join(missing)}"}

        return {"valid": True, "error": None}

    except Exception as e:
        return {"valid": False, "error": str(e)}


# JSON Schema for config validation
CONFIG_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Water Meter AI Service Configuration",
    "type": "object",
    "required": ["images", "mqtt", "inference"],
    "properties": {
        "images": {
            "type": "object",
            "description": "Image source configuration",
            "required": ["digits", "arrows"],
            "properties": {
                "process_separate": {
                    "type": "boolean",
                    "description": "Fetch individual images (true) or extract ROIs from whole image (false)",
                },
                "src": {"type": "string", "description": "URL of the whole meter image"},
                "timeout": {"type": "number", "description": "Image fetch timeout in seconds (default 30)"},
                "fetch_delay": {
                    "type": "number",
                    "description": "Delay between individual image fetches when process_separate is true (default 0.1)",
                },
                "digits": {"type": "array", "description": "List of digit image IDs", "items": {"type": "string"}},
                "arrows": {
                    "type": "array",
                    "description": "List of arrow/analog image IDs",
                    "items": {"type": "string"},
                },
            },
        },
        "detection": {
            "type": "object",
            "description": "ROI detection settings for whole image mode",
            "properties": {
                "rotation": {"type": "number", "description": "Image rotation in degrees"},
                "fisheye_correction": {"type": "number", "description": "Lens distortion correction coefficient k1 (-1.0 to +1.0)"},
                "digits": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "description": "Number of digit positions"},
                        "rois": {
                            "type": "array",
                            "description": "ROI coordinates (normalized 0-1)",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "x": {"type": "number", "minimum": 0, "maximum": 1},
                                    "y": {"type": "number", "minimum": 0, "maximum": 1},
                                    "width": {"type": "number", "minimum": 0, "maximum": 1},
                                    "height": {"type": "number", "minimum": 0, "maximum": 1},
                                },
                            },
                        },
                    },
                },
                "analogs": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "description": "Number of analog positions"},
                        "rois": {
                            "type": "array",
                            "description": "ROI coordinates (normalized 0-1)",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "x": {"type": "number", "minimum": 0, "maximum": 1},
                                    "y": {"type": "number", "minimum": 0, "maximum": 1},
                                    "width": {"type": "number", "minimum": 0, "maximum": 1},
                                    "height": {"type": "number", "minimum": 0, "maximum": 1},
                                },
                            },
                        },
                    },
                },
                "markers": {
                    "type": "array",
                    "description": "Alignment marker positions",
                    "items": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "width": {"type": "number"},
                            "height": {"type": "number"},
                        },
                    },
                },
            },
        },
        "trigger": {
            "type": "object",
            "description": "Trigger mode configuration",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["mqtt", "cyclic", "both"],
                    "description": "Trigger mode: mqtt (external triggers), cyclic (periodic polling), or both",
                },
                "cyclic_interval": {
                    "type": "integer",
                    "minimum": 10,
                    "description": "Seconds between readings in cyclic/both mode",
                },
            },
        },
        "mqtt": {
            "type": "object",
            "description": "MQTT broker settings",
            "required": ["broker", "port"],
            "properties": {
                "broker": {"type": "string", "description": "MQTT broker hostname/IP"},
                "port": {"type": "integer", "description": "MQTT broker port"},
                "client_id": {"type": "string", "description": "MQTT client identifier"},
                "keepalive": {"type": "integer", "description": "Keepalive interval in seconds"},
                "trigger_topic": {"type": "string", "description": "Topic to listen for triggers"},
                "trigger_payload": {"type": "string", "description": "Payload that triggers processing"},
                "reset_topic": {"type": "string", "description": "Topic for reset commands"},
                "username": {
                    "type": "string",
                    "description": "MQTT broker username (optional, env MQTT_USERNAME overrides)",
                },
                "password": {
                    "type": "string",
                    "description": "MQTT broker password (optional, env MQTT_PASSWORD overrides)",
                },
            },
        },
        "homeassistant": {
            "type": "object",
            "description": "Home Assistant integration",
            "properties": {
                "enabled": {"type": "boolean", "description": "Enable HA integration"},
                "discovery_prefix": {"type": "string", "description": "HA MQTT discovery prefix"},
                "publish_topic": {"type": "string", "description": "Topic to publish readings"},
                "device": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "manufacturer": {"type": "string"},
                        "model": {"type": "string"},
                    },
                },
            },
        },
        "inference": {
            "type": "object",
            "description": "Model inference settings",
            "required": ["confidence_threshold"],
            "properties": {
                "confidence_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Minimum confidence for valid predictions",
                },
                "device": {
                    "type": "string",
                    "enum": ["CPU", "GPU", "AUTO"],
                    "description": "OpenVINO inference device",
                },
                "digits_model": {"type": "string", "description": "Path to digits model"},
                "digits_classes": {"type": "array", "items": {"type": "string"}, "description": "Digit class labels"},
                "digits_resolution": {"type": "integer", "description": "Input resolution for digits"},
                "arrows_model": {"type": "string", "description": "Path to arrows model"},
                "arrows_classes": {"type": "array", "items": {"type": "string"}, "description": "Arrow class labels"},
                "arrows_resolution": {"type": "integer", "description": "Input resolution for arrows"},
                "arrows_mode": {
                    "type": "string",
                    "enum": ["model", "opencv", "calibrated"],
                    "description": (
                        "Arrow inference backend: 'model' (ML/OpenVINO), 'opencv' (color threshold around the crop "
                        "centre) or 'calibrated' (needle tip around the calibrated pivot, interpolated between the "
                        "scale ticks; needs POST /api/arrows/calibrate, falls back to 'opencv' per dial without it)"
                    ),
                    "default": "model",
                },
                "calibrated_arrows": {
                    "type": "object",
                    "description": "Settings for arrows_mode='calibrated' (colour thresholds come from opencv_arrows)",
                    "properties": {
                        "calibration_file": {
                            "type": "string",
                            "description": "Calibration JSON written by POST /api/arrows/calibrate",
                            "default": "/data/arrow_calibration.json",
                        },
                        "tip_percentile": {
                            "type": "number",
                            "minimum": 50,
                            "maximum": 99.5,
                            "description": "Needle pixels beyond this distance percentile count as the tip",
                            "default": 95,
                        },
                    },
                    "additionalProperties": False,
                },
                "opencv_arrows": {
                    "type": "object",
                    "description": "Settings for OpenCV-based arrow detection (used when arrows_mode='opencv')",
                    "properties": {
                        "hue_ranges": {
                            "type": "array",
                            "description": "HSV hue ranges for arrow color detection (OpenCV 0-179 scale)",
                            "items": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                            "default": [[0, 15], [165, 180]],
                        },
                        "saturation_min": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 255,
                            "description": "Minimum HSV saturation for arrow pixels",
                            "default": 50,
                        },
                        "value_min": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 255,
                            "description": "Minimum HSV value (brightness) for arrow pixels",
                            "default": 50,
                        },
                    },
                    "additionalProperties": False,
                },
                "data_collection": {
                    "type": "object",
                    "description": "Quota-based data collection mode",
                    "properties": {
                        "enabled": {"type": "boolean", "default": False},
                        "quota_per_class": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 10,
                            "description": "Max images to collect per class per ROI",
                        },
                        "dedup_enabled": {"type": "boolean", "default": True},
                        "dedup_threshold": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 64,
                            "default": 10,
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
        "plausibility": {
            "type": "object",
            "description": "Reading plausibility checks",
            "properties": {
                "enable_reverse_detection": {"type": "boolean", "description": "Reject readings that go backwards"},
                "enable_rate_limit": {"type": "boolean", "description": "Enable rate-based plausibility checks"},
                "max_rate_per_hour": {"type": "number", "description": "Maximum m³/hour rate"},
                "max_rate_per_reading": {"type": "number", "description": "Maximum change per reading"},
                "rate_history_size": {"type": "integer", "description": "Number of readings for rate averaging"},
                "reverse_tolerance": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Decreases up to this many m³ are treated as jitter (held, not rejected)",
                },
                "reanchor_after": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Re-anchor baseline after this many consistent lower readings (0 = off)",
                },
                "reanchor_max_spread": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Max spread (m³) of the lower readings that may trigger re-anchoring",
                },
                "enable_consistency_check": {
                    "type": "boolean",
                    "description": "Check half/upper consistency between positions",
                },
                "enable_leak_detection": {
                    "type": "boolean",
                    "description": "Detect sustained high consumption (leak warning)",
                    "default": True,
                },
                "sustained_rate_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Rate threshold in m³/h for leak detection (each interval must exceed this)",
                },
                "sustained_rate_readings": {
                    "type": "integer",
                    "minimum": 2,
                    "description": "Number of consecutive above-threshold intervals before warning fires",
                },
            },
        },
        "correction": {
            "type": "object",
            "description": "Value correction using temporal and spatial context (BL-04)",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "Enable confidence-weighted value correction",
                    "default": False,
                },
                "confidence_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Only correct positions with confidence below this threshold",
                },
                "min_signal_agreement": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4,
                    "description": "Minimum number of contextual signals that must agree to apply correction",
                },
                "min_alternative_confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Minimum softmax probability for an alternative to be considered",
                },
                "rate_tolerance_factor": {
                    "type": "number",
                    "minimum": 1.0,
                    "description": "Multiplier for expected rate to define plausible window",
                },
                "max_corrections_per_reading": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 7,
                    "description": "Maximum number of positions to correct per reading",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 10,
                    "description": "Number of softmax alternatives to retrieve from model",
                },
                "cross_arrow_confidence_gate": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Confidence gate for cross-arrow consistency signal",
                },
            },
        },
        "confirmation": {
            "type": "object",
            "description": "User confirmation via Home Assistant for uncertain readings (BL-07)",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "Enable user confirmation flow for uncertain readings",
                    "default": False,
                },
                "request_topic": {"type": "string", "description": "MQTT topic to publish confirmation requests"},
                "response_topic": {"type": "string", "description": "MQTT topic to subscribe for user responses"},
                "timeout_minutes": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 60,
                    "description": "Auto-reject pending confirmation after this many minutes",
                },
                "min_warnings": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Trigger confirmation if reading has >= N warnings",
                },
                "min_low_confidence_positions": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Trigger confirmation if >= N positions are below confidence threshold",
                },
                "max_rate_jump_factor": {
                    "type": "number",
                    "minimum": 1.0,
                    "description": "Trigger confirmation if rate exceeds factor * average rate",
                },
            },
        },
        "low_confidence": {
            "type": "object",
            "description": "Low confidence image handling",
            "properties": {
                "warn_enabled": {"type": "boolean", "description": "Show warnings for low confidence"},
                "save_enabled": {"type": "boolean", "description": "Save low confidence images for training"},
                "save_path": {"type": "string", "description": "Base path for saving images"},
                "save_rate_limit": {"type": "integer", "description": "Minimum seconds between saves per ID"},
                "dedup_enabled": {
                    "type": "boolean",
                    "description": "Deduplicate images before saving using perceptual hash",
                    "default": True,
                },
                "dedup_threshold": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 64,
                    "description": "Maximum hamming distance to consider images as duplicates (0=exact, 64=all different)",
                    "default": 10,
                },
                "dedup_scope": {
                    "type": "string",
                    "enum": ["input", "input+ground_truth"],
                    "description": "Compare against input folder only, or also against ground truth",
                    "default": "input+ground_truth",
                },
            },
        },
        "persistence": {
            "type": "object",
            "description": "State persistence settings",
            "properties": {
                "enabled": {"type": "boolean", "description": "Enable state persistence"},
                "state_file": {"type": "string", "description": "Path to state file"},
            },
        },
        "alignment": {
            "type": "object",
            "description": "Image alignment against the reference frame",
            "additionalProperties": False,
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["template", "features"],
                    "description": "template: marker matchTemplate; features: AKAZE keypoints + homography vs reference",
                },
                "min_inliers": {
                    "type": "integer",
                    "minimum": 8,
                    "description": "Feature alignment: minimum homography inlier matches",
                },
                "min_inlier_ratio": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Feature alignment: minimum inlier share of ratio-test matches",
                },
                "marker_confidence_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Template alignment: minimum matchTemplate score",
                },
                "archive_raw_images": {"type": "boolean", "description": "Archive every fetched whole image"},
                "archive_dir": {"type": "string", "description": "Raw image archive directory"},
                "archive_max_age_days": {"type": "integer", "minimum": 1, "description": "Archive retention in days"},
                "max_consecutive_failures": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Consecutive alignment failures before pipeline goes STALE",
                },
            },
        },
        "dashboard": {
            "type": "object",
            "description": "Web dashboard settings",
            "properties": {
                "host": {"type": "string", "description": "Listen address"},
                "port": {"type": "integer", "description": "Listen port"},
                "auto_refresh_interval": {"type": "integer", "description": "Auto-refresh interval in seconds"},
            },
        },
        "logging": {
            "type": "object",
            "description": "Logging configuration",
            "properties": {
                "level": {"type": "string", "enum": ["DEBUG", "INFO", "WARNING", "ERROR"], "description": "Log level"},
                "format": {"type": "string", "description": "Log format string"},
                "file": {"type": ["string", "null"], "description": "Log file path (null for stdout)"},
            },
        },
    },
}


def get_config_schema() -> Dict[str, Any]:
    """Get the JSON schema for config validation."""
    return CONFIG_SCHEMA


def get_config_schema_json() -> str:
    """Get the JSON schema as a formatted JSON string."""
    return json.dumps(CONFIG_SCHEMA, indent=2)


# ── Data-collection defaults ──────────────────────────────────────────────

DATA_COLLECTION_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "quota_per_class": 10,
    "dedup_enabled": True,
    "dedup_threshold": 10,
}


def get_data_collection_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return data_collection config with defaults applied.

    Works even when the ``data_collection`` key is absent from the
    inference section (returns all defaults).
    """
    inference = config.get("inference", {})
    user = inference.get("data_collection", {})
    return {**DATA_COLLECTION_DEFAULTS, **user}


def validate_config_schema(config: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a config dict against the full JSON Schema.

    Returns:
        Dict with 'valid' (bool) and 'error' (str or None).
    """
    import jsonschema

    try:
        jsonschema.validate(instance=dict(config), schema=CONFIG_SCHEMA)
        return {"valid": True, "error": None}
    except jsonschema.ValidationError as exc:
        return {"valid": False, "error": exc.message}
