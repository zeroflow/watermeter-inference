"""Tests for the admin credential store, scrypt hashing, session tokens and cookie builders."""

import json
import os
import re
import stat

import pytest

from watermeter import auth

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def password_hash():
    """One scrypt hash for the whole module (hashing costs about 200 ms)."""
    return auth.hash_password(PASSWORD)


@pytest.fixture
def auth_path(tmp_path, monkeypatch):
    """Point the store at a fresh auth.json location."""
    path = tmp_path / "data" / "auth.json"
    monkeypatch.setenv("WATERMETER_AUTH_FILE", str(path))
    return path


@pytest.fixture
def store(auth_path):
    """Store that follows WATERMETER_AUTH_FILE."""
    return auth.AuthStore()


@pytest.fixture
def record(store, password_hash):
    """A created record."""
    assert store.create(password_hash) is True
    return store.record()


def test_tracer_password_to_session_token(store, auth_path, password_hash):
    """Hash, store, verify, issue a token and survive a password replacement only with a new token."""
    assert store.state() is auth.AuthState.UNCONFIGURED
    assert store.record() is None

    assert store.create(password_hash) is True
    assert store.state() is auth.AuthState.CONFIGURED
    assert stat.S_IMODE(os.stat(auth_path).st_mode) == 0o600

    data = json.loads(auth_path.read_text())
    assert set(data) == {"version", "password_hash", "session_secret", "instance_id", "epoch", "created_at"}
    assert data["version"] == 1
    assert data["password_hash"].startswith("$scrypt$ln=15,r=8,p=3$")
    assert re.fullmatch(r"[0-9a-f]{64}", data["session_secret"])
    assert re.fullmatch(r"[0-9a-f]{8}", data["instance_id"])
    assert data["epoch"] == 1
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", data["created_at"])

    record = store.record()
    assert auth.verify_password(PASSWORD, record.password_hash) is True
    assert auth.verify_password(PASSWORD[:-1] + "x", record.password_hash) is False

    token = auth.issue_token(record)
    assert auth.verify_token(record, token) is not None

    replaced = store.replace_password(auth.hash_password("another password"))
    assert replaced.epoch == 2
    assert replaced.session_secret == record.session_secret
    assert replaced.instance_id == record.instance_id
    assert auth.verify_token(replaced, token) is None
    assert auth.verify_token(replaced, auth.issue_token(replaced)) is not None


def test_auth_code_does_not_touch_config_yaml():
    """The module never mentions config.yaml (the hash must not show up in the config editor)."""
    source = open(auth.__file__, encoding="utf-8").read()
    assert "config.yaml" not in source


def test_create_refuses_existing_file(store, password_hash):
    """A second create returns False and keeps the first hash."""
    assert store.create(password_hash) is True
    first = store.record()
    assert store.create("$scrypt$other") is False
    assert store.record() == first


def test_create_makes_parent_directory(store, auth_path, password_hash):
    """The data directory is created when missing."""
    assert not auth_path.parent.exists()
    assert store.create(password_hash) is True
    assert auth_path.is_file()


def test_create_leaves_no_temp_files(store, auth_path, password_hash):
    """Temp files are removed after a create and after a refused create."""
    store.create(password_hash)
    store.create(password_hash)
    assert [p.name for p in auth_path.parent.iterdir()] == ["auth.json"]


def test_replace_password_without_record_raises(store):
    """Replacing a password that was never set is an error."""
    with pytest.raises(auth.AuthStoreError):
        store.replace_password("$scrypt$x")


def test_replace_password_keeps_mode_and_leaves_no_temp_files(store, auth_path, record):
    """A replacement is still 0600 and leaves a single file behind."""
    store.replace_password(auth.hash_password("second password"))
    assert stat.S_IMODE(os.stat(auth_path).st_mode) == 0o600
    assert [p.name for p in auth_path.parent.iterdir()] == ["auth.json"]


def test_auth_file_path_is_resolved_per_call(tmp_path, monkeypatch):
    """WATERMETER_AUTH_FILE is read at call time, not import time."""
    monkeypatch.setenv("WATERMETER_AUTH_FILE", str(tmp_path / "a.json"))
    assert auth.auth_file_path() == tmp_path / "a.json"
    monkeypatch.setenv("WATERMETER_AUTH_FILE", str(tmp_path / "b.json"))
    assert auth.auth_file_path() == tmp_path / "b.json"
    monkeypatch.delenv("WATERMETER_AUTH_FILE")
    assert str(auth.auth_file_path()) == "/data/auth.json"


def test_cookie_name_uses_instance_id(record):
    """Cookie name is wm_session_ plus the 8 hex instance id."""
    assert record.cookie_name == "wm_session_" + record.instance_id


