"""
Config utilities with comment preservation using ruamel.yaml.

This module provides YAML handling that preserves comments and formatting
when loading and saving configuration files.
"""

import json
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Optional, Union
import logging

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

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
    with open(path, 'r', encoding='utf-8') as f:
        return _yaml.load(f)


def save_config(config: CommentedMap, path: Union[str, Path]) -> None:
    """
    Save a config to a YAML file, preserving comments.

    Args:
        config: The config data (CommentedMap)
        path: Path to save to
    """
    path = Path(path)
    with open(path, 'w', encoding='utf-8') as f:
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
        required_sections = ['images', 'mqtt', 'inference']
        missing = [s for s in required_sections if s not in config]

        if missing:
            return {
                'valid': False,
                'error': f"Missing required sections: {', '.join(missing)}"
            }

        return {'valid': True, 'error': None}

    except Exception as e:
        return {
            'valid': False,
            'error': str(e)
        }


# JSON Schema for config validation
CONFIG_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Water Meter AI Service Configuration",
    "type": "object",
    "required": ["images", "mqtt", "inference"],
    "properties": {
        "aiote": {
            "type": "object",
            "description": "AI-on-the-edge device settings",
            "properties": {
                "host": {"type": "string", "description": "Device IP or hostname"},
                "image_path": {"type": "string", "description": "Path to images on device"},
                "timeout": {"type": "number", "description": "Request timeout in seconds"},
                "fetch_delay": {"type": "number", "description": "Delay between image fetches"}
            }
        },
        "images": {
            "type": "object",
            "description": "Image source configuration",
            "required": ["digits", "arrows"],
            "properties": {
                "process_separate": {
                    "type": "boolean",
                    "description": "Fetch individual images (true) or extract ROIs from whole image (false)"
                },
                "src": {"type": "string", "description": "URL of the whole meter image"},
                "digits": {
                    "type": "array",
                    "description": "List of digit image IDs",
                    "items": {"type": "string"}
                },
                "arrows": {
                    "type": "array",
                    "description": "List of arrow/analog image IDs",
                    "items": {"type": "string"}
                }
            }
        },
        "detection": {
            "type": "object",
            "description": "ROI detection settings for whole image mode",
            "properties": {
                "rotation": {"type": "number", "description": "Image rotation in degrees"},
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
                                    "height": {"type": "number", "minimum": 0, "maximum": 1}
                                }
                            }
                        }
                    }
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
                                    "height": {"type": "number", "minimum": 0, "maximum": 1}
                                }
                            }
                        }
                    }
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
                            "height": {"type": "number"}
                        }
                    }
                }
            }
        },
        "trigger": {
            "type": "object",
            "description": "Trigger mode configuration",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["mqtt", "cyclic", "both"],
                    "description": "Trigger mode: mqtt (AIOTE triggers), cyclic (periodic polling), or both"
                },
                "cyclic_interval": {
                    "type": "integer",
                    "minimum": 10,
                    "description": "Seconds between readings in cyclic/both mode"
                }
            }
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
                "reset_topic": {"type": "string", "description": "Topic for reset commands"}
            }
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
                        "model": {"type": "string"}
                    }
                },
                "sensor": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "unit": {"type": "string"},
                        "device_class": {"type": "string"},
                        "state_class": {"type": "string"},
                        "icon": {"type": "string"}
                    }
                }
            }
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
                    "description": "Minimum confidence for valid predictions"
                },
                "device": {
                    "type": "string",
                    "enum": ["CPU", "GPU", "AUTO"],
                    "description": "OpenVINO inference device"
                },
                "digits_model": {"type": "string", "description": "Path to digits model"},
                "digits_classes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Digit class labels"
                },
                "digits_resolution": {"type": "integer", "description": "Input resolution for digits"},
                "arrows_model": {"type": "string", "description": "Path to arrows model"},
                "arrows_classes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Arrow class labels"
                },
                "arrows_resolution": {"type": "integer", "description": "Input resolution for arrows"}
            }
        },
        "plausibility": {
            "type": "object",
            "description": "Reading plausibility checks",
            "properties": {
                "enable_reverse_detection": {
                    "type": "boolean",
                    "description": "Reject readings that go backwards"
                },
                "enable_rate_limit": {
                    "type": "boolean",
                    "description": "Enable rate-based plausibility checks"
                },
                "max_rate_per_hour": {
                    "type": "number",
                    "description": "Maximum m³/hour rate"
                },
                "max_rate_per_reading": {
                    "type": "number",
                    "description": "Maximum change per reading"
                },
                "rate_history_size": {
                    "type": "integer",
                    "description": "Number of readings for rate averaging"
                },
                "enable_consistency_check": {
                    "type": "boolean",
                    "description": "Check half/upper consistency between positions"
                }
            }
        },
        "low_confidence": {
            "type": "object",
            "description": "Low confidence image handling",
            "properties": {
                "warn_enabled": {
                    "type": "boolean",
                    "description": "Show warnings for low confidence"
                },
                "save_enabled": {
                    "type": "boolean",
                    "description": "Save low confidence images for training"
                },
                "save_path": {
                    "type": "string",
                    "description": "Base path for saving images"
                },
                "save_rate_limit": {
                    "type": "integer",
                    "description": "Minimum seconds between saves per ID"
                },
                "dedup_enabled": {
                    "type": "boolean",
                    "description": "Deduplicate images before saving using perceptual hash",
                    "default": True
                },
                "dedup_threshold": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 64,
                    "description": "Maximum hamming distance to consider images as duplicates (0=exact, 64=all different)",
                    "default": 10
                },
                "dedup_scope": {
                    "type": "string",
                    "enum": ["input", "input+ground_truth"],
                    "description": "Compare against input folder only, or also against ground truth",
                    "default": "input+ground_truth"
                }
            }
        },
        "persistence": {
            "type": "object",
            "description": "State persistence settings",
            "properties": {
                "enabled": {"type": "boolean", "description": "Enable state persistence"},
                "state_file": {"type": "string", "description": "Path to state file"}
            }
        },
        "dashboard": {
            "type": "object",
            "description": "Web dashboard settings",
            "properties": {
                "host": {"type": "string", "description": "Listen address"},
                "port": {"type": "integer", "description": "Listen port"},
                "auto_refresh_interval": {
                    "type": "integer",
                    "description": "Auto-refresh interval in seconds"
                }
            }
        },
        "logging": {
            "type": "object",
            "description": "Logging configuration",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["DEBUG", "INFO", "WARNING", "ERROR"],
                    "description": "Log level"
                },
                "format": {"type": "string", "description": "Log format string"},
                "file": {
                    "type": ["string", "null"],
                    "description": "Log file path (null for stdout)"
                }
            }
        }
    }
}


def get_config_schema() -> Dict[str, Any]:
    """Get the JSON schema for config validation."""
    return CONFIG_SCHEMA


def get_config_schema_json() -> str:
    """Get the JSON schema as a formatted JSON string."""
    return json.dumps(CONFIG_SCHEMA, indent=2)
