"""Unit tests for config_utils.py - YAML config handling."""

from watermeter.config_utils import (
    DATA_COLLECTION_DEFAULTS,
    dump_config_string,
    get_config_schema,
    get_config_schema_json,
    get_data_collection_config,
    load_config,
    load_config_string,
    save_config,
    update_config,
    validate_config,
    validate_config_schema,
)


class TestLoadSaveConfig:
    """Tests for file-based config load/save."""

    def test_round_trip(self, tmp_path, sample_config_yaml):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(sample_config_yaml)

        config = load_config(config_file)
        assert config['mqtt']['broker'] == '192.168.1.50'
        assert config['mqtt']['port'] == 1883

        # Save and reload
        save_config(config, config_file)
        config2 = load_config(config_file)
        assert config2['mqtt']['broker'] == config['mqtt']['broker']

    def test_load_preserves_structure(self, tmp_path, sample_config_yaml):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(sample_config_yaml)

        config = load_config(config_file)
        assert 'images' in config
        assert 'mqtt' in config
        assert 'inference' in config
        assert config['images']['digits'] == ['digit_1', 'digit_2']

    def test_save_creates_file(self, tmp_path):
        config = load_config_string("test: value\n")
        out_file = tmp_path / "out.yaml"
        save_config(config, out_file)
        assert out_file.exists()


class TestConfigString:
    """Tests for string-based config operations."""

    def test_load_string(self):
        config = load_config_string("key: value\nnested:\n  a: 1\n")
        assert config['key'] == 'value'
        assert config['nested']['a'] == 1

    def test_dump_string(self):
        config = load_config_string("key: value\n")
        result = dump_config_string(config)
        assert 'key: value' in result

    def test_round_trip_string(self, sample_config_yaml):
        config = load_config_string(sample_config_yaml)
        dumped = dump_config_string(config)
        config2 = load_config_string(dumped)
        assert config2['mqtt']['port'] == config['mqtt']['port']

    def test_comment_preservation(self):
        """Comments should survive a load/dump round-trip."""
        yaml_with_comment = "# This is a comment\nkey: value\n"
        config = load_config_string(yaml_with_comment)
        dumped = dump_config_string(config)
        assert '# This is a comment' in dumped

    def test_empty_string(self):
        config = load_config_string("")
        assert config is None


class TestUpdateConfig:
    """Tests for the update_config helper."""

    def test_update_modifies_value(self, tmp_path, sample_config_yaml):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(sample_config_yaml)

        def set_port(cfg):
            cfg['mqtt']['port'] = 9999

        result = update_config(config_file, set_port)
        assert result['mqtt']['port'] == 9999

        # Verify persisted
        reloaded = load_config(config_file)
        assert reloaded['mqtt']['port'] == 9999

    def test_update_preserves_comments(self, tmp_path):
        yaml_str = "# Important comment\nmqtt:\n  port: 1883\nimages:\n  digits: []\n  arrows: []\ninference:\n  confidence_threshold: 0.5\n"
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_str)

        def bump_port(cfg):
            cfg['mqtt']['port'] = 2000

        update_config(config_file, bump_port)

        raw = config_file.read_text()
        assert '# Important comment' in raw
        assert '2000' in raw


class TestValidateConfig:
    """Tests for config validation."""

    def test_valid_config(self, sample_config_yaml):
        result = validate_config(sample_config_yaml)
        assert result['valid'] is True
        assert result['error'] is None

    def test_missing_images_section(self):
        result = validate_config("mqtt:\n  broker: x\ninference:\n  confidence_threshold: 0.5\n")
        assert result['valid'] is False
        assert 'images' in result['error']

    def test_missing_mqtt_section(self):
        result = validate_config("images:\n  digits: []\n  arrows: []\ninference:\n  confidence_threshold: 0.5\n")
        assert result['valid'] is False
        assert 'mqtt' in result['error']

    def test_missing_inference_section(self):
        result = validate_config("images:\n  digits: []\n  arrows: []\nmqtt:\n  broker: x\n")
        assert result['valid'] is False
        assert 'inference' in result['error']

    def test_missing_multiple_sections(self):
        result = validate_config("aiote:\n  host: x\n")
        assert result['valid'] is False
        assert 'images' in result['error']
        assert 'mqtt' in result['error']
        assert 'inference' in result['error']

    def test_invalid_yaml_syntax(self):
        result = validate_config("{{invalid: yaml: [}")
        assert result['valid'] is False

    def test_all_required_present(self):
        minimal = "images:\n  digits: []\n  arrows: []\nmqtt:\n  broker: x\n  port: 1883\ninference:\n  confidence_threshold: 0.5\n"
        result = validate_config(minimal)
        assert result['valid'] is True


