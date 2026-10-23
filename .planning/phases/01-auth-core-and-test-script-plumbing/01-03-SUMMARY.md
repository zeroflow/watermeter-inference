---
phase: 01-auth-core-and-test-script-plumbing
plan: 03
subsystem: auth
tags: [fastapi, jinja2, forms, throttle, setup-token, password-change, css-variables]

requires:
  - phase: 01-auth-core-and-test-script-plumbing
    provides: "watermeter/auth.py store, hashing, tokens, cookie builders, password policy, safe_next, setup token (plan 01)"
  - phase: 01-auth-core-and-test-script-plumbing
    provides: "watermeter/ratelimit.py FailureLimiter (plan 01)"
provides:
  - "watermeter/routes/auth.py: GET/POST /setup, GET/POST /login, POST /logout, GET /account, POST /api/auth/password and a module-level throttle (limiter)"
  - "setup.html and login.html: themed centered cards without the nav rail; account.html: change-password form and logout inside the normal layout"
  - "static/account.js: change-password fetch with result and throttle messages"
  - "Auth page classes in style.css built only on the theme variables (light and dark)"
affects: [01-04 app wiring and gate, 01-05 script and CI plumbing]

actuals:
  tokens: 12200
  tasks: 3
  commits: 5

plan_head_before: a549161dd0201233ad35f89252ffafac7f489163
plan_head_after: 93dda53a4808838528634259e5618c633342195d

tech-stack:
  added: []
  patterns:
    - "Auth router owns its Jinja2Templates instance so a stubbed pages template never affects the auth pages"
    - "Form bodies read with a byte-counted stream and parse_qs (no python-multipart), cap 16 KiB with or without Content-Length"
    - "Throttle pre-check before any hashing; record_failure only on a wrong password or wrong setup token"
    - "Blocking scrypt calls run in asyncio.to_thread"

key-files:
  created:
    - watermeter/routes/auth.py
    - watermeter/templates/setup.html
    - watermeter/templates/login.html
    - watermeter/templates/account.html
    - watermeter/static/account.js
    - tests/unit/test_auth_routes.py
  modified:
    - watermeter/static/style.css

key-decisions:
  - "Upgrade notice (D-06) is shown when reference_raw.jpg or state.json exists next to auth.json"
  - "A new password equal to the current one is accepted and still bumps the epoch"
  - "Wrong current password on the change-password API answers 403 because auth.js treats 401 as an expired session"
  - "The setup token check runs before password validation, so a stranger without the token learns nothing about the policy"
  - "The throttle pre-check for the setup token uses the key setup:<ip>, login uses <ip>, password change uses pw:<ip>"

patterns-established:
  - "Auth responses carry Cache-Control: no-store via _render"
  - "Redirect targets always pass through auth.safe_next"
  - "Log lines name the client IP and never a submitted password; only the setup token is logged on purpose (D-09)"

requirements-completed: [AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, AUTH-11, AUTH-13, AUTH-14]

coverage:
  - id: D1
    description: "First-run setup: themed card without nav, password and confirm, creates auth.json, logs the owner in and redirects to /, closed once configured, lost race redirects without a cookie"
    requirement: "AUTH-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_setup_creates_auth_file_and_logs_in"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_setup_closed_after_setup"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_setup_lost_race_redirects_without_cookie"
        status: pass
    human_judgment: false
  - id: D2
    description: "Password policy at the edges (8 and 1024 accepted, 7 and 1025 refused, one-character mismatch, empty or missing fields) and the 16 KiB body cap with and without Content-Length"
    requirement: "AUTH-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_setup_accepts_boundary_lengths"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_setup_rejects_out_of_range_lengths"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_oversized_form_is_rejected_without_content_length"
        status: pass
    human_judgment: false
  - id: D3
    description: "Login: generic Wrong password with 401, 429 with Retry-After and no hashing while throttled, redirect to a validated next, recovery hint, failure-only counting, log lines without the attempted password"
    requirement: "AUTH-11"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_throttled_login_gets_429_without_hashing"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_only_wrong_credentials_count_as_failures"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_failed_login_is_logged_with_ip_but_not_the_password"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_login_ignores_unsafe_next"
        status: pass
    human_judgment: false
  - id: D4
    description: "Logout clears the session cookie and redirects to /login"
    requirement: "AUTH-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_logout_clears_cookie"
        status: pass
    human_judgment: false
  - id: D5
    description: "Account page in the normal layout with change-password form and logout button; password change keeps this session, ends all others, throttles a wrong current password under pw:<ip> with 403"
    requirement: "AUTH-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_password_change_keeps_this_session_and_ends_others"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_wrong_current_password_is_403_throttled_and_logged"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_account_page_has_forms_nav_and_no_session_details"
        status: pass
    human_judgment: false
  - id: D6
    description: "Opt-in setup token (field only when required, 403 plus throttle plus token logged on a wrong or missing token) and the upgrade notice only on installs with meter data"
    requirement: "AUTH-14"
    verification:
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_setup_wrong_token_is_forbidden_and_logged"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_setup_token_failures_are_throttled"
        status: pass
      - kind: unit
        ref: "tests/unit/test_auth_routes.py#test_upgrade_notice_shown_when_meter_data_exists"
        status: pass
    human_judgment: false
  - id: D7
    description: "Visual result of the setup, login and account pages in light and dark themes on a phone and a desktop browser, and the account.js interaction in a real browser"
    verification: []
    human_judgment: true
    rationale: "Tests assert markup and behavior on a harness app; layout, contrast and the fetch flow need eyes on the debug container once plan 04 wires the router (plan 05 verification)"

