"""Startup auth steps: env seed, setup token log, warnings, and the promise that nothing here stops the app."""

import asyncio
import json
import logging

import pytest

from watermeter import auth

SEED = "seeded-password-123"


@pytest.fixture
def app_module():
    """watermeter.app, importable because the unit conftest stubs the heavy modules."""
    import watermeter.app as module

    return module


@pytest.fixture
def auth_path(tmp_path, monkeypatch):
    """Fresh auth.json location with every seed and token variable cleared."""
    path = tmp_path / "data" / "auth.json"
    monkeypatch.setenv(auth.AUTH_FILE_ENV, str(path))
    for name in (auth.ADMIN_PASSWORD_ENV, auth.ADMIN_PASSWORD_FILE_ENV, auth.SETUP_TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(auth, "_setup_token", None)
    return path


@pytest.fixture
def is_mount(monkeypatch):
    """Pretend the data directory is a mount point so the volume warning stays out of the other tests."""
    monkeypatch.setattr("watermeter.app.os.path.ismount", lambda _path: True)


def _run(app_module):
    asyncio.run(app_module._auth_startup())


def test_env_seed_creates_auth_file_without_logging_the_value(app_module, auth_path, is_mount, monkeypatch, caplog):
    """Unconfigured with a seed: auth.json appears, the hash verifies the seed, the log never shows the value."""
    monkeypatch.setenv(auth.ADMIN_PASSWORD_ENV, SEED)
    with caplog.at_level(logging.INFO):
        _run(app_module)
    assert auth_path.exists()
    stored = json.loads(auth_path.read_text(encoding="utf-8"))
    assert auth.verify_password(SEED, stored["password_hash"])
    assert auth.ADMIN_PASSWORD_ENV in caplog.text
    assert SEED not in caplog.text


def test_env_seed_never_overrides_an_existing_password(app_module, auth_path, is_mount, monkeypatch):
    """Configured with another hash and a seed in the environment: auth.json is byte-identical afterwards."""
    assert auth.get_store().create(auth.hash_password("original-password-1"))
    before = auth_path.read_bytes()
    monkeypatch.setenv(auth.ADMIN_PASSWORD_ENV, SEED)
    _run(app_module)
    assert auth_path.read_bytes() == before


def test_unconfigured_without_seed_warns_that_setup_is_open(app_module, auth_path, is_mount, caplog):
    """No password and no seed: a WARNING says the setup page is open."""
    with caplog.at_level(logging.WARNING):
        _run(app_module)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("No admin password is set" in m and "/setup" in m for m in warnings)
    assert not auth_path.exists()


def test_setup_token_is_logged_when_required_and_unconfigured(app_module, auth_path, is_mount, monkeypatch, caplog):
    """WATERMETER_SETUP_TOKEN_REQUIRED=1 puts the token that the setup page checks into the log."""
    monkeypatch.setenv(auth.SETUP_TOKEN_ENV, "1")
    with caplog.at_level(logging.WARNING):
        _run(app_module)
    token = auth.get_setup_token()
    assert token
    assert token in caplog.text


def test_setup_token_is_not_generated_when_not_required(app_module, auth_path, is_mount, caplog):
    """Without the flag no token exists and nothing about one is logged."""
    with caplog.at_level(logging.WARNING):
        _run(app_module)
    assert auth._setup_token is None
    assert "Setup token" not in caplog.text


def test_failure_in_auth_startup_is_logged_and_swallowed(app_module, auth_path, is_mount, monkeypatch, caplog):
    """D-07: a crash inside the auth steps never reaches the lifespan."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("seed exploded")

    monkeypatch.setattr(auth, "bootstrap_from_env", boom)
    with caplog.at_level(logging.ERROR):
        _run(app_module)
    assert any(r.levelno == logging.ERROR and "seed exploded" in r.getMessage() for r in caplog.records)


def test_missing_volume_warns_without_a_seed(app_module, auth_path, monkeypatch, caplog):
    """A data directory that is not a mount point and no env seed: a WARNING names the persistent /data volume."""
    monkeypatch.setattr("watermeter.app.os.path.ismount", lambda _path: False)
    with caplog.at_level(logging.WARNING):
        _run(app_module)
    assert any("persistent" in r.getMessage() and "/data" in r.getMessage() for r in caplog.records)


def test_missing_volume_warning_is_skipped_with_a_seed(app_module, auth_path, monkeypatch, caplog):
    """With an env seed the password survives a recreated container, so the volume warning is not needed."""
    monkeypatch.setattr("watermeter.app.os.path.ismount", lambda _path: False)
    monkeypatch.setenv(auth.ADMIN_PASSWORD_ENV, SEED)
    with caplog.at_level(logging.WARNING):
        _run(app_module)
    assert not any("persistent" in r.getMessage() for r in caplog.records)


def test_lifespan_calls_auth_startup_before_the_reading_loop_starts():
    """The auth step runs first in the lifespan and the reading components start after it."""
    import inspect

    import watermeter.app as module

    source = inspect.getsource(module.lifespan)
    assert source.index("await _auth_startup()") < source.index("service.start_mqtt()")
