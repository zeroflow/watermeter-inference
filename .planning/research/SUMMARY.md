# Project Research Summary

**Project:** Watermeter Inference (milestone: Reliability & Security Basics)
**Domain:** Self-hosted single-admin IoT web app (FastAPI + Jinja2/HTMX, one process, no database) shipped as a public Docker image, publishing to Home Assistant over MQTT
**Researched:** 2026-10-11
**Confidence:** MEDIUM-HIGH

## Executive Summary

This is a brownfield retrofit of auth, hardening and failure visibility onto an app that is wide open today. It has 53 unauthenticated write endpoints, returns the MQTT password in plaintext, fetches server-side URLs without a guard, and has no MQTT availability or stale signal. Comparable apps (Frigate, Grafana, Portainer, AdGuard Home, Zigbee2MQTT, ESPHome) converge on the same shape. A setup page claims the first admin password, and recovery needs host or container access. Login failures are throttled per IP in memory, secrets are write-only, and availability is a retained MQTT topic with a Last Will. None of the peers ship a "problem" sensor driven by pipeline health, which is the gap this milestone fills.

The recommended approach is almost entirely stdlib plus roughly 300 lines of project code, with no new runtime dependency. Auth is a default-deny pure-ASGI gate with a signed-cookie session. The session secret and password hash live in `/data/auth.json` (mode 0600), never in `config.yaml`. A thin client shim (`static/auth.js`) adds the CSRF header and handles 401 for all ~65 `fetch()` sites and HTMX/XHR without editing them. Reliability work centers on one pure `health.evaluate()` function. It feeds the HA problem binary_sensor, the dashboard banner and `/api/status`, so HA and the dashboard cannot disagree. MQTT gets a Will, a birth message on connect, an explicit offline on clean stop, and an `availability` block on every discovery payload.

The main risks are self-inflicted breakage:
- Auth lands without the test, script and CI plumbing.
- The 401 contract swaps login HTML into HTMX fragments.
- The secret mask round-trips into `config.yaml`.
- The SSRF guard blocks the LAN camera or silently freezes upgraded installs.
- The problem sensor stays "OK" when the service is dead or readings are rejected forever.
- Stale thresholds false-alarm in `mqtt` trigger mode.

Each has a concrete prevention below and maps to a phase.

## Resolved Researcher Conflicts

| Question | Positions | Recommendation |
|----------|-----------|----------------|
| CSRF mechanism | STACK: Origin / `Sec-Fetch-Site` check only, no token. ARCHITECTURE: signed double-submit token, and explicitly not Origin as the primary defence. PITFALLS: Origin plus custom header. | **Origin check plus a required custom header, no token.** Unsafe methods must pass an Origin / `Sec-Fetch-Site` check. Reject `Origin: null`, and allow a configurable `security.allowed_origins` for proxies. Cookie-authenticated `/api/*` writes must also carry `X-Requested-With: watermeter`. A cross-site page cannot set that header without a CORS preflight it fails, and `auth.js` adds it to fetch, XHR and HTMX in one place. Origin-only is not enough. Browsers send `Sec-Fetch-*` only to trustworthy origins, so on plain-HTTP LAN the check rests on `Origin` alone, and a missing `Origin` must not be an open door for cookie-bearing requests. Skip the double-submit token: it adds cookie and template plumbing for no gain over the header. `/login` and `/setup` form posts use the Origin check only. |
| Session mechanism | STACK: Starlette `SessionMiddleware` (adds `itsdangerous`). ARCHITECTURE: custom stdlib-`hmac` token (`v1.<expiry>.<epoch>.<hmac>`). PITFALLS: undecided, spike. | **Custom signed cookie, stdlib `hmac`.** It needs a password epoch so a password change revokes sessions. It sets `Secure` dynamically from the request scheme, because `SessionMiddleware`'s `https_only` is fixed at construction and a wrong value locks out plain-HTTP LANs. It uses a per-instance cookie name, because prod and debug share a host. It also avoids the `itsdangerous` dependency. The cost is about 60 lines, with the secret persisted in `/data/auth.json`. Default lifetime is 30 days, sliding (re-issue when more than half has elapsed), so wall-tablet kiosks stay logged in. |
| Password hash | STACK: `argon2-cffi`. FEATURES, ARCHITECTURE, PITFALLS: stdlib `scrypt` or argon2, decide against the real image. | **`hashlib.scrypt`, zero new dependency.** The image is Ubuntu 22.04 / Python 3.10 on an OpenVINO base, where a native wheel that fails to resolve breaks the build. One admin password does not need Argon2id's margin. Use a self-describing PHC-style string (params, salt, hash) so a later move to argon2 is a rehash-on-login swap. Set an explicit `maxmem`, because scrypt's default cap rejects n=2^15. Run hash and verify in `asyncio.to_thread` behind a small semaphore. Cap password length (1024) and compare with `hmac.compare_digest`. Revisit argon2 only if a reviewer insists. |
| Auth gate | STACK, PITFALLS: default-deny pure-ASGI middleware with allowlist. ARCHITECTURE: `protected` APIRouter with `Depends(require_auth)`, middleware only for headers. | **Default-deny pure-ASGI middleware.** A router dependency does not cover `StaticFiles`, `/docs`, `/redoc` or `/openapi.json`, and any future router is open unless someone remembers. The allowlist is `/health`, `/login`, `/setup` (while unconfigured), `/logout` (POST) and `/static/*`. Disable `docs_url`, `redoc_url` and `openapi_url`. Keep ARCHITECTURE's route-inventory test as the safety net. It walks `app.routes`, mounts included, and asserts 401/303 without a cookie except for the allowlist. Meet ARCHITECTURE's testability concern with a logged-in client fixture (tmp auth file, known password), not a bypass flag or `dependency_overrides`. Never add an auth-disabled switch. |

