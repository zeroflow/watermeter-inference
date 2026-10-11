# Phase 1: Auth Core and Test/Script Plumbing - Pattern Map

**Mapped:** 2026-10-11
**Files analyzed:** 27 new/modified
**Analogs found:** 23 / 27 (4 have no in-repo analog, use RESEARCH.md code examples)

All analog paths below were verified as git-tracked (`git ls-files`) or are in directories that hold only tracked source (`watermeter/`, `tests/`, scripts at repo root, `.github/`). None is a gitignored mirror.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `watermeter/auth.py` (NEW) | service/store (leaf) | file-I/O + transform | `watermeter/persistence.py` (`StateStore`) | role-match (atomic JSON idiom) |
| `watermeter/auth_middleware.py` (NEW) | middleware (pure ASGI) | request-response | none in repo | no analog (RESEARCH Code Examples 3, 4) |
| `watermeter/ratelimit.py` (NEW) | utility (leaf) | in-memory state | `watermeter/rate_tracker.py` (leaf, injectable state), RESEARCH Example 5 | partial |
| `watermeter/routes/auth.py` (NEW) | route | request-response (forms + JSON) | `watermeter/routes/pages.py` + `watermeter/routes/service.py` | role-match |
| `watermeter/routes/health.py` (NEW) | route | request-response | `watermeter/routes/service.py:224-239` (moved verbatim) | exact |
| `watermeter/routes/service.py` (MOD) | route | request-response | itself (delete `/health`) | exact |
| `watermeter/app.py` (MOD) | config/wiring | event-driven (lifespan) | itself, lines 43-94, 146-184 | exact |
| `watermeter/__main__.py` (MOD) | CLI | request-response | itself (argparse + lazy imports) | exact |
| `watermeter/static/auth.js` (NEW) | utility (client shim) | request-response | none (patches existing ~65 `fetch()` sites) | no analog |
| `watermeter/static/account.js` (NEW) | component (page JS) | request-response | `watermeter/static/mqtt-config.js` (small per-page fetch JS) | role-match |
| `watermeter/static/style.css` (MOD) | config (theme) | n/a | itself (CSS variables) | exact |
| `watermeter/templates/setup.html`, `login.html`, `account.html` (NEW) | component (template) | request-response | `watermeter/templates/*.html` pages (use `_theme.html`) | role-match |
| `watermeter/templates/_theme.html` (MOD) | component | n/a | itself | exact |
| `watermeter/templates/_nav.html` (MOD) | component | n/a | itself (`.nav-item`) | exact |
| `tests/unit/conftest.py` (MOD) | test fixture | request-response | itself, `test_client` | exact |
| `tests/unit/test_auth_*.py`, `test_ratelimit.py`, `test_cli_set_password.py` (NEW) | test | unit | `tests/unit/test_api_routes.py` | role-match |
| `tests/unit/test_route_inventory.py` (NEW) | test | request-response | `tests/unit/test_api_routes.py` | partial |
| `tests/conftest.py` (MOD) | test config | n/a | itself, `pytest_addoption` | exact |
| `tests/integration/conftest.py` (MOD) | test fixture | request-response | itself, `_docker_run`, `base_url`, `api` | exact |
| `tests/integration/test_smoke.py` (MOD/NEW test) | test | request-response | itself | exact |
| `debug.sh` (MOD) | script | batch | itself (`${HF_TOKEN:+-e ...}`) | exact |
| `debug_clean.sh` (MOD) | script | batch | `debug.sh` + itself | exact |
| `oneshot_test.sh` (verify only) | script | batch | itself | n/a |
| `.github/workflows/docker.yml` (MOD) | CI config | batch | itself, smoke step | exact |
| `docker-compose.yml`, `.env.example`, `README.md`, `DOCKER.md` (MOD) | config/docs | n/a | itself | exact |

## Pattern Assignments

### `watermeter/auth.py` (store, file-I/O)

**Analog:** `watermeter/persistence.py` (lines 1-50)

**Imports + logger pattern** (lines 1-14):
```python
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)
```

**Atomic write idiom** (lines 39-48, same at 181-186). Copy for the `replace()` path; add `os.link` for no-clobber create and `os.chown` when `geteuid()==0` (RESEARCH Code Example 2):
```python
fd, tmp_path = tempfile.mkstemp(dir=self.file_path.parent, suffix=".tmp")
try:
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, self.file_path)
except BaseException:
    os.unlink(tmp_path)
    raise
```

