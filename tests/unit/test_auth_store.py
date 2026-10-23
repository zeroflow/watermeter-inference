"""Tests for the admin credential store, scrypt hashing, session tokens and cookie builders."""

import json
import logging
import os
import re
import stat
import threading

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


# -- Fail-closed parsing, caching, races and ownership ---------------------------------------------------------------


def _valid_dict(record):
    return record.to_dict()


def _broken(record, **changes):
    data = _valid_dict(record)
    for key, value in changes.items():
        if value is KeyError:
            del data[key]
        else:
            data[key] = value
    return json.dumps(data)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda r: "", id="zero-byte"),
        pytest.param(lambda r: "not json {", id="invalid-json"),
        pytest.param(lambda r: "[]", id="json-list"),
        pytest.param(lambda r: '"text"', id="json-string"),
        pytest.param(lambda r: _broken(r, version=2), id="version-2"),
        pytest.param(lambda r: _broken(r, version=KeyError), id="no-version"),
        pytest.param(lambda r: _broken(r, password_hash=KeyError), id="no-password-hash"),
        pytest.param(lambda r: _broken(r, password_hash="plaintext"), id="bad-password-hash"),
        pytest.param(lambda r: _broken(r, password_hash=5), id="non-str-password-hash"),
        pytest.param(lambda r: _broken(r, session_secret=KeyError), id="no-secret"),
        pytest.param(lambda r: _broken(r, session_secret="ab" * 31), id="short-secret"),
        pytest.param(lambda r: _broken(r, session_secret="AB" * 32), id="upper-secret"),
        pytest.param(lambda r: _broken(r, instance_id=KeyError), id="no-instance-id"),
        pytest.param(lambda r: _broken(r, instance_id="abc"), id="short-instance-id"),
        pytest.param(lambda r: _broken(r, instance_id="ABCDEF01"), id="upper-instance-id"),
        pytest.param(lambda r: _broken(r, instance_id="abcdef012"), id="long-instance-id"),
        pytest.param(lambda r: _broken(r, epoch=KeyError), id="no-epoch"),
        pytest.param(lambda r: _broken(r, epoch=True), id="bool-epoch"),
        pytest.param(lambda r: _broken(r, epoch=0), id="zero-epoch"),
        pytest.param(lambda r: _broken(r, epoch="1"), id="str-epoch"),
        pytest.param(lambda r: _broken(r, created_at=KeyError), id="no-created-at"),
        pytest.param(lambda r: _broken(r, created_at=7), id="non-str-created-at"),
    ],
)
def test_invalid_auth_file_is_unavailable_and_not_overwritten(store, auth_path, record, build):
    """Any damaged auth.json is UNAVAILABLE (never UNCONFIGURED), has no record, and cannot be re-created."""
    content = build(record)
    auth_path.write_text(content)
    assert store.state() is auth.AuthState.UNAVAILABLE
    assert store.record() is None
    assert store.create("$scrypt$new") is False
    assert auth_path.read_text() == content


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a chmod 000 file")
def test_unreadable_auth_file_is_unavailable(store, auth_path, record):
    """An existing but unreadable file is UNAVAILABLE, and create refuses it."""
    auth_path.chmod(0)
    try:
        assert store.state() is auth.AuthState.UNAVAILABLE
        assert store.create("$scrypt$new") is False
    finally:
        auth_path.chmod(0o600)


def test_missing_instance_id_never_falls_back_to_a_default_cookie_name(store, auth_path, record):
    """No instance_id means UNAVAILABLE, so no default cookie name can ever exist."""
    auth_path.write_text(_broken(record, instance_id=KeyError))
    assert store.state() is auth.AuthState.UNAVAILABLE
    assert store.record() is None


def test_unavailable_state_is_logged_once_per_file_version(store, auth_path, record, caplog):
    """The error is logged when the damaged file is first seen, not on every request."""
    auth_path.write_text("garbage")
    with caplog.at_level(logging.ERROR, logger="watermeter.auth"):
        for _ in range(5):
            assert store.state() is auth.AuthState.UNAVAILABLE
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert str(auth_path) in errors[0].getMessage()


