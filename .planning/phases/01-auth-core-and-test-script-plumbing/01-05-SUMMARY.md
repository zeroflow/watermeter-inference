---
phase: 01-auth-core-and-test-script-plumbing
plan: 05
subsystem: testing
tags: [pytest, httpx, docker, bash, readme, auth, integration-tests]

requires:
  - phase: 01-auth-core-and-test-script-plumbing
    provides: "watermeter/auth.py store, env seed, set-password CLI (plan 01)"
  - phase: 01-auth-core-and-test-script-plumbing
    provides: "Gate switched on in the real app, scripts/auth_smoke.sh, unit fixtures (plan 04)"
provides:
  - "Integration fixtures that log in: Instance dataclass, watermeter_instance, admin_password, logged-in api client, --admin-password option"
  - "Self-started test containers get a fresh random password per run; --base-url runs read --admin-password, WATERMETER_TEST_PASSWORD or WATERMETER_ADMIN_PASSWORD and skip with a message when it is missing or rejected"
  - "Two unauthenticated smoke tests (401 JSON on /api, 303 to /login on pages, /health open)"
  - "debug.sh passes the seed and setup-token variables through and waits on /health"
  - "debug_clean.sh seeds and prints a throwaway random password, --setup shows the real setup page, waits on /health"
  - "README Authentication and Forgot the admin password sections under the anchors the app links to; DOCKER.md, docker-compose.yml, .env.example document every new variable"
affects: [02 hardening, 03 HA visibility, 04 dashboard banners]

actuals:
  tokens: 7200
  tasks: 3
  commits: 3

plan_head_before: 806c5d123e7ca612841c82cfa6f3911b909f6e03
plan_head_after: 7ab302067d92efce08d69c551968c7ec5d087472

tech-stack:
  added: []
  patterns:
    - "Integration password precedence: --admin-password, then WATERMETER_TEST_PASSWORD, then WATERMETER_ADMIN_PASSWORD; never read from the environment for a self-started container"
    - "A rejected login is a visible skip with its reason, never a bypass"
    - "Throwaway passwords live in a mktemp file outside the repository and are deleted at the end"

key-files:
  created: []
  modified:
    - tests/conftest.py
    - tests/integration/conftest.py
    - tests/integration/test_smoke.py
    - debug.sh
    - debug_clean.sh
    - README.md
    - DOCKER.md
    - docker-compose.yml
    - .env.example

key-decisions:
  - "debug_clean.sh passes PUID and PGID to the throwaway container, as debug.sh does, so the bind-mounted temp config dir stays removable by the caller"
  - "The README warning box no longer says the web UI has no authentication layer; it points at the Authentication section and keeps the do-not-expose warning"
  - "The 4 failures of the full integration run are classified as pre-existing or timing (see Issues Encountered); none shows a 401, 403 or redirect to /login or /setup"

patterns-established:
  - "Docs state the recovery command with -u watermeter and note that running as root still works because the file is handed back"

requirements-completed: [AUTH-01, AUTH-09, AUTH-10, AUTH-12, AUTH-14, AUTH-15]

