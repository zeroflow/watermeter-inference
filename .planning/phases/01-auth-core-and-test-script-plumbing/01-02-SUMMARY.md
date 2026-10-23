---
phase: 01-auth-core-and-test-script-plumbing
plan: 02
subsystem: auth
tags: [asgi, middleware, csrf, htmx, session-cookie, fetch-shim]

requires:
  - phase: 01-auth-core-and-test-script-plumbing
    provides: "watermeter/auth.py store, tokens, cookie builders and redirect helpers (plan 01)"
provides:
  - "watermeter/auth_middleware.py: pure-ASGI AuthMiddleware (state allowlist, CSRF check, 401/303 shaping by caller, sliding cookie refresh, websocket close 1008, fail-closed 503)"
  - "watermeter/static/auth.js: fetch + XMLHttpRequest shim adding X-Requested-With and redirecting to /login on a same-origin 401"
  - "WATERMETER_ALLOWED_ORIGINS consumed by the Origin check (comma-separated, for proxies that rewrite Host)"
affects: [01-03 auth routes, 01-04 app wiring and templates, 01-05 script and CI plumbing]

actuals:
  tokens: 11800
  tasks: 3
  commits: 5

plan_head_before: 42acb7518680d301cdaafcd5c0216b687258bc73
plan_head_after: 3ba1e2715f8060a7877ff361e6b73131d9897239

tech-stack:
  added: []
  patterns:
    - "Leaf pure-ASGI middleware (an AST test forbids importing the throttle or the service facade)"
    - "CSRF verdict is a pure function of the request's own headers and cookie validity, no shared state"
    - "Response-start send wrapper that appends Set-Cookie only when the response does not already set that cookie"
    - "Node behavior check run from pytest with a vm sandbox (fake window, fetch, XHR, location)"

key-files:
  created:
    - watermeter/auth_middleware.py
    - watermeter/static/auth.js
    - tests/unit/test_auth_middleware.py
    - tests/unit/js/auth_shim_check.js
    - tests/unit/test_auth_js.py
  modified: []

key-decisions:
  - "next values in Location and HX-Redirect are fully percent-encoded (quote safe=''), matching the plan's concrete expected strings"
  - "Origin with an empty netloc (for example the literal text 'evil') is refused instead of matching an empty Host"
  - "auth.js folds init into a rebuilt Request for Request inputs so the body survives and headers are not applied twice"
  - "XHR loadend 401 handling skips responses that carry HX-Redirect so htmx navigates once with the page-derived next"

patterns-established:
  - "Evaluation order: scope type, store state, session lookup, CSRF, allowlist, session pass, deny"
  - "Allowlist predicate _is_allowed(state, path) is exact and case-sensitive; method never matters"

requirements-completed: [AUTH-06, AUTH-07, AUTH-08, AUTH-12, AUTH-13]

coverage:
  - id: D1
    description: "Default-deny gate by store state with exact allowlist; unknown paths, /docs and /openapi.json denied before routing; damaged auth.json and store exceptions fail closed with 503"
    requirement: "AUTH-06"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_configured_unknown_and_docs_paths_are_denied_before_routing"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_empty_auth_file_fails_closed"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_near_miss_paths_are_protected"
        status: pass
    human_judgment: false
  - id: D2
    description: "401 contract by caller: 303 for pages, empty-body 401 plus HX-Redirect built from HX-Current-URL for htmx, JSON 401 with setup_required for /api"
    requirement: "AUTH-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_configured_htmx_gets_401_empty_body_and_hx_redirect_from_current_page"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_expired_session_polls_are_all_401_with_hx_redirect"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_middleware_module_is_a_leaf"
        status: pass
    human_judgment: false
  - id: D3
    description: "CSRF: Origin/Sec-Fetch-Site check on unsafe methods before the gate, X-Requested-With required on cookie-authenticated /api writes, allowed-origins env, X-Forwarded-Host never read"
    requirement: "AUTH-08"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_csrf_runs_before_the_setup_allowlist"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_csrf_forwarded_host_is_never_trusted"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_csrf_allowed_origins_env_accepts_a_proxy_origin"
        status: pass
    human_judgment: false
  - id: D4
    description: "Per-instance cookie lookup (exact name, case-sensitive) and sliding 30-day refresh after 15 days that never overrides a response which already sets the cookie; malformed cookies are anonymous"
    requirement: "AUTH-12"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_other_instance_cookie_is_ignored"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_refresh_does_not_undo_logout"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_two_requests_with_the_same_old_token_both_get_a_working_refresh"
        status: pass
    human_judgment: false
  - id: D5
    description: "Refreshed cookie keeps HttpOnly, SameSite=Lax, Max-Age and sets Secure only when the scope scheme is https"
    requirement: "AUTH-13"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_middleware.py#test_refresh_adds_secure_over_https"
        status: pass
    human_judgment: false
  - id: D6
    description: "auth.js adds the CSRF header to same-origin unsafe fetch (string, URL, Request) and XHR calls and redirects to /login?next on same-origin 401 except on /login, /setup and HX-Redirect responses"
    requirement: "AUTH-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_js.py#test_auth_js_shim_behavior"
        status: pass
      - kind: other
        ref: "node tests/unit/js/auth_shim_check.js watermeter/static/auth.js"
        status: pass
    human_judgment: false
  - id: D7
    description: "Shim behaves correctly in real browsers (Chrome, Firefox, Safari) with htmx 1.9.10 and the ZIP upload; the middleware wired into the real app"
    verification: []
    human_judgment: true
    rationale: "The node check uses fakes, and nothing is wired into app.py or templates in this plan. The real-browser pass and the route-inventory test belong to plans 04 and 05 on the debug container."

