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


# -- CSRF ----------------------------------------------------------------------------------------------------------------

CSRF = {auth.CSRF_HEADER: auth.CSRF_HEADER_VALUE}


def post(client, path="/api/trigger", **kwargs):
    """POST without following redirects."""
    return client.post(path, follow_redirects=False, **kwargs)


def test_csrf_header_without_origin_passes(logged_in):
    """A non-browser client with the header and no Origin is let through."""
    client, _ = logged_in
    assert post(client, headers=CSRF).status_code == 200


def test_csrf_missing_header_is_refused_with_json(logged_in):
    """A cookie-authenticated API write needs X-Requested-With."""
    client, _ = logged_in
    response = post(client)
    assert response.status_code == 403
    assert response.json() == {"success": False, "message": "Cross-site request refused"}


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_csrf_applies_to_every_unsafe_method(logged_in, method):
    """PUT, PATCH and DELETE are checked like POST."""
    client, _ = logged_in
    assert client.request(method, "/api/trigger", follow_redirects=False).status_code == 403
    assert client.request(method, "/api/trigger", follow_redirects=False, headers=CSRF).status_code not in (401, 403)


@pytest.mark.parametrize(
    "origin", ["http://evil.example", "null", "http://testserver.evil.example", "http://testserver:8001"]
)
def test_csrf_foreign_or_null_origin_is_refused(logged_in, origin):
    """The Origin host:port must equal the Host header."""
    client, _ = logged_in
    assert post(client, headers={**CSRF, "Origin": origin}).status_code == 403


def test_csrf_same_origin_header_passes(logged_in):
    """A browser write from the page itself passes."""
    client, _ = logged_in
    assert post(client, headers={**CSRF, "Origin": "http://testserver"}).status_code == 200


def test_csrf_origin_comparison_ignores_the_scheme(logged_in):
    """A TLS proxy without X-Forwarded-Proto still works because only host:port is compared."""
    client, _ = logged_in
    assert post(client, headers={**CSRF, "Origin": "https://testserver"}).status_code == 200


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_csrf_sec_fetch_site_outside_the_origin_is_refused(logged_in, site):
    """Other ports on the same host are same-site, so same-site is refused too."""
    client, _ = logged_in
    assert post(client, headers={**CSRF, "Sec-Fetch-Site": site}).status_code == 403


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_csrf_sec_fetch_site_same_origin_or_none_passes(logged_in, site):
    """Direct navigation and same-origin fetches pass."""
    client, _ = logged_in
    assert post(client, headers={**CSRF, "Sec-Fetch-Site": site}).status_code == 200


def test_csrf_sec_fetch_site_wins_over_a_matching_origin(logged_in):
    """A cross-site Sec-Fetch-Site is refused even when Origin looks right."""
    client, _ = logged_in
    headers = {**CSRF, "Sec-Fetch-Site": "cross-site", "Origin": "http://testserver"}
    assert post(client, headers=headers).status_code == 403


