---
phase: 01-auth-core-and-test-script-plumbing
plan: 04
subsystem: auth
tags: [fastapi, asgi-middleware, default-deny, jinja2, pytest, github-actions, smoke-test]

requires:
  - phase: 01-auth-core-and-test-script-plumbing
    provides: "watermeter/auth.py store, tokens, env seed, setup token (plan 01)"
  - phase: 01-auth-core-and-test-script-plumbing
    provides: "AuthMiddleware pure-ASGI gate and static/auth.js shim (plan 02)"
  - phase: 01-auth-core-and-test-script-plumbing
    provides: "routes/auth.py setup, login, logout, account, password change (plan 03)"
provides:
  - "Gate switched on in the real app: AuthMiddleware installed at import time, OpenAPI pages disabled, auth and health routers included"
  - "watermeter/routes/health.py: public /health, payload unchanged, moved out of routes/service.py"
  - "auth.js loaded on every page through _theme.html; Account link and POST Logout button in the nav (icon-only on mobile)"
  - "Unit fixtures test_client (logged in through a real auth.json and minted cookie), anon_client, anon_client_configured, session-scoped auth_password_hash"
  - "tests/unit/test_route_inventory.py: default-deny proof over 96 route/method pairs plus a route added after import"
  - "_auth_startup in the lifespan: env seed, setup-token log, open-setup and missing-volume warnings, never raises"
  - "scripts/auth_smoke.sh and a CI smoke step that seeds a random password and asserts the gate before the image is pushed"
affects: [01-05 integration fixtures and scripts, 02 hardening, 03 HA visibility, 04 dashboard banners]

actuals:
  tokens: 8500
  tasks: 3
  commits: 4

plan_head_before: 08de1d8c3a9971bf5580f35ab7ad0517d577f324
plan_head_after: 41083e73f3d2193033bf9f5259fd5a16a0e0f586

tech-stack:
  added: []
  patterns:
    - "App-level add_middleware(AuthMiddleware) right after FastAPI(...): new routers need no per-route dependency"
    - "Tests authenticate through a real auth.json and a cookie minted with auth.issue_token; no bypass flag, no dependency_overrides"
    - "Startup steps that touch auth are wrapped so they cannot stop the reading loop (D-07)"
    - "CI smoke logic lives in a script so CI and local runs execute the same checks"

key-files:
  created:
    - watermeter/routes/health.py
    - scripts/auth_smoke.sh
    - tests/unit/test_auth_wiring.py
    - tests/unit/test_route_inventory.py
    - tests/unit/test_auth_nav.py
    - tests/unit/test_auth_startup.py
  modified:
    - watermeter/app.py
    - watermeter/routes/service.py
    - watermeter/templates/_theme.html
    - watermeter/templates/_nav.html
    - tests/unit/conftest.py
    - .github/workflows/docker.yml

key-decisions:
  - "_auth_startup logs the name of the variable that supplied the seed (info) and never the value, because auth.bootstrap_from_env only says 'seeded from the environment'"
  - "The volume warning is skipped when either WATERMETER_ADMIN_PASSWORD or WATERMETER_ADMIN_PASSWORD_FILE is set"
  - "Unit conftest imports watermeter.auth and watermeter.ratelimit at module level after the mock block (both are stdlib-only leaves)"

patterns-established:
  - "Route inventory: walk app.routes, skip Mount, replace {param} with a literal, expect 303 or 401 for every non-allowlisted method and path"
  - "Late-route test removes its route in finally so other tests see the original app"

requirements-completed: [AUTH-01, AUTH-02, AUTH-04, AUTH-06, AUTH-07, AUTH-08, AUTH-09, AUTH-14, AUTH-15]

