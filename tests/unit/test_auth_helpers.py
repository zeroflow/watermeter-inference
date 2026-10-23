"""Tests for the password policy, redirect helpers, setup token, environment seed and the leaf-module rule."""

import ast
import logging
import re
from pathlib import Path

import pytest

from watermeter import auth

STDLIB_ALLOWLIST = {
    "base64",
    "collections",
    "dataclasses",
    "enum",
    "getpass",
    "hashlib",
    "hmac",
    "json",
    "logging",
    "math",
    "os",
    "pathlib",
    "re",
    "secrets",
    "stat",
    "sys",
    "tempfile",
    "threading",
    "time",
    "typing",
    "urllib",
}


@pytest.fixture
def auth_path(tmp_path, monkeypatch):
    """Fresh auth.json location, with every seed variable cleared."""
    path = tmp_path / "data" / "auth.json"
    monkeypatch.setenv("WATERMETER_AUTH_FILE", str(path))
    for name in ("WATERMETER_ADMIN_PASSWORD", "WATERMETER_ADMIN_PASSWORD_FILE", "WATERMETER_SETUP_TOKEN_REQUIRED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(auth, "_setup_token", None, raising=False)
    return path


# -- validate_new_password -------------------------------------------------------------------------------------------


def test_validate_new_password_empty():
    """An empty password is required."""
    assert auth.validate_new_password("", "") == "Password is required"


def test_validate_new_password_too_short():
    """Seven characters is below the minimum."""
    message = auth.validate_new_password("a" * 7, "a" * 7)
    assert message is not None and "at least 8" in message


@pytest.mark.parametrize("length", [8, 1024])
def test_validate_new_password_accepts_boundaries(length):
    """8 and 1024 characters pass."""
    assert auth.validate_new_password("a" * length, "a" * length) is None


def test_validate_new_password_too_long():
    """1025 characters is above the maximum."""
    message = auth.validate_new_password("a" * 1025, "a" * 1025)
    assert message is not None and "at most 1024" in message


def test_validate_new_password_mismatch():
    """A one-character difference in the confirmation is refused."""
    assert auth.validate_new_password("abcdefgh", "abcdefgi") == "Passwords do not match"


def test_validate_new_password_counts_code_points_not_bytes():
    """Eight two-byte characters pass, seven fail."""
    assert auth.validate_new_password("ä" * 8, "ä" * 8) is None
    assert auth.validate_new_password("ä" * 7, "ä" * 7) is not None


def test_passwords_are_not_unicode_normalized():
    """Composed and decomposed forms are different passwords."""
    composed = "café secret"
    decomposed = "café secret"
    stored = auth.hash_password(composed)
    assert auth.verify_password(composed, stored) is True
    assert auth.verify_password(decomposed, stored) is False


# -- safe_next / next_from_hx_current_url ----------------------------------------------------------------------------


def test_safe_next_keeps_local_paths():
    """A single-slash relative path with a query stays as it is."""
    assert auth.safe_next("/training?tab=2") == "/training?tab=2"
    assert auth.safe_next("/") == "/"
    assert auth.safe_next("/" + "a" * 2047) == "/" + "a" * 2047


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "training",
        "//evil.example",
        "/\\evil",
        "https://evil.example/",
        "javascript:alert(1)",
        "/login",
        "/login?x=1",
        "/setup",
        "/setup?next=/",
        "/logout",
        "/ok\r\nSet-Cookie: x=1",
        "/ok\x00",
        "/ok\x7f",
        "/ok\tpath",
        "/" + "a" * 2048,
    ],
)
def test_safe_next_rejects_everything_else(value):
    """Open-redirect shapes, auth pages, control characters and over-long values fall back to /."""
    assert auth.safe_next(value) == "/"


def test_next_from_hx_current_url_same_host():
    """Path and query come from HX-Current-URL when the host matches."""
    url = "http://meter.example.lan:8001/training?tab=2"
    assert auth.next_from_hx_current_url(url, "meter.example.lan:8001") == "/training?tab=2"
    assert auth.next_from_hx_current_url(url, "METER.example.lan:8001") == "/training?tab=2"


