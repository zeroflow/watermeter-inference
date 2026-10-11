# Phase 1: Auth Core and Test/Script Plumbing - Context

**Gathered:** 2026-10-11
**Status:** Ready for planning

<domain>
## Phase Boundary

One admin password protects every page and API on fresh and upgraded installs. This phase delivers:
- the first-run setup page, login, logout, change password, and the persistent session;
- the CSRF check and the 401 contract (pages vs HTMX vs `fetch()`);
- the login throttle, the env seed, the setup token, and the recovery CLI;
- the unit, integration, CI and helper-script plumbing, so everything stays green with auth on.

Requirements: AUTH-01 to AUTH-15 (`.planning/REQUIREMENTS.md`).

Not in this phase:
- write-only secrets, the SSRF guard, heavy-job limits, security headers, and vendored htmx (Phase 2);
- HA availability and the problem sensor (Phase 3);
- dashboard health and connection banners (Phase 4).

</domain>

<decisions>
## Implementation Decisions

### Already locked (research + requirements; do not re-open)
- Default-deny pure-ASGI auth middleware with an allowlist: `/health`, `/login`, `/setup` (only while unconfigured), `/logout`, `/static/*`. `docs_url`, `redoc_url` and `openapi_url` are disabled. A route-inventory test walks `app.routes`.
- Custom signed cookie using stdlib `hmac`, with a password epoch. `hashlib.scrypt` with an explicit `maxmem`, stored as a PHC-style string. The hash, signing secret and instance ID live in `/data/auth.json` (mode 0600, atomic write), never in `config.yaml`.
- CSRF: an Origin / `Sec-Fetch-Site` check on unsafe methods, plus a required `X-Requested-With: watermeter` on cookie-authenticated `/api/*` writes. `/login` and `/setup` form posts get the Origin check only.
- The 401 contract depends on the caller:
  - full pages get a 303 to `/login?next=…`, with `next` validated as same-origin;
  - HTMX gets a 401 plus `HX-Redirect`;
  - `/api/*` gets a JSON 401.
  `static/auth.js` patches fetch and XHR, adds the CSRF header and redirects on 401.
- `WATERMETER_ADMIN_PASSWORD` (or `_FILE`) is seed-only. It is ignored once a hash exists.
- There is no auth-disable switch anywhere in production code.

### Account controls (UI)
- **D-01:** Add two new nav items at the bottom of the rail, next to the theme toggle (`_nav.html`):
  - **Account**, which opens `/account`;
  - **Logout**, which is a POST form button, not a link.
  Both are visible on mobile too, which means two more slots in the bottom bar. Labels and icons may need to shrink to fit.
- **D-02:** The new `/account` page holds the change-password form (current, new, confirm) and a logout button. It does not use the config editor.
- **D-03:** After a password change the current session stays logged in: re-issue the cookie with the new epoch. Every other session is invalidated.
- **D-04:** No session info is shown (login time, expiry). Keep it minimal.

### Setup and login pages
- **D-05:** Setup and login are themed pages that reuse `_theme.html` and `style.css` (light and dark), shown as a centered card with **no nav rail**. Follow the "no inline styles" convention: add classes to `style.css`.
- **D-06:** Fresh and upgraded installs share one setup page. On an upgraded install (existing config or data), it also shows a short notice:
  - this version requires an admin password;
  - your configuration and readings are unchanged, and MQTT publishing keeps running;
  - a link to the README.
