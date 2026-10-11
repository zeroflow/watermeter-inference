# Pitfalls Research

**Domain:** Retrofitting single-admin auth, hardening, and stale-reading visibility onto an existing self-hosted FastAPI + Jinja2/HTMX IoT app that ships as a public Docker image and talks to Home Assistant over MQTT.
**Researched:** 2026-10-11
**Confidence:** HIGH for codebase-specific findings (read from source), HIGH for Starlette/paho/uvicorn behaviour (well-established), MEDIUM for HTMX header semantics (checked against htmx master source via Context7; confirm against the pinned 1.9.x when implementing).

Phase names used below (map to roadmap phases):
- **P-AUTH**: auth core (password, setup page, session, CSRF, 401 handling)
- **P-HARD**: hardening (write-only secrets, SSRF guard, rate limits, security headers)
- **P-HA**: Home Assistant visibility (availability/LWT, problem sensor, staleness)
- **P-UI**: dashboard banners (stale, pipeline not OK, connection loss)
- **P-X**: cross-cutting (tests, CI, scripts, upgrade path)

Codebase facts these pitfalls rest on (verified in source):
- 53 `POST/DELETE/PUT` routes and 36 `GET` routes across `watermeter/routes/*.py`, plus a mounted `StaticFiles` app and FastAPI's auto `/docs` and `/openapi.json`.
- Only `dashboard.html` uses HTMX (`hx-get="/api/status/html"` every 5s, `hx-post="/api/trigger"`); the other 7 pages use about 60 `fetch()` calls in 8 static JS files. HTMX 1.9.10 and Monaco are loaded from public CDNs (unpkg, jsdelivr) without SRI.
- `GET /api/config` returns the raw `config.yaml` text. `GET /api/mqtt/config` returns the password and `mqtt-config.js` writes it into the form and posts it back on save.
- `images.src` is fetched in `image_pipeline.fetch_whole_image`, `fetch_images`, `routes/roi.py` (two places), and is logged in clear (`Fetching whole image from {src_url}`, `Image source saved: {url}`).
- `MQTTPublisher.start()` creates the paho 2.x client and calls `connect()` with no `will_set`. `stop()` calls `loop_stop()` then `disconnect()`. A custom `_reconnect_loop` thread coexists with paho's own auto-reconnect. `reload_config` only reconnects when `broker/port/username/password` change.
- `pipeline_status` is derived from alignment failures only (STALE = `consecutive_alignment_failures >= max_consecutive_failures`). A reading rejected forever by plausibility yields `REJECTED`, never `STALE`. Age is computed with naive `datetime.now()`.
- `unit` tests build exactly one `TestClient` in `tests/unit/conftest.py` (`test_client`, no lifespan). Integration tests start a container with no env vars and call `/api/*` directly. CI smoke test (`docker.yml`) curls `/health` on a fresh container. `debug_clean.sh` and `oneshot_test.sh` start ephemeral containers; `--one-shot` never starts the HTTP app.
- Dev setup runs prod and debug on the same host on different ports (cookies are not port-isolated), and `config_debug` has HA publishing enabled.

---

## Critical Pitfalls

### Pitfall 1: Per-route auth leaves holes (mounted apps, docs, new routes)

**What goes wrong:**
Auth is added as `Depends(require_login)` on routers. `app.mount("/static", StaticFiles(...))` is a separate ASGI app and ignores router dependencies. `/docs`, `/redoc`, `/openapi.json` are registered on the app and ignore router dependencies, so the full list of 53 write endpoints stays publicly browsable. The next route someone adds forgets the dependency and ships open.

**Why it happens:**
FastAPI's idiomatic auth is dependency-based, and the app already has 11 routers; touching each is the "obvious" diff.

**How to avoid:**
Default-deny with one pure-ASGI middleware (not `BaseHTTPMiddleware`) and an explicit allowlist: `/health`, `/login`, `/setup` (only while unconfigured), `/static/*` (needed by the login page, contains no secrets), and nothing else. Disable or gate `/docs`, `/redoc`, `/openapi.json` (set `docs_url=None` etc., or put them behind auth). Add a unit test that iterates `app.routes` (including mounts), calls each with no cookie, and asserts 401/303 except the allowlist; this test is what keeps future routes honest.

**Warning signs:**
`curl` on any `/api/*` path without a cookie returns 200; `/openapi.json` reachable while logged out; the route-enumeration test does not exist.

**Phase to address:** P-AUTH

---

### Pitfall 2: Setup-takeover race and the "first visitor wins" window

**What goes wrong:**
On upgrade, every existing install exposes `/setup` until the owner visits. Anyone on the LAN (or anything that can reach the port) who gets there first sets the password and now owns the instance, including MQTT credentials via the old API surface and the SSRF-capable fetch. Secondary variants: two simultaneous POSTs to `/setup` both succeed (check-then-write race); `/setup` stays reachable after setup (returns the form or accepts a second POST); a container recreated without a persisted `/data` volume silently re-opens setup every time (Watchtower, `compose up --force-recreate`).

**Why it happens:**
The "set password on first visit" model is inherently a claim race (Grafana/Portainer accept it). Teams then forget the atomicity and the volume-loss case.

**How to avoid:**
- Setup POST takes a process-wide lock, re-checks "not configured" inside the lock, writes the hash with write-temp + `os.replace` (atomic), and from then on `/setup` returns 404/redirect to `/login` unconditionally.
- Support a non-interactive path that closes the window entirely: env var (e.g. `WATERMETER_ADMIN_PASSWORD` or a `_FILE` variant). If set, `/setup` never opens. Compare with `hmac.compare_digest`. This is also what `debug_clean.sh`, `oneshot_test.sh`, the integration fixture, and docs for hardened deploys use (see Pitfall 19).
- Log one loud startup line when running in setup-open state, with the bind address, so the window is at least visible.
- Detect "no persistent `/data`" (e.g. compare with a marker file the entrypoint writes only on first creation) and warn that the password will not survive recreation.
- Consider (decision for the roadmap, not assumed): an optional one-time setup token printed to the log. It closes the LAN race but contradicts "don't rely on users reading logs"; keep it optional if added.
- Accept and document that the residual race exists for non-env installs; the pipeline and MQTT must keep running while setup is open (do not gate `lifespan`).

**Warning signs:**
Two parallel `curl -X POST /setup` both return 200 in a test; `GET /setup` after setup returns anything but a redirect/404; no test covers "volume wiped".

**Phase to address:** P-AUTH

---