@pytest.mark.parametrize(
    "url, host",
    [
        ("http://other.example/training", "meter.example.lan:8001"),
        (None, "meter.example.lan:8001"),
        ("", "meter.example.lan:8001"),
        ("http://meter.example.lan:8001/login?next=/", "meter.example.lan:8001"),
        ("http://meter.example.lan:8001/api/../x", None),
        ("/training", "meter.example.lan:8001"),
    ],
)
def test_next_from_hx_current_url_falls_back_to_root(url, host):
    """A different host, a missing header or an auth page gives /."""
    assert auth.next_from_hx_current_url(url, host) == "/"


# -- setup token -----------------------------------------------------------------------------------------------------


def test_setup_token_not_required_by_default(auth_path):
    """Without the flag there is no token and every candidate passes."""
    assert auth.setup_token_required() is False
    assert auth.get_setup_token() is None
    assert auth.check_setup_token(None) is True
    assert auth.check_setup_token("anything") is True


@pytest.mark.parametrize("flag", ["1", "true", "YES", "True", "yes"])
def test_setup_token_flag_values(auth_path, monkeypatch, flag):
    """1, true and yes (any case) switch the token on."""
    monkeypatch.setenv("WATERMETER_SETUP_TOKEN_REQUIRED", flag)
    assert auth.setup_token_required() is True


@pytest.mark.parametrize("flag", ["", "0", "false", "no", "off"])
def test_setup_token_other_values_leave_it_off(auth_path, monkeypatch, flag):
    """Anything else leaves the token off."""
    monkeypatch.setenv("WATERMETER_SETUP_TOKEN_REQUIRED", flag)
    assert auth.setup_token_required() is False


def test_setup_token_is_stable_hex_and_logged_once(auth_path, monkeypatch, caplog):
    """One 16-hex token per process, logged at WARNING when generated."""
    monkeypatch.setenv("WATERMETER_SETUP_TOKEN_REQUIRED", "1")
    with caplog.at_level(logging.DEBUG, logger="watermeter.auth"):
        first = auth.get_setup_token()
        second = auth.get_setup_token()
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{16}", first)
    logged = [r for r in caplog.records if first in r.getMessage()]
    assert len(logged) == 1
    assert logged[0].levelno == logging.WARNING


def test_setup_token_check(auth_path, monkeypatch):
    """The right token (stripped) passes; wrong, empty and missing candidates fail."""
    monkeypatch.setenv("WATERMETER_SETUP_TOKEN_REQUIRED", "1")
    token = auth.get_setup_token()
    assert auth.check_setup_token(token) is True
    assert auth.check_setup_token(f"  {token}\n") is True
    assert auth.check_setup_token("0" * 16 if token != "0" * 16 else "1" * 16) is False
    assert auth.check_setup_token("") is False
    assert auth.check_setup_token(None) is False


def test_setup_token_is_never_written_to_disk(auth_path, monkeypatch, tmp_path):
    """Memory only: generating the token creates no files."""
    monkeypatch.setenv("WATERMETER_SETUP_TOKEN_REQUIRED", "1")
    before = sorted(tmp_path.rglob("*"))
    auth.get_setup_token()
    assert sorted(tmp_path.rglob("*")) == before


# -- bootstrap_from_env ----------------------------------------------------------------------------------------------


def test_bootstrap_without_env_does_nothing(auth_path):
    """No seed variable means nothing happens."""
    assert auth.bootstrap_from_env() is False
    assert not auth_path.exists()


def test_bootstrap_seeds_from_plain_variable(auth_path, monkeypatch):
    """An 8+ character seed creates auth.json when none exists."""
    monkeypatch.setenv("WATERMETER_ADMIN_PASSWORD", "seed-password-1")
    assert auth.bootstrap_from_env() is True
    record = auth.get_store().record()
    assert auth.verify_password("seed-password-1", record.password_hash) is True


def test_bootstrap_file_variable_wins_and_strips_one_newline(auth_path, monkeypatch, tmp_path):
    """WATERMETER_ADMIN_PASSWORD_FILE is preferred; one trailing newline is removed."""
    secret = tmp_path / "secret.txt"
    secret.write_text("from-the-file\n")
    monkeypatch.setenv("WATERMETER_ADMIN_PASSWORD_FILE", str(secret))
    monkeypatch.setenv("WATERMETER_ADMIN_PASSWORD", "from-the-variable")
    assert auth.bootstrap_from_env() is True
    record = auth.get_store().record()
    assert auth.verify_password("from-the-file", record.password_hash) is True
    assert auth.verify_password("from-the-variable", record.password_hash) is False


