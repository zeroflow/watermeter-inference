"""The real app with the login gate switched on: setup, login, next round trip, docs disabled, health."""

from urllib.parse import quote

from tests.unit.conftest import TEST_ADMIN_PASSWORD
from watermeter import auth
from watermeter.auth_middleware import AuthMiddleware

ORIGIN = {"Origin": "http://testserver"}


def test_middleware_is_installed_on_the_real_app():
    """The gate is part of the app object itself, not of a test harness."""
    import watermeter.app as app_module

    assert any(m.cls is AuthMiddleware for m in app_module.app.user_middleware)


def test_fresh_install_redirects_pages_to_setup(anon_client):
    """An anonymous visit to / on an unconfigured instance goes to /setup."""
    resp = anon_client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/setup"


def test_fresh_install_api_answers_401_with_setup_required(anon_client):
    """API callers get JSON 401 and learn that setup is still open."""
    resp = anon_client.get("/api/status", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json()["setup_required"] is True


def test_health_is_open_with_unchanged_payload_when_unconfigured(anon_client):
    """/health needs no session on a fresh install."""
    resp = anon_client.get("/health", follow_redirects=False)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"status", "mqtt_connected", "last_update", "processing"}
    assert body["status"] == "ok"


def test_health_is_open_when_configured(anon_client_configured):
    """/health needs no session on a configured instance either."""
    resp = anon_client_configured.get("/health", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_setup_then_api_round_trip(anon_client):
    """Setting the password logs the owner in and opens the API."""
    resp = anon_client.post(
        "/setup",
        data={"password": TEST_ADMIN_PASSWORD, "confirm": TEST_ADMIN_PASSWORD},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert any(name.startswith(auth.COOKIE_PREFIX) for name in anon_client.cookies.keys())
    status = anon_client.get("/api/status", follow_redirects=False)
    assert status.status_code == 200


def test_anonymous_page_on_configured_install_keeps_path_and_query(anon_client_configured):
    """AUTH-07: the login redirect carries the original path and query, percent-encoded."""
    resp = anon_client_configured.get("/training?tab=x", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?next=" + quote("/training?tab=x", safe="")


def test_login_returns_to_the_requested_page(anon_client_configured):
    """AUTH-07: logging in with that next value lands on the original page."""
    resp = anon_client_configured.post(
        "/login",
        data={"password": TEST_ADMIN_PASSWORD, "next": "/training?tab=x"},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/training?tab=x"


def test_htmx_poll_gets_401_with_hx_redirect(anon_client_configured):
    """An expired session during an htmx poll navigates the browser to the login page."""
    resp = anon_client_configured.get(
        "/api/status/html",
        headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert resp.headers["hx-redirect"] == "/login?next=%2F"


def test_docs_pages_are_gone_for_a_logged_in_client(test_client):
    """The OpenAPI viewers and schema do not exist, so even the owner gets 404."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert test_client.get(path, follow_redirects=False).status_code == 404, path


def test_docs_pages_are_gated_for_anonymous_clients(anon_client_configured):
    """Anonymous callers are sent to the login page, not told the paths do not exist."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert anon_client_configured.get(path, follow_redirects=False).status_code == 303, path


def test_setup_is_closed_for_the_logged_in_owner(test_client):
    """After setup, GET and POST /setup send the owner home and never render the form."""
    page = test_client.get("/setup", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/"
    assert 'name="confirm"' not in page.text
    post = test_client.post(
        "/setup",
        data={"password": TEST_ADMIN_PASSWORD, "confirm": TEST_ADMIN_PASSWORD},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert post.status_code == 303
    assert post.headers["location"] == "/"
    assert 'name="confirm"' not in post.text


def test_setup_is_closed_for_anonymous_clients_after_setup(anon_client_configured):
    """After setup, an anonymous caller of /setup is sent to the login page."""
    resp = anon_client_configured.get("/setup", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")
