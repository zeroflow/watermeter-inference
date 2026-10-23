"""Tests for the setup, login, logout, account and password-change routes on a harness app with the real templates."""

import logging
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from watermeter import auth
from watermeter.ratelimit import FailureLimiter
from watermeter.routes import auth as routes_auth

PASSWORD = "correct horse battery"
STATIC_DIR = Path(__file__).resolve().parents[2] / "watermeter" / "static"
FORM = {"content-type": "application/x-www-form-urlencoded"}


class FakeClock:
    """Manual monotonic clock for the throttle."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(scope="module")
def password_hash():
    """One scrypt hash for the whole module (hashing costs about 200 ms)."""
    return auth.hash_password(PASSWORD)


@pytest.fixture
def clock():
    """Fake clock shared with the throttle."""
    return FakeClock()


@pytest.fixture
def auth_path(tmp_path, monkeypatch):
    """Point the store at a fresh auth.json location and reset process-wide switches."""
    path = tmp_path / "data" / "auth.json"
    monkeypatch.setenv("WATERMETER_AUTH_FILE", str(path))
    monkeypatch.delenv(auth.SETUP_TOKEN_ENV, raising=False)
    monkeypatch.setattr(auth, "_setup_token", None)
    return path


@pytest.fixture
def limiter(clock, monkeypatch):
    """Fresh throttle driven by the fake clock."""
    fresh = FailureLimiter(clock=clock)
    monkeypatch.setattr(routes_auth, "limiter", fresh)
    return fresh


@pytest.fixture
def client(auth_path, limiter):
    """Harness app: only the auth router plus the real static directory."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(routes_auth.router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return TestClient(app)


@pytest.fixture
def configured(client, password_hash):
    """Client of an instance whose admin password is already set (anonymous browser)."""
    assert auth.get_store().create(password_hash)
    return client


def post_form(client, path, data, **kwargs):
    """POST an urlencoded form without following redirects."""
    return client.post(path, data=data, follow_redirects=False, **kwargs)


def session_cookie_name() -> str:
    return auth.get_store().record().cookie_name


# -- Tracer: set the password, land logged in, log out, log back in ------------------------------------------------


def test_setup_page_is_a_card_without_the_nav(client):
    """GET /setup on a fresh instance renders the form with both password fields and no nav rail."""
    resp = client.get("/setup", follow_redirects=False)
    assert resp.status_code == 200
    assert 'name="password"' in resp.text
    assert 'name="confirm"' in resp.text
    assert "app-nav" not in resp.text
    assert 'action="/setup"' in resp.text


def test_setup_creates_auth_file_and_logs_in(client, auth_path):
    """A valid pair creates auth.json, sets the session cookie and sends the owner to the dashboard."""
    resp = post_form(client, "/setup", {"password": PASSWORD, "confirm": PASSWORD})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert auth_path.exists()
    assert re.search(r"wm_session_[0-9a-f]{8}=wm1\.", resp.headers["set-cookie"])


def test_login_page_has_recovery_link(configured):
    """An anonymous visitor of a configured instance sees the login card with the recovery hint."""
    resp = configured.get("/login", follow_redirects=False)
    assert resp.status_code == 200
    assert 'action="/login"' in resp.text
    assert auth.README_RECOVERY_URL in resp.text
    assert "Forgot it?" in resp.text
    assert "app-nav" not in resp.text


def test_wrong_password_is_refused(configured):
    """A wrong password re-renders the card with the generic message and status 401."""
    resp = post_form(configured, "/login", {"password": "not the password"})
    assert resp.status_code == 401
    assert "Wrong password" in resp.text
    assert "set-cookie" not in resp.headers


