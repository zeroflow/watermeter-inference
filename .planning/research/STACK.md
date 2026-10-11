# Stack Research

**Domain:** Auth, hardening and MQTT availability for a self-hosted FastAPI + Jinja2/HTMX IoT app (single process, no database, JSON under `/data`, one admin)
**Milestone:** Reliability & Security Basics (additive; FastAPI, uvicorn, paho-mqtt, OpenVINO, PyTorch are already in place and not re-researched)
**Researched:** 2026-10-11
**Overall confidence:** MEDIUM-HIGH (versions verified against PyPI metadata; design claims cross-checked against official docs where available; items marked LOW or MEDIUM need a prototype)

## Headline Recommendation

**Add exactly two runtime dependencies: `argon2-cffi` and `itsdangerous`.** Everything else (CSRF, rate limiting, security headers, SSRF guard, availability) is stdlib plus about 250 lines of project code, because the maintained libraries in each category either do not fit a one-process, no-DB, HTMX-plus-fetch app, or are stale.

| Feature | Decision | New dependency |
|---------|----------|----------------|
| Password hashing | `argon2-cffi` (`PasswordHasher`), hash stored in `/data/auth.json` | `argon2-cffi` |
| Sessions | Starlette `SessionMiddleware` (signed cookie), secret persisted in `/data/auth.json` | `itsdangerous` |
| Auth gate | Hand-rolled pure-ASGI default-deny middleware with an allowlist | none |
| CSRF | `SameSite=Lax` plus hand-rolled `Origin` / `Sec-Fetch-Site` check (no tokens) | none |
| Rate limiting | Hand-rolled in-memory sliding window, used as a FastAPI dependency | none |
| Security headers | Hand-rolled pure-ASGI middleware with a static header dict | none |
| SSRF guard | Hand-rolled `ipaddress` denylist, resolve-then-pin, no redirects | none |
| MQTT availability / LWT | paho 2.1 `will_set()` before `connect()` plus HA discovery `availability` | none (already `paho-mqtt>=2.1.0`) |

## Recommended Stack

### Core Technologies (new)