### Pitfall 3: No recovery path for a forgotten password

**What goes wrong:**
There is a single admin and no email. The user forgets the password, and the only fix is `docker exec` and hand-deleting a file they do not know about. Or worse, a recovery path exists that is itself unauthenticated ("reset" endpoint, or an `AUTH_DISABLED=1` escape hatch that users copy into their compose file permanently).

**Why it happens:**
Recovery is not in the happy-path stories. Escape hatches get added in a hurry during development to unblock tests and then ship.

**How to avoid:**
Recovery is operator-only and out-of-band: either (a) `python -m watermeter set-password` CLI (extend the existing `__main__.py` argparse; works via `docker exec` or `docker compose run`), or (b) documented "delete `auth.json` in `/data`, restart; setup reopens". Never ship an env var that disables auth; tests use a fixture that sets a known password and logs in, or `dependency_overrides`, not a runtime bypass flag. Document the recovery in README next to the setup docs. The env-var password should also override (and recover from) a lost file-stored one.

**Warning signs:**
An `AUTH_*=false`/`disable` config key in `config.yaml` or env; README has no "forgot password" section.

**Phase to address:** P-AUTH

---

### Pitfall 4: Session secret generated per start, per process, or stored in the wrong place

**What goes wrong:**
`SessionMiddleware(secret_key=secrets.token_hex())` at import time logs everyone out on every restart and every `:dev` image pull. Worse variants: key stored in `config.yaml` (shows in the config editor and in `GET /api/config`), key committed or hard-coded in the public repo (forgeable sessions for every installation), or key shared across containers because it was baked into the image.

**Why it happens:**
Tutorials use a constant string or a per-start random. Persisting a generated secret needs a deliberate file under `/data`.