def test_login_redirects_to_validated_next(configured):
    """The right password logs in and goes to the requested page."""
    resp = post_form(configured, "/login", {"password": PASSWORD, "next": "/training"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/training"
    assert resp.headers["set-cookie"].startswith(session_cookie_name() + "=wm1.")


def test_logout_clears_cookie(configured):
    """Logout redirects to the login page and expires the session cookie."""
    resp = post_form(configured, "/logout", {})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert "Max-Age=0" in resp.headers["set-cookie"]
    assert resp.headers["set-cookie"].startswith(session_cookie_name() + "=;")


# -- Setup rules: password policy, body cap, token, upgrade notice, closed setup ----------------------------------


@pytest.mark.parametrize("length", [8, 1024])
def test_setup_accepts_boundary_lengths(client, auth_path, length):
    """Exactly 8 and exactly 1024 characters are valid passwords."""
    password = "a" * length
    resp = post_form(client, "/setup", {"password": password, "confirm": password})
    assert resp.status_code == 303
    assert auth_path.exists()


@pytest.mark.parametrize("length", [7, 1025])
def test_setup_rejects_out_of_range_lengths(client, auth_path, length):
    """7 and 1025 characters are refused with 400 and nothing is written."""
    password = "a" * length
    resp = post_form(client, "/setup", {"password": password, "confirm": password})
    assert resp.status_code == 400
    assert not auth_path.exists()


def test_setup_mismatch_by_one_character_is_refused(client, auth_path):
    """A confirm that differs by a single character gives 400 and the mismatch message."""
    resp = post_form(client, "/setup", {"password": PASSWORD, "confirm": PASSWORD[:-1] + "X"})
    assert resp.status_code == 400
    assert "Passwords do not match" in resp.text
    assert not auth_path.exists()


@pytest.mark.parametrize(
    "data",
    [
        {"password": "", "confirm": ""},
        {"password": PASSWORD, "confirm": ""},
        {"password": "", "confirm": PASSWORD},
        {},
    ],
    ids=["both-empty", "empty-confirm", "empty-password", "fields-missing"],
)
def test_setup_empty_or_missing_fields_are_refused(client, auth_path, data):
    """Empty values and a body without the fields give 400 and write no auth.json."""
    resp = post_form(client, "/setup", data)
    assert resp.status_code == 400
    assert not auth_path.exists()


@pytest.mark.parametrize("path", ["/setup", "/login"])
def test_oversized_form_is_rejected_with_content_length(client, auth_path, path):
    """A body above 16384 bytes gets 413 before parsing."""
    resp = client.post(path, content=b"password=" + b"a" * 16385, headers=FORM, follow_redirects=False)
    assert resp.status_code == 413
    assert not auth_path.exists()


@pytest.mark.parametrize("path", ["/setup", "/login"])
def test_oversized_form_is_rejected_without_content_length(client, auth_path, path):
    """The cap also holds for a streamed body that announces no length."""
    body = iter([b"password=" + b"a" * 16400])
    resp = client.post(path, content=body, headers=FORM, follow_redirects=False)
    assert resp.status_code == 413
    assert not auth_path.exists()


def test_form_at_the_cap_is_still_parsed(client):
    """A body of exactly 16384 bytes is not treated as too large."""
    resp = client.post("/setup", content=b"password=" + b"a" * (16384 - 9), headers=FORM, follow_redirects=False)
    assert resp.status_code == 400  # parsed, then refused as too long a password


def test_setup_closed_after_setup(client, auth_path):
    """Once the password exists, setup only redirects and never shows the form or touches the file."""
    post_form(client, "/setup", {"password": PASSWORD, "confirm": PASSWORD})
    before = auth_path.read_bytes()
    get = client.get("/setup", follow_redirects=False)
    assert get.status_code == 303 and get.headers["location"] == "/"
    post = post_form(client, "/setup", {"password": "another password", "confirm": "another password"})
    assert post.status_code == 303 and post.headers["location"] == "/"
    assert 'name="confirm"' not in get.text + post.text
    assert auth_path.read_bytes() == before


def test_setup_closed_for_anonymous_visitor(configured, auth_path):
    """An anonymous visitor of a configured instance is sent to the login page."""
    before = auth_path.read_bytes()
    get = configured.get("/setup", follow_redirects=False)
    post = post_form(configured, "/setup", {"password": "another password", "confirm": "another password"})
    assert get.status_code == post.status_code == 303
    assert get.headers["location"] == post.headers["location"] == "/login"
    assert 'name="confirm"' not in get.text + post.text
    assert auth_path.read_bytes() == before


def test_setup_lost_race_redirects_without_cookie(client, monkeypatch):
    """When another request created the file first, this one is sent to login and gets no session."""
    monkeypatch.setattr(auth.get_store(), "create", lambda password_hash: False)
    resp = post_form(client, "/setup", {"password": PASSWORD, "confirm": PASSWORD})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert "set-cookie" not in resp.headers


def test_setup_unavailable_store_is_503(client, auth_path):
    """A damaged auth.json is never overwritten and never shows the setup form."""
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text("not json")
    assert client.get("/setup", follow_redirects=False).status_code == 503
    assert post_form(client, "/setup", {"password": PASSWORD, "confirm": PASSWORD}).status_code == 503
    assert auth_path.read_text() == "not json"


# -- Upgrade notice -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("marker", ["state.json", "reference_raw.jpg"])
def test_upgrade_notice_shown_when_meter_data_exists(client, auth_path, marker):
    """An instance with readings or a reference image gets the upgrade notice with a README link."""
    auth_path.parent.mkdir(parents=True)
    (auth_path.parent / marker).write_bytes(b"x")
    resp = client.get("/setup")
    assert 'class="auth-notice"' in resp.text
    assert auth.README_AUTH_URL in resp.text
    assert "MQTT publishing keeps running" in resp.text


def test_upgrade_notice_absent_on_fresh_install(client):
    """A fresh install has no notice."""
    resp = client.get("/setup")
    assert "auth-notice" not in resp.text
    assert auth.README_AUTH_URL not in resp.text


# -- Setup token ----------------------------------------------------------------------------------------------------


@pytest.fixture
def token_required(monkeypatch, auth_path):
    """Turn the opt-in setup token on and return the token of this process."""
    monkeypatch.setenv(auth.SETUP_TOKEN_ENV, "1")
    return auth.get_setup_token()


def test_setup_token_field_only_when_required(client, token_required):
    """The token field and its hint are rendered only when the flag is set."""
    resp = client.get("/setup")
    assert 'name="token"' in resp.text
    assert "docker logs" in resp.text
    assert token_required not in resp.text


def test_setup_token_field_absent_without_flag(client):
    """No flag, no field."""
    assert 'name="token"' not in client.get("/setup").text


@pytest.mark.parametrize("token", [None, "0000000000000000"], ids=["missing", "wrong"])
def test_setup_wrong_token_is_forbidden_and_logged(client, auth_path, limiter, token_required, caplog, token):
    """A missing or wrong token gives 403, no file, one throttle failure and a log line that repeats the token."""
    caplog.set_level(logging.WARNING)
    data = {"password": PASSWORD, "confirm": PASSWORD}
    if token is not None:
        data["token"] = token
    resp = post_form(client, "/setup", data)
    assert resp.status_code == 403
    assert "Wrong or missing setup token" in resp.text
    assert not auth_path.exists()
    assert limiter.retry_after("setup:testclient") >= 1
    assert len(limiter) == 1
    messages = [
        record.getMessage() for record in caplog.records if "Setup attempt from testclient" in record.getMessage()
    ]
    assert len(messages) == 1 and token_required in messages[0]
    assert PASSWORD not in caplog.text


def test_setup_with_the_right_token_succeeds(client, auth_path, token_required):
    """The token printed in the log lets setup through."""
    resp = post_form(client, "/setup", {"password": PASSWORD, "confirm": PASSWORD, "token": token_required})
    assert resp.status_code == 303
    assert auth_path.exists()


def test_setup_token_failures_are_throttled(client, auth_path, limiter, token_required):
    """A second wrong token right after the first is answered 429 with Retry-After."""
    data = {"password": PASSWORD, "confirm": PASSWORD, "token": "0000000000000000"}
    assert post_form(client, "/setup", data).status_code == 403
    resp = post_form(client, "/setup", data)
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1
    assert "Too many attempts, try again in" in resp.text
    assert not auth_path.exists()


def test_setup_token_is_checked_before_validation(client, auth_path, token_required):
    """Without the token, even an invalid password gets 403 (nothing about the policy leaks to strangers)."""
    resp = post_form(client, "/setup", {"password": "short", "confirm": "short"})
    assert resp.status_code == 403


# -- Login: throttle, redirect safety, logging ----------------------------------------------------------------------


def test_throttled_login_gets_429_without_hashing(configured, monkeypatch, clock):
    """After a failure the next attempt is refused with Retry-After and no scrypt run; later it works again."""
    calls = []
    real_verify = auth.verify_password

    def spy(password, stored):
        calls.append(password)
        return real_verify(password, stored)

    monkeypatch.setattr(auth, "verify_password", spy)
    assert post_form(configured, "/login", {"password": "wrong password"}).status_code == 401
    assert len(calls) == 1

    resp = post_form(configured, "/login", {"password": PASSWORD})
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1
    assert "Too many attempts, try again in" in resp.text
    assert "set-cookie" not in resp.headers
    assert len(calls) == 1  # the throttled attempt was not hashed

    clock.advance(4000)
    ok = post_form(configured, "/login", {"password": PASSWORD})
    assert ok.status_code == 303
    assert len(calls) == 2


def test_successful_login_clears_the_failure_history(configured, limiter, clock):
    """A good login forgets earlier failures of the client."""
    post_form(configured, "/login", {"password": "wrong password"})
    clock.advance(4000)
    assert post_form(configured, "/login", {"password": PASSWORD}).status_code == 303
    assert len(limiter) == 0


def test_only_wrong_credentials_count_as_failures(client, configured, auth_path, limiter):
    """Page views, validation errors and oversized bodies never touch the throttle."""
    configured.get("/login")
    configured.get("/login?next=/training")
    configured.get("/setup", follow_redirects=False)
    post_form(configured, "/setup", {"password": "x", "confirm": "x"})
    configured.post("/login", content=b"password=" + b"a" * 16385, headers=FORM, follow_redirects=False)
    configured.post("/setup", content=b"password=" + b"a" * 16385, headers=FORM, follow_redirects=False)
    post_form(configured, "/login", {"password": PASSWORD})
    assert len(limiter) == 0


def test_expired_session_requests_do_not_throttle(configured, limiter):
    """A kiosk tab with a stale cookie hitting the login page over and over never counts as a failure."""
    configured.cookies.set(session_cookie_name(), "wm1.1.1." + "0" * 64)
    for _ in range(10):
        assert configured.get("/login", follow_redirects=False).status_code == 200
    assert len(limiter) == 0


def test_overlong_login_password_counts_as_wrong_without_hashing(configured, monkeypatch, limiter):
    """A 1025 character password is refused as wrong and never reaches scrypt."""

    def boom(password, stored):
        raise AssertionError("verify_password must not run")

    monkeypatch.setattr(auth, "verify_password", boom)
    resp = post_form(configured, "/login", {"password": "a" * 1025})
    assert resp.status_code == 401
    assert len(limiter) == 1


@pytest.mark.parametrize(
    "target", ["//evil.example", "https://evil.example/", "/\\evil.example", "javascript:alert(1)"]
)
def test_login_ignores_unsafe_next(configured, target):
    """Open-redirect attempts end up on the dashboard."""
    resp = post_form(configured, "/login", {"password": PASSWORD, "next": target})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_login_page_while_authenticated_redirects_to_safe_next(configured):
    """A logged-in browser opening /login is sent on to the validated next target."""
    post_form(configured, "/login", {"password": PASSWORD})
    ok = configured.get("/login?next=/training", follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/training"
    bad = configured.get("/login?next=//evil.example", follow_redirects=False)
    assert bad.status_code == 303 and bad.headers["location"] == "/"


def test_login_page_escapes_next(configured):
    """The next value is HTML-escaped in the hidden field."""
    resp = configured.get('/login?next=/a"><script>alert(1)</script>')
    assert "<script>alert(1)</script>" not in resp.text


def test_login_before_setup_redirects_to_setup(client):
    """GET and POST /login on an unconfigured instance go to /setup."""
    assert client.get("/login", follow_redirects=False).headers["location"] == "/setup"
    assert post_form(client, "/login", {"password": PASSWORD}).headers["location"] == "/setup"


def test_failed_login_is_logged_with_ip_but_not_the_password(configured, caplog):
    """The WARNING line names the client and never the attempted password."""
    caplog.set_level(logging.INFO)
    attempt = "attempted-secret-value"
    post_form(configured, "/login", {"password": attempt})
    assert "Failed login from testclient" in caplog.text
    assert attempt not in caplog.text


def test_auth_pages_are_not_cacheable(configured, client):
    """Setup and login pages carry Cache-Control: no-store."""
    assert configured.get("/login").headers["cache-control"] == "no-store"
    assert post_form(configured, "/login", {"password": "wrong password"}).headers["cache-control"] == "no-store"


def test_setup_page_is_not_cacheable(client):
    """The setup form is not cached either."""
    assert client.get("/setup").headers["cache-control"] == "no-store"