def test_hash_is_salted_and_phc_shaped(password_hash):
    """Two hashes of one password differ; the PHC string has five $-separated fields."""
    other = auth.hash_password(PASSWORD)
    assert other != password_hash
    assert password_hash.split("$")[:3] == ["", "scrypt", "ln=15,r=8,p=3"]
    assert auth.verify_password(PASSWORD, other) is True


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "plain text",
        "$scrypt$",
        "$scrypt$ln=15,r=8,p=3$onlyone",
        "$bcrypt$ln=15,r=8,p=3$c2FsdA$aGFzaA",
        "$scrypt$ln=x,r=8,p=3$c2FsdA$aGFzaA",
        "$scrypt$ln=15,r=8,p=3$!!!$aGFzaA",
        "$scrypt$ln=0,r=8,p=3$c2FsdA$aGFzaA",
        "$scrypt$ln=15,r=0,p=3$c2FsdA$aGFzaA",
        "$scrypt$ln=15,r=8,p=0$c2FsdA$aGFzaA",
        "$scrypt$ln=15,r=8,p=3$c2FsdA$" + "A" * 600,
    ],
)
def test_verify_password_returns_false_for_malformed_hash(stored):
    """A broken stored hash never raises and never verifies."""
    assert auth.verify_password(PASSWORD, stored) is False


def test_hashing_uses_explicit_maxmem(monkeypatch):
    """hashlib.scrypt always receives an explicit maxmem sized to its parameters."""
    seen = {}
    real = auth.hashlib.scrypt

    def spy(password, **kwargs):
        seen.update(kwargs)
        return real(password, **kwargs)

    monkeypatch.setattr(auth.hashlib, "scrypt", spy)
    auth.hash_password("spy password")
    assert seen["n"] == 2**15 and seen["r"] == 8 and seen["p"] == 3 and seen["dklen"] == 32
    assert seen["maxmem"] == 2 * 128 * 8 * (2**15 + 3 + 2)


def test_token_valid_at_exactly_thirty_days_and_expired_one_second_later(record):
    """Tokens last 2592000 seconds."""
    issued = 1_700_000_000
    token = auth.issue_token(record, now=issued)
    assert auth.verify_token(record, token, now=issued) == issued
    assert auth.verify_token(record, token, now=issued + 2592000) == issued
    assert auth.verify_token(record, token, now=issued + 2592001) is None


def test_token_issued_in_the_future_is_rejected_beyond_skew(record):
    """Up to 300 seconds of clock skew is tolerated, more is not."""
    now = 1_700_000_000
    assert auth.verify_token(record, auth.issue_token(record, now=now + 300), now=now) == now + 300
    assert auth.verify_token(record, auth.issue_token(record, now=now + 301), now=now) is None


def test_tampered_tokens_are_rejected(record):
    """Any change to the token text, a wrong prefix or garbage gives None."""
    now = 1_700_000_000
    token = auth.issue_token(record, now=now)
    prefix, issued, epoch, signature = token.split(".")

    changed_issued = ".".join([prefix, str(int(issued) + 1), epoch, signature])
    flipped = "0" if signature[0] != "0" else "1"
    changed_signature = ".".join([prefix, issued, epoch, flipped + signature[1:]])
    wrong_prefix = ".".join(["wm2", issued, epoch, signature])
    leading_zero = ".".join([prefix, "0" + issued, epoch, signature])
    for bad in (
        changed_issued,
        changed_signature,
        wrong_prefix,
        leading_zero,
        token + "0",
        token[:-1],
        "",
        "wm1",
        "a.b.c.d",
    ):
        assert auth.verify_token(record, bad, now=now) is None, bad
    assert auth.verify_token(record, None, now=now) is None


def test_token_from_another_instance_is_rejected(store, record, auth_path, password_hash):
    """A token signed with a different secret does not verify."""
    now = 1_700_000_000
    token = auth.issue_token(record, now=now)
    auth_path.unlink()
    store.create(password_hash)
    other = store.record()
    assert other.session_secret != record.session_secret
    assert auth.verify_token(other, token, now=now) is None


def test_session_issued_at_reads_the_instance_cookie(record):
    """Only the cookie named for this instance counts."""
    now = 1_700_000_000
    token = auth.issue_token(record, now=now)
    assert auth.session_issued_at(record, {record.cookie_name: token}, now=now) == now
    assert auth.session_issued_at(record, {"wm_session_00000000": token}, now=now) is None
    assert auth.session_issued_at(record, {}, now=now) is None
    assert auth.session_issued_at(None, {record.cookie_name: token}, now=now) is None


def test_session_cookie_attributes(record):
    """Persistent, HttpOnly, Lax, and Secure only when asked."""
    plain = auth.build_session_cookie(record, "tok", secure=False)
    assert plain == f"{record.cookie_name}=tok; Max-Age=2592000; Path=/; HttpOnly; SameSite=Lax"
    assert auth.build_session_cookie(record, "tok", secure=True) == plain + "; Secure"


def test_clear_cookie_attributes(record):
    """Clearing sets an empty value and Max-Age=0 with the same attributes."""
    plain = auth.build_clear_cookie(record, secure=False)
    assert plain == f"{record.cookie_name}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"
    assert auth.build_clear_cookie(record, secure=True) == plain + "; Secure"