def test_csrf_still_needs_the_header_when_the_browser_says_same_origin(logged_in):
    """Sec-Fetch-Site alone does not replace X-Requested-With on cookie-authenticated API writes."""
    client, _ = logged_in
    assert post(client, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 403


def test_csrf_forwarded_host_is_never_trusted(logged_in):
    """Only the real Host header counts, so a spoofed X-Forwarded-Host cannot legitimise a foreign Origin."""
    client, _ = logged_in
    headers = {**CSRF, "Origin": "http://evil.example", "X-Forwarded-Host": "evil.example"}
    assert post(client, headers=headers).status_code == 403


def test_csrf_allowed_origins_env_accepts_a_proxy_origin(logged_in, monkeypatch):
    """Origins listed in WATERMETER_ALLOWED_ORIGINS pass, with whitespace, a trailing slash and any case tolerated."""
    client, _ = logged_in
    monkeypatch.setenv(auth.ALLOWED_ORIGINS_ENV, "https://wm.example.lan, HTTPS://Other.example.lan/")
    assert post(client, headers={**CSRF, "Origin": "https://wm.example.lan"}).status_code == 200
    assert post(client, headers={**CSRF, "Origin": "https://other.example.lan"}).status_code == 200
    assert post(client, headers={**CSRF, "Origin": "https://third.example.lan"}).status_code == 403


@pytest.mark.parametrize("path", ["/login", "/logout"])
def test_csrf_form_posts_refuse_a_foreign_origin(configured_client, path):
    """Login and logout forms get the Origin check."""
    assert post(configured_client, path, headers={"Origin": "http://evil.example"}).status_code == 403


def test_csrf_login_form_needs_neither_header_nor_origin(configured_client):
    """The login form is a plain post: no X-Requested-With and no Origin is fine."""
    response = post(configured_client, "/login", data={"password": "x"})
    assert response.status_code == 200
    assert response.text == "login handled"


def test_csrf_logout_form_with_a_session_needs_only_the_origin_check(logged_in):
    """/logout is not under /api/, so a signed-in browser form post passes without the header."""
    client, _ = logged_in
    assert post(client, "/logout", headers={"Origin": "http://testserver"}).status_code == 200


def test_csrf_runs_before_the_setup_allowlist(client):
    """A cross-site POST to /setup is refused even though the instance is unconfigured."""
    assert post(client, "/setup", headers={"Origin": "http://evil.example"}).status_code == 403
    assert post(client, "/setup", headers={"Origin": "http://testserver"}).status_code == 200


def test_csrf_anonymous_api_write_without_origin_gets_401_from_the_gate(configured_client):
    """Without a session the gate answers; the header is only demanded of cookie-authenticated requests."""
    assert post(configured_client).status_code == 401


def test_csrf_anonymous_cross_site_api_write_is_refused_before_the_gate(configured_client):
    """Forged cross-site writes get 403 regardless of login state."""
    assert post(configured_client, headers={"Origin": "http://evil.example"}).status_code == 403


# -- Sliding session refresh ---------------------------------------------------------------------------------------------

DAY = 86400


def session_cookies(response, record):
    """Set-Cookie header values that set the session cookie."""
    return [value for value in response.headers.get_list("set-cookie") if value.startswith(record.cookie_name + "=")]


def token_of(set_cookie, record):
    """Token carried by a Set-Cookie header value."""
    return set_cookie.split(";", 1)[0].split("=", 1)[1]


def old_cookie_client(client, record, age_days):
    """Client whose session cookie was issued ``age_days`` ago."""
    import time

    client.cookies.set(record.cookie_name, auth.issue_token(record, now=time.time() - age_days * DAY))
    return client


def test_refresh_after_fifteen_days_sets_a_fresh_cookie(client, password_hash):
    """A token older than half the lifetime is re-issued with the login attributes."""
    record = make_configured(password_hash)
    old_cookie_client(client, record, 16)
    response = get(client, "/")
    assert response.status_code == 200
    cookies = session_cookies(response, record)
    assert len(cookies) == 1
    assert "Max-Age=2592000" in cookies[0]
    assert "HttpOnly" in cookies[0]
    assert "SameSite=Lax" in cookies[0]
    assert "Secure" not in cookies[0]
    assert auth.verify_token(record, token_of(cookies[0], record)) is not None


def test_refresh_adds_secure_over_https(static_dir, auth_path, password_hash):
    """The Secure attribute follows the request scheme."""
    secure_client = TestClient(build_app(static_dir), base_url="https://testserver")
    record = make_configured(password_hash)
    old_cookie_client(secure_client, record, 16)
    cookies = session_cookies(get(secure_client, "/"), record)
    assert len(cookies) == 1
    assert "Secure" in cookies[0]


def test_no_refresh_for_a_young_token(client, password_hash):
    """A one-day-old token is left alone."""
    record = make_configured(password_hash)
    old_cookie_client(client, record, 1)
    response = get(client, "/")
    assert response.status_code == 200
    assert session_cookies(response, record) == []


def test_refresh_does_not_undo_logout(client, password_hash):
    """A response that already sets the cookie (logout) is not given a second, stale refresh cookie."""
    record = make_configured(password_hash)
    old_cookie_client(client, record, 16)
    response = post(client, "/logout", headers={"Origin": "http://testserver"})
    cookies = session_cookies(response, record)
    assert len(cookies) == 1
    assert "Max-Age=0" in cookies[0]


def test_two_requests_with_the_same_old_token_both_get_a_working_refresh(client, password_hash):
    """Stateless tokens: concurrent refreshes cannot invalidate each other."""
    import time

    record = make_configured(password_hash)
    old = auth.issue_token(record, now=time.time() - 16 * DAY)
    fresh = []
    for _ in range(2):
        client.cookies.clear()
        client.cookies.set(record.cookie_name, old)
        cookies = session_cookies(get(client, "/"), record)
        assert len(cookies) == 1
        fresh.append(token_of(cookies[0], record))
    assert all(auth.verify_token(record, token) is not None for token in fresh)


def test_no_refresh_for_an_anonymous_request(configured_client, password_hash):
    """Only a valid session earns a cookie."""
    record = auth.get_store().record()
    assert session_cookies(get(configured_client, "/login"), record) == []


# -- Edge cases --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/healthz", "/health/x", "/Health", "/login/x", "/logoutx", "/staticx", "/static", "/Login"]
)
def test_near_miss_paths_are_protected(configured_client, path):
    """Allowlist matching is exact and case-sensitive."""
    assert get(configured_client, path).status_code == 303