coverage:
  - id: D1
    description: "Integration fixtures log in once (cookie jar, X-Requested-With: watermeter) against a self-started container with a random per-run password and against --base-url with an externally given password; a missing or rejected password becomes a skip naming --admin-password and WATERMETER_TEST_PASSWORD"
    requirement: "AUTH-15"
    verification:
      - kind: integration
        ref: "tests/integration/test_smoke.py (self-started, --no-build): 7 passed, 0 skipped"
        status: pass
      - kind: integration
        ref: "tests/integration/test_smoke.py --base-url=http://localhost:8002 with WATERMETER_TEST_PASSWORD: 7 passed, 0 skipped"
        status: pass
      - kind: integration
        ref: "tests/integration/test_smoke.py --base-url=http://localhost:8002 without a password: 2 passed, 5 skipped with the --admin-password message"
        status: pass
    human_judgment: false
  - id: D2
    description: "Anonymous /api/status is 401 JSON, /health is 200 without a session, and an anonymous page redirects to /login; the dashboard test accepts the first-run wizard redirect but fails on a /login or /setup redirect"
    requirement: "AUTH-14"
    verification:
      - kind: integration
        ref: "tests/integration/test_smoke.py#test_unauthenticated_api_is_refused"
        status: pass
      - kind: integration
        ref: "tests/integration/test_smoke.py#test_unauthenticated_page_redirects_to_login"
        status: pass
    human_judgment: false
  - id: D3
    description: "debug.sh passes WATERMETER_ADMIN_PASSWORD and WATERMETER_SETUP_TOKEN_REQUIRED through and waits on /health"
    requirement: "AUTH-09"
    verification:
      - kind: other
        ref: "WATERMETER_ADMIN_PASSWORD=<random> ./debug.sh --detach, then docker inspect ... | grep -c '^WATERMETER_ADMIN_PASSWORD=' prints 1; bash scripts/auth_smoke.sh http://localhost:8002 <pw>: 5 PASS lines"
        status: pass
    human_judgment: false
  - id: D4
    description: "debug_clean.sh seeds a random password and prints it by default; --setup seeds nothing and the real setup page answers (303 / to /setup, /setup 200)"
    requirement: "AUTH-01"
    verification:
      - kind: other
        ref: "./debug_clean.sh --detach then bash scripts/auth_smoke.sh http://localhost:8003 <printed pw>: 5 PASS lines"
        status: pass
      - kind: other
        ref: "./debug_clean.sh --detach --setup: curl / gives 303 http://localhost:8003/setup, /setup gives 200"
        status: pass
    human_judgment: false
  - id: D5
    description: "python3 -m watermeter set-password run as root through docker exec leaves /data/auth.json owned by the app user with mode 600, and the running server accepts the new password and rejects the old one without a restart"
    requirement: "AUTH-10"
    verification:
      - kind: other
        ref: "docker exec -i watermeter-dashboard-debug python3 -m watermeter set-password (stdin) exits 0; stat -c '%U %a' /data/auth.json prints 'watermeter 600'; second password set: old login 401, new login 303, restore 303"
        status: pass
    human_judgment: false
  - id: D6
    description: "oneshot_test.sh, the unit and regression suite and the full integration suite exit 0 with auth on (AUTH-15)"
    requirement: "AUTH-15"
    verification:
      - kind: other
        ref: "./oneshot_test.sh prints 'PASS: one-shot reading succeeded (exit 0)'"
        status: pass
      - kind: unit
        ref: "pytest tests/unit tests/regression -q: 1281 passed (reruns); one earlier run showed 2 timing failures, see Issues Encountered"
        status: pass
      - kind: integration
        ref: "pytest tests/integration -m integration --base-url=http://localhost:8002: 29 passed, 4 failed (3 training tests fail identically on the pre-phase image, 1 benchmark cancel timing race that passed on 5 reruns)"
        status: fail
    human_judgment: true
    rationale: "The full integration run is not exit 0: the training tests exceed their 300 s limit on this CPU-only host before and after the phase, and one benchmark cancel race is flaky. A human should accept that classification or ask for the tests to be fixed."
  - id: D7
    description: "README has '## Authentication' and '### Forgot the admin password' under the anchors auth.README_AUTH_URL and auth.README_RECOVERY_URL and covers first visit, upgrade notice, sessions, seed, setup token, persistent /data, proxies, API clients and recovery; DOCKER.md, docker-compose.yml and .env.example document the new variables with example values; no doc points at the disabled API pages"
    requirement: "AUTH-10"
    verification:
      - kind: other
        ref: "anchor check script prints 'anchors ok'; ! grep -nE '`/docs`|`/redoc`' README.md DOCKER.md; docker compose -f docker-compose.yml config -q exits 0"
        status: pass
    human_judgment: true
    rationale: "Anchors and variable names are checked by script; whether the wording is clear to a first-time reader is a human judgment"
  - id: D8
    description: "Account and Logout fit the mobile bottom bar and desktop rail at 360 px and 390 px in light and dark themes"
    verification:
      - kind: automated_ui
        ref: "headless Chrome 145 via CDP screenshots of / on :8002 at 360, 390 and 1280 px, light and dark: scrollWidth equals clientWidth at every size; 360 light and 390 dark screenshots reviewed by the executor, 9 nav items visible, Account and Logout icons present"
        status: pass
    human_judgment: true
    rationale: "Fit and legibility at real breakpoints is a visual judgment on a real phone; the executor reviewed emulated screenshots only"
  - id: D9
    description: "Real-browser behavior: login over plain HTTP on a LAN address, two instances' cookies coexisting (AUTH-12), session expiry sending the browser to /login?next=/ (AUTH-07), restart persistence and password change across two browsers (AUTH-03, AUTH-05), themed setup page and interactive set-password prompt"
    requirement: "AUTH-12"
    verification: []
    human_judgment: true
    rationale: "The plan marks these five checks human-only (real Origin and cookie behavior over a LAN IP, two live instances, htmx in a browser, browser restarts, a TTY prompt); they are listed under Human verification below"

