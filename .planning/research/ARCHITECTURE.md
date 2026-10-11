# Architecture Research

**Domain:** Retrofitting auth, hardening and stale-reading visibility into an existing single-process FastAPI + Jinja2/HTMX IoT service (milestone "Reliability & Security Basics")
**Researched:** 2026-10-11
**Confidence:** HIGH for integration points (read from the code), MEDIUM for HTMX/paho behaviours cited from docs (verified against upstream source/docs, not run here)

All paths are repo-relative. This file answers: where each piece plugs in, what talks to what, where the secret material lives, how 401s reach HTMX and `fetch()`, how the tests keep working, and what order to build in.

## Standard Architecture

### System Overview

```
 Browser (7 pages: dashboard, label, training, explore, roi-config, config-editor, calibration)
   |  cookie wm_session (HttpOnly, SameSite=Lax)   cookie wm_csrf (JS-readable) -> header X-CSRF-Token
   |  static/auth.js (NEW, loaded from _theme.html): patches fetch + XMLHttpRequest (HTMX uses XHR)
   v
+--------------------------------------------------------------------------------------+
| ASGI: SecurityHeadersMiddleware (NEW, pure ASGI; wraps every response incl. /static) |
+--------------------------------------------------------------------------------------+
   |                         |                                |
   v                         v                                v
 /static (mount, PUBLIC)   PUBLIC routers (NEW)            `protected` APIRouter (NEW wrapper)
                           routes/health.py  GET /health    dependencies=[Depends(require_auth)]
                           routes/auth.py  /login /setup    includes the 11 EXISTING routers
                                           /logout          (88 routes) + per-route
                                                            Depends(rate_limit(...)) on heavy ones
                                                                   |
   watermeter/auth.py (NEW, leaf)  <-------------------------------+   uses: AuthStore, sessions,
   watermeter/ratelimit.py (NEW, leaf)                                    CSRF, error shaping
   watermeter/url_guard.py (NEW, leaf)  <-- called by routes/roi.py, routes/mqtt.py,
                                            image_pipeline.py, config_utils.validate_config
   watermeter/config_utils.py  (+ secret redaction/restore) <-- routes/config.py, routes/mqtt.py
                                                                   |
                                                                   v
+--------------------------------------------------------------------------------------+
| WatermeterService facade (watermeter_service.py)  --singleton--  get_service()       |
|   _derive_pipeline_status() --> health.evaluate() (NEW, pure) --> current_state      |
|   MqttPublisher (mqtt_publisher.py): will_set LWT, availability, health topic        |
|   SchedulingManager (scheduling.py): + health tick (60 s)                            |
+--------------------------------------------------------------------------------------+
   |                         |
   v                         v
 /data/auth.json (NEW, 0600)   MQTT broker  --retained availability + health--> Home Assistant
 /data/state.json etc. (existing)
```

Data direction summary: browser -> middleware -> dependency chain (rate limit, auth, CSRF) -> route -> service singleton -> MQTT/HA. Auth state flows from `/data/auth.json` (AuthStore) to `require_auth` only. Health state flows service -> `health.evaluate` -> (a) `current_state` -> status fragment/JSON -> dashboard, (b) `MqttPublisher` -> broker -> HA. MQTT inbound (trigger/reset/confirmation topics) never touches HTTP auth; the broker is the trust boundary there (BL-37 is out of scope).

### Component Responsibilities