def test_valid_file_is_parsed_once_until_it_changes(store, auth_path, record, monkeypatch):
    """The cache key is (mtime_ns, size, inode); an untouched file is not parsed again."""
    store.state()
    calls = []
    real = auth.json.loads
    monkeypatch.setattr(auth.json, "loads", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(5):
        assert store.state() is auth.AuthState.CONFIGURED
        assert store.record() == record
    assert calls == []


def test_replacement_by_another_process_is_seen_without_restart(store, auth_path, record):
    """An os.replace done elsewhere (the CLI) shows up in the next record() call."""
    assert store.record().epoch == 1
    other = auth.AuthRecord(
        password_hash=record.password_hash,
        session_secret=record.session_secret,
        instance_id=record.instance_id,
        epoch=7,
        created_at=record.created_at,
    )
    tmp = auth_path.with_name("other.tmp")
    tmp.write_text(json.dumps(other.to_dict()))
    os.replace(tmp, auth_path)
    assert store.record().epoch == 7


def test_deleting_the_file_returns_to_unconfigured_with_new_identity(store, auth_path, record, password_hash):
    """Recovery by deleting auth.json rotates secret and instance id."""
    auth_path.unlink()
    assert store.state() is auth.AuthState.UNCONFIGURED
    assert store.create(password_hash) is True
    fresh = store.record()
    assert fresh.session_secret != record.session_secret
    assert fresh.instance_id != record.instance_id


def test_concurrent_create_has_exactly_one_winner(store, auth_path):
    """Two threads creating at the same moment: one True, and the file holds the winner's hash."""
    barrier = threading.Barrier(2)
    results = {}

    def worker(name):
        barrier.wait()
        results[name] = store.create(f"$scrypt$thread-{name}")

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [name for name, won in results.items() if won]
    assert len(winners) == 1
    assert store.record().password_hash == f"$scrypt$thread-{winners[0]}"
    assert [p.name for p in auth_path.parent.iterdir()] == ["auth.json"]


def _record_chown_calls(monkeypatch, auth_path):
    calls = []

    def fake_chown(path, uid, gid):
        calls.append((str(path), uid, gid, auth_path.exists()))

    monkeypatch.setattr(auth.os, "geteuid", lambda: 0)
    monkeypatch.setattr(auth.os, "chown", fake_chown)
    return calls


def test_create_chowns_temp_file_to_parent_owner_when_root(store, auth_path, password_hash, monkeypatch):
    """As root, the temp file takes the directory owner before it is linked into place."""
    calls = _record_chown_calls(monkeypatch, auth_path)
    assert store.create(password_hash) is True
    parent = os.stat(auth_path.parent)
    assert len(calls) == 1
    path, uid, gid, final_existed = calls[0]
    assert os.path.dirname(path) == str(auth_path.parent)
    assert path != str(auth_path)
    assert (uid, gid) == (parent.st_uid, parent.st_gid)
    assert final_existed is False


def test_replace_password_chowns_temp_file_to_parent_owner_when_root(store, auth_path, record, monkeypatch):
    """The same holds for a replacement, which would otherwise leave a root-owned file."""
    calls = _record_chown_calls(monkeypatch, auth_path)
    store.replace_password(auth.hash_password("second password"))
    parent = os.stat(auth_path.parent)
    assert len(calls) == 1
    path, uid, gid, _ = calls[0]
    assert os.path.dirname(path) == str(auth_path.parent)
    assert path != str(auth_path)
    assert (uid, gid) == (parent.st_uid, parent.st_gid)


def test_no_chown_when_not_root(store, auth_path, password_hash, monkeypatch):
    """Non-root writers never call chown."""
    calls = []
    monkeypatch.setattr(auth.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(auth.os, "chown", lambda *a: calls.append(a))
    store.create(password_hash)
    assert calls == []


def test_failed_write_leaves_no_temp_file(store, auth_path, record, monkeypatch):
    """If chown fails the temp file is removed and the original file survives."""
    before = auth_path.read_text()

    def boom(*args):
        raise OSError("chown denied")

    monkeypatch.setattr(auth.os, "geteuid", lambda: 0)
    monkeypatch.setattr(auth.os, "chown", boom)
    with pytest.raises(OSError):
        store.replace_password(auth.hash_password("second password"))
    assert auth_path.read_text() == before
    assert [p.name for p in auth_path.parent.iterdir()] == ["auth.json"]


@pytest.mark.parametrize(
    "stored",
    [
        "$scrypt$ln=30,r=8,p=3$c2FsdA$aGFzaA",
        "$scrypt$ln=15,r=8,p=100$c2FsdA$aGFzaA",
        "$scrypt$ln=15,r=999,p=3$c2FsdA$aGFzaA",
        "$scrypt$ln=18,r=8,p=1$c2FsdA$aGFzaA",
    ],
)
def test_verify_password_refuses_oversized_parameters_promptly(stored, monkeypatch):
    """A tampered hash cannot make verify_password allocate gigabytes: it never reaches scrypt."""
    monkeypatch.setattr(auth.hashlib, "scrypt", lambda *a, **k: pytest.fail("scrypt must not run"))
    assert auth.verify_password("whatever", stored) is False