coverage:
  - id: D1
    description: "Real app is gated: fresh install redirects pages to /setup and answers /api with 401 setup_required, setup logs the owner in and opens the API, a configured install redirects pages to /login?next= and htmx polls get 401 with HX-Redirect"
    requirement: "AUTH-06"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_wiring.py#test_setup_then_api_round_trip"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_wiring.py#test_htmx_poll_gets_401_with_hx_redirect"
        status: pass
    human_judgment: false
  - id: D2
    description: "Login returns the browser to the originally requested page including its query (AUTH-07)"
    requirement: "AUTH-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_wiring.py#test_anonymous_page_on_configured_install_keeps_path_and_query"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_wiring.py#test_login_returns_to_the_requested_page"
        status: pass
    human_judgment: false
  - id: D3
    description: "/docs, /redoc and /openapi.json are not served; /health is unchanged and open in every auth state; /setup is closed after setup"
    requirement: "AUTH-08"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_wiring.py#test_docs_pages_are_gone_for_a_logged_in_client"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_wiring.py#test_health_is_open_with_unchanged_payload_when_unconfigured"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_wiring.py#test_setup_is_closed_for_the_logged_in_owner"
        status: pass
    human_judgment: false
  - id: D4
    description: "Every registered route and any route added later refuses anonymous callers with 303 or 401, configured and unconfigured, with at least 85 route/method pairs checked"
    requirement: "AUTH-15"
    verification:
      - kind: unit
        ref: "tests/unit/test_route_inventory.py#test_configured_instance_denies_every_non_allowlisted_route"
        status: pass
      - kind: unit
        ref: "tests/unit/test_route_inventory.py#test_unconfigured_instance_sends_everything_to_setup"
        status: pass
      - kind: unit
        ref: "tests/unit/test_route_inventory.py#test_route_added_after_import_is_denied_the_same_way"
        status: pass
    human_judgment: false
  - id: D5
    description: "Every page template includes _theme.html (which loads /static/auth.js) and the nav carries an Account link and a POST Logout form"
    requirement: "AUTH-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_nav.py"
        status: pass
    human_judgment: false
  - id: D6
    description: "Startup seeds the password from the environment only when none exists, logs the setup token, warns about open setup and a missing /data volume, and cannot stop the lifespan"
    requirement: "AUTH-09"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_startup.py"
        status: pass
    human_judgment: false
  - id: D7
    description: "CI smoke step seeds a random password and checks health, anonymous 401, login 303, authenticated 200 and wrong-password 401 through scripts/auth_smoke.sh"
    requirement: "AUTH-14"
    verification:
      - kind: other
        ref: "bash scripts/auth_smoke.sh http://localhost:8004 <seed> against a throwaway container built from this tree: 5 PASS lines"
        status: pass
    human_judgment: false
  - id: D8
    description: "Account and Logout fit the desktop rail and the mobile bottom bar at 360 and 390 px in light and dark themes"
    verification: []
    human_judgment: true
    rationale: "Layout and fit at real breakpoints cannot be asserted by a unit test; plan 05 verifies it on the debug container"

duration: 14min
completed: 2026-10-23
status: complete
---

# Phase 1 Plan 04: Gate Switch-On Summary

**Default-deny AuthMiddleware wired into the real app with OpenAPI disabled, the auth shim and Account/Logout nav on every page, a logged-in unit fixture, a 96-pair route-inventory proof, a never-raising startup seed, and a CI smoke test that checks the gate in the built image before it is pushed**

## Performance