| Component | Responsibility | Mechanism and why |
|-----------|----------------|-------------------|
| `watermeter/auth.py` (NEW leaf, imports nothing from `app` or the service) | `AuthStore` (hash + session secret + epoch in `/data/auth.json`), password hash/verify, session cookie sign/verify, CSRF token derive/check, `require_auth` dependency, `AuthRequired` exception + handler, env bootstrap | Single module so one function object (`require_auth`) can be overridden in tests; leaf so the `routes/*` -> `app` import cycle noted in `.planning/codebase/ARCHITECTURE.md` is not extended |
| `watermeter/routes/auth.py` (NEW) | `GET/POST /login`, `GET/POST /setup`, `POST /logout`, `POST /api/auth/password`; renders `templates/login.html`, `templates/setup.html` | Public router (no `require_auth`); `/api/auth/password` is on the protected router |
| `watermeter/routes/health.py` (NEW, `/health` MOVES here from `routes/service.py`) | Keeps `/health` unauthenticated for the compose healthcheck, CI smoke test (`.github/workflows/docker.yml`), integration `_wait_healthy` | Must leave `service.py` because that router goes under `protected` |
| `SecurityHeadersMiddleware` (NEW, in `watermeter/security.py` or `app.py`) | `X-Content-Type-Options`, `Referrer-Policy`, framing control, light CSP | Middleware, not dependency: must also cover the `/static` mount, 401/403/404/500 responses and redirects, none of which pass through route dependencies |
| `watermeter/ratelimit.py` (NEW leaf) | In-memory sliding-window limiter + `rate_limit(bucket, limit, window)` dependency factory | Single uvicorn worker (Dockerfile `CMD`), so process memory is the correct scope; no Redis/slowapi |
| `watermeter/url_guard.py` (NEW leaf) | `validate_fetch_url(url)` (async/DNS) and `check_url_static(url)` (sync, no DNS) | Library function called at the fetch chokepoints; not middleware (it guards outbound, not inbound) |
| `watermeter/health.py` (NEW pure) | `evaluate(current_state, config, now) -> HealthReport(problem, reason, pipeline_status, last_valid_age_s, too_old)` | One definition of "problem" shared by HA sensor, dashboard banner and `/api/status`. Pure, so unit-testable without the MQTT/paho mocks |
| `watermeter/config_utils.py` (EXTEND) | `SECRET_PATHS`, `redact_config_text()`, `restore_secrets()`, schema entries for `health.*` and `homeassistant.availability_topic` | CLAUDE.md: all config reads/writes go through this module. Do not name a module `secrets.py` (shadows stdlib) |
| `watermeter/mqtt_publisher.py` (EXTEND) | `will_set` before `connect`, birth message on connect, explicit offline on `stop()`, `availability` block in every discovery payload, `publish_health()`, new `problem` entity | Already owns the paho client, discovery list `_HA_ENTITIES`, and the `training_stats` secondary-topic pattern to copy |
| `watermeter/scheduling.py` (EXTEND) | Third loop: health tick every 60 s (`health_fn` optional ctor arg, default `None` so the 6 existing `SchedulingManager(...)` test constructions in `tests/unit/test_trigger_mode.py` keep working) | Needed because in `mqtt` trigger mode no cycle may run for hours; a time-based "reading too old" can only be noticed by a timer |
| `static/auth.js` (NEW) | Adds `X-CSRF-Token` to unsafe same-origin requests; redirects on 401; exposes nothing | One file instead of editing 65 `fetch()` sites + 1 XHR (see "Client side" below) |
| `static/dashboard.js` + `templates/dashboard.html` + `templates/status_fragment.html` (EXTEND) | Stale/not-OK banner (server-rendered in the polled fragment), connection-lost banner (client-driven, OUTSIDE the swap target) | Same split already used by the Pipeline Health widget |

## Recommended Project Structure

```
watermeter/
├── auth.py                 # NEW leaf: AuthStore, hashing, session+CSRF, require_auth, bootstrap
├── ratelimit.py            # NEW leaf: limiter + rate_limit() dependency factory
├── url_guard.py            # NEW leaf: SSRF guard
├── health.py               # NEW pure: evaluate() -> HealthReport
├── security.py             # NEW: SecurityHeadersMiddleware (or inline in app.py)
├── app.py                  # EDIT: middleware, exception handler, `protected` router, lifespan bootstrap
├── config_utils.py         # EDIT: secret redaction/restore, schema, static URL check on images.src
├── mqtt_publisher.py       # EDIT: LWT, availability, health topic, problem entity
├── scheduling.py           # EDIT: health tick
├── watermeter_service.py   # EDIT: health hook in _derive_pipeline_status/finally, reload_config
├── image_pipeline.py       # EDIT: validate_fetch_url before fetch_images/fetch_whole_image
├── routes/
│   ├── auth.py             # NEW: login/setup/logout/change-password
│   ├── health.py           # NEW: /health (moved)
│   └── (11 existing)       # unchanged except: roi.py (guard), mqtt.py (redact + guard on /test),
│                           #   config.py (redact/restore), heavy endpoints get Depends(rate_limit)
├── static/auth.js          # NEW
└── templates/
    ├── login.html, setup.html   # NEW, standalone (include _theme.html, NOT _nav.html)
    └── _theme.html              # EDIT: add <script src="/static/auth.js">
tests/unit/
├── conftest.py             # EDIT: test_client bypasses auth + disables limiter (one place)
└── test_auth.py, test_csrf.py, test_url_guard.py, test_ratelimit.py, test_secrets_redaction.py,
    test_security_headers.py, test_health_eval.py, test_route_guard_inventory.py   # NEW
```

### Structure Rationale

- **Leaf modules (`auth`, `ratelimit`, `url_guard`, `health`)**: `routes/label.py` and `routes/service.py` already `from ..app import safe_subpath`. New code must not deepen that cycle; `app.py` only imports the leaves.
- **`protected` wrapper router instead of per-router dependencies**: default-deny by structure. A future 12th router is protected unless someone deliberately mounts it on the public side. Guard test below enforces it.
- **`_theme.html` as the injection point**: all 7 pages include it in `<head>` (verified: calibration, config_editor, dashboard, explore, label, training, roi_config). One line there loads `auth.js` before any page script.

## Architectural Patterns

### Pattern 1: Default-deny router tree