**Load-failure idiom** (persistence `StateStore.load`: log and return default). DEVIATION required: auth must be three-state, so only `FileNotFoundError` means UNCONFIGURED; any other read/parse error is UNAVAILABLE (fail closed), per RESEARCH Pitfall 2.

**Scrypt PHC hash/verify, token sign/verify, `safe_next`, path resolved at call time (`WATERMETER_AUTH_FILE`, default `/data/auth.json`)**: no repo analog; use RESEARCH Code Examples 1 and 3 verbatim. Keep cookie name, state names and allowlist constants in this module and import them elsewhere.

**Style notes:** f-string logging, dataclass for multi-field results (`PlausibilityResult` style), `X | None` typing, no `print`, no bare `except:`. `threading.Lock` module-level for the create-once check.

---

### `watermeter/ratelimit.py` (utility leaf, in-memory)

**Analog:** `watermeter/rate_tracker.py` (leaf module, state held on an object, no service imports). Read it at implementation time for constructor/injection shape. Body: RESEARCH Code Example 5 (`FailureLimiter`, windows `(1,1),(60,5),(3600,20)`, `threading.Lock`, injectable `clock`, 1024-key bound).

Test analog for injectable clock: `tests/unit/test_plausibility.py` style (one-line docstring per test).

---

### `watermeter/auth_middleware.py` (pure ASGI middleware)

**No analog.** The repo has no middleware. Use RESEARCH Pattern 1, 4, 5 and Code Examples 3, 4 (spike-verified on Starlette 0.52.1 and 1.7.0). Order inside `__call__`: lifespan pass-through, websocket close 1008, CSRF check, gate, deny shaping, cookie refresh `send` wrapper. Registered via `app.add_middleware(...)` in `app.py`.

JSON 401 body must follow the repo error contract (`success`/`message`), see `routes/service.py` lines 119-128:
```python
return JSONResponse({"success": False, "message": "Value must be >= 0"}, status_code=400)
```

---

### `watermeter/routes/auth.py` (route, form + JSON request-response)

**Analog:** `watermeter/routes/pages.py` (templates) and `watermeter/routes/service.py` (Pydantic body + JSON errors)

**Template ownership pattern** (pages.py lines 1-29). Auth routes must own their own `Jinja2Templates` on the REAL templates dir so the unit fixture's monkeypatch of `pages_module.templates` does not stub them:
```python
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
_pkg_dir = Path(__file__).resolve().parent.parent

def register_template_globals(tmpl: Jinja2Templates) -> None:
    tmpl.env.globals["arrows_mode"] = current_arrows_mode

templates = Jinja2Templates(directory=str(_pkg_dir / "templates"))
register_template_globals(templates)
```
Import `register_template_globals` from `.pages` rather than redefining it.

**Page handler + decorator metadata** (pages.py lines 32-55):
```python
@router.get("/", response_class=HTMLResponse, tags=["Pages"], summary="Dashboard page", description="...")
async def dashboard(request: Request):
    ...
    return templates.TemplateResponse(request, "dashboard.html", context={"nav_active": "dashboard", ...})
```
Redirect with `RedirectResponse(url, status_code=303)` (same file line 43).

**Pydantic body + validator** (service.py lines 13-14, 36-52): `BaseModel`, `field_validator` for `/api/auth/password`.

**Rules:** hash/verify through `await asyncio.to_thread(...)`; limiter pre-check before hashing; failed login 401, throttled 429 + `Retry-After`, setup validation 400; `Cache-Control: no-store`; hidden `autocomplete="username"` field.

---

### `watermeter/routes/health.py` (route)

**Analog:** `watermeter/routes/service.py` lines 224-239. Move verbatim; payload unchanged:
```python
@router.get("/health", tags=["Status & Reading"], summary="Health check",
            description="Health check endpoint for monitoring system availability")
def health():
    """Health check endpoint."""
    service = watermeter_service.get_service()
    return {
        "status": "ok",
        "mqtt_connected": service.mqtt_client.is_connected() if service.mqtt_client else False,
        "last_update": service.current_state.get("last_update"),
        "processing": service.current_state["processing"],
    }
```
Imports needed: `from .. import watermeter_service` and `APIRouter` (same as service.py header). Delete the original from `service.py`. Check `tests/unit/test_api_routes.py` for `/health` tests that still pass after the move.

