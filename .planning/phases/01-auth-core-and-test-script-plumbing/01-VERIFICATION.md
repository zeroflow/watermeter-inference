---
phase: 01-auth-core-and-test-script-plumbing
verified: 2026-10-23T20:45:00Z
status: human_needed
score: 5/5 roadmap success criteria verified (15/15 requirements satisfied at code and live-instance level); 0 failed
covered_files:
  - .github/workflows/docker.yml
  - DOCKER.md
  - README.md
  - debug.sh
  - debug_clean.sh
  - scripts/auth_smoke.sh
  - tests/conftest.py
  - tests/integration/conftest.py
  - tests/integration/test_smoke.py
  - tests/unit/conftest.py
  - watermeter/__main__.py
  - watermeter/app.py
  - watermeter/auth.py
  - watermeter/auth_middleware.py
  - watermeter/ratelimit.py
  - watermeter/routes/auth.py
  - watermeter/routes/health.py
  - watermeter/static/account.js
  - watermeter/static/auth.js
  - watermeter/templates/_nav.html
  - watermeter/templates/_theme.html
  - watermeter/templates/account.html
  - watermeter/templates/login.html
  - watermeter/templates/setup.html
covered_digest: "v2:sha256:9bc3cd05232ac8291697a39b254e72e0051ce796476918ae7c25080826e852ed"
behavior_unverified: 0
overrides_applied: 0
re_verification: false
gaps: []
deferred: []
unverified_prohibitions:
  - statement: "MUST NOT add an auth-disable switch, bypass flag, trusted-network exception or test-only path skip (plans 01, 02, 04; judgment tier)"
    verdict: "LLM judge, NON-AUTHORITATIVE: no such switch found (grep of watermeter/ for disable/bypass/skip_auth/dependency_overrides is empty; AuthMiddleware is added unconditionally at app.py:200)"
    flag: "unverified-prohibition - human review recommended"
  - statement: "MUST NOT show configuration values, readings, camera URL, MQTT details or the setup token on the unauthenticated setup and login pages (plan 03; judgment tier)"
    verdict: "LLM judge, NON-AUTHORITATIVE: login.html and setup.html render only error text, hidden next, token field and README links. The setup token is shown nowhere on the page; it is only written to the container log"
    flag: "unverified-prohibition - human review recommended"
warnings:
  - id: WR-01
    summary: "A bad or unreadable WATERMETER_ADMIN_PASSWORD seed leaves /setup open (reproduced: seed 'short' -> state unconfigured)"
  - id: WR-02
    summary: "AuthStore.create needs hard links (os.link) with no fallback; setup_submit has no OSError handler (confirmed in code, not reproduced on an exotic filesystem)"
  - id: WR-03
    summary: "More than 20 form fields on anonymous POST /login or /setup gives HTTP 500 (reproduced on :8002)"
  - id: WR-04
    summary: "Login throttle is keyed on the full IPv6 address, so a /64 holder gets a fresh key per guess (confirmed in code)"