**What:** Public surface is an explicit allowlist of routers; everything else hangs off one router carrying `Depends(require_auth)`.
**When to use:** Any app where "forgot to protect the new endpoint" is the main risk (89 route decorators today, 53 of them writes: 43 POST + 10 DELETE).
**Trade-offs:** Needs `/health` moved out of `routes/service.py`. `/static` is a mount and stays public (JS/CSS carry no secrets and login needs the stylesheet). FastAPI's built-in `/docs`, `/redoc`, `/openapi.json` are Starlette routes that `dependencies=` does not cover: disable the defaults (`docs_url=None, redoc_url=None, openapi_url=None`) and re-add them on the protected router, or leave them off. Swagger "try it out" for unsafe methods will fail CSRF; accept that.

```python
# app.py (after the existing "# noqa: E402" router imports)
from fastapi import APIRouter, Depends
from .auth import require_auth
from .routes.auth import router as auth_router
from .routes.health import router as health_router

protected = APIRouter(dependencies=[Depends(require_auth)])
for r in (pages_router, service_router, config_router, roi_router, label_router, training_router,
          models_router, synthetic_router, mqtt_router, metrics_router, calibration_router):
    protected.include_router(r)

app.include_router(health_router)    # public
app.include_router(auth_router)      # public: /login /setup /logout
app.include_router(protected)        # everything else
```

Guard test (`tests/unit/test_route_guard_inventory.py`): walk `app.routes`, assert every `APIRoute` whose path is not in `{"/health", "/login", "/setup", "/logout"}` has `require_auth` in `route.dependant` (recursively). Fails the build when someone mounts a router on the wrong side.

### Pattern 2: One dependency does session + CSRF; 401 shape depends on caller

**What:** `require_auth(request)` (a) if no password configured -> `AuthRequired(setup=True)`, (b) verify `wm_session` cookie -> else `AuthRequired()`, (c) if method is unsafe, compare `X-CSRF-Token` header to the token derived from the session -> else 403. `AuthRequired` is turned into a response by one exception handler registered in `app.py`:

| Caller | Detection | Response |
|--------|-----------|----------|
| HTMX poll/button (`HX-Request: true`) | header | `401` + `HX-Redirect: /login?next=<path>` (or `/setup`) and empty body |
| `fetch()`/XHR JSON (`path.startswith("/api/")`) | path | `401` JSON `{"success": false, "message": "Authentication required", "detail": "...", "setup_required": bool}` |
| Browser navigation to a page (non-`/api/`) | default | `303` to `/login?next=<path+query>` (or `/setup`) |

**Why not a plain 303 for everything:** XHR follows redirects transparently, so the `<main hx-get="/api/status/html" hx-trigger="load, every 5s">` poll would receive the 200 login HTML and swap it into the dashboard. This is exactly the failure PROJECT.md calls out. All HTMX fragments in this app live under `/api/` (only `/api/status/html` and `/api/trigger` are HTMX targets), so both rules agree.
**Both `message` and `detail` keys are deliberate:** existing JS error paths read `data.message` (config/mqtt/roi routes) or `data.detail` (FastAPI default, training upload), so a single JSON body renders sensibly everywhere. Same for 403 (CSRF) and 429.
**HTMX behaviour:** upstream `handleAjaxResponse` processes `HX-Redirect` before the status-code check, so it fires on a 401 (verified in htmx source; MEDIUM confidence for the pinned 1.9.10 build, so `auth.js` also redirects on any 401 XHR as a belt-and-braces fallback).
**Open-redirect guard:** accept `next` only if it starts with a single `/` (reject `//host`, schemes, backslashes).

### Pattern 3: Client-side auth shim instead of touching call sites

**What:** `static/auth.js` (loaded from `_theme.html`, skipped on `/login` and `/setup`) does three things:
1. Wraps `window.fetch`: for same-origin requests with method not in GET/HEAD/OPTIONS, add `X-CSRF-Token` read from the `wm_csrf` cookie. If the response is 401, `location.assign('/login?next=...')` (or `/setup` when `setup_required`).
2. Patches `XMLHttpRequest.prototype.open/send` the same way. HTMX uses XHR, and `static/training.js` (~line 1554) uses XHR for the ZIP upload, so this covers `hx-post="/api/trigger"`, the 5 s status poll and the upload with no edits.
3. Nothing else. No framework, no global state.

Affected call sites that need NO edit: 64 `fetch(` in 8 files under `static/` (training.js 23, roi-config.js 18, explore.js 8, config-editor.js 5, dashboard.js 3, label.js 3, mqtt-config.js 3, calibration.js 1 via its `api()` helper) + 1 inline `fetch('/api/reset'...)` in `templates/config_editor.html` (~line 348) + 1 XHR + 2 HTMX attributes in `templates/dashboard.html`. Only `config-editor.js` and `mqtt-config.js` need real edits, for write-only secrets (below).

**CSRF token form:** signed double-submit. `wm_csrf = HMAC(secret, session_cookie_value)`; the server recomputes and compares with `hmac.compare_digest`. Chosen over a `<meta>` token because 7 standalone templates (no base layout) and a unit-test environment that renders stub templates make template-context plumbing costly. Do not use `Origin` checking as the primary defence: behind a reverse proxy the `Host` header often differs from the browser origin.