---

### `watermeter/app.py` (wiring + lifespan)

**Analog:** itself.

**App constructor** (lines 146-155): add `docs_url=None, redoc_url=None, openapi_url=None`. Note `tags_metadata`/`openapi_tags` become inert; leave them.

**Router registration** (lines 162-184): imports after `safe_subpath`, each with `# noqa: E402`:
```python
from .routes.calibration import router as calibration_router  # noqa: E402
...
app.include_router(pages_router)
app.include_router(service_router)
```
Add `from .routes.auth import router as auth_router  # noqa: E402`, same for `health`, and `app.include_router(...)`. Add `app.add_middleware(AuthMiddleware)` right after the `FastAPI(...)` call or after mount (line 158 mounts `/static`; the middleware covers it).

**Lifespan** (lines 43-94): put `auth.bootstrap_from_env()` inside `try/except Exception as e: logger.error(...)` so a failure never stops the reading loop (D-07). Pattern to copy from lines 52-58:
```python
try:
    inference_svc.initialize(service.config)
except Exception as e:
    logger.warning(f"Inference initialization failed (no models?): {e}")
```
Also log the `/data`-not-a-mount warning (`os.path.ismount`) and the setup token here.

---

### `watermeter/__main__.py` (CLI)

**Analog:** itself (whole file, 30 lines). Extend with subparsers while keeping `--one-shot`/`--config` working (verified later by `oneshot_test.sh`). Lazy-import pattern to keep:
```python
if args.one_shot:
    from watermeter.oneshot import main as oneshot_main
    sys.exit(oneshot_main(args.config))
else:
    from watermeter.app import main
    main()
```
`set-password` branch must lazily import only `watermeter.auth` (no cv2/openvino), use `getpass` when TTY else one stdin line. `print()` is allowed here, like `oneshot.py`. Argparse caveat: top-level flags with defaults plus a subparser; use `dest="command"` and keep the no-subcommand path identical to today.

---

### `watermeter/static/auth.js` (client shim) and `account.js`

**auth.js:** no analog; RESEARCH Pattern 8 (wrap `fetch`, patch `XMLHttpRequest.prototype.open/send`, `X-Requested-With: watermeter`, redirect on 401 except on `/login` and `/setup`). Include from `_theme.html` (NOT on setup/login).

**account.js:** copy structure from an existing small per-page script (for example `watermeter/static/mqtt-config.js`; read it for the fetch/JSON error-display idiom, which uses `success`/`message`). Do not add the CSRF header manually; `auth.js` does it.

---

### Templates: `setup.html`, `login.html`, `account.html`, `_theme.html`, `_nav.html`

**Analog:** existing pages that include `_theme.html` (for example `watermeter/templates/dashboard.html`, `config_editor.html`) for head structure.

**`_theme.html`** (whole file is a single inline FOUC script, lines 1-6): add `<script src="/static/auth.js"></script>` here. Setup/login include the theme snippet but must NOT load `auth.js`; so either split the include or add a flag. Decide in the plan (for example a second `_theme_boot.html`, or `{% if with_auth_js|default(true) %}`).

**`_nav.html`** (item structure lines 20-45): items are `.app-nav .nav-item` with `.nav-icon` and `.nav-label`; the whole template starts with `{% set active = nav_active|default('') -%}`. New Account link and Logout form must use only `nav_active` (tested via `env.get_template("_nav.html").render(nav_active="")` in `tests/unit/test_calibration_page.py`). Logout is a static `<form method="post" action="/logout">` containing `<button class="nav-item">`; make the form `display: contents` or `flex: 1` (RESEARCH Pitfall 8). Mobile bar already has up to 7 items; shrink labels for 9.

**`style.css`:** add `.auth-card`, `.auth-form`, `.auth-notice`, `.auth-error` using existing CSS variables (`--primary`, `--nav-bg`); no inline `style=`. Note `_nav.html` carries its own inline `<style>` block; nav additions can go there to stay consistent, but the CONTEXT says style.css for the auth pages.

---

### `tests/unit/conftest.py` (fixture changes)

