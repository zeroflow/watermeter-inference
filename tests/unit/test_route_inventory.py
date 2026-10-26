"""Default-deny proof: every registered route refuses an anonymous caller, including routes added later."""

from starlette.routing import Mount

CONFIGURED_ALLOWED = {("GET", "/health"), ("GET", "/login"), ("POST", "/login"), ("POST", "/logout")}
UNCONFIGURED_ALLOWED = {("GET", "/health"), ("GET", "/setup"), ("POST", "/setup")}


def _route_pairs(app):
    """(method, path template) for every non-mount route, HEAD and OPTIONS excluded."""
    pairs = []

    def walk(routes):
        for route in routes:
            if isinstance(route, Mount):
                continue
            # Newer FastAPI keeps included routers as one wrapper entry instead of copying their routes.
            # Our routers are included without a prefix, so the original paths are already complete.
            included = getattr(route, "original_router", None)
            if included is not None:
                walk(included.routes)
                continue
            for method in sorted(getattr(route, "methods", None) or ()):
                if method in ("HEAD", "OPTIONS"):
                    continue
                pairs.append((method, route.path))

    walk(app.routes)
    return pairs


def _concrete(path):
    """Replace every {param} (including {param:path}) with a literal."""
    out, depth = [], 0
    for char in path:
        if char == "{":
            depth += 1
            if depth == 1:
                out.append("x")
        elif char == "}":
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


def _request(client, method, path):
    return client.request(method, _concrete(path), follow_redirects=False)


def test_configured_instance_denies_every_non_allowlisted_route(anon_client_configured):
    """Anonymous callers get 303 (pages) or 401 (API) on every route that is not explicitly public."""
    checked = 0
    for method, path in _route_pairs(anon_client_configured.app):
        if (method, path) in CONFIGURED_ALLOWED:
            continue
        resp = _request(anon_client_configured, method, path)
        assert resp.status_code in (303, 401), f"{method} {path} answered {resp.status_code} to an anonymous caller"
        checked += 1
    assert checked >= 85, f"only {checked} route/method pairs were checked"


def test_unknown_and_documentation_paths_are_denied_when_configured(anon_client_configured):
    """Paths that do not exist are denied before routing, and static files stay public."""
    for path in ("/docs", "/redoc", "/openapi.json", "/does-not-exist"):
        resp = anon_client_configured.get(path, follow_redirects=False)
        assert resp.status_code in (303, 401), path
    assert anon_client_configured.get("/static/style.css", follow_redirects=False).status_code == 200


def test_unconfigured_instance_sends_everything_to_setup(anon_client):
    """Before setup, pages redirect to /setup and API calls get 401 with setup_required."""
    checked = 0
    for method, path in _route_pairs(anon_client.app):
        if (method, path) in UNCONFIGURED_ALLOWED:
            continue
        resp = _request(anon_client, method, path)
        if path.startswith("/api/"):
            assert resp.status_code == 401, f"{method} {path} answered {resp.status_code}"
            assert resp.json()["setup_required"] is True, f"{method} {path}"
        else:
            assert resp.status_code == 303, f"{method} {path} answered {resp.status_code}"
            assert resp.headers["location"] == "/setup", f"{method} {path}"
        checked += 1
    assert checked >= 85, f"only {checked} route/method pairs were checked"


def test_route_added_after_import_is_denied_the_same_way(anon_client_configured):
    """The gate wraps the whole app, so a router included later needs no per-route dependency."""
    app = anon_client_configured.app

    def late():
        return {"ok": True}

    before = len(app.router.routes)
    app.add_api_route("/api/late-test-route", late, methods=["GET"])
    try:
        resp = anon_client_configured.get("/api/late-test-route", follow_redirects=False)
        assert resp.status_code == 401
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/api/late-test-route"]
    assert len(app.router.routes) == before


def test_logged_in_client_reaches_a_protected_route(test_client):
    """Sanity check that the walk above is not passing only because everything is refused."""
    assert test_client.get("/api/status", follow_redirects=False).status_code == 200