duration: 7min
completed: 2026-10-23
status: complete
---

# Phase 1 Plan 02: Auth Middleware and Browser Shim Summary

**Pure-ASGI default-deny AuthMiddleware (state allowlist, Origin/Sec-Fetch-Site plus X-Requested-With CSRF, 303/401+HX-Redirect/JSON-401 shaping, 15-day sliding cookie refresh) and a vanilla-JS fetch/XHR shim, both proven on a harness app and a node behavior check, still unwired from the real app**

## Performance

- **Duration:** 7 min (start time not captured at launch; estimated from the previous plan's end)
- **Started:** 2026-10-23T08:18Z (approximate)
- **Completed:** 2026-10-23T08:25Z
- **Tasks:** 3
- **Files modified:** 5 (all created)

## Accomplishments
- `watermeter/auth_middleware.py` (190 lines): lifespan pass-through, websocket close 1008, three-state allowlist with exact case-sensitive matching, 503 for an unavailable store (also when the store itself raises), CSRF refusal before the allowlist so a cross-site POST to `/setup` is 403 even while unconfigured, deny shaping by caller, and a `send` wrapper that re-issues the session cookie after 15 days unless the response already sets it.
- `watermeter/static/auth.js` (130 lines, ES5): guarded IIFE wrapping `fetch` and patching `XMLHttpRequest.prototype.open/send`; adds `X-Requested-With: watermeter` to same-origin unsafe requests, redirects to `/login?next=<page>` on a same-origin 401 once per page, and leaves `HX-Redirect` responses to htmx.
- 79 middleware tests on a harness FastAPI app, plus a node vm check (25-odd scenarios) run by two pytest tests. Full suite: `tests/unit` plus `tests/regression` 1172 passed (baseline 1091 after plan 01). The middleware tests also pass on Starlette 1.9.0 in an isolated uv environment (local venv has 0.52.1).
- `watermeter/app.py` and `watermeter/templates/` untouched, so `main` is still shippable.

## Task Commits

1. **Task 1: Tracer - anonymous request denied by caller type, logged-in request passes** - `4f1586b` (feat)
2. **Task 2: CSRF check, sliding cookie refresh, allowed origins, edge cases**
   - RED `b769ba6` (test, 21 failed on new behavior), GREEN `8ace9bc` (feat, 79 passed)
3. **Task 3: Browser shim auth.js with a node behavior check**
   - RED `b4073b4` (test), GREEN `3ba1e27` (feat)

The tracer feedback gate ran after Task 1 (`<verify>` re-run, 28 passed) before expansion. No refactor commits were needed.

**Plan metadata:** committed with this summary (docs: complete plan)

## Files Created/Modified
- `watermeter/auth_middleware.py` - `AuthMiddleware`, `_is_allowed`, `_csrf_refusal`, `_allowed_origins`, `_deny`
- `watermeter/static/auth.js` - fetch and XHR shim
- `tests/unit/test_auth_middleware.py` - harness app and 79 tests
- `tests/unit/js/auth_shim_check.js` - node vm behavior check
- `tests/unit/test_auth_js.py` - pytest wrapper (syntax check and behavior check, skips without node)

## Decisions Made
- `next` is percent-encoded completely (`quote(..., safe="")`), so the `Location` is `/login?next=%2Ftraining%3Fa%3D1` and the `HX-Redirect` is `/login?next=%2Ftraining`, exactly the strings the plan's test descriptions give.
- Origin matching compares netloc only and requires a non-empty netloc; an unparseable Origin is refused.
- For `Request` inputs `auth.js` rebuilds the request with `init` folded in (and passes no second `init`), because headers in `init` replace the Request's own headers in the fetch spec.
- `HX-Redirect` exemption in the XHR 401 handler keeps htmx to a single navigation with the page-derived `next`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan text contradicted itself on percent-encoding of `next`**
- **Found during:** Task 1
- **Issue:** The action text says `quote(..., safe="/")`, which yields `/login?next=/training`, while the same plan's test description and truths require `/login?next=%2Ftraining` and `%2Ftraining%3Fa%3D1`.
- **Fix:** Used `safe=""` (full encoding), which is also the correct form for a query value. The tests assert the concrete strings.
- **Files modified:** `watermeter/auth_middleware.py`
- **Verification:** `test_configured_page_redirects_to_login_with_next`, `test_configured_htmx_gets_401_empty_body_and_hx_redirect_from_current_page`
- **Committed in:** `4f1586b`

**2. [Rule 2 - Missing Critical] Origin with an empty netloc could match an empty Host**
- **Found during:** Task 2 GREEN
- **Issue:** A bare Origin such as `evil` parses to an empty netloc and would equal a missing Host header.
- **Fix:** Refuse when the netloc is empty, and refuse an unparseable Origin (`ValueError` from `urlsplit`).
- **Files modified:** `watermeter/auth_middleware.py`
- **Committed in:** `8ace9bc`

### Process notes (not code deviations)

- **Protected-branch commit guard.** As in plan 01, the executor's pre-commit assertion treats `main` as protected while `git.allow_default_branch_commits` is unset. The dispatch explicitly authorizes committing on `main` (project `CLAUDE.md` documents it), so I committed there, did not touch `.planning/config.json`, and pushed nothing.
- **Task 3 RED evidence.** The RED commit contains the node check and the pytest wrapper without `auth.js`. Before the GREEN commit I also ran the check against an empty stub file and it failed on the intended assertion (`__wmAuthShim` expected `true`, got `undefined`). `workflow.tdd_mode` is off and the plan is `type: execute`, so `check tdd-red-evidence` was not run.
- **Commit subjects** start with `claude: ` followed by the GSD `type(01-02):` scope; use `git log --grep='(01-02)'` to find them.
- **Start time** was not recorded at launch, so Started/Duration are estimates.

---

**Total deviations:** 2 auto-fixed (1 bug, 1 missing critical), plus the process notes.
**Impact on plan:** Both fixes are needed for the stated truths. No scope creep; `files_modified` matches the plan exactly.

## Issues Encountered
None. All acceptance greps print their lines; the HX-Redirect test asserts the value does not contain `api`.

## Known Stubs
None.

## Threat Flags
None. The new surface (Origin/Host comparison, cookie refresh, shim) is covered by T-01-11 to T-01-18 in the plan. T-01-17 (cookie on plain-HTTP LAN) stays accepted.

## User Setup Required
None. `WATERMETER_ALLOWED_ORIGINS` is optional and nothing reads it until plan 04 wires the middleware into the app.

## Next Phase Readiness
- Plan 03 (routes and pages) can rely on the harness-proven contract: `/login` and `/logout` are allowlisted when configured, `/setup` when unconfigured, and the middleware never imports the throttle, so the login route owns failure counting.
- Plan 04 must add the middleware with `app.add_middleware(AuthMiddleware)`, load `/static/auth.js` from `_theme.html` (not on setup or login) and add the route-inventory test. Until then no template references the shim.
- Worth checking in the real-browser pass (plan 05): htmx 1.9.10 following `HX-Redirect` on an empty-body 401, the Origin check over plain HTTP on a LAN IP, and the ZIP upload XHR carrying the header.

## Self-Check: PASSED

- Files found: `watermeter/auth_middleware.py`, `watermeter/static/auth.js`, `tests/unit/test_auth_middleware.py`, `tests/unit/js/auth_shim_check.js`, `tests/unit/test_auth_js.py`.
- Commits found in `git log`: `4f1586b`, `b769ba6`, `8ace9bc`, `b4073b4`, `3ba1e27`.
- Acceptance criteria re-run: 79 middleware tests pass (needs 35); `class AuthMiddleware`, `HX-Redirect`, `1008`, `sec-fetch-site`, `SESSION_REFRESH_AFTER` and `__wmAuthShim` greps print lines; `tests/unit/test_auth_js.py` reports 2 passed (not skipped); no commit of this plan touches `watermeter/templates/` or `watermeter/app.py`.
- Plan verification: `.venv/bin/python -m pytest tests/unit tests/regression -q` 1172 passed; `uvx ruff check watermeter/ tests/` clean; `uvx black --check` clean on the three new Python files; `node tests/unit/js/auth_shim_check.js watermeter/static/auth.js` prints `auth.js shim: all checks passed`; `git status --porcelain watermeter/app.py watermeter/templates/` empty.

---
*Phase: 01-auth-core-and-test-script-plumbing*
*Completed: 2026-10-23*