- **Duration:** about 14 min
- **Started:** about 2026-10-23T08:29Z (not recorded at start; estimated from the previous plan's end time)
- **Completed:** 2026-10-23T08:44Z
- **Tasks:** 3
- **Files modified:** 12 (6 created, 6 edited)

## Accomplishments
- `watermeter/app.py`: `app.add_middleware(AuthMiddleware)` directly after `FastAPI(...)`, with `docs_url`, `redoc_url` and `openapi_url` set to `None`; `auth_router` and `health_router` included before `pages_router`. There is no flag, env var or override that skips the gate.
- `/health` now lives in `watermeter/routes/health.py` with the same decorator arguments and payload; it is gone from `routes/service.py`.
- `tests/unit/conftest.py`: `test_client` is logged in by default (tmp `auth.json`, cookie from `auth.issue_token`, `X-Requested-With: watermeter`); `anon_client` (setup state) and `anon_client_configured` (no cookie) cover the gate tests; the scrypt hash is computed once per session. Developer shell variables and the throttle state cannot leak into tests.
- `_theme.html` loads `/static/auth.js` on all ten page templates; `_nav.html` gets an Account link and a `POST /logout` button, icon-only in the mobile bar (44 px slots, `aria-label` and `title` keep them accessible).
- `test_route_inventory.py` walks `app.routes` (96 route/method pairs today) in the configured and unconfigured state, and proves a route added after import is refused the same way.
- `_auth_startup()` runs first in `lifespan`: env seed (seed-only), setup token logged when required, WARNING when setup is open, WARNING when the data directory is not a mount point and no seed is configured. Everything is wrapped so a failure cannot stop inference, MQTT, the stats loop or the cyclic trigger.
- `scripts/auth_smoke.sh` plus the rewritten `docker.yml` smoke step (random `openssl rand -hex 16` password, container logs dumped on failure, pinned action SHAs untouched).
- Verification: `tests/unit` plus `tests/regression` 1281 passed (1237 after plan 03, +44 new). `uvx ruff check watermeter/ tests/` clean, `uvx black --check` clean on all seven checked Python files.

## Task Commits

1. **Task 1: Tracer - real app gated end to end** - `29086d3` (feat). The tracer feedback gate re-ran the full unit and regression suite (1253 passed) before expansion.
2. **Task 2: Shim on every page, Account and Logout, route inventory** - `96a4197` (feat)
3. **Task 3: Startup seed, setup-token log, warnings, CI smoke** (tdd)
   - RED `ea88ca8` (test): 3 failed and 6 errors, because `_auth_startup` and `watermeter.app.os` did not exist yet
   - GREEN `41083e7` (feat): 9 passed, plus the script and the workflow edit

**Plan metadata:** committed with this summary (docs: complete plan)

## Files Created/Modified
- `watermeter/app.py` - middleware, docs disabled, routers, `_auth_startup`
- `watermeter/routes/health.py` - public `/health`
- `watermeter/routes/service.py` - `/health` removed, docstring updated
- `watermeter/templates/_theme.html`, `_nav.html` - shim include, Account and Logout items, nav CSS in the existing style block
- `tests/unit/conftest.py` - logged-in and anonymous client fixtures
- `tests/unit/test_auth_wiring.py`, `test_route_inventory.py`, `test_auth_nav.py`, `test_auth_startup.py` - 44 new tests
- `scripts/auth_smoke.sh` - reusable smoke check (mode 100755)
- `.github/workflows/docker.yml` - smoke step "Smoke test (health, auth gate, login)"

## Decisions Made
- The seed log line names the variable (`WATERMETER_ADMIN_PASSWORD` or `_FILE`) that supplied the password and never the value. `auth.bootstrap_from_env` itself only logs "seeded from the environment", so the app-level line adds the name the behavior spec asked for.
- The missing-volume warning is skipped when either seed variable is set, since the password then survives container recreation.
- The route-inventory count assertion is written as a literal `>= 85`, matching the plan's acceptance wording; the current count is 96.

## Deviations from Plan

None - plan executed exactly as written.

### Process notes (not code deviations)

- **Local replay of the CI smoke.** `docker build -t watermeter-smoke:local .` then a throwaway container on port 8004 (not 8001, never the production container) with a random `openssl rand -hex 16` seed. Output:
  ```
  PASS health: /health answered after 4 tries
  PASS anonymous /api/status is refused: HTTP 401
  PASS login with the seeded password: HTTP 303
  PASS authenticated /api/status: HTTP 200
  PASS wrong password is refused: HTTP 401
  All auth smoke checks passed
  ```
  The container log showed neither the "No admin password is set" warning nor the persistent-volume warning, and `/data/auth.json` existed with mode 0600 owned by `watermeter`, which is the evidence for the seed. The seed INFO line itself does not appear in `docker logs` because the app's INFO lines are below the default log level in the container (the existing "Starting Water Meter Dashboard..." INFO line is absent too); warnings do appear. The container was removed afterwards (`docker ps -a --filter name=watermeter-auth-smoke -q` prints nothing).
- **Protected-branch commit guard.** As in plans 01 to 03, the dispatch authorizes committing on `main`; `.planning/config.json` was not touched and nothing was pushed. Every commit subject starts with `claude: `.
- **TDD.** `workflow.tdd_mode` is off and the plan is `type: execute`, so `check tdd-red-evidence` was not run. The Task 3 RED failures were the missing function and the missing `os` import in `watermeter.app`, which is the missing implementation, not a syntax or fixture error.
- **Pre-existing flaky test.** `tests/unit/test_mqtt_reconnect.py::test_reconnect_uses_exponential_backoff` failed once on the baseline run before any change (it asserts 4 sleep calls after a fixed 0.3 s real sleep, so it depends on thread timing) and passed in all later runs. It is unrelated to this plan and was left alone.

---

**Total deviations:** 0 auto-fixed.
**Impact on plan:** None. `files_modified` matches the plan exactly.

## Issues Encountered
None.

## Known Stubs
None.

## Threat Flags
None. The new surface (gate on all routes, setup and login pages) is covered by T-01-26 to T-01-31. `/health` is unchanged and public as before.

## User Setup Required
None - no external service configuration required. Do not push yet: every push to `main` publishes `:dev`, and the plan says to push only after the whole plan verification passes (it does) and with plan 05 still pending for the live debug-container check.

## Next Phase Readiness
- Plan 05 can build on `test_client`, `anon_client` and `anon_client_configured` for the integration fixtures, and on `scripts/auth_smoke.sh` for the debug scripts.
- Plan 05 should do the visual check of the nav at 360 and 390 px in light and dark on the debug container (deliverable D8, `human_judgment: true`). With the calibration tab visible there are 10 items in the mobile bar; the labels clip with an ellipsis but the layout is untested at that width.
- `docs_url`, `redoc_url` and `openapi_url` are `None`, so `tags_metadata` is now inert; leaving it was the plan's decision.

## Self-Check: PASSED

- Files found: `watermeter/routes/health.py`, `scripts/auth_smoke.sh`, `tests/unit/test_auth_wiring.py`, `tests/unit/test_route_inventory.py`, `tests/unit/test_auth_nav.py`, `tests/unit/test_auth_startup.py`, and the six edited files.
- Commits found in `git log`: `29086d3`, `96a4197`, `ea88ca8`, `41083e7`; `git rev-list --count 08de1d8..HEAD` gave 4 before this summary.
- Acceptance criteria re-run: wiring, nav, inventory and startup tests pass (44 new); `openapi_url=None` and `app.add_middleware(AuthMiddleware)` each print one line; `"/health"` appears once in `routes/health.py` and 0 times in `routes/service.py`; `def anon_client` prints 2 lines; `dependency_overrides` prints nothing; `src="/static/auth.js"`, `action="/logout"`, `href="/account"` each print one line; `await _auth_startup()` prints one line inside `lifespan`; `WATERMETER_ADMIN_PASSWORD`, `scripts/auth_smoke.sh` and `openssl rand` appear in `docker.yml`; `git ls-files -s scripts/auth_smoke.sh` shows 100755; the local replay printed five PASS lines (four required).
- Plan verification: `.venv/bin/python -m pytest tests/unit tests/regression -q` gave 1281 passed; ruff clean; black check clean; `bash -n scripts/auth_smoke.sh` and a YAML parse of `docker.yml` succeed.

---
*Phase: 01-auth-core-and-test-script-plumbing*
*Completed: 2026-10-23*