### Pattern 4: Secrets are write-only at the config boundary

**Where the secret leaks today (3 places, not 1):**
1. `GET /api/config` (`routes/config.py`) returns the raw file text, including a literal `mqtt.password`, straight into Monaco.
2. `GET /api/mqtt/config` (`routes/mqtt.py`) returns `dict(raw_config["mqtt"])` including `password`; `static/mqtt-config.js` `loadConfig()` puts it in `#mqtt-password`.
3. `POST /api/mqtt/test` takes the password from the browser, so once the UI no longer has it, the test would send an empty one.

**Design:** `config_utils.SECRET_PATHS = (("mqtt", "password"),)` with a fixed sentinel (`"********"`).
- Read paths: `redact_config_text(text)` (ruamel round-trip, comments preserved) for `/api/config`; drop the key and add `"password_set": true/false` for `/api/mqtt/config`. Leave `${ENV_VAR}` tokens visible (they are references, not secrets).
- Write paths: `POST /api/config/save` calls `restore_secrets(new_cm, old_cm)` (sentinel or missing -> keep existing value) BEFORE `save_config`. `POST /api/mqtt/config` skips `password` when it is absent/empty/sentinel; an explicit `clear_password: true` removes it. `POST /api/mqtt/test` with empty/sentinel password falls back to `MQTT_PASSWORD` env, then the stored value.
- `reload_config` needs no change: `routes/config.py` and `routes/mqtt.py` already re-read the saved file with `yaml.safe_load`, so the service always receives the real value; `mqtt_changed` comparison stays correct.
- Env-sourced credentials (`MQTT_PASSWORD` wins over config in `MqttPublisher.start`) are unaffected.

### Pattern 5: SSRF guard at the fetch chokepoints, LAN-permissive

**Critical constraint:** the camera is a LAN device. RFC1918 / ULA addresses and Docker-network hostnames (`debug_clean.sh` points `images.src` at a sidecar container name) MUST stay allowed. Block: non-http(s) schemes, missing host, loopback, link-local (covers the cloud metadata address), unspecified, multicast, reserved. Resolve with `getaddrinfo` and reject if ANY resolved address is blocked.

**Chokepoints (all must call it; today there are 4 outbound HTTP sites):**

| Site | Why |
|------|-----|
| `routes/roi.py` `POST /api/roi/image-source` (`httpx.get(url)` of user-supplied URL) | The flagged endpoint; validate before fetch AND before writing `images.src` |
| `routes/roi.py` `POST /api/roi/fetch-image` | Fetches whatever is in config |
| `image_pipeline.py` `fetch_images` / `fetch_whole_image` | Runs every reading cycle; `images.src` can arrive via config editor or hand edit, bypassing the first endpoint |
| `config_utils.validate_config` | Static-only check (scheme, literal-IP loopback/link-local, no DNS) so the config editor shows an error at save time; stays sync and test-friendly |
| `routes/mqtt.py` `POST /api/mqtt/test` | Same class of bug (TCP connect to arbitrary host:port); apply the host blocklist |

Keep `httpx` at its default `follow_redirects=False`; a redirect to a blocked address would otherwise bypass the check. Residual risk: DNS rebinding between check and connect (TOCTOU). Acceptable for a LAN appliance behind auth; document it in `docs/decisions.md` rather than building a pinned-IP transport.

### Pattern 6: Availability + health on separate retained topics

- **LWT:** `self.mqtt_client.will_set(availability_topic, "offline", qos=1, retain=True)` before `connect()` in `MqttPublisher.start()`. Birth: publish `"online"` retained at the end of `on_connect` (after `publish_discovery`).
- **Gotcha:** paho's clean `disconnect()` suppresses the will. `MqttPublisher.stop()` must publish `"offline"` retained and `wait_for_publish(timeout=2)` before `disconnect()`. `reload_config` does `stop_mqtt()` then `start_mqtt()` on broker/credential change, so HA sees a brief offline/online flap; accept it, or pass `graceful_offline=False` from that path.
- **Topic:** `homeassistant.availability_topic` optional, default `f"{publish_topic}/availability"` (same derivation style as the existing `publish_topic + "/training_stats"`). Absent in upgraded configs, so the default is mandatory. Add to the schema in `config_utils.py`.
- **Discovery:** add `availability_topic`, `payload_available`, `payload_not_available` to every payload in `publish_discovery()` (single place; the loop over `_HA_ENTITIES`). Retained republish is idempotent, so upgrades are safe.
- **Health topic:** the main state topic carries a full-state JSON that only `publish_to_mqtt` writes on an accepted/rejected reading; writing partial health into it would clobber `water_usage`. Use a third topic `f"{publish_topic}/health"` (retained) via a new `state_topic_key: "health"`; replace the current two-way `if/else` on `training_stats` with a dict lookup. Payload: `{"problem": bool, "reason": str, "pipeline_status": str, "last_valid_reading_age_s": int|null}`.
- **Problem entity:** new `_HA_ENTITIES` row `object_id: "problem"`, `component: "binary_sensor"`, `device_class: "problem"`, `state_topic_key: "health"`. `tests/unit/test_mqtt_discovery.py` iterates `_HA_ENTITIES` generically, so adding a row is safe.
- **Semantics to document for users:** unavailable = service dead or disconnected (LWT); problem ON = service alive but pipeline degraded/failed/stale or last good reading too old.
- **Publish triggers (three, all needed):** (1) in `process_reading`'s `finally`, right after the existing `_derive_pipeline_status()` (covers every exit path including alignment failure and inference failure; publish only when the report changed or on a 5 min heartbeat); (2) the new 60 s scheduler tick (covers "no trigger ever arrived", the actual silent failure in `mqtt` trigger mode); (3) `on_connect`.
- **Gate everything on `ha_publish_enabled`** like the existing publishers. Note `toggle_ha_publish` flips at runtime but the LWT is fixed at connect time.