Two smaller conflicts:
- **SSRF DNS rebinding:** STACK proposes pinning the connection to the validated IP, while ARCHITECTURE and PITFALLS accept check-then-fetch. Recommend check-then-fetch. Resolve every returned address immediately before each request and keep redirects off. Record the accepted TOCTOU gap in `docs/decisions.md`. Pinning is a later hardening item, since only the authenticated admin can set the URL.
- **Where staleness is evaluated:** use ARCHITECTURE's three triggers: end of `process_reading`, a 60 s scheduler tick, and `on_connect`. The tick is the one that catches "no trigger ever arrived".

## Key Findings

### Recommended Stack

Stay inside FastAPI/Starlette, Jinja2 and HTMX. Add no runtime dependency this milestone. Everything uses the stdlib (`hmac`, `hashlib.scrypt`, `secrets`, `ipaddress`, `asyncio` `getaddrinfo`, `threading`). `paho-mqtt>=2.1` already supports the Will.

**Core technologies:**
- stdlib `hmac` signed cookie, secret in `/data/auth.json` (0600, atomic write): sessions with a password epoch and per-instance cookie name
- `hashlib.scrypt`: admin password hash, computed in a worker thread
- Hand-rolled pure-ASGI middlewares (auth gate, CSRF/Origin check, security headers): avoid `BaseHTTPMiddleware`
- Hand-rolled in-memory sliding-window limiter (`watermeter/ratelimit.py`): counts failures only for login, plus single-flight 409 guards for heavy jobs
- `watermeter/url_guard.py` (stdlib `ipaddress`): explicit denylist of 0.0.0.0/8, loopback, link-local (including the metadata address), multicast and reserved ranges, with IPv4-mapped IPv6 unwrapped. It must allow RFC 1918 and CGNAT ranges, because the camera lives there.
- paho 2.x: `will_set` before `connect`, retained `online` in `on_connect`, explicit retained `offline` in `stop()` with `wait_for_publish`
- htmx 1.9.12 vendored into `watermeter/static/`: removes the unpkg origin and the offline-LAN failure. Monaco stays on its CDN and is allowed in the CSP.
- CI: add a matrix entry with the latest fastapi/starlette, since a clean Docker build already resolves a newer pair than the local venv.

### Expected Features

