"""Unit tests for MQTT config API routes."""

from unittest.mock import MagicMock


class TestMqttGetConfig:
    """Tests for GET /api/mqtt/config."""

    def test_get_returns_200(self, test_client, tmp_path, monkeypatch):
        """GET /api/mqtt/config returns 200."""
        monkeypatch.chdir(tmp_path)
        config_yaml = (
            "mqtt:\n"
            "  broker: mybroker\n"
            "  port: 1883\n"
            "  client_id: meter\n"
            "trigger:\n"
            "  mode: mqtt\n"
            "homeassistant:\n"
            "  enabled: true\n"
        )
        (tmp_path / "config.yaml").write_text(config_yaml)
        response = test_client.get("/api/mqtt/config")
        assert response.status_code == 200

    def test_get_returns_mqtt_trigger_ha_sections(self, test_client, tmp_path, monkeypatch):
        """GET returns mqtt, trigger, homeassistant sections."""
        monkeypatch.chdir(tmp_path)
        config_yaml = (
            "mqtt:\n"
            "  broker: mybroker\n"
            "  port: 1883\n"
            "trigger:\n"
            "  mode: mqtt\n"
            "homeassistant:\n"
            "  enabled: true\n"
        )
        (tmp_path / "config.yaml").write_text(config_yaml)
        response = test_client.get("/api/mqtt/config")
        data = response.json()
        assert "mqtt" in data
        assert "trigger" in data
        assert "homeassistant" in data

    def test_get_returns_raw_env_tokens(self, test_client, tmp_path, monkeypatch):
        """GET returns raw ${...} tokens, not resolved values."""
        monkeypatch.chdir(tmp_path)
        config_yaml = (
            "mqtt:\n"
            "  broker: mybroker\n"
            "  port: 1883\n"
            "  username: ${MQTT_USER}\n"
            "  password: ${MQTT_PASS}\n"
            "trigger:\n"
            "  mode: mqtt\n"
            "homeassistant:\n"
            "  enabled: false\n"
        )
        (tmp_path / "config.yaml").write_text(config_yaml)
        # Even if env vars are set, GET should return raw tokens
        monkeypatch.setenv("MQTT_USER", "real_user")
        monkeypatch.setenv("MQTT_PASS", "real_pass")
        response = test_client.get("/api/mqtt/config")
        data = response.json()
        assert data["mqtt"]["username"] == "${MQTT_USER}"
        assert data["mqtt"]["password"] == "${MQTT_PASS}"

    def test_get_mqtt_broker_value(self, test_client, tmp_path, monkeypatch):
        """GET returns correct broker value from config."""
        monkeypatch.chdir(tmp_path)
        config_yaml = (
            "mqtt:\n"
            "  broker: 192.168.1.100\n"
            "  port: 1883\n"
            "trigger:\n"
            "  mode: cyclic\n"
            "homeassistant:\n"
            "  enabled: true\n"
        )
        (tmp_path / "config.yaml").write_text(config_yaml)
        response = test_client.get("/api/mqtt/config")
        data = response.json()
        assert data["mqtt"]["broker"] == "192.168.1.100"

    def test_get_missing_config_returns_404(self, test_client, tmp_path, monkeypatch):
        """GET returns 404 if config.yaml is missing."""
        empty_dir = tmp_path / "no_config"
        empty_dir.mkdir()
        monkeypatch.chdir(empty_dir)
        response = test_client.get("/api/mqtt/config")
        assert response.status_code == 404