human_verification:
  - test: "Open http://<LAN IP of this host>:8002 from another device over plain HTTP. Log in, watch the dashboard poll for a minute, press Read Now."
    expected: "Login works, session cookie has no Secure flag and is kept, polling updates, no 403 in the console."
    why_human: "Real Origin/Host/cookie behaviour from a browser over a LAN IP cannot be reproduced with curl (Sec-Fetch-Site and Origin are browser-set). Covers AUTH-03, AUTH-08, AUTH-13."
  - test: "Log in to :8002 and to a second instance (./debug_clean.sh --detach, :8003, password printed); log out of :8003."
    expected: "Two different wm_session_xxxxxxxx cookies exist and :8002 stays logged in."
    why_human: "Cookie coexistence across two live instances in one browser (AUTH-12). The code gives each instance a random 8-hex instance_id, but the browser interplay is only checkable in a browser."
  - test: "On :8002 delete the wm_session_ cookie in dev tools and wait for the next 5 s HTMX poll."
    expected: "Browser is sent to /login?next=/ and returns to the dashboard after login; no login HTML appears inside a fragment."
    why_human: "htmx processing of HX-Redirect and the auth.js fetch/XHR shim need a real browser (AUTH-07). curl confirmed the 401 + HX-Redirect response; the node shim test covers auth.js, but not htmx itself."
  - test: "Close and reopen the browser on :8002 (still logged in). Change the password on /account in browser A while browser B is logged in."
    expected: "A stays logged in; B is sent to /login on its next poll."
    why_human: "Browser-restart persistence and two-browser behaviour (AUTH-03, AUTH-05). curl confirmed the epoch bump (other session 401, changer 200) and restart persistence of the secret."
  - test: "Open http://localhost:8003/setup from ./debug_clean.sh --detach --setup in light and dark themes; run `docker exec -it -u watermeter watermeter-dashboard-debug python3 -m watermeter set-password` in a real terminal and log in to :8002 with the new password."
    expected: "Themed setup card without the nav rail in both themes; interactive TTY prompt asks for the password and a confirmation; login with the new password works. NOTE: the debug container's current password is unknown (random value, deleted), so this step is also how you get back into :8002."
    why_human: "Visual theming and the TTY getpass prompt (AUTH-01, AUTH-10). The stdin path of the CLI was verified live."
  - test: "Optional, on a real phone: the nav at 360 and 390 px, light and dark."
    expected: "Account and Logout fit the bottom bar without overflow."
    why_human: "Visual judgment; the executor only used emulated Chrome screenshots."
  - test: "Decide whether AUTH-15 is met as written. The full integration run (-m integration against :8002) is 29 passed / 4 failed: test_training_e2e, test_training_cancel, test_training_queue (300 s / 30 s timeouts on this CPU-only host, same failure on a pre-phase image per the executor) and test_benchmark_cancel (timing race, passed on 3 of 3 reruns). I re-ran test_smoke.py (7 passed) against a fresh :8003 instance but did NOT re-run the full integration suite."
    expected: "Accept that the failures are pre-existing/flaky and independent of auth, or ask for the tests to be fixed or skipped on slow hosts before the phase is closed."
    why_human: "AUTH-15 says integration tests 'pass with auth enabled'. The failures are not auth failures, but the literal exit-0 criterion is not met. This is a scope decision."
  - test: "Judgment-tier prohibitions and the CSRF statelessness claim (plan 02, verification: backstop). Skim watermeter/auth_middleware.py `_csrf_refusal`."
    expected: "Confirm the CSRF verdict depends only on the request's own headers and cookie validity and no auth-disable switch exists."
    why_human: "Judgment-tier and backstop-tier truths are never silently auto-passed. My reading says the function is pure (it reads headers, method, path and an env allowlist), and no shared mutable state exists, but a held-out or property-based test does not exist."
---

# Phase 1: Auth Core and Test/Script Plumbing - Verification Report

**Phase Goal:** Every page and API on a fresh or upgraded install is protected by one admin password that the owner sets on first visit, and the existing tests, CI and helper scripts keep working with auth on.
**Verified:** 2026-10-23T20:45:00Z
**Status:** human_needed
**Re-verification:** No, initial verification

The goal is achieved in the code and on live instances. I found no FAILED truth and no blocker. Status is `human_needed` because (a) the plans themselves mark five browser/TTY checks as human-only and none was performed, (b) AUTH-15's literal "integration tests pass" is not met on this host because of 4 failing integration tests that the executor classified as non-auth, and (c) judgment-tier prohibitions must go to a human. The 4 review warnings do not undermine the phase goal, but two of them (WR-01, WR-04) weaken the intent of AUTH-09 and AUTH-11 and I recommend fixing them early in Phase 2.

## How I verified

SUMMARY claims were not used as evidence. What I ran myself:

- `.venv/bin/python -m pytest tests/unit tests/regression -q`: **1281 passed**.
- `./debug_clean.sh --detach` (builds the image, starts :8003 with a random seeded password). `scripts/auth_smoke.sh` against it: 5 PASS lines. I then drove the instance with curl: login with `next`, CSRF refusals, setup closure, password change, container restart, root-run CLI, deleting `auth.json`, and 10 concurrent setup posts (details below). The container, stub and network were removed afterwards.
- `./oneshot_test.sh`: `PASS: one-shot reading succeeded (exit 0)`.
- `pytest tests/integration/test_smoke.py -m integration --base-url=http://localhost:8003` with a known password: 7 passed.
- Anonymous probes against the already-running debug container :8002 (no state change except 3 failed logins from my address). Port 8001 and the prod container were not touched, and no prod container exists on this host.
- Read `auth.py`, `auth_middleware.py`, `ratelimit.py`, `routes/auth.py`, `routes/health.py`, `app.py`, `__main__.py`, `static/auth.js`, the login/setup templates, `scripts/auth_smoke.sh`, `debug_clean.sh`, the CI workflow diff and the README Authentication section.