## Data Flow

### Request flow (protected API call)

```
fetch('/api/trigger', POST)  --auth.js adds X-CSRF-Token-->  SecurityHeadersMiddleware
   -> protected router deps, in order: rate_limit(bucket) -> require_auth
        require_auth: AuthStore.is_configured? -> verify wm_session -> CSRF check (unsafe method)
        failure -> AuthRequired -> app exception handler -> 303 | 401+HX-Redirect | 401 JSON | 403 | 429
   -> route handler -> get_service() -> WatermeterService method
   <- response (headers added on the way out by the middleware)
```

Dependency order matters: rate limit first on heavy endpoints is wasteful for unauthenticated callers; put `require_auth` first (router-level deps run before route-level ones), then `rate_limit`. Login/setup are public and carry their own limiter.

### First-run / upgrade flow

```
upgrade pull -> container start -> lifespan: auth.bootstrap_from_env()
   WATERMETER_ADMIN_PASSWORD set AND no /data/auth.json -> hash + create store (non-interactive path)
   else if no store -> log WARNING "no admin password; open /setup"
any page, no password set  -> 303 /setup ;  any /api/*, no password -> 401 {setup_required:true}
POST /setup (public ONLY while unconfigured; atomic create via O_EXCL; rate-limited)
   -> hash in a thread (asyncio.to_thread; scrypt/argon2 would block the loop) -> write auth.json
   -> set cookies -> 303 to /  (then existing "/" logic: no /data/reference_raw.jpg -> /roi-config?setup=1)
```

Precedence: auth/setup check runs before the existing first-run ROI redirect in `routes/pages.py`.

### Login / session

`POST /login` (rate-limited: per-IP window plus a global failure backoff, because one admin account and a proxy collapses all clients to one IP) -> verify in a thread -> `Set-Cookie wm_session` (HttpOnly, SameSite=Lax, `Secure` iff `request.url.scheme == "https"`, Path=/, fixed 14-day expiry) and `wm_csrf`. Session token: `v1.<expiry>.<epoch>.<hmac>` (stdlib `hmac`, no itsdangerous). `epoch` increments on password change, invalidating every session. Logout clears both cookies (stateless; cannot revoke a stolen cookie before expiry except via epoch bump).

### Where the password hash lives

`/data/auth.json`, mode 0600, atomic write copied from `StateStore._save` in `watermeter/persistence.py` (tempfile in same dir + `os.replace`). Content: `{"version": 1, "password_hash": "<PHC string>", "session_secret": "<hex>", "epoch": 1, "created_at": ...}`. Never in `config.yaml` (user-editable, shown in Monaco), never in `/config` (users commit it to dotfile repos). The path is fixed (override `WATERMETER_AUTH_FILE` for tests), NOT derived from `persistence.state_file`, because persistence can be disabled. `docker-entrypoint.sh` already `chown -R watermeter /data`; no entrypoint change needed. Resolve the path at call time, not import time, so `monkeypatch`/`tmp_path` works under the mocked-module unit test setup. Store the hash as a self-describing string so the algorithm can change later. Recovery: delete `/data/auth.json` and restart; the setup page reappears (document in README).

**Risk to call out:** if `/data` is not a persistent volume, every restart reopens the unauthenticated `/setup` claim window. Log a loud warning when `/data` is not a mount, and recommend `WATERMETER_ADMIN_PASSWORD` for ephemeral deployments.

### Health / stale flow (reliability)

```
process_reading finally ----+                      +--> status_fragment.html banner (polled /api/status/html)
60 s scheduler tick --------+--> _derive_pipeline_status() --> health.evaluate() --> current_state
MQTT on_connect ------------+                      |        {problem, problem_reason, reading_too_old}
                                                   +--> /api/status JSON (same keys)
                                                   +--> MqttPublisher.publish_health() --> <publish_topic>/health (retained)
broker/LWT ---> <publish_topic>/availability (retained online/offline) ---> HA marks ALL entities unavailable
```