duration: 5min
completed: 2026-10-23
status: complete
---

# Phase 1 Plan 03: Auth Pages and Endpoints Summary

**Setup, login, logout, account and change-password routes with themed card templates, a 16 KiB form cap, failure-only throttling before hashing, an opt-in setup token and an epoch-bumping password change that keeps the changing browser logged in, all proven on a harness app and not yet wired into `app.py`**

## Performance

- **Duration:** 5 min
- **Started:** 2026-10-23T08:27:20Z
- **Completed:** 2026-10-23T08:32:05Z
- **Tasks:** 3
- **Files modified:** 7 (6 created, 1 edited)

## Accomplishments
- `watermeter/routes/auth.py` (about 330 lines): seven routes. Forms are read as bytes with a hard cap and parsed with `parse_qs`, so the 16 KiB limit holds for streamed bodies without Content-Length and no new dependency is needed. Hashing and verification run in `asyncio.to_thread`. The throttle is checked before any hashing and only a wrong password or wrong setup token is recorded.
- `setup.html`, `login.html`: standalone documents with the `_theme.html` include, no nav, no htmx, no inline styles, hidden `username` input for password managers, the recovery link on the login card and the upgrade notice and token field on the setup card.
- `account.html` and `account.js`: password-change form and a separate logout form inside the normal layout; no session details shown.
- `POST /api/auth/password`: current password verified (1024-character cap), new pair validated, `replace_password` bumps the epoch, and the response carries a cookie for the new epoch so this browser stays logged in while every other token fails `verify_token`.
- `style.css`: `.auth-page`, `.auth-card`, `.auth-lead`, `.auth-form`, `.auth-error`, `.auth-notice`, `.auth-success`, `.auth-hint`, `.visually-hidden`, `.account-panel`, all on existing theme variables.
- 65 new route tests. `tests/unit` plus `tests/regression`: 1237 passed (baseline 1172 after plan 02). `uvx ruff check watermeter/ tests/` clean, `uvx black --check` clean on the two new Python files, `node --check watermeter/static/account.js` clean. `watermeter/app.py`, `_nav.html` and `_theme.html` untouched, so `main` is still shippable.

## Task Commits

1. **Task 1: Tracer - setup, login and logout on a harness app with real templates** - `885b174` (feat)
2. **Task 2: Setup and login rules**
   - RED `d4bf4eb` (test, 7 failed on the token and notice behavior, 42 already passed), GREEN `717d903` (feat, 49 passed)
3. **Task 3: Account page and password change**
   - RED `f0351b3` (test, 16 failed: route and template missing), GREEN `93dda53` (feat, 65 passed)

The tracer feedback gate ran after Task 1 (`<verify>` re-run, 6 passed) before expansion. No refactor commits were needed.

**Plan metadata:** committed with this summary (docs: complete plan)