| Technology | Version | Purpose | Why Recommended | Confidence |
|------------|---------|---------|-----------------|------------|
| argon2-cffi | `>=25.1.0,<26` (latest 25.1.0, 2025-06-03) | Admin password hashing and verification | Argon2id is the OWASP first choice for new systems. It has no 72-byte input limit (bcrypt does). Maintained, `cffi` wheels exist, and `check_needs_rehash()` handles future parameter bumps. One dependency (`argon2-cffi-bindings`). Defaults are RFC 9106 low-memory (m=64 MiB, t=3, p=4); measured at about 0.12 s for hash+verify on a dev host. Requires Python >=3.8, so the Ubuntu 22.04 base image (py3.10) is fine. | HIGH |
| itsdangerous | `>=2.2.0,<3` (latest 2.2.0, 2024-04-16) | Required by Starlette `SessionMiddleware` (timestamp-signed cookie) | Pallets project, tiny, stable (a 2.2.0 release in 2024 is "done", not abandoned). Starlette asserts at startup if it is missing, and it is not pulled in by `fastapi` or `starlette` without the `full` extra, so it must be listed explicitly. | HIGH |
| Starlette `SessionMiddleware` | already installed (local venv 0.52.1; a clean image build today resolves 1.7.0) | Signed, HttpOnly session cookie holding `{auth, pv, issued}` | Official, same API before and after Starlette 1.0 (the 1.0 removals concern `on_startup`, `@app.middleware`, the old `TemplateResponse` signature, and similar; none touch sessions). Smoke-tested on fastapi 0.143.0 + starlette 1.7.0: emits `httponly; samesite=lax; Max-Age=...`. | HIGH |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `secrets`, `hmac`, `ipaddress`, `socket`/`asyncio.getaddrinfo`, `time`, `threading` (stdlib) | n/a | Secret generation, constant-time compare, SSRF address checks, limiter | Always. No new packages. |
| htmx (vendored into `watermeter/static/`) | 1.9.12 (same major as today's 1.9.10 CDN pin) or 2.0.11 | Removes the third-party script origin from the CSP and keeps the dashboard working with no internet | Recommended in the security-headers phase. The dashboard currently loads `htmx.org@1.9.10` from unpkg with no SRI. Do not jump to 2.x in this milestone (default behaviour changes are unrelated risk). |
| Monaco editor (jsdelivr CDN, config editor only) | 0.45.0 (existing) | Config editor | Leave on the CDN, allow `https://cdn.jsdelivr.net` in CSP `script-src`/`worker-src` for now. Vendoring is large and out of scope. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `fastapi.testclient.TestClient` (existing) | Auth/CSRF/limiter tests | It sends no `Origin` and no `Sec-Fetch-Site`, so the CSRF check treats it as a non-browser client and existing tests keep working. Add explicit tests with `Origin` set. Unit tests need a logged-in-client fixture in `conftest.py`, not an "auth disabled" env switch. |
| CI matrix entry with latest fastapi/starlette | Catch version skew | The local venv has fastapi 0.129.0 / starlette 0.52.1; a clean Docker build resolves fastapi 0.143.0 / starlette 1.7.0 (requirements use only upper caps `<1.0`). Starlette 1.7 emits a `StarletteDeprecationWarning` when `TestClient` runs on `httpx` ("install httpx2"). Harmless today (no `-W error` in `pytest.ini`), but a watch item. |

## Feature-by-Feature Detail

### 1. Password hashing: argon2-cffi directly (not passlib, not pwdlib, not bcrypt)

- **Use `argon2.PasswordHasher()`** with defaults. Run `hash()`/`verify()` in a worker thread (`asyncio.to_thread`) behind a small semaphore (1 to 2): each call allocates 64 MiB and takes about 100 ms, which would stall the event loop and the reading pipeline's lock holders otherwise.
- After a successful `verify()`, call `check_needs_rehash()` and rewrite the stored hash if true.
- **Storage:** `/data/auth.json` (not `config.yaml`, which the config editor renders). Create with mode `0600`, write atomically (temp file + `os.replace`). Contents: `{password_hash, session_secret, created_at}`.
- **Non-interactive provisioning** (oneshot, `debug_clean.sh`, integration fixture): a `WATERMETER_ADMIN_PASSWORD` env var read at startup, hashed into `auth.json` only when no password exists yet. The setup page is then skipped. Document that this is for automation, not recommended for production.
- **Why not passlib:** last release 1.7.4 in October 2020, unmaintained. Its bcrypt backend breaks with `bcrypt>=5.0.0` (5.0.0 raises `ValueError` for inputs over 72 bytes, and passlib's startup self-test trips on it). It also depends on the stdlib `crypt` module, removed in Python 3.13. Confidence HIGH on "unmaintained" (PyPI), MEDIUM on the exact breakage mechanism (secondary write-ups).
- **Why not pwdlib:** `pwdlib 0.3.1` (2026-08-12) is the maintained passlib successor and is a fine library, but for exactly one algorithm it only adds a wrapper over `argon2-cffi`. Pick it instead only if a second hash scheme must be verified. Its `verify_and_update()` equals `verify()` plus `check_needs_rehash()` here.
- **Why not bcrypt:** the 72-byte limit forces pre-hashing or truncation decisions; Argon2id has none.

### 2. Sessions: Starlette `SessionMiddleware` + persisted secret

```python
app.add_middleware(
    SessionMiddleware,
    secret_key=auth_store.session_secret,   # secrets.token_urlsafe(48), persisted in /data/auth.json
    session_cookie="wm_session",
    max_age=7 * 24 * 3600,
    same_site="lax",
    https_only=settings.cookie_secure,      # default False (plain-HTTP LAN); env/config toggle for TLS proxies
)
```

- Session payload: `{"auth": True, "pv": <first 16 hex of sha256(password_hash)>, "iat": <epoch>}`. The gate rejects a cookie whose `pv` no longer matches the stored hash. That makes a password change invalidate all sessions without needing a rotatable secret (`SessionMiddleware`'s key is fixed at construction).
- `HttpOnly` is always set by Starlette. Set `https_only` through a toggle, since TLS is terminated at a reverse proxy per Out of Scope. A `Secure` cookie on a plain-HTTP LAN origin would be silently dropped by the browser and lock everyone out, so the default must be `False`.
- **Logout** = `request.session.clear()`. Limitation: a stolen cookie stays valid until `max_age` or a password change. Accepted for a single-admin home device; document it.
- **Why not server-side sessions:** an in-memory store gives true revocation but logs everyone out on every container restart (every `:latest` pull), and persisting sessions to JSON is more code than it is worth. **Why not a fully hand-rolled signed cookie:** it would re-implement what Starlette ships; only worth it if per-request `Secure` detection is later required (then use `itsdangerous.URLSafeTimedSerializer` directly).

### 3. Auth gate: hand-rolled pure-ASGI, default-deny

- A middleware reading `scope["session"]`, with an explicit allowlist: `/health`, `/login`, `/logout` (POST), `/setup`, `/static/*`. New routes are protected automatically, which is the point for "53 unauthenticated write endpoints".
- **Do not use `BaseHTTPMiddleware`** (breaks streaming and background tasks, known contextvar and perf quirks). Write plain ASGI `__call__(scope, receive, send)`.
- **Setup mode:** when `auth.json` has no password, redirect every non-allowlisted request to `/setup` (HTML) or return 503/401 with a `setup_required` body (API).
- **Response by client type** (this is what keeps HTMX and `fetch()` working):
  - Navigation (`Accept` includes `text/html`, no `HX-Request`): `303` to `/login?next=...`.
  - HTMX (`HX-Request: true`): `401` plus `HX-Redirect: /login`. htmx processes `HX-Redirect` regardless of status and does not swap the 401 body into the target, so no login page lands in a fragment.
  - Everything else (`/api/*`, `fetch()`): `401` JSON `{"detail": "authentication required"}`; the static JS handles 401 by `location.assign('/login')`. Add one shared helper in the static JS instead of patching each of the page scripts.
- **Middleware order** (FastAPI `add_middleware` makes the last added the outermost): add AuthGate first, then CSRF/Origin check, then `SessionMiddleware`, then SecurityHeaders last. Resulting request flow: SecurityHeaders, Session, Origin check, AuthGate, app. Forged cross-site writes then get 403 before any 401/redirect logic.

### 4. CSRF: SameSite=Lax plus Origin / Sec-Fetch-Site check (no tokens)

**Recommendation:** a ~25-line pure-ASGI check on all unsafe methods (`POST/PUT/PATCH/DELETE`), including `/login` and `/setup` (login CSRF, and a cross-site page setting the first password):

1. If `Sec-Fetch-Site` is present: allow only `same-origin` or `none`; otherwise 403.
2. Else if `Origin` is present: allow only when its scheme+netloc equals the request's own origin (from the `Host` header) or is in `security.allowed_origins`. Treat `Origin: null` as a reject.
3. Else (neither header, i.e. a non-browser client such as curl or `TestClient`): allow. Such clients do not carry the browser's ambient session cookie unless they put it there deliberately.

This is the same rule set as Go 1.25's `http.CrossOriginProtection` and is what Datasette moved to in 2026 (secondary sources; the original essay URL was not retrievable, so treat the rule set as MEDIUM). Its advantages here: no token plumbing, so HTMX (`hx-post`) and every vanilla `fetch()` call keep working unchanged, because browsers add the headers automatically.

**Critical caveat for this app:** browsers send `Sec-Fetch-*` only to "potentially trustworthy" origins (HTTPS or localhost; confirmed on MDN). A typical install is plain `http://<lan-ip>:8001`, where `Sec-Fetch-Site` is **absent**, so rule 2 (`Origin`, always sent on non-GET same-origin and cross-origin requests by current browsers) is what actually protects most users. Do not test only against localhost.

**Behind a reverse proxy**, `Host` may be rewritten (nginx's default `proxy_set_header Host $proxy_host` does this), making same-origin requests look cross-origin. Provide `security.allowed_origins` (list) and document `proxy_set_header Host $host`. Do not trust `X-Forwarded-Host` implicitly.

**Also required for this to be sound:** audit that no `GET`/`HEAD` route has side effects (Lax cookies are sent on top-level GET navigations). Keep `SameSite=Lax`, not `None`.

| Option | Verdict |
|--------|---------|
| `starlette-csrf` 3.0.0 | **Do not use.** Last release June 2023; double-submit cookie design that requires JS to echo a token header into every request (every `hx-post` and every `fetch()` call site in the static JS would need patching). |
| `fastapi-csrf-protect` 1.0.7 (2025-09-16) | Maintained, but per-route dependency design with token-in-header/cookie; forces token plumbing into HTMX and JS and a pydantic-settings config. Not worth it versus the header check. |
| `asgi-csrf` 0.11 | Form/cookie oriented (datasette), no JSON/HTMX header story. No. |
| Synchronizer/double-submit token (hand-rolled) | Fallback only if a requirement later says "support browsers without Origin". Can be layered on the existing session (`session["csrf"]`, `hx-headers` on `<body>`, one JS wrapper) without redoing anything. |

### 5. Rate limiting: hand-rolled in-memory sliding window

- A ~50-line class: `dict[key, deque[float]]` guarded by a `threading.Lock` (sync routes run in the threadpool), `time.monotonic()`, bounded key count (evict oldest at e.g. 1024 keys). Exposed as a FastAPI dependency factory `Depends(rate_limit("trigger", max=6, per=60))` that raises `HTTPException(429, headers={"Retry-After": ...})`. Attach it at router level or per endpoint for: `POST /api/trigger`, training start, synthetic generation.
- **Login:** count only *failures*, keyed per client IP plus one global counter (a single admin has no per-user dimension, so also throttle globally, e.g. 5 failures per 5 min per IP, 20 per 15 min global). Return `429` with `Retry-After`. Reset the per-IP counter on success. Argon2 verification cost is itself a throttle; the semaphore from section 1 bounds memory use under attack.
- **Compute-heavy endpoints** need concurrency limits more than rates: training and synthetic generation should return `409` when a job is already running (the training manager already serializes jobs), and `process_reading` already has an asyncio lock. The rate limit is the extra guard against request floods.
- **Client IP:** use `request.client.host`. Behind a proxy this is the proxy's address, so the per-IP limit degrades to a global limit (acceptable, the global counter covers it). To get real client IPs, run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy ip>` (uvicorn's default trusts only 127.0.0.1). Never parse `X-Forwarded-For` by hand: it is spoofable.
- State is lost on restart (BL-28 is explicitly deferred). Fine for brute-force throttling at this scale.
- **Why not slowapi 0.1.10** (2026-06-13, builds on `limits`): it is alive again, but it counts every request (cannot count only failed logins without private hooks); needs a `request: Request` argument in every decorated endpoint, with known decorator-ordering and annotation quirks in FastAPI; and adds `limits` (and transitive packages) for what 50 lines do. It earns its place with Redis/multi-process storage, which this app does not have. Confidence MEDIUM-HIGH (design reasoning, not a benchmark).

### 6. Security headers: hand-rolled pure-ASGI middleware (not `secure`)

Wrap `send`, add to the `http.response.start` message, never override a header a route set explicitly. `secure` 2.0.1 (2026-04-22) is fine, but it is a header-dict builder; the dict is the code, and the CSP has to be app-specific anyway.

```python
SECURITY_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",                         # make configurable: HA iframe embedding
    b"referrer-policy": b"same-origin",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
    b"cross-origin-opener-policy": b"same-origin",
    b"cross-origin-resource-policy": b"same-origin",
    b"content-security-policy": CSP,
}
# plus Cache-Control: no-store on HTML pages and /api/* responses (config, login redirects)
```

**CSP, based on what the templates actually do today:**
- 83 inline event-handler attributes (`onclick=` and similar), 94 `style="..."` attributes, 3 inline `<script>` blocks, htmx's own injected `<style>`. A strict `script-src 'self'` would break the UI. Ship `script-src 'self' 'unsafe-inline'` and `style-src 'self' 'unsafe-inline'` now, and treat inline-handler removal as later debt. The directives that deliver most of the value need no refactor: `default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'; img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:`.
- Add `https://cdn.jsdelivr.net` for Monaco; drop `https://unpkg.com` once htmx is vendored.
- **Embedding decision:** users may embed the dashboard in a Home Assistant iframe card. `frame-ancestors 'none'` / `X-Frame-Options: DENY` breaks that. Make it a config value (`security.frame_ancestors`, default `'none'`; documented override such as the HA origin). Same-site cookies are sent across ports on the same host, so a same-host HA iframe works once allowed; a different hostname makes it cross-site and the login cookie will not be sent in the iframe (document).
- **No HSTS from the app.** It is ignored over HTTP and belongs to the TLS-terminating proxy.
- Caveat: Starlette's `ServerErrorMiddleware` sits outside user middleware, so unhandled-exception 500 pages will not carry these headers. Accept it.

### 7. SSRF guard: hand-rolled `ipaddress` denylist, resolve-then-pin

There is no library worth adding: `advocate` is requests-based and unmaintained, and nothing mainstream integrates with httpx. Put one helper in a new module (for example `watermeter/net_guard.py`) and use it at **all four** server-side fetch sites:
- `image_pipeline.py` (two `httpx.AsyncClient` fetches, whole image and per-ROI)
- `routes/roi.py` reference-image fetch from `images.src`
- `routes/roi.py` test-URL endpoint, which takes a **user-supplied `url` in the request body** and echoes the error text back (`Could not reach device: {e}`): a second, higher-risk sink and a port-scan oracle. Reduce the echoed error to a generic message.

**Rules:**
- Scheme must be `http` or `https`; reject userinfo tricks by using `urlsplit(...).hostname`, not string matching.
- Resolve with `asyncio.get_running_loop().getaddrinfo()` and check **every** returned address. Reject if any is blocked.
- **Denylist of networks** (explicit, not `ip.is_private`): `0.0.0.0/8`, `127.0.0.0/8`, `169.254.0.0/16` (covers cloud metadata `169.254.169.254`), `224.0.0.0/4`, `240.0.0.0/4`, `100.100.100.200/32` (Alibaba metadata), `::/128`, `::1/128`, `fe80::/10`, `ff00::/8`, `fd00:ec2::254/128`.
- **Do not block RFC 1918 private ranges or `100.64.0.0/10`:** the camera lives on the LAN (or a Tailscale/CGNAT address). Blocking "private" would break the product.
- Unwrap IPv4-mapped IPv6 (`ip.ipv4_mapped`) before checking. Verified on Python 3.11: `ip_address('::ffff:127.0.0.1').is_loopback` is `False`, and `ip_address('0.1.2.3').is_unspecified` is `False` although `0.0.0.0/8` reaches localhost on Linux. This is why the stdlib `is_*` flags alone are not enough (their results also differ across Python 3.10 to 3.12; the image runs 3.10).
- **Pin the connection** to defeat DNS rebinding: after validation, send the request to the validated IP (rewrite the URL host, keep the original `Host` header; for https pass `extensions={"sni_hostname": original_host}` so SNI and certificate checks use the real name). Keep `follow_redirects=False` (httpx's default; the code sets none today) and treat any 3xx as an error so a redirect cannot bypass the check. Add a response size cap and the existing timeout.
- Validate at **config save** time as well (fast `422` in the UI) and again at fetch time (authoritative).
- Provide an explicit opt-in (config flag) for loopback targets, because some users run a camera proxy on the same host with host networking. Default off.
- Confidence: denylist and redirect policy HIGH; the URL-rewrite pin with `sni_hostname` is MEDIUM (standard technique, but prototype it against a local TLS test server, and check mDNS `.local` names still resolve in the container). If it proves fiddly, the check-then-fetch variant without pinning is an acceptable first cut because only the authenticated admin can set the URL after this milestone.

### 8. MQTT availability, LWT and the HA problem sensor (paho 2.1)

`paho-mqtt` latest is still 2.1.0 (2024-04-29), so the current pin `>=2.1.0,<3` stays. Verified against paho source and docs:

- **`will_set(topic, payload, qos, retain)` must be called before `connect()`.** In `MqttPublisher.start()` that means between `username_pw_set` and `connect`. Use `will_set(avail_topic, "offline", qos=1, retain=True)`. The will is sent in every CONNECT, so it survives paho's automatic reconnects.
- **Publish `"online"` (qos=1, retain=True) from `on_connect`** on `reason_code == 0` (not once at startup), so each reconnect re-asserts availability. The existing `on_connect` already subscribes and republishes discovery there; add it to the same place and also on the `homeassistant/status` "online" birth message.
- **A clean `disconnect()` does NOT trigger the will.** In `stop()`, publish `"offline"` explicitly and wait (`info = client.publish(...); info.wait_for_publish(timeout=2)`) **before** `loop_stop()` and `disconnect()`. The current order (`loop_stop()` then `disconnect()`) cannot flush a publish.
- **Detection latency:** a broker declares the client dead after about 1.5x keepalive. With the shipped `keepalive: 60` HA sees "unavailable" about 90 s after a crash. Suggest 30 s as the shipped default (about 45 s).
- **HA discovery:** add `availability: [{"topic": avail_topic, "payload_available": "online", "payload_not_available": "offline"}]` to every entity's discovery payload (or `availability_topic`; the two are mutually exclusive). Republish discovery (already retained) so existing entities pick it up. Use `availability_mode: latest` only if a second topic is ever added.
- **Problem sensor:** discovery on `<prefix>/binary_sensor/<id>/problem/config` with `device_class: problem`, `entity_category: diagnostic`, a `state_topic` carrying `ON`/`OFF` (retained, qos=1), the same `availability`, and optionally `json_attributes_topic` with a `reason` (`pipeline_degraded|failed|stale|reading_too_old`). Publish on pipeline-status transitions (the status machine and `_notify_stale` are the hook) and from the existing stats loop for the "last good reading too old" case, with the age threshold as a new config field.
- When the service dies, the problem sensor becomes `unavailable`, not `on`. That is correct but needs documenting: users alert on `unavailable` as well. HA's per-entity `expire_after` is a possible extra safety net for the reading sensor but flaps if readings only publish on change, so leave it out by default.
- **Unique `client_id` per instance** (INTEGRATIONS notes the shipped default is a `...-debug` id). Two clients with one id kick each other off the broker and, with an LWT, will flap `offline`/`online`. Derive the availability topic from the id or a configured device id. Confidence on takeover-will ordering is LOW (broker-specific; test against the target Mosquitto).

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Password hash | argon2-cffi | passlib | Unmaintained since 2020; breaks with bcrypt 5; relies on `crypt` removed in Python 3.13 |
| Password hash | argon2-cffi | pwdlib[argon2] 0.3.1 | Fine, but a wrapper with no benefit for one algorithm |
| Password hash | argon2-cffi | bcrypt 5.0.0 | 72-byte limit; Argon2id is the current OWASP first choice |
| Sessions | SessionMiddleware | server-side in-memory store | Logs everyone out on every restart/upgrade; more code |
| Sessions | SessionMiddleware | JWT cookie | Adds nothing for one user; same revocation limits, more footguns |
| CSRF | Origin/Sec-Fetch-Site | starlette-csrf, fastapi-csrf-protect, asgi-csrf | Token plumbing through HTMX and ad-hoc fetch; one is stale (2023) |
| Rate limiting | hand-rolled | slowapi 0.1.10 | Counts all requests not failures; decorator signature quirks; extra deps for 50 lines |
| Security headers | hand-rolled middleware | `secure` 2.0.1 | A dict builder; CSP is app-specific anyway |
| SSRF | stdlib `ipaddress` | advocate, various | Requests-only or unmaintained; not httpx compatible |
| Auth gate | default-deny ASGI middleware | per-router `Depends(require_auth)` | A new router or route would be open by default |

## What NOT to Use

- **passlib**: unmaintained, breaks on current bcrypt and Python 3.13.
- **`BaseHTTPMiddleware`** for any of the new middleware: interferes with streaming responses and background tasks; use plain ASGI.
- **starlette-csrf**: stale (2023) and token-based.
- **A "disable auth" env switch for tests or CI**: it becomes a production footgun in a public image. Use a logged-in client fixture, and the provisioning env var for fresh containers.
- **`ip.is_private` / `is_global` as the SSRF test**: blocks the LAN camera, misses `0.0.0.0/8` and IPv4-mapped IPv6, and differs between Python versions.
- **Parsing `X-Forwarded-For` manually** for rate limiting or logging: spoofable. Use uvicorn's trusted-proxy handling.
- **`Secure` cookie by default**: breaks plain-HTTP LAN installs.

## Installation

```bash
# requirements.txt and requirements-docker.txt (both; the Docker image does not use requirements.txt)
argon2-cffi>=25.1.0,<26
itsdangerous>=2.2.0,<3

# Local env
uv pip install -r requirements.txt -r requirements-dev.txt

# No new dev dependencies.
```

Optional static asset: download `htmx.org@1.9.12` `dist/htmx.min.js` into `watermeter/static/` and replace the unpkg tag.

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| argon2-cffi 25.1.0 | Python >=3.8 (image runs 3.10, dev 3.12) | Pure wheels via `argon2-cffi-bindings`; smoke-tested on Python 3.13 |
| itsdangerous 2.2.0 | Starlette 0.52.1 and 1.7.0 | `SessionMiddleware` API identical across 1.0 |
| fastapi 0.143.0 / starlette 1.7.0 vs local fastapi 0.129.0 / starlette 0.52.1 | requirements use `fastapi>=0.100,<1.0` | A clean Docker build already resolves the newer pair. Test the auth stack on both. fastapi requires `starlette>=0.46.0`. |
| paho-mqtt 2.1.0 | `CallbackAPIVersion.VERSION2` (in use) | `will_set` signature unchanged for 2.x |
| httpx 0.28.1 | Starlette 1.7 TestClient | Works, with a deprecation warning pointing at `httpx2`. LOW risk now; revisit if Starlette drops httpx support. |

## Sources

- PyPI JSON metadata for argon2-cffi, bcrypt, passlib, pwdlib, itsdangerous, starlette, fastapi, slowapi, starlette-csrf, fastapi-csrf-protect, asgi-csrf, secure, paho-mqtt, limits, httpx, uvicorn (queried 2026-10-11): versions and release dates. HIGH.
- Context7 `/kludex/starlette` (1.0.0 docs): `SessionMiddleware` parameters; 1.0 removals list. MEDIUM-HIGH (docs, cross-checked by a live smoke test on starlette 1.7.0).
- Context7 `/eclipse-paho/paho.mqtt.python`: `will_set` must precede `connect`; clean disconnect does not send the will; VERSION2 callbacks. MEDIUM-HIGH.
- Context7 `/frankie567/pwdlib`: API and extras. MEDIUM.
- Home Assistant MQTT binary_sensor docs, https://www.home-assistant.io/integrations/binary_sensor.mqtt/: `availability` list, `availability_mode`, `expire_after`, `device_class`. MEDIUM (fetched via summarizer).
- MDN `Sec-Fetch-Site`, https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Sec-Fetch-Site: header only sent to potentially trustworthy URLs; baseline since March 2023. MEDIUM.
- Secondary write-ups of Go 1.25 `http.CrossOriginProtection` (Simon Willison, Alex Edwards, calhoun.io) for the Origin/Sec-Fetch-Site rule set. LOW-MEDIUM (original essay unavailable).
- passlib/bcrypt 5.0.0 breakage: web search, secondary write-ups. MEDIUM-LOW on mechanism, HIGH on passlib's 2020 last release (PyPI).
- Local checks: Python 3.11 `ipaddress` behavior for `::ffff:127.0.0.1`, `0.1.2.3`, `100.64.0.1`; throwaway venv smoke test of fastapi 0.143.0 + starlette 1.7.0 + argon2-cffi 25.1.0 (session cookie flags, argon2 default parameters, about 0.12 s hash+verify). HIGH for what was observed.
- Repo inspection: template inline-handler/style counts, CDN scripts (htmx via unpkg, Monaco via jsdelivr), the four httpx fetch sites, the paho client construction order in `mqtt_publisher.py`.

---
*Stack research for: Reliability & Security Basics milestone*
*Researched: 2026-10-11*