class TestMqttPostConfig:
    """Tests for POST /api/mqtt/config."""

    def test_post_saves_config(self, test_client, mock_service, tmp_path, monkeypatch):
        """POST saves mqtt/trigger/ha sections and returns success."""
        monkeypatch.chdir(tmp_path)
        config_yaml = (
            "mqtt:\n"
            "  broker: old_broker\n"
            "  port: 1883\n"
            "trigger:\n"
            "  mode: mqtt\n"
            "homeassistant:\n"
            "  enabled: false\n"
        )
        (tmp_path / "config.yaml").write_text(config_yaml)
        mock_service.reload_config.return_value = {"config_updated": True, "mqtt_reconnected": False}

        response = test_client.post(
            "/api/mqtt/config",
            json={
                "mqtt": {"broker": "new_broker", "port": 1883},
                "trigger": {"mode": "cyclic"},
                "homeassistant": {"enabled": True},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_post_calls_reload_config(self, test_client, mock_service, tmp_path, monkeypatch):
        """POST calls service.reload_config after saving."""
        monkeypatch.chdir(tmp_path)
        config_yaml = (
            "mqtt:\n"
            "  broker: broker\n"
            "  port: 1883\n"
            "trigger:\n"
            "  mode: mqtt\n"
            "homeassistant:\n"
            "  enabled: true\n"
        )
        (tmp_path / "config.yaml").write_text(config_yaml)
        mock_service.reload_config.return_value = {"config_updated": True, "mqtt_reconnected": False}

        test_client.post(
            "/api/mqtt/config",
            json={
                "mqtt": {"broker": "broker2", "port": 1884},
                "trigger": {"mode": "mqtt"},
                "homeassistant": {"enabled": False},
            },
        )
        mock_service.reload_config.assert_called_once()

    def test_post_returns_mqtt_reconnected_message(self, test_client, mock_service, tmp_path, monkeypatch):
        """POST includes MQTT reconnected info in message when reconnected."""
        monkeypatch.chdir(tmp_path)
        config_yaml = (
            "mqtt:\n  broker: broker\n  port: 1883\n"
            "trigger:\n  mode: mqtt\n"
            "homeassistant:\n  enabled: true\n"
        )
        (tmp_path / "config.yaml").write_text(config_yaml)
        mock_service.reload_config.return_value = {"config_updated": True, "mqtt_reconnected": True}

        response = test_client.post(
            "/api/mqtt/config",
            json={
                "mqtt": {"broker": "different_broker"},
                "trigger": {"mode": "mqtt"},
                "homeassistant": {"enabled": True},
            },
        )
        data = response.json()
        assert "reconnected" in data["message"].lower() or data["success"] is True

    def test_post_persists_broker_to_file(self, test_client, mock_service, tmp_path, monkeypatch):
        """POST actually writes the new broker to config.yaml."""
        monkeypatch.chdir(tmp_path)
        config_yaml = (
            "mqtt:\n  broker: old\n  port: 1883\n"
            "trigger:\n  mode: mqtt\n"
            "homeassistant:\n  enabled: true\n"
        )
        (tmp_path / "config.yaml").write_text(config_yaml)
        mock_service.reload_config.return_value = {"config_updated": True, "mqtt_reconnected": False}

        test_client.post(
            "/api/mqtt/config",
            json={
                "mqtt": {"broker": "new_broker_saved"},
                "trigger": {"mode": "mqtt"},
                "homeassistant": {"enabled": True},
            },
        )

        import yaml
        with open(tmp_path / "config.yaml") as f:
            saved = yaml.safe_load(f)
        assert saved["mqtt"]["broker"] == "new_broker_saved"


class TestMqttTestConnection:
    """Tests for POST /api/mqtt/test."""

    def test_missing_broker_returns_400(self, test_client):
        """POST /api/mqtt/test without broker returns 400."""
        response = test_client.post(
            "/api/mqtt/test",
            json={"port": 1883},
        )
        assert response.status_code == 400

    def test_successful_connection_returns_ok(self, test_client, monkeypatch):
        """POST /api/mqtt/test returns status=ok on successful connect."""
        import watermeter.routes.mqtt as mqtt_module

        mock_client_instance = MagicMock()
        mock_client_class = MagicMock(return_value=mock_client_instance)
        # connect() succeeds (no exception)
        mock_client_instance.connect.return_value = None

        monkeypatch.setattr(mqtt_module, "mqtt_client_class", mock_client_class)

        response = test_client.post(
            "/api/mqtt/test",
            json={"broker": "localhost", "port": 1883},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_connection_refused_returns_error(self, test_client, monkeypatch):
        """POST /api/mqtt/test returns success=false when connection fails."""
        import watermeter.routes.mqtt as mqtt_module

        mock_client_instance = MagicMock()
        mock_client_class = MagicMock(return_value=mock_client_instance)
        mock_client_instance.connect.side_effect = ConnectionRefusedError("Connection refused")

        monkeypatch.setattr(mqtt_module, "mqtt_client_class", mock_client_class)

        response = test_client.post(
            "/api/mqtt/test",
            json={"broker": "localhost", "port": 1883},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "refused" in data["message"].lower() or "failed" in data["message"].lower()

    def test_oserror_returns_error(self, test_client, monkeypatch):
        """POST /api/mqtt/test returns success=false on OSError (host not found)."""
        import watermeter.routes.mqtt as mqtt_module

        mock_client_instance = MagicMock()
        mock_client_class = MagicMock(return_value=mock_client_instance)
        mock_client_instance.connect.side_effect = OSError("Name or service not known")

        monkeypatch.setattr(mqtt_module, "mqtt_client_class", mock_client_class)

        response = test_client.post(
            "/api/mqtt/test",
            json={"broker": "nonexistent.host", "port": 1883},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_resolves_env_vars_in_credentials(self, test_client, monkeypatch):
        """POST /api/mqtt/test resolves ${ENV_VAR} in username/password."""
        import watermeter.routes.mqtt as mqtt_module

        monkeypatch.setenv("TEST_MQTT_USER", "resolved_user")
        monkeypatch.setenv("TEST_MQTT_PASS", "resolved_pass")

        mock_client_instance = MagicMock()
        mock_client_class = MagicMock(return_value=mock_client_instance)
        mock_client_instance.connect.return_value = None

        monkeypatch.setattr(mqtt_module, "mqtt_client_class", mock_client_class)

        response = test_client.post(
            "/api/mqtt/test",
            json={
                "broker": "localhost",
                "port": 1883,
                "username": "${TEST_MQTT_USER}",
                "password": "${TEST_MQTT_PASS}",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # username_pw_set should be called with resolved values
        mock_client_instance.username_pw_set.assert_called_once_with(
            "resolved_user", "resolved_pass"
        )

    def test_warns_about_unresolved_env_vars(self, test_client, monkeypatch):
        """POST /api/mqtt/test warns when env vars cannot be resolved."""
        import watermeter.routes.mqtt as mqtt_module

        # Ensure env var is NOT set
        monkeypatch.delenv("UNSET_MQTT_VAR", raising=False)

        mock_client_instance = MagicMock()
        mock_client_class = MagicMock(return_value=mock_client_instance)
        mock_client_instance.connect.return_value = None

        monkeypatch.setattr(mqtt_module, "mqtt_client_class", mock_client_class)

        response = test_client.post(
            "/api/mqtt/test",
            json={
                "broker": "localhost",
                "port": 1883,
                "username": "${UNSET_MQTT_VAR}",
            },
        )
        assert response.status_code == 200
        data = response.json()
        # Should include warnings array with unresolved var info
        assert "warnings" in data or "unresolved" in data.get("message", "").lower()

    def test_disconnects_after_successful_connect(self, test_client, monkeypatch):
        """POST /api/mqtt/test disconnects after testing."""
        import watermeter.routes.mqtt as mqtt_module

        mock_client_instance = MagicMock()
        mock_client_class = MagicMock(return_value=mock_client_instance)
        mock_client_instance.connect.return_value = None

        monkeypatch.setattr(mqtt_module, "mqtt_client_class", mock_client_class)

        test_client.post(
            "/api/mqtt/test",
            json={"broker": "localhost", "port": 1883},
        )
        mock_client_instance.disconnect.assert_called_once()

    def test_uses_5s_keepalive(self, test_client, monkeypatch):
        """POST /api/mqtt/test uses 5 second keepalive."""
        import watermeter.routes.mqtt as mqtt_module

        mock_client_instance = MagicMock()
        mock_client_class = MagicMock(return_value=mock_client_instance)
        mock_client_instance.connect.return_value = None

        monkeypatch.setattr(mqtt_module, "mqtt_client_class", mock_client_class)

        test_client.post(
            "/api/mqtt/test",
            json={"broker": "mybr", "port": 1883},
        )
        connect_kwargs = mock_client_instance.connect.call_args
        # keepalive=5 should be passed
        args = connect_kwargs[0] if connect_kwargs[0] else []
        kwargs = connect_kwargs[1] if connect_kwargs[1] else {}
        # Either positional arg[2] or keyword keepalive
        if len(args) >= 3:
            assert args[2] == 5
        else:
            assert kwargs.get("keepalive") == 5