`health.evaluate` inputs: `pipeline_status` (existing OK/DEGRADED/REJECTED/FAILED/STALE from `_derive_pipeline_status`), `last_valid_reading_age_seconds` (already computed there), and a new optional `health.max_reading_age_seconds` config (default derived: `max(3 x cyclic_interval, 1800)` in cyclic/both mode; a fixed generous default such as 6 h in `mqtt` mode, since cadence is external). Absent config must work (upgrade). `_notify_stale` stays as the one-shot event; it is not the state source.

### Dashboard banners

- **Stale / not-OK banner:** extend `templates/status_fragment.html` (already renders a `pipeline-status-badge` and a `stale-marker`) with a prominent banner driven by `problem`/`reading_too_old`. The template is unit-tested with a bare Jinja `Environment` and a hand-built context (`tests/unit/test_status_fragment.py`, no `request`, no globals), so every new variable must be guarded with `is defined`.
- **Connection-lost banner:** element in `dashboard.html` OUTSIDE `<main>` (same reasoning as the Pipeline Health widget: it must survive swaps). Driven by JS in `dashboard.js`: `htmx:sendError`, `htmx:timeout`, `htmx:responseError` (5xx) show it, a successful `htmx:afterRequest` hides it. Because `toggleAutoRefresh` can switch polling off, also run an independent `fetch('/health')` heartbeat (~10 s). `/health` is public, so the heartbeat cleanly distinguishes "server gone" (network error) from "session expired" (401 handled by `auth.js`), and a 401 must never show the connection-lost banner.

## Test strategy (how ~670 unit + integration tests keep working)

**Unit tests have exactly one entry point.** `TestClient(` appears only in `tests/unit/conftest.py` (`test_client` fixture); `test_calibration_tab_routes.py` etc. consume it via the fixture. It imports the module-level `watermeter.app.app` with `watermeter.watermeter_service`, `inference`, `cv2`, `paho` mocked, and without lifespan. So:

1. `test_client` sets `app_module.app.dependency_overrides[require_auth] = lambda: None`, becomes a `yield` fixture, and pops the override on teardown (the app object is process-global; leaking the override would hide auth bugs in `test_auth.py`). Because CSRF lives inside `require_auth`, the same override skips CSRF. Existing tests POST without tokens today and keep doing so.
2. An autouse fixture sets `ratelimit.limiter.enabled = False` and clears buckets. The limiter must be a module-level object with an `enabled` flag (dependency factories return new closures per route, so they cannot be overridden by key).
3. New `auth_client` fixture: same app, NO override, `WATERMETER_AUTH_FILE=tmp_path/auth.json`, used by `test_auth.py`/`test_csrf.py`/`test_route_guard_inventory.py`.
4. Do not add module-level work to `auth.py` that needs the real service: the conftest replaces `watermeter.watermeter_service` with a `MagicMock` module.
5. `status_fragment.html` tests: see guarded variables above. `test_mqtt_discovery.py` / `test_mqtt_reconnect.py` construct `MqttPublisher` with a `MagicMock` client, so `will_set` is a harmless call; add assertions rather than changing existing ones.
6. `SchedulingManager(health_fn=None)` default keeps `tests/unit/test_trigger_mode.py` unchanged.

**Integration tests** (`tests/integration/`, 65 `api.` call sites, all via the session-scoped `api` httpx client): `_docker_run` gets `-e WATERMETER_ADMIN_PASSWORD=<generated test value>`; the `api` fixture does `POST /login` once, httpx keeps the cookie jar, and sets `client.headers["X-CSRF-Token"]` from the `wm_csrf` cookie. For `--base-url` runs against the debug container, read the password from `WATERMETER_TEST_PASSWORD` and `pytest.skip` with a clear message if absent. `test_smoke.py::test_health_endpoint` is unchanged (public). Add one test asserting an unauthenticated `GET /api/status` is 401.

**Scripts and CI:** `oneshot` (`python -m watermeter --one-shot`) never imports `app.py`, so it is unaffected. `debug_clean.sh` (ephemeral, no volumes, so first-run every time) needs `-e WATERMETER_ADMIN_PASSWORD=...` on its `docker run`, and its readiness probe `curl -sf http://localhost:${PORT}/` should become `/health` (a 303 passes `-f` today, but this is cleaner). `debug.sh` keeps `config_debug/data_debug/` so it sets the password once through `/setup`. `docker-compose.yml` healthcheck and the CI smoke test already use `/health`. Add a commented `WATERMETER_ADMIN_PASSWORD` line to `docker-compose.yml`.

**No "auth disabled" escape hatch.** A `WATERMETER_AUTH_DISABLED` flag would be the first thing misconfigured users set and contradicts "secure by default". Tests use the dependency override; ephemeral deployments use the env-seeded password.

## Anti-Patterns

### Anti-Pattern 1: Auth as one global middleware with a path allowlist

**What people do:** `BaseHTTPMiddleware` that checks the cookie for everything except a list of paths.
**Why it's wrong here:** cannot be bypassed per-test without env flags (the global `app` object is shared), `BaseHTTPMiddleware` interacts badly with streaming/background tasks, and the allowlist drifts. Also hides which routes are protected from the router structure.
**Do this instead:** Pattern 1 (router tree + dependency) for auth/CSRF/rate limit; middleware only for response headers.