## Files Created/Modified
- `watermeter/routes/auth.py` - routes, `limiter`, `PasswordChangeRequest`, helpers
- `watermeter/templates/setup.html`, `login.html`, `account.html` - the three pages
- `watermeter/static/account.js` - change-password fetch
- `watermeter/static/style.css` - auth section appended (125 lines)
- `tests/unit/test_auth_routes.py` - harness fixtures and 65 tests

## Decisions Made
- Upgrade detection looks for `reference_raw.jpg` or `state.json` in the directory of `auth.json`, the same signal `routes/pages.py` uses for first run.
- The setup token is checked before password validation; the throttle for it is keyed `setup:<ip>`.
- A same-password change is allowed and bumps the epoch, as the plan decided.
- The 403 for a wrong current password avoids the auth.js 401 redirect, as the plan decided.

## Deviations from Plan

None - plan executed exactly as written.

### Process notes (not code deviations)

- **Protected-branch commit guard.** As in plans 01 and 02, the executor's pre-commit assertion treats `main` as protected while `git.allow_default_branch_commits` is unset. The dispatch explicitly authorizes committing on `main` (project `CLAUDE.md` documents it), so I committed there, did not touch `.planning/config.json` and pushed nothing.
- **Commit subjects** start with `claude: ` followed by the GSD `type(01-03):` scope; use `git log --grep='(01-03)'` to find them. The plan is `type: execute` and `workflow.tdd_mode` is off, so `check tdd-red-evidence` was not run. Task 2 RED failures were assertions on the new token and notice behavior; Task 3 RED failures were 404 responses and a missing template for routes that did not exist yet.
- **Pre-existing behavior kept.** Many Task 2 behaviors (body cap, throttle, redirect safety, logging) were already specified in Task 1's action text, so they passed at RED and serve as regression coverage rather than RED drivers.

---

**Total deviations:** 0 auto-fixed.
**Impact on plan:** None. `files_modified` matches the plan exactly.

## Issues Encountered
None. `uvx black` reformatted the new Python files between edits (allowed on new files only).

## Known Stubs
None.

## Threat Flags
None. The surface added (setup, login, logout, account, password change) is covered by T-01-19 to T-01-25. The setup token being written to the log is the planned D-09 behavior.

## User Setup Required
None - no external service configuration required. The router is not included in `app.py`, so none of these endpoints is reachable in the running app yet.

## Next Phase Readiness
- Plan 04 includes `routes_auth.router` in `app.py`, adds the middleware, and loads `/static/auth.js` from `_theme.html`. The setup and login templates also include `_theme.html`, so they will load the shim too; the shim already skips its 401 redirect on `/login` and `/setup`.
- Until Plan 04 adds the gate, `/api/auth/password` performs no session check of its own: it relies on `AuthMiddleware` (the path is not on the allowlist, so it is protected once wired). The route-inventory test planned for Plan 04 should cover `/account` and `/api/auth/password`.
- Worth checking on the debug container in plan 05: theme look of the three pages in light and dark, the account.js flow with the CSRF header from auth.js, and the 403 (not 401) behavior in a real browser.

## Self-Check: PASSED

- Files found: `watermeter/routes/auth.py`, `watermeter/templates/setup.html`, `watermeter/templates/login.html`, `watermeter/templates/account.html`, `watermeter/static/account.js`, `tests/unit/test_auth_routes.py`, `watermeter/static/style.css` (modified).
- Commits found in `git log`: `885b174`, `d4bf4eb`, `717d903`, `f0351b3`, `93dda53`.
- Acceptance criteria re-run: 65 route tests pass (needs 33); `class PasswordChangeRequest` and `"/api/auth/password"` greps print one line each; `limiter.record_failure(` count is 3; no `style=` in the three new templates; `register_template_globals(templates)`, `asyncio.to_thread(auth.verify_password`, `Retry-After`, `name="token"`, `auth-notice` and `.auth-card` greps print lines; `node --check watermeter/static/account.js` exits 0.
- Plan verification: `.venv/bin/python -m pytest tests/unit tests/regression -q` gave 1237 passed; `uvx ruff check watermeter/ tests/` clean; `uvx black --check watermeter/routes/auth.py tests/unit/test_auth_routes.py` clean; `git status --porcelain watermeter/app.py watermeter/templates/_nav.html watermeter/templates/_theme.html` empty and no commit of this plan touches them.

---
*Phase: 01-auth-core-and-test-script-plumbing*
*Completed: 2026-10-23*