## Goal Achievement

### Observable Truths (ROADMAP success criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Fresh/upgraded install with no password: every page redirects to setup (min 8, confirm); only `/health`, setup and static reachable; setup permanently closed afterwards, including concurrent attempts | VERIFIED | Live on :8003 after deleting `auth.json`: `/` 303 -> `/setup`, `/docs` 303 -> `/setup`, `/api/status` 401, `/health` 200. Posts with 7 chars and with a mismatched confirm both 400. **10 concurrent `POST /setup`: exactly 1 received a `Set-Cookie`, the other 9 got 303 with none.** After setup `GET /setup` is 303 and anonymous `POST /setup` is 303 -> `/login`. `AuthStore.create` uses `_WRITE_LOCK` plus `os.link` no-clobber (auth.py:313-335). |
| 2 | Login, login survives browser restart, logout from any page, change password (current required) logs out other sessions; works over plain HTTP; prod and debug keep separate sessions | VERIFIED (browser parts routed to human) | Live: login 303 with `Set-Cookie: wm_session_<8hex>=...; Max-Age=2592000; Path=/; HttpOnly; SameSite=Lax` (no Secure over HTTP). Wrong current password 403; correct change returned 200 and a new cookie; the second session then got **401** while the changer got 200. After `docker restart` the old cookie still returned 200 (secret persisted in `auth.json`). Logout is POST-only and clears the cookie (Max-Age=0). Cookie name is `wm_session_` + random per-install `instance_id` (auth.py:95, 323). Nav has Account link and Logout POST button on every page (`_nav.html:172-180`; `_theme.html` loads `auth.js`). Real-browser confirmation is in `human_verification`. |
| 3 | Anything outside the allowlist refused (incl. `/docs`, `/openapi.json`, later routes); pages redirect to login and return; HTMX/fetch get 401 -> login; no login HTML in fragments; cross-site state-changing request refused | VERIFIED | Gate is a pure-ASGI middleware installed unconditionally (`app.py:200`); `docs_url/redoc_url/openapi_url=None`. Live anonymous: `/docs`, `/openapi.json`, `/redoc`, `/nope/x`, `/account` all 303 to `/login?next=...`; `/api/status` 401 JSON; an `HX-Request` poll got **401 with empty body and `HX-Redirect: /login?next=%2Ftraining%3Ftab%3Dx`** taken from `HX-Current-URL`. Login with `next=/training?tab=x` redirected there. Authenticated `/docs` and `/openapi.json` are 404. `/static/../api/status` is 404. CSRF: `Origin: http://evil.example` and `Sec-Fetch-Site: cross-site` both 403 (anonymous and with session); a cookie-authenticated `POST /api/reset` without `X-Requested-With` is 403. `test_route_inventory.py` (5 passed) walks `app.routes` in both states with >= 85 pairs and adds a route after import to prove it is denied. |
| 4 | Repeated failed logins throttled (429 + Retry-After) without permanent lockout; host-only recovery (`set-password`, delete auth file); seed via `WATERMETER_ADMIN_PASSWORD[_FILE]`; optional setup token; README documents recovery and upgrade | VERIFIED | Live: rapid wrong passwords gave 401/429 with `retry-after: 1`; limiter is in-memory only (`ratelimit.py` has no file I/O; `test_nothing_is_persisted`). Root-run `docker exec -i ... set-password` (stdin) exited 0, left `/data/auth.json` as `watermeter 600`, and the new password logged in without a restart; a 5-char password was rejected with exit 1. Deleting `auth.json` brought the setup page back. Seed ignored once a password exists: after the restart the container env still held the old seed, old password 401, new password 303. Setup-token logic in `auth.py:504-534` (`hmac.compare_digest`); route behaviour covered by `test_auth_routes.py`/`test_auth_startup.py`, which I ran but did not exercise live. README has `## Authentication` and `### Forgot the admin password` covering first visit, upgrade, sessions, seed, token, persistence, proxies, API clients and recovery. |
| 5 | Unit tests, integration tests, CI smoke test, `debug_clean.sh`, `oneshot_test.sh` pass with auth on; no auth-disable switch | VERIFIED with a flagged exception | Unit+regression 1281 passed. `debug_clean.sh --detach` ran end to end and printed a random password; `auth_smoke.sh` 5 PASS. `oneshot_test.sh` exit 0. CI workflow now seeds `openssl rand -hex 16` and runs `scripts/auth_smoke.sh` (diff vs pre-phase). Integration smoke tests 7 passed against :8003. No disable switch: grep of `watermeter/` for disable/bypass/skip_auth/dependency_overrides finds nothing relevant. **Exception:** the executor's full integration run had 4 failures that it attributes to a slow CPU-only host and a timing race (see human item 7). I did not re-run the full suite. |