**Analog:** itself, `test_client` (lines ~95-155). Current shape to extend:
```python
@pytest.fixture
def test_client(mock_service, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ...
    monkeypatch.setattr(pages_module, "templates", Jinja2Templates(directory=str(templates_dir)))
    app_module.app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    from fastapi.testclient import TestClient
    client = TestClient(app_module.app, raise_server_exceptions=False)
    return client
```
Add: `monkeypatch.setenv("WATERMETER_AUTH_FILE", str(tmp_path / "auth.json"))`; a `scope="session"` fixture that computes ONE scrypt hash; write `auth.json` from it; mint the cookie with `auth.issue_token(...)` onto `client.cookies`; set `client.headers["X-Requested-With"] = "watermeter"`. Add `anon_client` (same wiring, no cookie; unconfigured/configured variants). No `dependency_overrides`, no bypass flag, no per-test hashing (about 107 tests use `test_client`). Keep the existing `sys.modules` mock block at top untouched; `watermeter.watermeter_service` is a MagicMock, so `auth.py` and the middleware must not import it.

Note the fixture re-mounts `/static` onto the global `app` on each call; the gate must still allowlist `/static/*` and the inventory test must tolerate duplicate mounts.

---

### Unit tests for auth (`test_auth_*.py`, `test_ratelimit.py`, `test_cli_set_password.py`, `test_route_inventory.py`)

**Analog:** `tests/unit/test_api_routes.py` (uses `test_client`, asserts JSON `success` keys), `tests/unit/test_calibration_page.py` (real-template rendering via `Jinja2Templates(directory=TEMPLATES_DIR)`, `_wire_service` helper).

Template-render pattern (test_calibration_page.py lines 1-30):
```python
TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "watermeter" / "templates"
...
@pytest.fixture
def real_templates(test_client, monkeypatch):
    import watermeter.routes.pages as pages
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
```
Test style: one-line docstring stating the expectation per test; Black 120; run `uvx ruff check watermeter/ tests/`. Route-inventory test: RESEARCH Code Example 7 (walk `app.routes` including `Mount`, anonymous client, expect non-allowlisted paths to return 303/401, allowlist exactly `/health`, `/login`, `/logout`, `/setup`, `/static/*`).

---

### `tests/conftest.py` (add `--admin-password`)

**Analog:** itself, `pytest_addoption` (lines 16-32):
```python
parser.addoption("--base-url", default=None, help="Use an already-running service instead of starting a fresh container")
```
Add `--admin-password` the same way.

---

### `tests/integration/conftest.py`

**Analog:** itself.

**`_docker_run(image, host_port)`** (lines 57-73): command list; add `-e`, `f"WATERMETER_ADMIN_PASSWORD={password}"` before `image`. Generate `secrets.token_urlsafe(16)` once per session.

**`base_url` fixture** (lines 120-170): the `explicit_url` branch calls `_wait_healthy` (which hits `/health`, stays open) then yields. Password resolution for that branch: option / `WATERMETER_TEST_PASSWORD` / `WATERMETER_ADMIN_PASSWORD`; skip with a message if missing (D-12).

**`api` fixture** (lines 174-178):
```python
@pytest.fixture(scope="session")
def api(base_url):
    """httpx client with base_url pre-configured."""
    with httpx.Client(base_url=base_url, timeout=30) as client:
        yield client
```
Change: log in once (`client.post("/login", data={"password": pw})`, no redirect following, assert 303), cookie jar persists, set `client.headers["X-Requested-With"] = "watermeter"`; `pytest.skip` when login is rejected. Add a test that unauthenticated `GET /api/status` is 401 and `/health` is 200 (use a fresh `httpx.get`). Pitfall 9: `test_dashboard_page` may already 303 without a reference image; run baseline first. `tests/integration/test_oneshot.py` is standalone, leave it.

---

### Scripts: `debug.sh`, `debug_clean.sh`, `oneshot_test.sh`

**`debug.sh`** (two `docker run` blocks, lines ~59-73 detached and ~94-106 interactive): copy the existing optional-env idiom:
```bash
${HF_TOKEN:+-e HF_TOKEN="$HF_TOKEN"} \
```
to add `${WATERMETER_ADMIN_PASSWORD:+-e WATERMETER_ADMIN_PASSWORD="$WATERMETER_ADMIN_PASSWORD"} \` in both blocks. Line ~83 readiness probe `curl -sf http://localhost:8002/` must become `/health` (the root path now 303s).