duration: "about 60 min (estimated; the host clock jumped during the run, so the start and end timestamps are unusable)"
completed: 2026-10-23
status: complete
---

# Phase 1 Plan 05: Test and Script Plumbing and Docs Summary

**Integration suite that logs in by itself (random per-run password for self-started containers, an external password for --base-url, a visible skip otherwise), debug scripts that seed and print throwaway passwords, a live proof of the root-run recovery CLI inside the real container, and README/DOCKER/compose/.env docs under the anchors the app links to**

## Performance

- **Duration:** about 60 min, estimated (the host clock jumped, see the frontmatter)
- **Tasks:** 3
- **Files modified:** 9 (no files created; plus this summary)

## Accomplishments
- `tests/integration/conftest.py`: `Instance` dataclass, session fixtures `watermeter_instance`, `base_url`, `admin_password`, and a logged-in `api` client (`POST /login` once, cookie jar kept, `X-Requested-With: watermeter` on every request). `_docker_run` takes the password and passes it as `WATERMETER_ADMIN_PASSWORD`; the self-started password is always a fresh `secrets.token_urlsafe(16)`. `tests/conftest.py` gains `--admin-password`.
- `tests/integration/test_smoke.py`: two unauthenticated tests; `test_dashboard_page` accepts the first-run `/roi-config` redirect and fails on any `/login` or `/setup` redirect.
- `debug.sh` passes the seed and setup-token variables through in both `docker run` blocks and probes `/health`.
- `debug_clean.sh`: random `openssl rand -hex 16` password per run, printed after the ready line (or before `docker run -it`); `--setup` seeds nothing and prints the setup URL; probes `/health`.
- README: `## Authentication` (first visit, upgrade, sessions, seed, setup token, persistent data, proxies, API clients) and `### Forgot the admin password`; the Key API Endpoints, Debug Container, Clean-Install, Tests, Volumes and Utility Scripts sections follow the gate; DOCKER.md gets the five variables, the three pages, persistence, health and four troubleshooting entries; compose and `.env.example` carry commented examples only.

## Task Commits

1. **Task 1: Tracer - integration suite logs in** - `b57d917` (test). The tracer feedback gate ran the live steps 2 to 7 and the unit suite before expansion (see Live evidence).
2. **Task 2: Clean-install script, one-shot script, full integration run** - `7ab3020` (feat)
3. **Task 3: README, DOCKER.md, compose, .env.example** - `8f6b2fb` (docs)

**Plan metadata:** committed with this summary (docs: complete plan)

## Live evidence (passwords never recorded)