**Score:** 5/5 roadmap success criteria verified; 0 failed; 0 behavior-unverified. Items needing human confirmation are listed above.

### Requirements Coverage

All 15 IDs appear in the PLAN frontmatter and in REQUIREMENTS.md; no orphaned requirement is mapped to Phase 1 without a plan.

| Requirement | Source Plan(s) | Status | Evidence |
|-------------|----------------|--------|----------|
| AUTH-01 | 01, 03, 04, 05 | SATISFIED | Live setup redirect, allowlist, 8-char minimum, confirm field (see SC 1). |
| AUTH-02 | 01, 03, 04 | SATISFIED | 10 concurrent setup posts, one winner. |
| AUTH-03 | 01, 03, 05 | SATISFIED (browser restart: human) | 30-day Max-Age cookie, sliding refresh after 15 days (`auth_middleware.py`), secret persisted across container restart. |
| AUTH-04 | 03, 04 | SATISFIED | POST `/logout` clears the cookie; button in nav. |
| AUTH-05 | 03 | SATISFIED | Epoch bump verified live (other session 401). |
| AUTH-06 | 02, 04 | SATISFIED | Default-deny middleware, docs disabled, route inventory test. |
| AUTH-07 | 02, 04 | SATISFIED (htmx in browser: human) | 303/`next` round trip, 401 + `HX-Redirect`, JSON 401 for `/api`, `auth.js` node check. |
| AUTH-08 | 02, 04 | SATISFIED | Origin/Sec-Fetch-Site check plus `X-Requested-With`; refused cross-site cases live. |
| AUTH-09 | 01, 04, 05 | SATISFIED (see WR-01) | Seed creates the password once and is ignored afterwards. |
| AUTH-10 | 01, 05 | SATISFIED (TTY: human) | CLI via stdin as root works; file ownership handed back; README. |
| AUTH-11 | 01, 03 | SATISFIED literally (see WR-04) | 429 + Retry-After, in-memory, warnings logged with IP and no password. |
| AUTH-12 | 01, 02, 05 | SATISFIED (browser: human) | Random per-install `instance_id` in the cookie name. |
| AUTH-13 | 01, 02, 03 | SATISFIED | Cookie flags observed; Secure only when scheme is https; secret in `auth.json`. |
| AUTH-14 | 01, 03, 04, 05 | SATISFIED | Token logic and tests; `debug.sh` passes the flag. Not exercised live. |
| AUTH-15 | 04, 05 | SATISFIED with exception | See SC 5 and human item 7. |

### Required Artifacts and Key Links

| Artifact / Link | Status | Details |
|-----------------|--------|---------|
| `watermeter/auth.py` (629 lines), `auth_middleware.py` (190), `ratelimit.py` (117), `routes/auth.py` (323), `routes/health.py` (24), `static/auth.js` (130), `scripts/auth_smoke.sh` (49) | VERIFIED | Exist, substantive, meet `min_lines`. |
| `app.add_middleware(AuthMiddleware)` | WIRED | `app.py:200`, unconditional. |
| `include_router(auth_router)`, `include_router(health_router)` | WIRED | `app.py:222-223`. `/health` payload moved unchanged to `routes/health.py`. |
| `_theme.html` -> `/static/auth.js`; every page template includes `_theme.html` | WIRED | Only partials (`_nav`, `_theme`, `status_fragment`) lack it. |
| `__main__.py` -> `watermeter.auth.cli_set_password` | WIRED | Lazy import in the `set-password` branch; `--one-shot` parsing unchanged (oneshot_test.sh passes). |
| `auth.py` -> `os.link` (no-clobber), `hashlib.scrypt` with `maxmem` | WIRED | Lines 132, 329. |
| `docker.yml` -> `scripts/auth_smoke.sh` | WIRED | Workflow diff checked; the script itself ran green against :8003. |
| `lifespan` -> `_auth_startup` (never raises) | WIRED | `app.py:48-77, 82`; covered by `test_auth_startup.py`. |

