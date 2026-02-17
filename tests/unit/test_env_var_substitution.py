"""Tests for ${ENV_VAR} substitution in config values."""

from watermeter.config_utils import resolve_env_vars


class TestResolveEnvVars:

    def test_simple_string_substitution(self, monkeypatch):
        monkeypatch.setenv("MQTT_HOST", "192.168.1.1")
        assert resolve_env_vars("${MQTT_HOST}") == "192.168.1.1"

    def test_string_with_surrounding_text(self, monkeypatch):
        monkeypatch.setenv("PORT", "1883")
        assert resolve_env_vars("broker:${PORT}") == "broker:1883"

    def test_multiple_vars_in_string(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "1883")
        assert resolve_env_vars("${HOST}:${PORT}") == "localhost:1883"

    def test_missing_env_var_keeps_original(self):
        result = resolve_env_vars("${NONEXISTENT_VAR_XYZ}")
        assert result == "${NONEXISTENT_VAR_XYZ}"

    def test_plain_string_unchanged(self):
        assert resolve_env_vars("hello world") == "hello world"

    def test_empty_string(self):
        assert resolve_env_vars("") == ""

    def test_dict_substitution(self, monkeypatch):
        monkeypatch.setenv("BROKER", "10.0.0.1")
        data = {"broker": "${BROKER}", "port": 1883}
        result = resolve_env_vars(data)
        assert result == {"broker": "10.0.0.1", "port": 1883}

    def test_nested_dict(self, monkeypatch):
        monkeypatch.setenv("USER", "admin")
        data = {"mqtt": {"username": "${USER}", "port": 1883}}
        result = resolve_env_vars(data)
        assert result == {"mqtt": {"username": "admin", "port": 1883}}

    def test_list_substitution(self, monkeypatch):
        monkeypatch.setenv("TOPIC", "watermeter/status")
        data = ["${TOPIC}", "other"]
        result = resolve_env_vars(data)
        assert result == ["watermeter/status", "other"]

    def test_non_string_passthrough(self):
        assert resolve_env_vars(42) == 42
        assert resolve_env_vars(True) is True
        assert resolve_env_vars(None) is None
        assert resolve_env_vars(3.14) == 3.14

    def test_mixed_nested_structure(self, monkeypatch):
        monkeypatch.setenv("PASS", "secret")
        data = {
            "mqtt": {
                "password": "${PASS}",
                "port": 1883,
                "topics": ["a", "${PASS}"],
            }
        }
        result = resolve_env_vars(data)
        assert result["mqtt"]["password"] == "secret"
        assert result["mqtt"]["port"] == 1883
        assert result["mqtt"]["topics"] == ["a", "secret"]

    def test_dollar_without_braces_unchanged(self):
        assert resolve_env_vars("$NOT_A_VAR") == "$NOT_A_VAR"

    def test_partial_syntax_unchanged(self):
        assert resolve_env_vars("${") == "${"
        assert resolve_env_vars("${}") == "${}"
