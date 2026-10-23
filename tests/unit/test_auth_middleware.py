"""Tests for the default-deny AuthMiddleware on a harness FastAPI app (the middleware is wired into the real app later)."""

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from watermeter import auth
from watermeter.auth_middleware import AuthMiddleware

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
    monkeypatch.delenv(auth.ALLOWED_ORIGINS_ENV, raising=False)
    return path


def build_app(static_dir) -> FastAPI:
    """Harness app with one route of every kind the gate has to tell apart."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    def index():
        return HTMLResponse("<html>home</html>")

    @app.get("/training")
    def training():
        return HTMLResponse("<html>training</html>")

    @app.get("/api/status")
    def status():
        return JSONResponse({"success": True})

    @app.post("/api/trigger")
    def trigger():
        return JSONResponse({"success": True})

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/login")
    def login_page():
        return HTMLResponse("login form")

    @app.post("/login")
    def login_post():
        return HTMLResponse("login handled")

    @app.get("/setup")
    def setup_page():
        return HTMLResponse("setup form")

    @app.post("/setup")
    def setup_post():
        return HTMLResponse("setup handled")

    @app.post("/logout")
    def logout():
        response = HTMLResponse("bye")
        record = auth.get_store().record()
        if record is not None:
            response.headers.append("set-cookie", auth.build_clear_cookie(record, secure=False))
        return response

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.add_middleware(AuthMiddleware)
    return app


@pytest.fixture
def static_dir(tmp_path):
    """Directory behind the harness /static mount."""
    directory = tmp_path / "static"
    directory.mkdir()
    (directory / "x.css").write_text("body {}")
    return directory


@pytest.fixture
def client(auth_path, static_dir):
    """Anonymous client against an unconfigured instance."""
    return TestClient(build_app(static_dir))


def make_configured(password_hash):
    """Create the admin record and return it."""
    assert auth.get_store().create(password_hash) is True
    return auth.get_store().record()


def log_in(client, record):
    """Give the client a valid session cookie for ``record``."""
    client.cookies.set(record.cookie_name, auth.issue_token(record))


@pytest.fixture
def configured_client(client, password_hash):
    """Anonymous client against a configured instance."""
    make_configured(password_hash)
    return client


@pytest.fixture
def logged_in(client, password_hash):
    """Client holding a valid session, plus its record."""
    record = make_configured(password_hash)
    log_in(client, record)
    return client, record


def get(client, path, **kwargs):
    """GET without following redirects."""
    return client.get(path, follow_redirects=False, **kwargs)


# -- Unconfigured instance ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/training", "/docs", "/openapi.json", "/does-not-exist", "/login", "/setup/"])
def test_unconfigured_pages_redirect_to_setup(client, path):
    """Every page except the allowlist sends the owner to /setup."""
    response = get(client, path)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_unconfigured_api_is_401_with_setup_required(client):
    """API callers learn that setup is pending."""
    response = get(client, "/api/status")
    assert response.status_code == 401
    assert response.json() == {"success": False, "message": "Authentication required", "setup_required": True}


@pytest.mark.parametrize("path", ["/setup", "/health", "/static/x.css"])
def test_unconfigured_allowlist_reaches_the_app(client, path):
    """Setup, health and static files stay reachable before a password exists."""
    assert get(client, path).status_code == 200


def test_unconfigured_htmx_gets_hx_redirect_to_setup(client):
    """htmx pollers are sent to /setup with an empty body."""
    response = get(client, "/api/status", headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/"})
    assert response.status_code == 401
    assert response.content == b""
    assert response.headers["hx-redirect"] == "/setup"


# -- Configured instance -----------------------------------------------------------------------------------------------


def test_configured_page_redirects_to_login_with_next(configured_client):
    """The requested path and query come back as an encoded next parameter."""
    response = get(configured_client, "/training?a=1")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Ftraining%3Fa%3D1"


def test_configured_htmx_gets_401_empty_body_and_hx_redirect_from_current_page(configured_client):
    """The redirect target is the page the user is on, not the polled fragment URL."""
    response = get(
        configured_client, "/api/status", headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/training"}
    )
    assert response.status_code == 401
    assert response.content == b""
    assert response.headers["hx-redirect"] == "/login?next=%2Ftraining"
    assert "api" not in response.headers["hx-redirect"]


def test_configured_htmx_with_foreign_current_url_falls_back_to_root(configured_client):
    """An HX-Current-URL on another host never becomes the next target."""
    response = get(
        configured_client, "/api/status", headers={"HX-Request": "true", "HX-Current-URL": "http://evil.example/x"}
    )
    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login?next=%2F"


def test_configured_api_is_json_401(configured_client):
    """API callers get the app's success/message error shape."""
    response = get(configured_client, "/api/status")
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["setup_required"] is False


@pytest.mark.parametrize("path", ["/docs", "/openapi.json", "/does-not-exist", "/setup", "/"])
def test_configured_unknown_and_docs_paths_are_denied_before_routing(configured_client, path):
    """Docs, unknown paths and /setup give anonymous callers nothing to probe."""
    assert get(configured_client, path).status_code == 303


def test_configured_allowlist_reaches_the_app(configured_client):
    """Health, login page and static files stay open."""
    for path in ("/health", "/login", "/static/x.css"):
        assert get(configured_client, path).status_code == 200


def test_head_and_options_are_gated_like_get(configured_client):
    """The method never widens the allowlist."""
    assert configured_client.head("/", follow_redirects=False).status_code == 303
    assert configured_client.options("/api/status").status_code == 401


def test_valid_session_reaches_the_app(logged_in):
    """A cookie of the current epoch passes the gate."""
    client, _ = logged_in
    assert get(client, "/training").status_code == 200
    assert get(client, "/api/status").status_code == 200


def test_password_change_invalidates_the_old_cookie(logged_in, password_hash):
    """A new epoch makes every older cookie anonymous."""
    client, _ = logged_in
    assert get(client, "/").status_code == 200
    auth.get_store().replace_password(password_hash)
    assert get(client, "/").status_code == 303


# -- Store failures and websockets -------------------------------------------------------------------------------------


def test_empty_auth_file_fails_closed(auth_path, static_dir):
    """A damaged auth.json gives 503 everywhere except /health."""
    auth_path.parent.mkdir(parents=True)
    auth_path.write_bytes(b"")
    client = TestClient(build_app(static_dir))
    assert get(client, "/").status_code == 503
    assert get(client, "/api/status").status_code == 503
    assert get(client, "/api/status").json()["success"] is False
    assert get(client, "/setup").status_code == 503
    assert get(client, "/health").status_code == 200


def test_store_exception_fails_closed(client, monkeypatch):
    """Anything raised by the store is treated like an unavailable store."""

    def boom(self):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(auth.AuthStore, "state", boom)
    assert get(client, "/").status_code == 503
    assert get(client, "/health").status_code == 200


def test_websocket_is_closed_with_1008(configured_client):
    """The app has no websockets, so every connection is refused."""
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with configured_client.websocket_connect("/ws"):
            pass
    assert excinfo.value.code == 1008