All on throwaway containers or the debug container; production on :8001 and `watermeter-dashboard-prod` were not touched (`docker ps -a` on this host lists no production container at all).

| Step | Command | Result |
|------|---------|--------|
| Debug start | `WATERMETER_ADMIN_PASSWORD=<random> ./debug.sh --detach` | "Container is ready" via `/health` |
| Pass-through | `docker inspect ... \| grep -c '^WATERMETER_ADMIN_PASSWORD='` | `1` |
| Recovery as root | `printf '%s\n' <pw> \| docker exec -i watermeter-dashboard-debug python3 -m watermeter set-password` | exit 0; `stat -c '%U %a' /data/auth.json` prints `watermeter 600` |
| No-restart reload | set a second random password, wait, login | old password 401, new password 303; restored the first one, 303 |
| Auth smoke on debug | `bash scripts/auth_smoke.sh http://localhost:8002 <pw>` | 5 PASS lines (plan asked for 4) |
| Smoke tests, `--base-url` | `WATERMETER_TEST_PASSWORD=<pw> pytest tests/integration/test_smoke.py -m integration --base-url=http://localhost:8002 -q -rs` | 7 passed, 0 skipped |
| Smoke tests, self-started | `pytest tests/integration/test_smoke.py -m integration --no-build -q -rs` (no password variables) | 7 passed, 0 skipped; no `watermeter-test-*` container left |
| Skip path | same with `--base-url` and no password | 2 passed, 5 skipped, each skip names `--admin-password` and `WATERMETER_TEST_PASSWORD` |
| Clean install | `./debug_clean.sh --detach`, password read from the output, `scripts/auth_smoke.sh http://localhost:8003 <pw>` | 5 PASS lines |
| Clean `--setup` | `./debug_clean.sh --detach --setup` | `/` gives `303 http://localhost:8003/setup`, `/setup` gives 200, no "upgrade" text on the page |
| One-shot | `./oneshot_test.sh` | `PASS: one-shot reading succeeded (exit 0)` |
| Unit and regression | `pytest tests/unit tests/regression -q` | 1281 passed |
| Lint | `uvx ruff check watermeter/ tests/`; `uvx black --check tests/integration/test_smoke.py` | clean |
| Docs | anchor script; `! grep -nE '`/docs`\|`/redoc`' README.md DOCKER.md`; `docker compose config -q` | `anchors ok`; no matches; exit 0 |
| Secret scan | `git log -p --format= 806c5d1..HEAD \| grep -E '\b[0-9a-f]{32}\b'` and a `git grep` of the run password over all tracked files | no match; throwaway containers, networks, temp dirs and the password file removed |

### Full integration run against :8002

`WATERMETER_TEST_PASSWORD=<pw> pytest tests/integration -m integration --base-url=http://localhost:8002 -q -rs`: **29 passed, 0 skipped, 4 failed** in 6 min 54 s.

| Test | Failure | Classification |
|------|---------|----------------|
| `test_training.py::test_training_e2e` | "Training did not complete within 300s" | Pre-existing. The same test fails the same way against an image built from `54ef8d3` (pre-phase), run from a `git archive` of that commit in a fresh container. The model trained here took 475 s on this CPU-only host. |
| `test_training.py::test_training_cancel` | `POST /api/training/cancel?job_id=` answers 404 (empty job id, the e2e job still held the queue) | Pre-existing, same 404 on `54ef8d3` |
| `test_training.py::test_training_queue` | "Training did not complete within 30s" | Pre-existing, same message on `54ef8d3` |
| `test_benchmark.py::test_benchmark_cancel` | status still `running` 3 s after the cancel call | Timing race, not auth. It passed 3 of 3 alone and again in the next 2 runs of `test_benchmark.py`. NOT reproduced on the baseline (it passed there on a fresh container), so it is not proven pre-existing, only flaky. |

No failure shows a 401, 403 or a redirect to `/login` or `/setup`; the server log of the run has only 200 and the one 404 above.