**Must have (table stakes):**
- Setup gate: with no hash on disk every page redirects to `/setup`. Fresh and upgrading installs take the same path. The POST is locked, atomic and one-shot.
- Login, logout (POST), and change password with session invalidation
- A 401 contract by caller type: 303 for pages, 401 plus `HX-Redirect` for HTMX, JSON 401 for `/api/*`, with a validated same-origin `next`
- Seed-only env bootstrap `WATERMETER_ADMIN_PASSWORD` (plus `_FILE`) for `debug_clean.sh`, the integration fixture and CI
- Documented recovery: `python -m watermeter set-password`, or delete `auth.json` and restart
- Per-IP login failure throttle (Frigate numbers: 1/s, 5/min, 20/h) plus a global failure backoff. 429 with `Retry-After`, no account lockout, no `X-Forwarded-For` trust.
- Single-flight guards and rate limits on trigger, training, synthetic generation and similar endpoints
- Write-only MQTT password across `/api/config`, the YAML editor, `/api/mqtt/config` and `/api/mqtt/test`, with keep/replace/clear save semantics and a `password_set` flag. Redact camera-URL credentials in logs and responses.
- SSRF guard at all fetch sites and in config validation
- Security headers: nosniff, referrer policy, configurable frame-ancestors, light CSP
- Availability topic with Will and birth, and `availability` on every discovery entity
- A `problem` binary_sensor with reason attributes, a last-good-reading timestamp sensor, and a staleness watchdog with a mode-aware threshold
- One shared health function
- Dashboard banners: a server-computed stale / not-OK banner and a connection-lost banner. The connection-lost banner triggers after N consecutive failures, treats 401 as distinct, sits outside the swap target, and resyncs on `visibilitychange`.
- README sections for proxy setup, HA automations (alert on `on` and on `unavailable`), recovery and the upgrade notice

**Should have (differentiators):**
- Instance-unique cookie name (cheap; prevents prod/debug logout loops)
- Pipeline-status enum sensor and a plain-language reason in the banner
- Heartbeat plus `expire_after` hang detection (P2, beats Frigate and Zigbee2MQTT)
- Boot-id auto-reload and a tab-title indicator

**Defer (v2+):**
- Optional setup-claim token
- Progressive backoff UI
- Separate secrets file
- Trusted-proxy auth
- API tokens
- Deeper `/health`
- Rate-history persistence

Never build an auth-disabled mode.

### Architecture Approach

New leaf modules (`auth.py`, `ratelimit.py`, `url_guard.py`, `health.py`, `security.py`) keep the existing `routes -> app` import cycle from deepening.
- `routes/auth.py` and `routes/health.py` form the public surface, with `/health` moved out of `routes/service.py`.
- Config redaction and restore live in `config_utils.py`, with one sentinel and one `SECRET_PATHS` list.
- The service facade only gains a call to `health.evaluate()` inside `_derive_pipeline_status`.
- HA health goes on a separate retained `/health` topic, never into the main state document.
- The availability topic is derived from the publish topic, so prod and debug do not share one.

**Major components:**
1. `auth.py` and `routes/auth.py`: AuthStore, hashing, signed session, CSRF/Origin check, setup, login, logout, change-password
2. Gate, CSRF and security-headers middlewares: pure ASGI, ordered so 401 and 403 responses also carry the security headers
3. `static/auth.js`, loaded from `_theme.html`: patches `fetch` and XHR (HTMX and the training ZIP upload use XHR), adds the CSRF header and redirects on 401
4. `config_utils` redaction and restore, `url_guard`, `ratelimit`: hardening primitives called at the chokepoints
5. `health.py` (pure): feeds `MqttPublisher.publish_health`, `status_fragment.html` and `/api/status`
6. `scheduling.py` health tick: 60 s, with an optional constructor argument so existing tests are unchanged

### Critical Pitfalls

1. **Test, script and CI breakage when auth lands.** Unit tests, the integration fixture, `debug_clean.sh`, `oneshot_test.sh` and the `docker.yml` smoke test all hit auth at once. Land the logged-in client fixture, the env-seeded password in scripts and CI, and the `/health` probe in the same plan as the gate. Keep `/health` open and free of data. Add no bypass flag.
2. **401 handled as a redirect to login HTML.** HTMX would swap a login page into `<main>`, and `fetch().json()` would throw. Shape the response by caller, ship `auth.js`, and validate `next`. Verify `HX-Redirect` on a 401 against the pinned htmx version, with the shim's 401 handler as the fallback.
3. **Write-only secrets overwritten by the mask.** Never round-trip the sentinel. The server treats the sentinel or an absent field as "keep". The raw-YAML editor needs redaction on read and restoration on save, with a regression test: open, change an unrelated key, save, then check the password is unchanged and comments are intact. `/api/mqtt/test` falls back to the stored password only for the saved broker host and port.
4. **SSRF guard that blocks the camera or freezes an upgrade.** Allow RFC 1918, check every resolved address, and unwrap IPv4-mapped IPv6. Parse with the same parser the fetcher uses (httpx), and test decimal, hex, octal and short IPv4 forms. Validate strictly when a URL is set. At fetch time, treat a policy block as permanent (log once, show a distinct visible error) and a DNS failure as transient (normal DEGRADED path).
5. **A HA problem state that is silent when it matters.**
   - The sensor must share the availability topic.
   - The condition must cover "alignment fine but every reading rejected" (the `REJECTED`-forever case), not only FAILED/STALE, and also "no accepted reading for T".
   - It must be evaluated on an independent timer without taking `processing_lock`, and recomputed on startup.
   - Measure age since the last accepted reading, not since the value last changed.
   - Use UTC or monotonic clocks and a startup grace period.
   - The threshold must be trigger-mode aware, with an explicit generous default in `mqtt` mode.