### Anti-Pattern 2: 303 to the login page for everything

**What people do:** one redirect for pages and fragments.
**Why it's wrong here:** HTMX poll swaps the login page into `<main>`; `fetch().json()` throws on HTML.
**Do this instead:** Pattern 2 (shape by caller).

### Anti-Pattern 3: Strict CSP copied from a checklist

**What people do:** `script-src 'self'`.
**Why it's wrong here:** `dashboard.html` loads HTMX from a CDN (`unpkg.com`, pinned 1.9.10), `config_editor.html` loads Monaco from `cdn.jsdelivr.net` (needs `worker-src blob:` and inline styles), and several templates use inline `<script>` blocks and `onclick=` handlers (`config_editor.html`, `roi_config.html`, `_nav.html`, `_theme.html`). A strict script policy blanks half the UI.
**Do this instead:** this milestone ships `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, and a CSP limited to `frame-ancestors`, `base-uri 'self'`, `object-src 'none'`, `form-action 'self'`. Make framing configurable (`WATERMETER_FRAME_ANCESTORS`, default `'self'`) because embedding the dashboard in a Home Assistant iframe card is a plausible use; note that `SameSite=Lax` cookies are not sent in a cross-site iframe, so embedded use would need the same origin via a reverse proxy. Self-hosting HTMX/Monaco to allow a tight `script-src` is a later hardening item (also removes the offline-LAN CDN dependency).

### Anti-Pattern 4: Putting the hash or session secret in `config.yaml` or deriving it from user config

**Why it's wrong:** the config editor displays and rewrites that file; the hash would be exposed and could be overwritten by a save.
**Do this instead:** `/data/auth.json` as above.

### Anti-Pattern 5: Blocking all private ranges in the SSRF guard

**Why it's wrong:** the only legitimate target IS a private-range camera. Blocking RFC1918 breaks every install and `debug_clean.sh`.
**Do this instead:** Pattern 5.

### Anti-Pattern 6: Publishing health into the main state topic

**Why it's wrong:** the main topic is a full-state document written per reading; partial writes drop `water_usage` and friends from HA.
**Do this instead:** separate retained `/health` topic via `state_topic_key`.

### Anti-Pattern 7: Hashing on the event loop

**Why it's wrong:** scrypt/argon2 take 50-500 ms of CPU; a login attempt would stall the reading pipeline's loop (the same loop runs `process_reading` and the HTTP handlers).
**Do this instead:** `await asyncio.to_thread(verify, ...)`.

## Scaling Considerations

This is a single-container home appliance; "scale" means number of browser tabs and failure episodes, not users.

| Concern | Today | Note |
|---------|-------|------|
| Dashboard polling | 1 request / 5 s / open tab to `/api/status/html`, which calls `_derive_pipeline_status()` (a GET that mutates `current_state`; idempotent) | Auth adds one HMAC verify per request; negligible. Keep `require_auth` free of disk reads (cache `AuthStore` in memory, reload on mtime change) |
| Rate limiter memory | Per-IP dict | Prune entries older than the window on each hit; bounded by LAN size |
| Multiple uvicorn workers | Not used (Dockerfile `CMD` has no `--workers`) | In-memory limiter and the asyncio lock assume one worker; add a note in `docs/decisions.md` |
| Client IP behind a reverse proxy | `request.client.host` is the proxy | Uvicorn only trusts forwarded headers from `FORWARDED_ALLOW_IPS` (default loopback); document the env var for proxy users, and keep a global failure backoff so one proxy IP cannot be abused to lock out nobody-in-particular nor let brute force through |

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| MQTT broker / Home Assistant | LWT + retained availability, retained health topic, discovery `availability` block | Birth after discovery; explicit offline on `stop()`; HA also publishes `homeassistant/status` which the publisher already handles by re-running discovery (availability then re-announced for free) |
| ESP32-CAM image source | `httpx` GET in `image_pipeline.py` and `routes/roi.py` | All through `url_guard`; private ranges allowed |
| CDN assets (HTMX, Monaco) | `<script src=...>` in templates | Constrain CSP accordingly (Anti-Pattern 3) |
| Reverse proxy (optional, user supplied) | TLS termination | `Secure` cookie flag from request scheme; document `FORWARDED_ALLOW_IPS` |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `routes/*` -> `auth.py` | `Depends(require_auth)` at router level, `rate_limit()` per heavy route | Heavy routes to tag: `POST /api/trigger`, `/api/training/start`, `/api/synthetic/generate`, `/api/models/benchmark-all`, `/api/models/{type}/{id}/benchmark`, `/api/training-data/mislabel/scan`, `/api/training-data/dedup`, `/api/training-data/prune/*`, `/api/arrows/calibrate`, `/api/calibration/run` |
| `routes/config.py`, `routes/mqtt.py` -> `config_utils` | redact on read, restore on write | Sentinel round trip must be covered by a Monaco save test: open, change an unrelated key, save, assert password in the file is unchanged |
| `watermeter_service` -> `health.py` | pure function call inside `_derive_pipeline_status` | Keeps the facade thin (the codebase map flags facade growth as an anti-pattern) |
| `watermeter_service` -> `MqttPublisher.publish_health` | method call from `process_reading` finally and the scheduler tick (tick runs via `run_in_executor` like `_stats_fn`, since paho publish is thread-safe but the callback pattern already exists) | Edge-triggered with heartbeat |
| `auth.py` <-> `app.py` | exception handler + `bootstrap_from_env()` in lifespan | Bootstrap in lifespan, not import time, so unit tests (no lifespan) stay hermetic |
| MQTT inbound <-> HTTP auth | none | Trigger/reset/confirmation topics bypass HTTP auth by design (BL-37 deferred) |

## Suggested Build Order

Dependencies drive this; auth is the only large, indivisible slice.

1. **Phase A, Auth vertical slice (largest, riskiest; must land whole).** Order inside: `auth.py` core + `AuthStore` + unit tests -> exception handler + `require_auth` + CSRF -> `routes/auth.py`, `login.html`, `setup.html` -> move `/health` to `routes/health.py` -> `protected` router tree in `app.py` + route inventory guard test -> `static/auth.js` + `_theme.html` include -> `test_client`/limiter fixtures, `auth_client` -> env bootstrap + `debug_clean.sh`, `docker-compose.yml`, integration `conftest.py` -> login throttle (public-endpoint limiter only). It cannot be split further: enabling `require_auth` without `auth.js` breaks every page; without `/setup` it locks out upgrades; without the fixture changes CI goes red.
2. **Phase B, Hardening that sits behind auth (parallelizable, each small):**
   - B1 write-only secrets (`config_utils`, `routes/config.py`, `routes/mqtt.py`, `config-editor.js`/`mqtt-config.js`; depends on nothing in A except that the editor must still work through `auth.js`)
   - B2 `url_guard.py` + 5 call sites + `docs/decisions.md` AD-01/AD-02 superseded
   - B3 compute-endpoint rate limits (needs the limiter from A)
   - B4 security headers middleware (do after A so CSP is written against the final set of templates including login/setup and `auth.js`)
3. **Phase C, MQTT availability and HA problem sensor (independent of auth; can run in parallel with B):** `health.py` -> `_derive_pipeline_status` hook + `current_state` keys -> `will_set`/birth/`stop()` offline -> discovery `availability` -> `/health` topic + `problem` entity -> scheduler tick -> config schema keys.
4. **Phase D, Dashboard banners (depends on C's `current_state` keys; touches `dashboard.js` which A's `auth.js` interacts with):** stale banner in the fragment, then connection-lost banner + `/health` heartbeat; verify a 401 does not show the lost-connection banner.
5. **Docs/upgrade notes** (README: first-visit setup, `WATERMETER_ADMIN_PASSWORD`, recovery by deleting `auth.json`, proxy env vars, HA availability semantics) alongside the phase that introduces each behaviour.

**Ordering rationale:** C and D do not need auth, so they could precede A if the user wants the HA visibility sooner; the roadmap should pick by risk appetite. A before B because B1/B3 assume the final request path. D after C because both banners read the same `health.evaluate` output.

## Sources

- Codebase read for this document: `watermeter/app.py`, `watermeter/routes/{pages,config,mqtt,service,roi,metrics}.py` (89 route decorators: 43 POST, 10 DELETE, 36 GET), `watermeter/config_utils.py`, `watermeter/mqtt_publisher.py`, `watermeter/watermeter_service.py` (`_notify_stale`, `_derive_pipeline_status`, `reload_config`, `process_reading` finally), `watermeter/scheduling.py`, `watermeter/persistence.py`, `watermeter/image_pipeline.py`, `watermeter/static/*.js`, `watermeter/templates/*.html`, `docker-entrypoint.sh`, `docker-compose.yml`, `debug_clean.sh`, `oneshot_test.sh`, `tests/unit/conftest.py`, `tests/integration/conftest.py`, `tests/unit/test_api_routes.py` (HIGH confidence).
- HTMX source/docs (Context7, `/bigskysoftware/htmx`): `HX-Redirect` handled before the response-status check in `handleAjaxResponse`; `htmx:responseError`, `htmx:configRequest` events (MEDIUM: quoted from master, the app pins 1.9.10, hence the XHR-level fallback).
- paho-mqtt: `will_set` must precede `connect`; clean `disconnect()` does not fire the will (standard MQTT semantics, HIGH).
- Home Assistant MQTT discovery `availability_topic` / `payload_available` / `payload_not_available` and `binary_sensor` `device_class: problem` (standard documented fields, MEDIUM, not re-fetched this session).

---
*Architecture research for: auth + hardening + stale visibility on an existing FastAPI/HTMX IoT app*
*Researched: 2026-10-11*
