"""Regression test: YAML config comments must survive save round-trips.

Bug: Using plain PyYAML (yaml.dump) strips comments from config files.
Fix: Using ruamel.yaml with comment preservation.
"""

import pytest

from config_utils import load_config, save_config, load_config_string, dump_config_string


def test_inline_comment_preserved():
    yaml_str = "mqtt:\n  port: 1883  # default MQTT port\n"
    config = load_config_string(yaml_str)
    result = dump_config_string(config)
    assert "# default MQTT port" in result


def test_block_comment_preserved():
    yaml_str = "# Main configuration\n# Do not edit manually\nmqtt:\n  broker: localhost\n"
    config = load_config_string(yaml_str)
    result = dump_config_string(config)
    assert "# Main configuration" in result
    assert "# Do not edit manually" in result


def test_comment_survives_file_round_trip(tmp_path):
    yaml_str = "# Important comment\nmqtt:\n  port: 1883  # keep this\nimages:\n  digits: []\n  arrows: []\ninference:\n  confidence_threshold: 0.5\n"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml_str)

    config = load_config(config_file)
    config['mqtt']['port'] = 9999
    save_config(config, config_file)

    raw = config_file.read_text()
    assert "# Important comment" in raw
    assert "# keep this" in raw
    assert "9999" in raw