## Files Created/Modified
- `tests/conftest.py` - `--admin-password` option
- `tests/integration/conftest.py` - `Instance`, `watermeter_instance`, `admin_password`, logged-in `api`, `_docker_run(image, host_port, admin_password)`
- `tests/integration/test_smoke.py` - two unauthenticated tests, first-run-aware dashboard test
- `debug.sh`, `debug_clean.sh` - pass-through, `--setup`, `/health` probes, PUID/PGID for the clean container
- `README.md`, `DOCKER.md`, `docker-compose.yml`, `.env.example` - authentication docs

## Decisions Made
- The recovery CLI is documented with `-u watermeter` and a note that running it as root also works; this was proven in the real container (owner `watermeter`, mode 600).
- The README warning box now links to the Authentication section instead of claiming the UI has no authentication.
- Training and benchmark-cancel failures are reported, not hidden or fixed: they are outside the auth work and the plan allows classification with evidence.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] debug_clean.sh left an undeletable temp directory**
- **Found during:** Task 2, live run 1
- **Issue:** The container entrypoint chowns the bind-mounted temp config directory to uid 1000 (no `PUID`/`PGID` passed), so the script's cleanup `rm -rf "$STUB_DIR"` fails for the calling user (uid 1002 here) and the plan's "remove the printed temp directory" step could not complete. The failure is swallowed by `|| true`, so it was silent.
- **Fix:** Pass `-e PUID=$(id -u) -e PGID=$(id -g)` in both `docker run` blocks, the same as `debug.sh`. Removed the leftover directory once with a root helper container (`docker run --rm --entrypoint rm -v /tmp:/t nginx:alpine ...`).
- **Files modified:** `debug_clean.sh`
- **Verification:** the second `--detach --setup` run's temp directory was removable by the caller with plain `rm -rf`.
- **Committed in:** `7ab3020`

**2. [Rule 2 - Missing critical] README warning box contradicted the gate**
- **Found during:** Task 3
- **Issue:** The box still said "The web UI has no authentication layer", and the Quick Start did not mention the setup page.
- **Fix:** Reworded the sentence to point at the Authentication section (the do-not-expose warning stays) and added the setup step to the Quick Start comment.
- **Files modified:** `README.md`
- **Committed in:** `8f6b2fb`

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 missing critical)
**Impact on plan:** Small and in files the plan already touched. `files_modified` matches the plan exactly.

## Issues Encountered
- **Full integration run not exit 0**: 4 failures, classified above. Left unfixed because they are not auth regressions; the training tests need a faster host or a larger timeout, which is a separate task. Candidate backlog item: raise `TRAINING_TIMEOUT` or skip training tests on slow CPUs.
- **Test pollution on the debug container**: the timed-out `test_training_e2e` run finished its training later and left a model `mobilenetv3_small_050_59c87b` in `models_debug/digits`, which `_find_benchmarkable_model` then picked (15.8 % accuracy), failing `test_benchmark_digits` on a rerun. I deleted that model through `DELETE /api/models/digits/<id>`; `test_benchmark.py` then passed 3 of 3. The debug container was otherwise left as it was.
- **Login throttle during manual checks**: the throttle (1 request per second window) answered 429 when I tried an old and a new password within one second; with 3 s between attempts the checks behaved as expected. Not a defect.
- **Unit suite flakiness**: one run of `tests/unit tests/regression` showed 2 failures (the known `test_mqtt_reconnect.py::test_reconnect_uses_exponential_backoff` and one more I did not capture while a Chrome and Docker load was running); the next 4 runs were 1281 passed. `test_training_config.py::...test_learning_rate_passed_through_dict` prints a warning line but passes.
- **Process notes**: Task 3 (docs) was committed before Task 2 because the Task 2 full integration run needed about 7 minutes and the docs were written meanwhile; each commit still contains only its own files. As in earlier plans, commits were made on `main` as authorized by the dispatch; `.planning/config.json` was not touched and nothing was pushed.