**How to avoid:**
Generate once with `secrets.token_bytes(32)`, persist at `/data` with mode 0600 (atomic write; entrypoint already `chown -R` `/data` to `watermeter`), load on startup. Derive nothing from the image. Keep it out of `config.yaml`. uvicorn runs a single process in this image (CMD has no `--workers`), so an in-memory session store would work, but the persisted secret is still needed for restart survival. If a future `--workers N` appears, per-worker random keys break sessions; state in a comment/test that workers=1 is assumed. Include a "password epoch" in the signed session so changing the password invalidates old cookies (SessionMiddleware cookies are signed, not revocable server-side; logout only clears the browser's copy).

**Warning signs:**
Logged out after every container restart; `secret_key=` literal in source; two debug restarts produce different cookies for the same login.

**Phase to address:** P-AUTH

---

### Pitfall 5: Secure/SameSite cookie flags that break LAN HTTP, or that do nothing

**What goes wrong:**
`https_only=True` (cookie `Secure`) makes login "succeed" and the very next request be anonymous over plain `http://` on a LAN, which is the default deployment. The opposite mistake: leaving `Secure` off even when behind an HTTPS proxy. Also `__Host-` cookie prefix requires `Secure`, so it cannot be used on HTTP.

**Why it happens:**
Security checklists say "always set Secure". The app is explicitly HTTP-first (TLS is out of scope).

**How to avoid:**
Set `Secure` dynamically: only when the request is HTTPS as seen through a *trusted* proxy (`X-Forwarded-Proto` honoured only from configured proxy IPs; see Pitfall 12). Always `HttpOnly`, `SameSite=Lax`, bounded `Max-Age` (not Starlette's 14-day default without thought). Document that over HTTP the session is sniffable on the LAN and TLS via reverse proxy is the recommended answer. Test login over plain HTTP in the integration suite (it is the real deployment).

**Warning signs:**
Login redirects straight back to `/login` over HTTP; `Set-Cookie` lacks `HttpOnly`; no test logs in through plain HTTP.

**Phase to address:** P-AUTH

---

### Pitfall 6: Cookie collisions between instances on the same host (prod :8001 vs debug :8002)

**What goes wrong:**
Cookies are scoped by host, not port. Logging in to the debug instance overwrites the cookie for the production instance with the same name `session`, signed by a different secret. Both instances then reject each other's cookie and the developer is constantly bounced to `/login`. The same happens to any user running two containers (two meters, per `BL-69` scaling note) behind one hostname.

**Why it happens:**
The default cookie name is a constant and nobody tests two instances side by side.

**How to avoid:**
Name the cookie per instance, e.g. `wm_session_<first 8 hex of sha256(secret)>`, or include the listening port from config. Apply the same to the CSRF cookie if one is used. Treat this as a first-class acceptance test using two instances on one hostname.

**Warning signs:**
"Logged out" loops when switching between :8001 and :8002 tabs.

**Phase to address:** P-AUTH

---

### Pitfall 7: HTMX and `fetch()` get a login page instead of a 401

**What goes wrong:**
Middleware redirects unauthenticated requests with 302/303 to `/login` for everything. The browser's XHR/fetch follows the redirect transparently and receives 200 + login HTML. Effects: HTMX swaps the entire login form into `<main>` on the 5-second poll after the session expires (a login page nested inside the dashboard); every `fetch(...).then(r => r.json())` in the 8 JS files throws `Unexpected token '<'` and shows a generic error; `hx-post="/api/trigger"` swaps login HTML into `#status-message`.

**Why it happens:**
One redirect rule is written for browsers navigating and reused for machine requests.

**How to avoid:**
Decide response by request type, in one place:
- Page navigation (`Accept: text/html`, not `HX-Request`, not `/api/`): 303 to `/login?next=<safe relative path>`.
- HTMX (`HX-Request: true`): 401 with `HX-Redirect: /login` (htmx processes `HX-Redirect` before the status check, so it works on a 401; confirm on the pinned 1.9.x). Note: htmx 1.x does not swap 4xx/5xx by default, so without the header the dashboard would silently freeze with stale content.
- Everything else under `/api/`: JSON `{"detail": "..."}` with 401, no redirect.
- Add one shared JS helper (e.g. `static/api.js`) that wraps `fetch`, checks `status === 401` and navigates to `/login`, and attaches the CSRF header (Pitfall 8). Migrate all ~60 call sites, otherwise one page silently breaks. Add a lint-ish test that greps static JS for bare `fetch(` outside the helper.
- Validate `next` is a single-slash relative path (reject `//host`, `/\host`, absolute URLs) to avoid an open redirect.
- The login POST should answer 303 (not 307) so the browser does not replay the POST to the target.

**Warning signs:**
Dashboard shows a nested login form; browser console `Unexpected token <` after session expiry; `next=https://evil` is honoured.

**Phase to address:** P-AUTH (server behaviour, JS helper), P-UI (banner for session-expired vs connection-lost)

---

### Pitfall 8: CSRF protection that misses fetch(), FormData, HTMX, or is defeated by "same-site"

**What goes wrong:**
- Relying on `SameSite=Lax` alone: ports are ignored by the same-site check, so any other web service on the same LAN host (another container's UI on a different port) is "same-site" and can POST with the cookie. Lax also does nothing for non-browser contexts.
- Most endpoints use `await request.json()` without checking `Content-Type`, so a cross-site `<form enctype="text/plain">` can deliver a JSON-shaped body.
- A token added to HTMX (`hx-headers` / `htmx:configRequest`) but not to the ~60 `fetch()` calls, or not to `FormData`/ZIP upload (`training.js`), or the token lives in a meta tag only some templates render (only `dashboard.html` loads HTMX; there is no shared base template, so each of 8 pages needs the token).
- Putting `hx-headers` on an element inside the swapped `<main>`, so the first poll replaces it and later requests lose the header.
- Any state-changing `GET` slipping through.

**Why it happens:**
CSRF is bolted on after the fact into a frontend with no central request layer and no base template.

**How to avoid:**
Layer two cheap, stateless checks on every unsafe method (POST/PUT/PATCH/DELETE) for cookie-authenticated requests: (1) `Origin` (fall back to `Referer`) must match the request `Host`/trusted origin; (2) a custom header (`X-CSRF-Token` equal to an HMAC of the session, or at minimum `X-Requested-With`) which forces a CORS preflight that a foreign origin cannot pass. Install the header in exactly two places: the shared fetch wrapper and a `htmx:configRequest` listener registered on `document.body` (outside any swapped region). Return 403 JSON on failure so the wrapper can show a clear message. Audit that no GET mutates state (grep routes). Browser native `<form>` posts (`/login`, `/setup`, logout) need the token as a hidden field or the Origin check.

**Warning signs:**
A forged cross-origin POST from another local port succeeds in a manual test; one page's save button returns 403 while others work; token missing after the first HTMX poll.

**Phase to address:** P-AUTH

---

### Pitfall 9: Write-only secrets get blanked, overwritten with the mask, or leak via the raw editor

**What goes wrong:**
- `GET /api/mqtt/config` starts returning `""`/`"********"`; `mqtt-config.js` writes it into the field and posts the whole form back on save, so the stored password is overwritten with empty or with the literal mask. MQTT auth silently breaks on the next reconnect (and `reload_config` compares `password` and reconnects with the wrong one).
- `GET /api/config` returns the raw YAML text; masking only the JSON endpoint leaves the password in plain text in the config editor (Monaco). Masking by regex in the raw text then saving the masked text back writes the mask into `config.yaml` (comment-preserving ruamel round-trip faithfully preserves the bug).
- "Test connection" (`/api/mqtt/test`) with an empty password field now cannot test, or, if it falls back to the stored password, can be used to send the stored password to an attacker-chosen broker host.
- `${MQTT_PASSWORD}` env-token values are references, not secrets; masking them confuses users and loses the existing token-substitution feature.
- Camera URL with embedded credentials (`http://user:pass@cam/...`, supported by the camera firmware) lives in `images.src` and is logged in clear (`Fetching whole image from ...`, `Image source saved: ...`) and returned by several endpoints.
- Other leaks: error messages that echo the config, debug/export bundles, `/api/status`.

**Why it happens:**
"Return `***`" is done on one endpoint; the frontend treats GET output as the source of truth for the form on save.

**How to avoid:**
Three-state semantics for secret fields on write: field absent/`null` = keep stored value; non-empty string = replace; explicit clear flag = delete. Never round-trip the mask: the server treats a value equal to the mask sentinel as "keep". Return a boolean `password_set: true` instead of the value; the UI shows "(unchanged)" placeholder. For the raw editor: serve the YAML with literal secret values replaced by a sentinel and, on save, re-substitute the stored value wherever the sentinel remains (validate before writing; add a regression test in `tests/regression/test_config_comments.py` style that comments survive and the stored secret is intact after save-without-edit). Leave `${ENV}` tokens visible. For test-connection with an unchanged password, only reuse the stored one when broker host and port equal the saved ones. Redact userinfo in every log line and API response that prints a URL (one `redact_url()` helper). Add `password`-like fields to one central list in `config_utils.py` so new secrets are masked by default.

**Warning signs:**
After saving the MQTT page without touching the password, HA entities go stale (broker auth failing); `grep -rn "password" watermeter/routes` returns more than one place that builds a response; `curl /api/config | grep -i pass` still shows a literal.

**Phase to address:** P-HARD

---

### Pitfall 10: SSRF guard blocks the legitimate camera, or is bypassable

**What goes wrong:**
Two failure directions.
Over-blocking: the camera is on a private range (RFC1918), so blocking "private" addresses breaks every install, including the stub sidecar in `debug_clean.sh`/`oneshot_test.sh` (docker-network address) and the integration fixture. Blocking loopback can also break users who run a camera relay (go2rtc and similar) on the same host with host networking; at minimum the error must say so.
Under-blocking (bypasses):
- Decimal/hex/octal/short IPv4 (`http://2130706433/`, `http://0x7f.1/`, `http://127.1/`): `ipaddress.ip_address()` rejects these as non-IPs, so the check falls through to "it's a hostname", yet glibc `getaddrinfo`/`inet_aton` resolve them to 127.0.0.1.
- IPv4-mapped IPv6 (`::ffff:127.0.0.1`, `[::ffff:a9fe:a9fe]`) passes a naive IPv4-only denylist; check `ip.ipv4_mapped` too, plus `is_loopback`, `is_link_local` (169.254.0.0/16 incl. the cloud metadata address, `fe80::/10`), `is_unspecified` (`0.0.0.0`, `::`), `is_multicast`.
- Hostnames that resolve to several addresses: must check all A and AAAA results, not the first.
- Parser differentials: validating with `urllib.parse.urlparse` while fetching with `httpx.URL` (userinfo tricks like `http://cam@127.0.0.1/`, `http://127.0.0.1#@cam/`, trailing-dot `localhost.`). Validate using the same parser the fetcher uses.
- Redirects: `follow_redirects` is off by default in httpx, so do not turn it on; if it is ever enabled, re-validate each hop.
- DNS rebinding (resolve-then-fetch TOCTOU): a full pin is heavy; the pragmatic design is to resolve and check immediately before each request and keep redirects off. Because the endpoint now requires auth, rebinding is low residual risk; do not over-engineer, but write down the accepted gap in the new decision record.
- Guard applied only to `/api/roi/image-source`: `images.src` can also be set through `POST /api/config/save` (raw editor), by editing `config.yaml`, and is fetched at three other sites (`fetch_whole_image`, `fetch_images` which rewrites the URL with `rsplit`, `fetch-image`), so the guard must be a single function called at every fetch site and in config validation (`config_utils` schema).

**Why it happens:**
Denylists are written against dotted-quad strings; the guard is placed on the one form that was in the threat report.

**How to avoid:**
One `validate_fetch_url(url)` in a small module: scheme in {http, https}; parse with httpx; reject empty host and userinfo if undesired; resolve via `socket.getaddrinfo` (in a thread) and check every returned address against: loopback, link-local (including metadata), unspecified, multicast, reserved, plus IPv4-mapped unwrapping. Allow RFC1918 and ULA. Call it from the three `image_pipeline`/`roi` fetch sites and from config validation. Table-driven unit tests with the bypass strings above (decimal, hex, octal, `127.1`, `::ffff:`, `localhost.`, userinfo) and one "private LAN camera IP is allowed" test.

**Warning signs:**
Test list contains only `127.0.0.1` and `169.254.169.254` literals; the guard has no call in `image_pipeline.py`; `grep -rn "httpx" watermeter/` shows fetches not routed through the validator.

**Phase to address:** P-HARD

---

### Pitfall 11: SSRF guard turns an existing install into a silently dead pipeline on upgrade

**What goes wrong:**
An existing `images.src` that the new guard rejects (loopback relay, a hostname that fails DNS at startup, a camera briefly offline during resolution) makes `fetch_whole_image` return `None` on every cycle. The failure path logs an error, alignment counters climb to STALE after N cycles, and HA shows nothing until then. This is exactly the "silently frozen" failure the milestone is meant to remove, introduced by the milestone.

**Why it happens:**
Validation written for the "set URL" form is reused in the runtime fetch path where failure semantics differ (DNS hiccup is transient, a blocked range is permanent).

**How to avoid:**
Separate the two: strict validation when a URL is *set* (reject with a clear message), and at *fetch* time distinguish "blocked by policy" (permanent: log once at ERROR, set a distinct `last_alignment_error`/status string visible on dashboard and HA, do not retry-storm) from "DNS failed/timeout" (transient: normal DEGRADED path). Do not fail startup on DNS failure. Give a documented escape hatch for the loopback relay case only if the roadmap accepts it (explicit config key, default off), instead of silently weakening the guard.

**Warning signs:**
After deploying to the debug container the dashboard shows alignment failures with no hint that the URL was refused.

**Phase to address:** P-HARD (with P-HA for surfacing the reason)

---

### Pitfall 12: Rate limiting and cookie logic behind a proxy see one client IP

**What goes wrong:**
Behind nginx/Traefik/Caddy/HA ingress, `request.client.host` is the proxy for everyone, so a per-IP login limiter locks out the owner after a stranger's attempts (or a single failed-login counter is shared by all). The "fix", `--forwarded-allow-ips '*'` or trusting `X-Forwarded-For` blindly, lets any LAN client spoof the header and evade the limiter. uvicorn's `--proxy-headers` is on by default but only trusts `127.0.0.1` (or `$FORWARDED_ALLOW_IPS`), which never matches a proxy in another container, so `X-Forwarded-Proto` is ignored and the dynamic `Secure` flag from Pitfall 5 never turns on.

**Why it happens:**
Limiters are keyed `get_remote_address` by habit.

**How to avoid:**
Single admin means the thing to protect is one account, not many: throttle the login endpoint globally with progressive delay/backoff (not hard lockout, which hands an attacker a denial of service on the owner), and additionally per-IP only when the peer is not a trusted proxy. Add a config/env `trusted_proxies` list that feeds both uvicorn's forwarded-allow-ips handling and your own checks; default empty (do not trust). Cap the limiter's dictionary size and expire entries. Document the compose snippet for proxies.

**Warning signs:**
One wrong password from the phone locks the laptop; limiter test only covers a direct connection; `X-Forwarded-For: 1.2.3.4` changes the limiter bucket without any proxy configured.

**Phase to address:** P-HARD

---

### Pitfall 13: Login throttling counts the dashboard's own polling, and rate limits break HTMX UX

**What goes wrong:**
(a) Unauthenticated polling (`every 5s`) after session expiry produces a stream of 401s; if 401s feed the brute-force counter, the legitimate user triggers their own lockout. Only failed *login attempts* should count. (b) Limits on `/api/trigger`, training, synthetic generation return 429; htmx 1.x does not swap error responses, so the "Read Now" button just does nothing. The `/api/trigger` limit also must not apply to the MQTT trigger and the cyclic loop (they call the service directly, not HTTP), or the wrong layer is limited. (c) Time-based limits on training/synthetic do not address the real problem (CPU contention in the single process); a concurrency guard ("one job at a time, 409 if running") is what protects inference.

**Why it happens:**
Limiter is applied as a blanket middleware rather than per-endpoint with purpose.

**How to avoid:**
Decorate only the compute-heavy handlers and the login POST. Use 429 + `Retry-After` and have the frontend wrapper/HTMX `htmx:responseError` handler show it in `#status-message`. For `/api/trigger` prefer "reject if `processing_lock` is held" over a rate. Keep `/health`, `/api/status*`, `/api/metrics` unlimited.

**Warning signs:**
Lockout appears after leaving a dashboard tab open overnight; clicking "Read Now" twice quickly yields no feedback.

**Phase to address:** P-HARD, P-UI

---

### Pitfall 14: Password hashing that is slow in the wrong place or silently weak

**What goes wrong:**
Argon2/bcrypt/scrypt run synchronously in an `async def` handler and block the single event loop (and the reading pipeline's awaits) for 100-500 ms per attempt on the small hardware this runs on; an attacker's login flood becomes a DoS on the meter reading. bcrypt silently truncates input at 72 bytes. Verifying an env-var password with `==` instead of a constant-time compare. Adding a native dependency (`argon2-cffi`, `bcrypt`) to `requirements-docker.txt` that lacks a wheel for the base image's Python (OpenVINO Ubuntu 22.04 base, Python 3.10) and breaks the image build.

**How to avoid:**
Pick one: stdlib `hashlib.scrypt` (zero new dependency, wheel-free) or `argon2-cffi` with moderate parameters, after confirming a manylinux wheel resolves in the actual Docker build and CI. Run hash/verify via `asyncio.to_thread` after the rate-limit check passes. Store as a self-describing string (params + salt + hash) so parameters can be upgraded. Use `hmac.compare_digest` for any secret comparison. Cap password length (e.g. 1024) to avoid hashing megabyte bodies.

**Warning signs:**
Reading pipeline timings spike during login tests; `docker build` fails at `pip install`; `time` on a login attempt exceeds ~300 ms on the target device.

**Phase to address:** P-AUTH

---

### Pitfall 15: Strict CSP / `X-Frame-Options` breaks the app (CDN scripts, inline code, HA iframes)

**What goes wrong:**
- `script-src 'self'` blocks HTMX (unpkg) and Monaco (jsdelivr, which also needs `worker-src blob:` and inline style). The dashboard polling and the config editor die with only a console message. Templates contain many inline `style=`/`onclick=`/`onchange=` attributes and inline scripts (counts per template: roi_config ~64, training ~71, explore ~22), so `unsafe-inline` or a nonce/refactor is unavoidable for any real CSP.
- Offline installs: CDN-hosted HTMX/Monaco already fail without internet; a CSP makes that failure permanent even with internet.
- `X-Frame-Options: DENY` breaks users who embed the dashboard in a Home Assistant panel/iframe card, and under `SameSite=Lax` the embedded page would not get the session cookie anyway (cross-site frame), so it shows the login page inside the iframe.
- Headers added via `add_middleware` order: the security-headers middleware must be outermost so 401/403/500 and static responses also carry them; headers added in a route-level dependency miss errors and `StaticFiles`.

**How to avoid:**
Ship in two steps. Step one (safe, no breakage): `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`/`no-referrer`, `frame-ancestors 'none'` (or `X-Frame-Options: DENY`) with a documented, config-gated allow for HA embedding (`allow_framing` / `frame-ancestors <origin>`), `Cache-Control: no-store` on authenticated HTML and API JSON. Step two (optional, separate): vendor HTMX and Monaco under `/static/vendor` (also fixes offline LAN installs and gives you SRI-free pinned assets), then a CSP like `default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; worker-src 'self' blob:`. Do not claim CSP-based XSS protection while `unsafe-inline` is on. `status_fragment.html` embeds `data:` images, so `img-src` needs `data:`.

**Warning signs:**
Blank dashboard + CSP violations in the console after the header change; the config editor spinner never resolves.

**Phase to address:** P-HARD

---

### Pitfall 16: Availability topic and LWT mistakes with paho-mqtt 2.x

**What goes wrong:**
- `will_set()` called after `connect()`: it only applies on the *next* connect. In `MQTTPublisher.start()` the order must be `username_pw_set` -> `will_set` -> `connect`.
- Will published without `retain=True`: HA restarting after the broker published `offline` never sees it, and entities appear available.
- "online" published once at startup instead of in `on_connect`: after an unclean disconnect the broker publishes the retained `offline` will, the client reconnects, and nothing publishes `online` again, so HA shows unavailable forever while data flows. `on_connect` fires on every reconnect (both paho's auto-reconnect and the custom `_reconnect_loop`), so publishing retained `online` there (QoS 1) is the correct and idempotent place.
- A clean `disconnect()` does **not** trigger the will. Publish retained `offline` explicitly in `stop()` *before* `loop_stop()`/`disconnect()` and wait (`wait_for_publish`, short timeout). The existing `stop()` calls `loop_stop()` first, which can leave the final publish unsent.
- Fixed `client_id` collisions: two clients with the same id kick each other, and each kick fires the other's will. `config_debug` ships a different id from the default, but if prod and debug share the availability/state topic (the debug config has HA enabled and the shipped default topic), the debug container's clean `offline` on `stop()` marks **production** entities unavailable, and its `online` masks a dead prod. Derive the availability topic from the (unique) publish topic and make `debug.sh` configs use a distinct topic or disable HA publishing.
- Availability topic and `client_id` changed via the config editor take effect only after reconnect; `reload_config` currently reconnects only on `broker/port/username/password`, so add `client_id`, `publish_topic` and the availability topic to that comparison or the old will stays registered.
- MQTT-level liveness != pipeline liveness: if the pipeline hangs (unbounded `processing_lock.acquire()`, a hung camera fetch) the MQTT thread still pings, the will never fires, and availability stays `online`. Availability only covers process/connection death.

**How to avoid:**
Implement exactly: `will_set(avail_topic, "offline", qos=1, retain=True)` before `connect()`; in `on_connect` publish `online` retained; in `stop()` publish `offline` retained first; add `availability_topic`, `payload_available`, `payload_not_available` to every discovery payload (or a device-level `availability` list with `availability_mode: all` if combining with the problem sensor), and republish `online` when `homeassistant/status` == `online`. Test with a fake paho client asserting call order (`will_set` before `connect`), retain flags, and that `on_connect` publishes `online`. Update `tests/unit/test_mqtt_reconnect.py` expectations rather than weakening them.

**Warning signs:**
Entities stuck `unavailable` while logs show readings published; entities `available` while the service is crashed; debug container restart flips production entities.

**Phase to address:** P-HA

---

### Pitfall 17: The HA "problem" state is itself silent when it matters, or misses the actual failure modes

**What goes wrong:**
- A `binary_sensor` problem entity that publishes `off` and has **no availability** shows "OK" while the service is dead. Automations written as `state: on` never fire for `unavailable`. The problem sensor must share the availability topic, and docs/blueprint should alert on `on` **or** `unavailable`.
- Staleness evaluated only inside `process_reading`: if the loop is hung or the camera never answers, nothing runs and nothing is flagged. Evaluate on an independent timer (the existing stats loop is a candidate) and publish from there. Mind the existing unbounded lock (CONCERNS.md): a timer must not take `processing_lock`.
- Status derived from alignment failures only: `STALE` ignores the case "alignment fine, every reading rejected by plausibility" (`REJECTED` forever, previous value frozen). That is the "silently frozen" scenario of the core value. Include `consecutive_rejections >= N` and "no accepted reading for T" in the problem condition, not just `pipeline_status in {FAILED, STALE}`.
- Mapping a single transient `DEGRADED`/`REJECTED` to `on` produces flapping and notification fatigue; use `FAILED`/`STALE`, N consecutive rejections, or age threshold, with the thresholds in config and exposed as attributes.
- Adding a new enum value (e.g. `stale`) to the `status` sensor payload without adding it to the discovery `options` list makes HA log errors and reject the state. `pipeline_status` options are `OK/DEGRADED/REJECTED/FAILED/STALE`; extend the discovery list and republish discovery in the same change.
- Existing `_notify_stale` is edge-triggered, non-retained and QoS 1: a restart during STALE loses the one-shot. A level-based retained entity is the fix; keep the event as extra.
- Retained state after restart: the sensor must be recomputed and republished on startup, otherwise HA keeps the last retained `on`/`off` from before the restart.

**How to avoid:**
One `problem` boolean computed from a single function in `WatermeterService`/`meter_state` (unit-testable, no I/O), published on the same payload as the others via the existing `_HA_ENTITIES` table with `component: binary_sensor`, `device_class: problem`, sharing availability. Add a `problem_reason` text attribute (reuse `last_alignment_error`, last rejection reason). Document the automation snippet for `unavailable`.

**Warning signs:**
Kill the container: problem sensor still `off`; make plausibility reject every reading: `pipeline_status` is `REJECTED` but problem stays `off`; HA logs "Invalid option for enum sensor".

**Phase to address:** P-HA

---

### Pitfall 18: Stale-detection false positives and wrong clocks

**What goes wrong:**
- Fixed thresholds ignore the trigger mode. `cyclic_interval` defaults to 300 s but the shipped `trigger.mode` is `mqtt` (external, unknown cadence): a 10-minute fixed threshold flags a healthy install that triggers hourly. Conversely at low intervals (10-30 s) a single slow fetch or long processing crosses a "2 x interval" line.
- After a restart, `last_update_time` is loaded from persisted state, so age is large until the first successful reading: a false "stale" for one interval (or forever in `mqtt` mode until the next external trigger). Needs a startup grace period and/or an "unknown" state distinct from "stale".
- Timestamps are naive local `datetime.now()` (`last_published_iso`, `_derive_pipeline_status`). DST changes, TZ changes between container restarts, or container TZ != HA TZ create hour-sized errors, even negative ages (clamped to 0, hiding staleness). Use `time.time()`/UTC (aware datetimes) for age math and keep display formatting separate.
- "Unchanged value" is not stale: at night the meter does not move, so age since *value change* is the wrong clock. Age must be "since last accepted reading (even if unchanged)" and it must be verified that unchanged accepted readings still advance `last_update_time`/`last_published_iso` and are still published.
- `expire_after` on HA sensors as a free safety net is tempting; if set below the real publish cadence the entities flap `unavailable`. Only set it in cyclic mode, as a multiple (>= 3x) of the interval plus processing time.

**How to avoid:**
Threshold = `max(configured_min, k x cyclic_interval)` in cyclic/both mode; in `mqtt` mode require an explicit `stale_after_seconds` (generous default, e.g. hours) or disable the age condition and rely on consecutive-failure logic. Use monotonic/UTC clocks, a startup grace window, and a "no reading yet" state. Unit tests with an injected clock for: restart, DST jump, interval 10 s, interval 3600 s, unchanged value overnight.

**Warning signs:**
Problem sensor flaps after every restart; sensor on at 03:00 when the clocks change; false alarms only in `mqtt` trigger mode.

**Phase to address:** P-HA, P-UI

---

### Pitfall 19: Existing tests, CI smoke test, helper scripts and one-shot break the moment auth lands

**What goes wrong:**
- `tests/unit/conftest.py::test_client` builds the app without lifespan; the default-deny middleware makes every existing unit test that hits an API return 401/303. The tempting quick fix is a global bypass flag in production code (see Pitfall 3).
- Tests that run in a `tmp_path` cwd rely on `config.yaml` and `/data` paths; the auth store (hash file, session secret) must be injectable/relocatable or the first test run writes into the real `/data` or shares state across tests (module-level singleton).
- `tests/integration/conftest.py::_docker_run` starts `docker run image` with no env and then calls `/api/*` with a bare `httpx.Client`; every integration test would hit the setup redirect. It needs a password env var in `_docker_run` and a login step (cookie jar + CSRF header) in the `api` fixture, and `--base-url` mode against :8002 needs `WATERMETER_TEST_PASSWORD`-style input.
- `docker.yml` smoke test curls `/health` on a fresh container: keep `/health` 200 and unauthenticated in setup-open state, with no redirect (curl `-sf` fails on 3xx? it treats redirects as success only when followed; do not rely on it). Do not enrich `/health` with data that needs the session.
- Compose healthcheck uses httpx on `localhost`: **never** add a "localhost is trusted" exemption to auth; combined with SSRF to self it would become a bypass.
- `debug_clean.sh`, `oneshot_test.sh`, `debug.sh`: containers start with no password so any scripted `curl` to `/api/...` follows a redirect to `/setup`. Pass the env var in their `docker run` lines (they also mount nothing persistent, so setup would reopen on every run).
- `--one-shot` runs `python -m watermeter --one-shot` and does not start the HTTP app, so it must not import or require auth state; keep the auth module import-light and lazy. If the SSRF guard lives in the shared fetch path, `oneshot_test.sh`'s stub hostname (docker-network private address) must be allowed and DNS must work in that container.
- Regression tests (`tests/regression`) are not in CI (`BL-36`): auth plus config round-trip regressions could land unnoticed. Add the new route-enumeration and secret-masking tests under `tests/unit`.

**How to avoid:**
Provide a fixture-level seam only: an `auth_store` dependency that tests override (`app.dependency_overrides`) or a fixture that sets a known password in a tmp dir and returns an authenticated client wrapper. Update `test_client` once. Update integration `_docker_run` and the `api` fixture once. Update the three scripts and README/DOCKER.md snippets together with the auth change, in the same plan, not afterwards. Add an explicit acceptance step: run `debug_clean.sh`, `oneshot_test.sh`, and the `docker.yml` smoke commands against the branch before merging.

**Warning signs:**
Hundreds of unit failures with 401/303; CI smoke job red on the first auth commit; `debug_clean.sh` leaves you at `/setup`.

**Phase to address:** P-X (plan alongside P-AUTH; do not defer)

---

### Pitfall 20: Upgrade path surprises for existing installs

**What goes wrong:**
Users pull `:latest`/`:dev` via Watchtower or cron `compose pull`; the next visit shows `/setup` (intended), but other consumers break without notice: bookmarked scripts, home-grown `curl` automations against `/api/trigger` or `/api/reset`, third-party dashboards polling `/api/status`, HA REST sensors. The release note is the only warning. Also `restart: unless-stopped` plus a missing `/data` mount (compose in the repo mounts it, but custom setups may not) means setup reopens on each image update.

**How to avoid:**
Be explicit in the milestone's docs and in the setup page: "API clients now need a session; machine tokens are out of scope" and state the MQTT trigger/reset path is unaffected and recommended for automation (both exist: `trigger_topic`, `reset_topic`). Keep `/health` open. Log a clear warning at startup for unauthenticated-state installs. Add the env-var password to `docker-compose.yml` comments and `.env.example`. Do not move or rename `/data` or `/config` contents.

**Warning signs:**
GitHub issues titled "trigger API returns 401 after update"; users with no persistent `/data` repeatedly hitting setup.

**Phase to address:** P-AUTH (docs), P-X

---

## Moderate Pitfalls

### Pitfall 21: Dashboard banners that cannot appear when needed

**What goes wrong:**
- Banner placed inside `<main hx-get=...>`: it is replaced on every swap; and on connection loss HTMX leaves the old (healthy-looking) fragment in place, so the stale content looks fine. Place banners outside the swap target (same reason the Pipeline Health widget already lives outside it).
- htmx default `timeout` is 0 (no timeout): a hung server never fires `htmx:timeout`/`sendError`, so no banner. Set `htmx.config.timeout` (e.g. 8-10 s, polling every 5 s).
- Single failed poll flips the banner: the server is legitimately slow during training (in-process, GIL) and during synchronous scans (`mislabel scan`), so require N consecutive failures (e.g. 3) and clear on first success.
- Distinguish causes: network error/timeout/5xx = "connection lost", 401 = "session expired, redirect to login", 429 = rate-limited. Wire `htmx:sendError`, `htmx:timeout`, `htmx:responseError`.
- Age computed from absolute timestamps compared against the browser clock breaks with clock skew between browser and container; use the server-provided `last_valid_reading_age_seconds` plus locally elapsed monotonic time since that poll.
- Background tabs throttle timers; use `visibilitychange` to re-poll and re-evaluate on return.
- The auto-refresh toggle (`toggleAutoRefresh`) disables polling entirely; with polling off the connection-lost banner will never trigger. Keep a lightweight heartbeat independent of the toggle, or show "auto-refresh off" explicitly.
- Banner copy/strings: static JS has German strings (`BL-55`); new banner text should be English.

**How to avoid:** One small `status-banner.js` (loaded on dashboard only) owning the state machine; unit-test the pure state function (inputs: last-success age, failure count, pipeline status, session state).

**Phase to address:** P-UI

---

### Pitfall 22: Reverse proxy / sub-path assumptions

**What goes wrong:**
The app uses absolute paths everywhere (`/static/...`, `fetch('/api/...')`, `hx-get="/api/status/html"`). Auth redirects to absolute `/login` and `Set-Cookie Path=/` additionally break when proxied under a sub-path, and `root_path` fixes only generated OpenAPI URLs. Trying to support sub-paths during this milestone expands scope across every template and JS file.

**How to avoid:**
State "sub-path proxying is unsupported; use a dedicated hostname or port" in the docs (consistent with the existing code), keep redirects relative-path-validated (Pitfall 7), and do not add `root_path` handling. Make sure trusted proxy settings (Pitfall 12) are documented instead.

**Phase to address:** P-AUTH (docs)

---

### Pitfall 23: Config changes not threaded through `config_utils` schema, `reload_config`, and the entrypoint

**What goes wrong:**
New config keys (stale thresholds, `trusted_proxies`, `allow_framing`, availability topic) added to code but not to the `config_utils.py` validation schema are rejected or dropped by the editor. `docker-entrypoint.sh` still `sed`-edits `config.yaml` for model paths on first run; adding auth state to that file contradicts the constraint to keep the hash out of `config.yaml`. The auth store belongs in `/data` (hash, session secret), which the entrypoint already `chown`s.

**How to avoid:** Add keys to the schema first, run `tests/regression/test_config_comments.py` locally (it is not in CI), keep auth state in `/data/*.json` with 0600, and extend `reload_config` for MQTT keys that change the will.

**Phase to address:** P-HARD, P-HA

---

### Pitfall 24: `BaseHTTPMiddleware` pitfalls and middleware ordering

**What goes wrong:**
Starlette's `BaseHTTPMiddleware` interferes with background tasks and streaming and adds overhead on every 5-second poll; `@app.middleware("http")` has the same behaviour. Middleware added later wraps earlier ones, so auth before security-headers (or the reverse) changes which responses carry headers; exceptions raised in auth middleware bypass later middleware.

**How to avoid:** Pure ASGI classes for auth, CSRF, security headers; register security-headers last (outermost); test that 401/403/500/static responses carry the headers.

**Phase to address:** P-AUTH, P-HARD

---

### Pitfall 25: Duplicate reconnect machinery makes availability messages noisy

**What goes wrong:**
The existing `_reconnect_loop` thread plus `reconnect_delay_set` + `loop_start()` means two reconnect mechanisms race (noted as fragile in CONCERNS.md). Adding "publish online in `on_connect`" is idempotent, but adding stateful logic (counters, "published once" flags) in `on_connect` will misbehave when it fires twice.

**How to avoid:** Keep the availability publish stateless and idempotent; do not refactor the reconnect logic in this milestone (out of scope), but do not add new non-idempotent state to those callbacks.

**Phase to address:** P-HA

---

## Minor Pitfalls

### Pitfall 26: Login page hygiene
**What goes wrong:** Login/setup page includes the dashboard's polling script or `_nav.html` links that fetch status (leaking pipeline info pre-login); missing `autocomplete="current-password"` / `new-password`; password confirmation field absent on setup (typo locks owner out); trivial passwords accepted.
**Prevention:** Minimal standalone templates; confirmation field; minimum length (e.g. 8) without composition rules.
**Phase:** P-AUTH

### Pitfall 27: Error messages leak internals
**What goes wrong:** Handlers return `f"Error: {str(e)}"` (seen in `roi.py`, `config.py`); with SSRF now guarded, messages differentiate "blocked", "refused", "timed out", turning the endpoint into an internal port scanner for an authenticated user or a CSRF'd request.
**Prevention:** Generic message for blocked/unreachable on the SSRF path; details to logs only.
**Phase:** P-HARD

### Pitfall 28: Public-repo hygiene while documenting
**What goes wrong:** README/docs examples for proxy and HA automations contain real hostnames, IPs, or HA entity names; test fixtures include real camera addresses.
**Prevention:** Use `192.168.x.x`/`example.lan` placeholders as the repo already does; add the CI grep suggested in CONCERNS.md for private IPs and home paths.
**Phase:** P-X

### Pitfall 29: Unlimited unauthenticated body size on setup/login
**What goes wrong:** Login/setup accept arbitrary-length bodies and fields into the hasher.
**Prevention:** Cap body and password length before hashing (the existing unbounded base64 endpoints are a separate concern, but do not copy the pattern).
**Phase:** P-AUTH

### Pitfall 30: HTMX pinned from a CDN without SRI
**What goes wrong:** `https://unpkg.com/htmx.org@1.9.10` is a third-party script with full session access; unpkg compromise or outage equals dashboard compromise or breakage. Auth raises the stakes of that trust.
**Prevention:** Vendor it under `/static/vendor` (combines with Pitfall 15 step two); at minimum add `integrity` + `crossorigin`.
**Phase:** P-HARD

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| P-AUTH: middleware design | Per-route dependencies miss mounts/docs/new routes (1) | Default-deny pure-ASGI middleware + route-enumeration test |
| P-AUTH: setup page | Claim race, reopen after wipe, no recovery (2, 3) | Lock + atomic write, env-var password, CLI/file-delete recovery |
| P-AUTH: sessions | Secret regenerated, cookie collisions, Secure on HTTP (4, 5, 6) | Persisted 0600 secret in `/data`, per-instance cookie name, dynamic Secure |
| P-AUTH: frontend | HTML login swapped into fragments; fetch gets HTML (7) | 401 + `HX-Redirect` for HTMX, JSON 401 for `/api`, shared fetch wrapper |
| P-AUTH: CSRF | Lax is not enough on a shared host; missing header on fetch/FormData (8) | Origin check + custom header, installed in wrapper and `htmx:configRequest` on `body` |
| P-AUTH: hashing | Blocking event loop, native-wheel build break (14) | `hashlib.scrypt` or verified argon2 wheel, `to_thread` |
| P-HARD: secrets | Mask round-tripped and overwrites password; raw editor leak (9) | Three-state write semantics, sentinel re-substitution, `password_set` flag |
| P-HARD: SSRF | Blocks the camera or is bypassed; guard on one route only (10, 11) | Single resolver-based validator, allow RFC1918, call at every fetch site and in config validation |
| P-HARD: limits | Shared proxy IP, polling counts as failures, 429 invisible in HTMX (12, 13) | Global login backoff, trusted-proxy config, concurrency guards for compute |
| P-HARD: headers/CSP | CDN scripts, inline code, HA iframe embedding (15, 30) | Staged headers, vendor assets, gated framing |
| P-HA: availability | `will_set` order, retain, online-on-connect, clean-stop offline, shared topic with debug (16) | Fake-client tests for ordering and flags; per-instance topic |
| P-HA: problem sensor | Silent when service dead; REJECTED-forever not covered; enum options (17) | Share availability, include rejection/age conditions, extend discovery options |
| P-HA / P-UI: staleness | Wrong threshold per trigger mode, restarts, naive clocks (18) | Mode-aware threshold, grace period, UTC/monotonic clocks, injected-clock tests |
| P-UI: banners | Inside swap target, no timeout, flapping, skewed clocks (21) | Banner outside `<main>`, `htmx.config.timeout`, N-failure debounce, server-provided age |
| P-X: tests/scripts | 401 everywhere in unit/integration/smoke/scripts (19) | Update `test_client`, `_docker_run`/`api`, three scripts in the same plan |
| P-X: upgrade | Existing API consumers get 401, setup reopens without `/data` (20) | Release notes, startup warning, MQTT trigger as the supported automation path |

## Research Flags for Phases

- P-AUTH: standard patterns, but needs a short spike to settle (a) session mechanism (Starlette `SessionMiddleware` vs a small custom signed cookie with password epoch), (b) hash library choice verified against the OpenVINO base image, (c) whether to offer the optional one-time setup token.
- P-HARD (SSRF, secrets): needs design-level care for the raw-editor secret round trip and for the fetch-time vs set-time policy split; the rest is standard.
- P-HA: needs a short design decision on problem-sensor conditions and thresholds (rejections, age, trigger-mode awareness) and a check of what HA does with `unavailable` for `binary_sensor` triggers; verify paho `wait_for_publish` ordering in `stop()` against the installed paho 2.x.
- P-UI: standard; verify htmx 1.9.10 `HX-Redirect` handling on 401 against the pinned version before relying on it.

## Sources

- Codebase (read directly, HIGH): `watermeter/app.py`, `watermeter/mqtt_publisher.py`, `watermeter/watermeter_service.py` (`_derive_pipeline_status`, `_notify_stale`, `reload_config`), `watermeter/image_pipeline.py`, `watermeter/routes/{config,mqtt,roi,service,pages}.py`, `watermeter/static/{dashboard,mqtt-config}.js`, `watermeter/templates/dashboard.html`, `tests/unit/conftest.py`, `tests/integration/conftest.py`, `.github/workflows/docker.yml`, `docker-entrypoint.sh`, `Dockerfile`, `docker-compose.yml`, `debug_clean.sh`, `oneshot_test.sh`, `.planning/PROJECT.md`, `.planning/codebase/CONCERNS.md`, `docs/decisions.md` (AD-01, AD-02).
- htmx source via Context7 (`/bigskysoftware/htmx`): `HX-Redirect` is handled before the response-status check; `htmx:responseError`, `htmx:sendError`, `htmx:timeout` events; 4xx/5xx not swapped by default. MEDIUM: from master source and 1.9.12 docs, confirm for pinned 1.9.10.
- Starlette `SessionMiddleware` (signed cookie via itsdangerous, `https_only`, `same_site`, `max_age` default 14 days), uvicorn `--proxy-headers` / `--forwarded-allow-ips` (default trusts only 127.0.0.1), httpx `follow_redirects` default False, Python `ipaddress` (`ipv4_mapped`, `is_link_local`), `socket.getaddrinfo`/`inet_aton` numeric-host behaviour, paho-mqtt 2.x `will_set`/`on_connect`/`disconnect` semantics, Home Assistant MQTT `availability_topic`/`expire_after`/`binary_sensor` discovery: established behaviour from training knowledge, HIGH to MEDIUM; not re-fetched in this session. Verify paho and HA details when planning P-HA.