def anonymous_with_cookie(client, cookie):
    """GET / with a raw Cookie header."""
    client.cookies.clear()
    return get(client, "/", headers={"Cookie": cookie})


def test_empty_cookie_value_is_anonymous(logged_in):
    """An empty session cookie is not a session."""
    client, record = logged_in
    assert anonymous_with_cookie(client, f"{record.cookie_name}=").status_code == 303


def test_other_instance_cookie_is_ignored(logged_in):
    """A cookie named for another instance never authenticates, even with a valid token."""
    client, record = logged_in
    token = auth.issue_token(record)
    assert anonymous_with_cookie(client, f"wm_session_ffffffff={token}").status_code == 303


def test_cookie_name_is_case_sensitive(logged_in):
    """The right name in the wrong case is a different cookie."""
    client, record = logged_in
    token = auth.issue_token(record)
    assert anonymous_with_cookie(client, f"{record.cookie_name.upper()}={token}").status_code == 303


@pytest.mark.parametrize("token", ["wm1.x.1.ab", "wm1.1.1", "wm1.1.1.zz", "wm1..1.", "garbage", "wm1.1.1.2.3.4"])
def test_malformed_tokens_are_anonymous_not_errors(logged_in, token):
    """Wrong part counts, non-numeric fields and bad signatures are plain anonymous requests."""
    client, record = logged_in
    assert anonymous_with_cookie(client, f"{record.cookie_name}={token}").status_code == 303


def test_expired_session_polls_are_all_401_with_hx_redirect(configured_client):
    """Simultaneous expiring pollers each get the empty 401 and none of them is counted anywhere."""
    headers = {"HX-Request": "true", "HX-Current-URL": "http://testserver/"}
    for _ in range(25):
        response = get(configured_client, "/api/status", headers=headers)
        assert response.status_code == 401
        assert response.content == b""
        assert response.headers["hx-redirect"] == "/login?next=%2F"


def test_middleware_module_is_a_leaf():
    """The gate imports neither the throttle nor the service facade."""
    import ast
    from pathlib import Path

    from watermeter import auth_middleware

    tree = ast.parse(Path(auth_middleware.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported.update(f"{module}.{alias.name}".strip(".") for alias in node.names)
    assert not [name for name in imported if "ratelimit" in name or "watermeter_service" in name]