6. **Availability mistakes.**
   - `will_set` must precede `connect`, and the Will must be retained.
   - `online` must be published in `on_connect`.
   - `offline` must be published explicitly before `loop_stop()` in `stop()`.
   - Prod and debug must not share the availability topic.
   - `client_id`, `publish_topic` and the availability topic must be added to the `reload_config` reconnect comparison.

## Implications for Roadmap

Four phases, in this order. Phases 3 and 4 do not depend on auth, so the order can change with risk appetite. Auth first is recommended because a public image with open write endpoints is the larger exposure.

### Phase 1: Auth Core and Test/Script Plumbing
**Rationale:** The largest and riskiest slice, and it cannot be split. The gate without `auth.js` breaks every page. Without `/setup` it locks out upgrades. Without the fixture changes CI goes red. Every later phase assumes the final request path.
**Delivers:**
- The `/data/auth.json` store, scrypt hash, and signed cookie session with epoch and per-instance name
- Setup, login, logout and change-password
- The default-deny middleware with its allowlist
- Origin plus custom-header CSRF, the 401 contract, and `auth.js`
- `/health` moved to `routes/health.py`, with the docs endpoints disabled
- Env bootstrap and the recovery CLI
- The login throttle and `ratelimit.py`
- Updated unit conftest, integration fixture, `debug_clean.sh`, `oneshot_test.sh` and compose snippet
- The route-inventory test and a README upgrade notice

**Addresses:** setup gate, login/logout/401, env seed, recovery, change password, login throttle, instance-unique cookie
**Avoids:** setup race, secret persistence, cookie collisions, Secure on HTTP, test and script breakage, BaseHTTPMiddleware problems, upgrade surprises (pitfalls 1-8, 14, 19, 20, 24, 26-29)

### Phase 2: Hardening Behind Auth
**Rationale:** Depends on the final request path. The items are small and parallelizable. Security headers come last, written against the final templates.
**Delivers:**
- Write-only secrets across the API, raw YAML editor, MQTT page, test-connection, and log and URL redaction
- `url_guard.py` at all fetch sites and in config validation
- Single-flight guards and rate limits on heavy endpoints
- The security headers middleware and vendored htmx
- `docs/decisions.md` with AD-01 and AD-02 superseded and the TOCTOU gap recorded

**Uses:** `ratelimit.py` from Phase 1, the `config_utils` schema
**Avoids:** pitfalls 9-13, 15, 27, 30

### Phase 3: HA Visibility (Availability and Problem Sensor)
**Rationale:** Delivers the core value toward HA (never silently frozen). The pure `health.evaluate()` comes first, because Phase 4 reads the same output.
**Delivers:**
- `health.py` and the new `current_state` keys
- The Will, birth message and offline-on-stop
- Discovery `availability` and the retained `/health` topic
- The `problem` binary_sensor and a last-good-reading timestamp sensor
- The `mqtt_connected` fix
- The scheduler tick and `health.*` config keys through the schema
- A per-instance availability topic
- README HA automation examples

**Avoids:** pitfalls 16-18, 23, 25

### Phase 4: Dashboard Banners and Release Docs
**Rationale:** Depends on Phase 3's health output and Phase 1's 401 contract, so a session expiry never shows "connection lost".
**Delivers:**
- A server-rendered stale / not-OK banner in the polled fragment, with `is defined` guards for the template tests
- A connection-lost banner outside `<main>`, driven by `htmx:sendError`, `htmx:timeout` and `htmx:responseError` plus a `/health` heartbeat
- `htmx.config.timeout`, N-failure debounce, and `visibilitychange` resync
- The final README covering proxy setup, recovery and upgrade