## Skips
- Full integration run: 0 skipped.
- Skip-path demonstration (no password, `--base-url`): 5 skipped on purpose (all login-dependent tests), 2 unauthenticated tests passed.

## Human verification (from the plan, not performed by the executor)
1. Open `http://<LAN address of this host>:8002` from another device (plain HTTP): log in, watch the dashboard poll for a minute, press Read Now. Expect: login works, session kept (cookie without Secure), polling updates, no 403 in the console.
2. Log in to :8002 and to a clean instance (`./debug_clean.sh --detach`, :8003, password printed); log out of :8003. Expect two different `wm_session_xxxxxxxx` cookies and :8002 still logged in (AUTH-12).
3. On :8002 delete the `wm_session_` cookie in dev tools and wait for the next 5 s poll. Expect a redirect to `/login?next=/` and a return to the dashboard after login (AUTH-07).
4. Close and reopen the browser on :8002 (still logged in, AUTH-03); change the password on `/account` in browser A while browser B is logged in (A stays in, B is sent to `/login` on its next poll, AUTH-05).
5. Open `http://localhost:8003/setup` from `./debug_clean.sh --detach --setup` in light and dark; then set your own debug password with `docker exec -it -u watermeter watermeter-dashboard-debug python3 -m watermeter set-password` and log in to :8002 with it (AUTH-10). Note: the debug container's current password is a random value that was deleted with the temporary file, so step 5 (or deleting `data_debug/auth.json` so the setup page returns) is how you get back in.
6. Optional on a real phone: the nav at 360 and 390 px (executor screenshots from headless Chrome looked fine, see D8).

The debug container `watermeter-dashboard-debug` is left running on :8002 for these checks. No clean container is running.

## User Setup Required
None - no external service configuration required.

## Known Stubs
None.

## Threat Flags
None. This plan adds no network endpoint; it changes test fixtures, scripts and docs. T-01-32 (no fixed or committed password), T-01-34 (example values only) and T-01-35 (login failure is a visible skip) are covered by the checks above.

## Next Phase Readiness
- Phase 1 is complete once this plan's metadata commit lands: all five plans have summaries. Do not push yet without reviewing: every push to `main` runs CI and publishes `:dev`; CI runs the unit tests and the smoke step added in plan 04.
- The training integration tests need attention before anyone treats `-m integration` as a gate on this host (3 of 4 failures are independent of this phase).
- Phase 2 (hardening) can rely on the logged-in `api` fixture and on `scripts/auth_smoke.sh`.

## Self-Check: PASSED

- Files found: `tests/conftest.py`, `tests/integration/conftest.py`, `tests/integration/test_smoke.py`, `debug.sh`, `debug_clean.sh`, `README.md`, `DOCKER.md`, `docker-compose.yml`, `.env.example`.
- Commits found in `git log`: `b57d917`, `7ab3020`, `8f6b2fb`; `git rev-list --count 806c5d1..HEAD` gave 3 before this summary.
- Acceptance criteria re-run: `WATERMETER_TEST_PASSWORD`, `secrets.token_urlsafe(16)` and `--admin-password` present; `WATERMETER_ADMIN_PASSWORD` in `debug.sh` twice or more and `localhost:8002/health` once; `--setup` and `openssl rand -hex 16` present in `debug_clean.sh`; the `/health` probe line is present (`grep -F`); README anchors ok; `set-password`, `FORWARDED_ALLOW_IPS`, `WATERMETER_ALLOWED_ORIGINS` each appear in README.md and DOCKER.md; compose and `.env.example` mention only commented-out `WATERMETER_ADMIN_PASSWORD` lines; no 32-hex literal in the plan's diff.
- Plan verification: unit and regression 1281 passed; ruff and black clean; `bash -n` on both scripts passes.

---
*Phase: 01-auth-core-and-test-script-plumbing*
*Completed: 2026-10-23*
