"""Tests for the setup, login, logout, account and password-change routes on a harness app with the real templates."""

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