def test_bootstrap_strips_crlf_but_only_one_line_ending(auth_path, monkeypatch, tmp_path):
    """A CRLF ending is removed; an extra blank line stays part of the password."""
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"windows-style\r\n")
    monkeypatch.setenv("WATERMETER_ADMIN_PASSWORD_FILE", str(secret))
    assert auth.bootstrap_from_env() is True
    assert auth.verify_password("windows-style", auth.get_store().record().password_hash) is True


def test_bootstrap_unreadable_file_variable_logs_and_returns_false(auth_path, monkeypatch, tmp_path, caplog):
    """A missing secret file is reported by variable name and nothing is created."""
    monkeypatch.setenv("WATERMETER_ADMIN_PASSWORD_FILE", str(tmp_path / "missing.txt"))
    monkeypatch.setenv("WATERMETER_ADMIN_PASSWORD", "from-the-variable")
    with caplog.at_level(logging.ERROR, logger="watermeter.auth"):
        assert auth.bootstrap_from_env() is False
    assert "WATERMETER_ADMIN_PASSWORD_FILE" in caplog.text
    assert not auth_path.exists()


def test_bootstrap_short_seed_writes_nothing_and_is_not_logged(auth_path, monkeypatch, caplog):
    """A 7-character seed is refused with an error that does not contain the value."""
    monkeypatch.setenv("WATERMETER_ADMIN_PASSWORD", "s3cr3t!")
    with caplog.at_level(logging.DEBUG, logger="watermeter.auth"):
        assert auth.bootstrap_from_env() is False
    assert not auth_path.exists()
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert "s3cr3t!" not in caplog.text


def test_bootstrap_ignores_seed_once_a_password_exists(auth_path, monkeypatch):
    """The env value never replaces a stored hash."""
    store = auth.get_store()
    assert store.create(auth.hash_password("original-password")) is True
    before = auth_path.read_bytes()
    monkeypatch.setenv("WATERMETER_ADMIN_PASSWORD", "different-password")
    assert auth.bootstrap_from_env() is False
    assert auth_path.read_bytes() == before


def test_bootstrap_leaves_unavailable_file_alone(auth_path, monkeypatch):
    """A damaged auth.json is not replaced by the seed."""
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text("garbage")
    monkeypatch.setenv("WATERMETER_ADMIN_PASSWORD", "seed-password-1")
    assert auth.bootstrap_from_env() is False
    assert auth_path.read_text() == "garbage"


def test_bootstrap_never_logs_the_seed_value(auth_path, monkeypatch, caplog):
    """A successful seed logs that it happened, not the password."""
    monkeypatch.setenv("WATERMETER_ADMIN_PASSWORD", "very-secret-seed")
    with caplog.at_level(logging.DEBUG):
        assert auth.bootstrap_from_env() is True
    assert "very-secret-seed" not in caplog.text
    assert "seeded" in caplog.text.lower()


def test_bootstrap_accepts_an_explicit_store(tmp_path, monkeypatch):
    """A store passed in is used instead of the process-wide one."""
    for name in ("WATERMETER_ADMIN_PASSWORD_FILE",):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WATERMETER_ADMIN_PASSWORD", "seed-password-1")
    store = auth.AuthStore(tmp_path / "other" / "auth.json")
    assert auth.bootstrap_from_env(store) is True
    assert (tmp_path / "other" / "auth.json").is_file()


# -- leaf module -----------------------------------------------------------------------------------------------------


def test_auth_module_imports_only_the_standard_library():
    """auth.py must stay importable by the CLI without fastapi, starlette or other watermeter modules."""
    source = Path(auth.__file__).read_text(encoding="utf-8")
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import in auth.py"
            modules.add((node.module or "").split(".")[0])
    assert modules <= STDLIB_ALLOWLIST, sorted(modules - STDLIB_ALLOWLIST)