class TestConfigSchema:
    """Tests for the JSON schema."""

    def test_schema_has_required_fields(self):
        schema = get_config_schema()
        assert schema['required'] == ['images', 'mqtt', 'inference']
        assert 'properties' in schema

    def test_schema_json_is_valid(self):
        import json
        schema_json = get_config_schema_json()
        parsed = json.loads(schema_json)
        assert parsed['title'] == 'Water Meter AI Service Configuration'

    def test_schema_has_data_collection(self):
        schema = get_config_schema()
        inf_props = schema["properties"]["inference"]["properties"]
        assert "data_collection" in inf_props
        dc = inf_props["data_collection"]
        assert dc["type"] == "object"
        assert "quota_per_class" in dc["properties"]
        assert "dedup_threshold" in dc["properties"]


class TestDataCollectionDefaults:
    """Tests for data_collection defaults and schema validation."""

    def test_defaults_applied_when_section_absent(self):
        """Config without data_collection section gets all defaults."""
        config = {"inference": {"confidence_threshold": 0.5}}
        result = get_data_collection_config(config)
        assert result == DATA_COLLECTION_DEFAULTS
        assert result["enabled"] is False
        assert result["quota_per_class"] == 10
        assert result["dedup_enabled"] is True
        assert result["dedup_threshold"] == 10
        assert "counters_file" not in result

    def test_defaults_applied_when_inference_absent(self):
        """Config without inference section still returns defaults."""
        config = {}
        result = get_data_collection_config(config)
        assert result == DATA_COLLECTION_DEFAULTS

    def test_user_values_override_defaults(self):
        """User-provided values override defaults."""
        config = {
            "inference": {
                "confidence_threshold": 0.5,
                "data_collection": {"enabled": True, "quota_per_class": 50},
            }
        }
        result = get_data_collection_config(config)
        assert result["enabled"] is True
        assert result["quota_per_class"] == 50
        # Non-overridden defaults still present
        assert result["dedup_enabled"] is True
        assert result["dedup_threshold"] == 10

    def test_quota_per_class_below_minimum_fails_validation(self):
        """quota_per_class < 1 must fail schema validation."""
        config = {
            "images": {"digits": [], "arrows": []},
            "mqtt": {"broker": "x", "port": 1883},
            "inference": {
                "confidence_threshold": 0.5,
                "data_collection": {"quota_per_class": 0},
            },
        }
        result = validate_config_schema(config)
        assert result["valid"] is False
        assert "minimum" in result["error"].lower() or "0" in result["error"]

    def test_dedup_threshold_above_maximum_fails_validation(self):
        """dedup_threshold > 64 must fail schema validation."""
        config = {
            "images": {"digits": [], "arrows": []},
            "mqtt": {"broker": "x", "port": 1883},
            "inference": {
                "confidence_threshold": 0.5,
                "data_collection": {"dedup_threshold": 65},
            },
        }
        result = validate_config_schema(config)
        assert result["valid"] is False
        assert "maximum" in result["error"].lower() or "65" in result["error"]

    def test_valid_data_collection_passes_validation(self):
        """A valid data_collection config passes schema validation."""
        config = {
            "images": {"digits": [], "arrows": []},
            "mqtt": {"broker": "x", "port": 1883},
            "inference": {
                "confidence_threshold": 0.5,
                "data_collection": {
                    "enabled": True,
                    "quota_per_class": 20,
                    "dedup_enabled": False,
                    "dedup_threshold": 5,
                },
            },
        }
        result = validate_config_schema(config)
        assert result["valid"] is True

    def test_additional_properties_rejected(self):
        """Unknown keys in data_collection are rejected."""
        config = {
            "images": {"digits": [], "arrows": []},
            "mqtt": {"broker": "x", "port": 1883},
            "inference": {
                "confidence_threshold": 0.5,
                "data_collection": {"unknown_key": "value"},
            },
        }
        result = validate_config_schema(config)
        assert result["valid"] is False