### Data-Flow Trace (Level 4)

Not applicable in the usual sense (no dynamic data rendering added). The one data path that matters, password -> scrypt hash -> `auth.json` -> `verify_token` on each request, was exercised live end to end (login, change, restart, CLI reset).

### Behavioral Spot-Checks and Probes

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full unit + regression | `pytest tests/unit tests/regression -q` | 1281 passed | PASS |
| Route inventory default-deny | `pytest tests/unit/test_route_inventory.py -q` | 5 passed | PASS |
| Auth smoke on clean install | `scripts/auth_smoke.sh http://localhost:8003 <pw>` | 5 PASS | PASS |
| One-shot with auth on | `./oneshot_test.sh` | exit 0 | PASS |
| Integration smoke, external instance | `pytest tests/integration/test_smoke.py -m integration --base-url=:8003` | 7 passed | PASS |
| Concurrent setup | 10 parallel `POST /setup` | 1 cookie issued | PASS |
| Recovery CLI as root | `docker exec -i ... python3 -m watermeter set-password` | exit 0, `watermeter 600`, no restart | PASS |

Probe execution: the phase declares no `probe-*.sh` scripts; Step 7c not applicable.

### Anti-Patterns

No TBD/FIXME/XXX/TODO/HACK markers in any phase source file. No stubs, no hardcoded empty data flowing to output. No hard-coded passwords in scripts, CI or fixtures (CI and `debug_clean.sh` generate a random one per run).

### Code Review Warnings (advisory) vs. the phase goal

None of the four is a goal-blocking gap; none is a bypass. I reproduced WR-01 and WR-03 myself and read the code for WR-02 and WR-04.

| Warning | Effect on the goal | My view |
|---------|-------------------|---------|
| WR-01 invalid/unreadable seed leaves `/setup` open | Instance is in the same state as an unseeded fresh install: the owner can still set a password on first visit, so "protected by a password the owner sets on first visit" holds. But an operator who seeded to avoid exposing setup is silently exposed. | Real hardening gap for AUTH-09; only an ERROR log line signals it. Fix soon (503 on `/setup` while a seed was supplied but failed). |
| WR-02 `os.link` with no fallback; unhandled `OSError` in `setup_submit` | On a filesystem without hard links (SMB/CIFS, some FUSE) the password can never be created and, by design, there is no switch to disable the gate. The UI would be unusable for that install. | Environment-specific, but an upgrade-time lockout for such users. Fix with the `O_EXCL` fallback; low effort. |
| WR-03 >20 form fields -> 500 | Anonymous 500 plus traceback; no security impact. | Cosmetic robustness. |
| WR-04 throttle keyed on full IPv6 address | AUTH-11 says "from one client", which the code satisfies literally. An IPv6 client with a /64 can rotate source addresses and skip the 5/min and 20/hour limits. The scrypt cost (~200 ms, two concurrent hashes) still bounds guess rate and passwords are >= 8 chars, but the limiter is the only brute-force defence. | Fix with /64 normalisation. Recommend before the image is published. |

Review ledger `01-REVIEW-DISPOSITION.md` lists all 10 findings as `open`; none is marked fixed or deferred. IN-04 (`debug.sh` sources `.env`, so a password with `$`, spaces or `#` is mangled) and IN-01 (proxies share one throttle key; README wording) are the info items most likely to bite a real operator.

### Gaps Summary

No gaps. All five roadmap success criteria and all fifteen AUTH requirements are achieved in the codebase and were confirmed on live throwaway instances. Remaining work before calling the phase fully closed is human confirmation (browser/TTY checks and the AUTH-15 integration-suite decision) and a triage decision on WR-01 through WR-04.

---

_Verified: 2026-10-23T20:45:00Z_
_Verifier: Claude (gsd-verifier)_