**`debug_clean.sh`** (two `docker run` blocks at ~91 and ~124; probe at line 106 `curl -sf http://localhost:${PORT}/`): add `--setup` flag parsing (copy the existing flag loop at the top of the script, read it first), generate `openssl rand -hex 16` / `python3 -c 'import secrets...'` password unless `--setup`, pass `-e WATERMETER_ADMIN_PASSWORD=...`, print the password once, probe `/health`. No fixed password literal.

**`oneshot_test.sh`**: no edit expected; re-run as AUTH-15 evidence after the `__main__.py` change.

---

### `.github/workflows/docker.yml` (smoke step, lines 56-60)

**Analog:** itself:
```yaml
- name: Smoke test (container starts, /health responds)
  run: |
    docker run -d --name smoke -p 8001:8001 watermeter-smoke:ci
    for i in $(seq 1 60); do
      if curl -sf http://localhost:8001/health; then echo; echo "healthy after ${i}x2s"; exit 0; fi
      sleep 2
    done
    echo "container did not become healthy"; docker logs smoke; exit 1
```
Add `PW=$(openssl rand -hex 16)` and `-e WATERMETER_ADMIN_PASSWORD=$PW`, keep the `/health` loop, then assert unauth `/api/status` = 401, `curl -c jar -d "password=$PW" /login` = 303, authed `/api/status` = 200. Note the `exit 0` inside the loop would skip later assertions; restructure (break instead of exit). `.gitea/workflows/` only has `ci.yml` (unit tests), no change.

---

## Shared Patterns

### Atomic JSON write
**Source:** `watermeter/persistence.py` lines 39-48 (and 181-186)
**Apply to:** `auth.py` (both create and replace paths)
Same-dir `mkstemp` + `os.replace`; auth adds `os.link` for no-clobber and chown-to-parent-owner when root.

### Error response contract
**Source:** `watermeter/routes/service.py` lines 119-128
**Apply to:** `auth_middleware.py` JSON 401/403/429, `routes/auth.py` JSON endpoints
`{"success": False, "message": "..."}` with explicit status. HTML form pages use the template re-render with status 400/401/429 instead.

### Route registration without circular imports
**Source:** `watermeter/app.py` lines 160-184
**Apply to:** `routes/auth.py`, `routes/health.py`
Import after `safe_subpath`/`_create_background_task`, `# noqa: E402`, then `app.include_router(...)`. Do not `from ..app import ...` in new routes unless required.

### Never break the reading loop
**Source:** `watermeter/app.py` lifespan lines 52-58 (try/except around optional init)
**Apply to:** `auth.bootstrap_from_env()`, setup-token generation, `/data` mount warning

### Logging
f-strings, `logger = logging.getLogger(__name__)`, `logger.warning(f"Failed login from {ip}")`, never log passwords. Setup token is the only secret logged, by D-09.

### Test auth seam
**Source:** `tests/unit/conftest.py` `test_client`
**Apply to:** every unit test; real templates for auth pages via their own `Jinja2Templates`.

### Blocking work off the loop
`await asyncio.to_thread(hash_password, ...)` plus `threading.Semaphore(2)` inside the worker (RESEARCH Pitfall 6).

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `watermeter/auth_middleware.py` | middleware | request-response | No middleware exists; use RESEARCH Pattern 1/4/5 and Code Examples 3-4 |
| `watermeter/static/auth.js` | client shim | request-response | No global fetch/XHR patching exists; RESEARCH Pattern 8 |
| scrypt/HMAC/`safe_next` functions in `auth.py` | utility | transform | No crypto code in repo; RESEARCH Code Examples 1, 3 |
| `ratelimit.py` sliding windows | utility | in-memory | `rate_tracker.py` is about meter rates, not failure windows; RESEARCH Example 5 |

## Metadata

**Analog search scope:** `watermeter/` (app.py, routes/, persistence.py, __main__.py, templates/_nav.html, _theme.html), `tests/unit/conftest.py`, `tests/conftest.py`, `tests/integration/conftest.py`, `debug.sh`, `debug_clean.sh`, `.github/workflows/docker.yml`
**Not read in full (read at implementation time):** `rate_tracker.py`, `static/mqtt-config.js`, `tests/unit/test_api_routes.py`, `oneshot_test.sh`, remaining RESEARCH.md lines 519-735 (Code Examples 5-7, validation architecture; this map relied on the Code Example 5/7 references in lines 1-518 and the Pattern sections)
**Pattern extraction date:** 2026-10-11