- **D-07:** Before a password is set, **the reading loop, the cyclic trigger and MQTT keep running**. The gate covers HTTP only, so an upgrade never interrupts the value in HA.
- **D-08:** Login errors are generic ("Wrong password"). On a 429 the page shows "Too many attempts, try again in N s", with N taken from `Retry-After`. A recovery hint under the form ("Forgot it? See README") links to the reset instructions.
- **D-09 (AUTH-14):** When the opt-in env flag is set (suggested name `WATERMETER_SETUP_TOKEN_REQUIRED=1`, final name is the planner's call):
  - the setup form gets an extra "Setup token" field;
  - the token is logged at startup and again on each setup attempt with a wrong or missing token;
  - it is regenerated on every start and never written to disk;
  - it is never placed in a URL.
- **D-10:** A successful setup logs the owner in automatically (it issues the session) and redirects to the dashboard.

### Dev and test credentials
- **D-11:** `debug.sh` (:8002) already sources `.env`. It passes `WATERMETER_ADMIN_PASSWORD` through to the container when it is set. This is seed-only, so `data_debug/auth.json` wins afterwards. If it is unset, the owner goes through the setup page.
- **D-12:** Integration tests against an existing instance (`--base-url` / `WATERMETER_TEST_URL`) take the password from:
  1. `WATERMETER_TEST_PASSWORD` or a `--admin-password` option;
  2. falling back to `WATERMETER_ADMIN_PASSWORD`.
  They log in once and share a cookie-carrying client that also sends the CSRF header. If the password is missing or wrong, they **skip with a clear message**.
- **D-13:** Self-started throwaway containers use a **random password per run**, generated by the script or fixture and passed via `WATERMETER_ADMIN_PASSWORD`. This covers the integration fixture, `debug_clean.sh` (:8003), `oneshot_test.sh` and the CI smoke test. No fixed test password goes in the public repo. `debug_clean.sh` prints the generated password so the owner can log in from a browser.
- **D-14:** `debug_clean.sh` seeds a password by default. A `--setup` flag skips seeding so the real first-boot setup page can be clicked through.
- **D-15:** The unit-test `test_client` fixture (`tests/unit/conftest.py`) is **logged in by default**: a tmp `auth.json`, a known password, and the CSRF header sent. A separate `anon_client` fixture serves the auth and gate tests. Do not use a bypass flag or `dependency_overrides` for auth.

### Instance identity and sessions
- **D-16:** The cookie name is unique per instance, `wm_session_<8 hex>`. The suffix is a random instance ID generated with the secret on first start and stored in `auth.json`. It needs no configuration and is stable across restarts. Prod and debug differ because their `/data` is separate. Do not derive it from the port (the container always listens on 8001) or from the MQTT `client_id` (copied configs would collide).
- **D-17:** The session lifetime is fixed at 30 days, sliding: re-issue the cookie once more than half the lifetime has passed. There is no config knob.
- **D-18:** There is no "Remember me" checkbox. The session always persists (`Max-Age`), as AUTH-03 requires.

### Claude's Discretion
- **Upgrade detection** for the D-06 notice is a heuristic of the planner's choosing, for example "any persisted state file in `/data`" or "config differs from the shipped default". It only affects one notice, so a wrong guess is harmless.
- The env flag name for the setup token, the exact copy text, the icons for Account and Logout, and the mobile nav layout tweaks.
- The internal structure of `auth.py`, `routes/auth.py`, `ratelimit.py` and the middleware order, within the locked research decisions.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Scope and requirements
- `.planning/REQUIREMENTS.md`: AUTH-01 to AUTH-15 (acceptance wording), Out of Scope table (no open mode, no Basic auth, no env override of a stored password)
- `.planning/ROADMAP.md` § Phase 1: success criteria
- `.planning/PROJECT.md`: constraints (prod :8001 untouchable, public repo, hash not in `config.yaml`, minimal deps)
- `.planning/STATE.md` § Blockers/Concerns: Phase 1 spike items (`HX-Redirect` on 401 with the pinned htmx, scrypt `maxmem` on the image's OpenSSL, real-browser Origin behavior over plain HTTP) and the warning about a missing persistent `/data` volume

### Research (locked technical approach)
- `.planning/research/SUMMARY.md`: resolved conflicts (CSRF, session, hash, gate) and the Phase 1 delivery list
- `.planning/research/ARCHITECTURE.md`: module layout (`auth.py`, `routes/auth.py`, `routes/health.py`, `ratelimit.py`), `auth.js` shim, route-inventory test
- `.planning/research/PITFALLS.md`: pitfalls 1-8, 14, 19, 20, 24, 26-29 (Phase 1 relevant)
- `.planning/research/STACK.md`: stdlib-only choices, scrypt parameters, throttle numbers
- `.planning/research/FEATURES.md`: peer-app patterns (Grafana and Portainer setup, Frigate throttle numbers)

### Existing decisions superseded later
- `docs/decisions.md`: AD-01 (no auth) is superseded by this milestone. The formal update lands in Phase 2 (HARD-10).

### Codebase maps
- `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/CONVENTIONS.md`, `.planning/codebase/TESTING.md`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `watermeter/templates/_theme.html` and `watermeter/static/style.css`: theme variables (light and dark) for the setup, login and account pages.
- `watermeter/templates/_nav.html`: nav rail and bottom bar. The theme toggle sits at the bottom (`margin-top: auto` on desktop). The Account and Logout items go beside it.
- `watermeter/__main__.py`: an argparse CLI (`--one-shot`). Extend it with a `set-password` subcommand for AUTH-10.
- `tests/integration/conftest.py`: `base_url` fixture (self-started container via `_docker_run` or `--base-url`) and `api` httpx client fixture. Add password seeding and login here.
- `tests/unit/conftest.py`: `test_client(mock_service, tmp_path, monkeypatch)`. Make it logged in by default.

### Established Patterns
- Router registration lives in `watermeter/app.py` (lines ~174-184). Static files are mounted at `/static` (line 158). Routers are imported after `safe_subpath` to avoid circular imports. New `routes/auth.py` and `routes/health.py` follow the same pattern.
- `/health` currently lives in `watermeter/routes/service.py:230`. Move it to `routes/health.py` (public surface).
- Route errors return JSON with `success` and `message` keys. Logging uses f-strings. There is no `print` in the service.
- Use `threading.Lock` for state touched from threads. Run blocking work (scrypt) through `asyncio.to_thread`.

### Integration Points
- Scripts: `debug.sh` (sources `.env`, `docker run` at line ~60), `debug_clean.sh` (health poll uses `curl /` at line 106, so it must probe `/health` or log in), `oneshot_test.sh` (the one-shot CLI runs without the web server, so mostly unaffected, but its nginx stub and container runs should be checked).
- CI: `.github/workflows/docker.yml` smoke test curls `/health` (stays open). It is mirrored in `.gitea/workflows/`.
- Compose: `docker-compose.yml` and `.env.example` document `WATERMETER_ADMIN_PASSWORD` (seed-only) and the setup-token flag.
- About 65 `fetch()` call sites in `watermeter/static/*.js`, plus HTMX polling in templates. `auth.js` must be loaded by every page, including setup and login if they use fetch.

</code_context>

<specifics>
## Specific Ideas

- Grafana and Portainer are the reference for first-run setup: same page for fresh and upgraded installs, with an explanatory notice on upgrade.
- The kiosk or wall tablet use case drives the long sliding session and the lack of a "Remember me" checkbox.
- The core value must hold during the upgrade window: HA keeps receiving readings even if nobody completes setup for weeks.

</specifics>

<deferred>
## Deferred Ideas

None. The discussion stayed within the phase scope. The already-deferred v2 items (UX-01 failed-attempt notice, API tokens, trusted-proxy auth) are unchanged.

</deferred>

---

*Phase: 01-auth-core-and-test-script-plumbing*
*Context gathered: 2026-10-11*