**Avoids:** pitfall 21

### Phase Ordering Rationale

- Auth goes first because it is the highest exposure and every later phase assumes the final request path.
- Hardening comes before HA so the CSP and headers are written against final templates and the limiter already exists.
- The health function precedes the banners so HA and the dashboard share one definition.
- Test and script plumbing lives inside Phase 1, not after it.

### Research Flags

Phases needing deeper research or a spike during planning:
- **Phase 1:** a short prototype to confirm `HX-Redirect` handling on 401 with the pinned htmx. Also confirm scrypt `maxmem` on the image's OpenSSL, and real-browser behavior of the `Origin`-only path over plain HTTP.
- **Phase 2:** the raw-YAML secret round trip with ruamel, the highest-risk item. Also check whether `images.src` credentials are echoed by config and `/api/roi/*` before scoping redaction.
- **Phase 3:** the problem-condition and threshold design (rejections, age, trigger-mode awareness), HA behavior of a `binary_sensor` going `unavailable`, and broker behavior for duplicate client ids.

Phases with standard patterns (skip research-phase):
- **Phase 4:** HTMX events and a banner state machine are well documented. The one htmx check above is the exception.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | MEDIUM-HIGH | Versions and Starlette/paho behavior verified against PyPI and docs. The Origin / `Sec-Fetch-Site` rule set rests on secondary sources. The chosen scrypt path was not benchmarked on target hardware. |
| Features | MEDIUM | HA, Frigate, Zigbee2MQTT and ESPHome claims come from official docs. Portainer, Uptime Kuma and Grafana specifics are third-party or prior knowledge. |
| Architecture | HIGH | Integration points were read from the code. HTMX and paho behaviors are cited from docs, not run. |
| Pitfalls | HIGH | Codebase-specific findings were read from source. Behavior of the pinned htmx 1.9.10 is MEDIUM. |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **htmx 1.9.10 `HX-Redirect` on 401:** verify in the Phase 1 spike. The `auth.js` XHR-level 401 redirect is the fallback.
- **Camera-URL credential exposure:** confirm which endpoints and logs echo `images.src` before sizing the redaction work.
- **Plain-HTTP Origin behavior and proxy `Host` rewriting:** test in real browsers. Document that proxies should preserve `Host` and set `security.allowed_origins`.
- **Stale threshold default in `mqtt` trigger mode:** choose a generous default and make it configurable. Decide in Phase 3 discussion.
- **Optional setup-claim token:** deferred as opt-in. Revisit only if the first-visit claim race draws concern.
- **No persistent `/data` volume:** setup reopens on every container recreate. Warn at startup and recommend the env var.
- **Home Assistant iframe embedding:** frame-ancestors is configurable, but `SameSite=Lax` withholds the cookie in a cross-site frame. Document it.
- **Duplicate client ids and the debug config:** ensure the shipped debug config uses a distinct availability topic or disables HA publishing.

## Sources

### Primary (HIGH confidence)
- Repository source and tests, read directly: app, routes, `config_utils`, `mqtt_publisher`, `watermeter_service`, `scheduling`, `image_pipeline`, `persistence`, templates, static JS, Dockerfile, entrypoint, compose, helper scripts, CI workflows, unit and integration conftests, and `.planning/codebase/`
- PyPI metadata for the candidate libraries (queried 2026-10-11)
- paho-mqtt 2.x docs: `will_set` must precede `connect`, and a clean `disconnect()` does not send the Will

### Secondary (MEDIUM confidence)
- Home Assistant MQTT, binary_sensor and sensor docs (availability, `expire_after`, `device_class: problem`)
- Frigate, Zigbee2MQTT, ESPHome and AI-on-the-edge-device official docs (auth, throttling, availability patterns)
- htmx source and docs via Context7 (`HX-Redirect`, error events), MDN `Sec-Fetch-Site`, Starlette docs, and a smoke test on a recent Starlette 1.x

### Tertiary (LOW confidence)
- Secondary write-ups of Go 1.25 `CrossOriginProtection` for the Origin rule set
- Portainer, Uptime Kuma, Grafana and Node-RED behaviors from third-party pages or prior knowledge
- Session-lifetime values and the passlib/bcrypt breakage mechanism

---
*Research completed: 2026-10-11*
*Ready for roadmap: yes*
