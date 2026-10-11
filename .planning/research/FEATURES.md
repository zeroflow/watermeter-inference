# Feature Research

**Domain:** Self-hosted single-user home-automation web app, shipped as a public Docker image and installed by strangers. Milestone scope: admin-password auth, login throttling, write-only secrets, Home Assistant visibility of a broken pipeline, dashboard stale/connection banners.
**Researched:** 2026-10-11
**Confidence:** MEDIUM overall. Home Assistant MQTT, Frigate, Zigbee2MQTT, ESPHome, Grafana CLI and AdGuard Home claims were read from official docs (MEDIUM, cross-checked). Portainer and Uptime Kuma specifics come from third-party write-ups (LOW-MEDIUM). Node-RED and Grafana `secureJsonFields` write-only patterns and the cookie/session-lifetime recommendations come from prior knowledge plus engineering judgement (LOW, flagged inline). The AI-on-the-edge-device MQTT doc lists `Connection`, `main/status`, `main/error`, `main/timestamp` but does not document an LWT or a problem sensor.

Terminology used below: "pipeline status" is the existing OK / DEGRADED / FAILED / STALE machine. "Last good reading" is the last reading that passed plausibility and was accepted.

## Feature Landscape

### Area 1: Single admin password, first-run setup, upgrade, recovery, headless bootstrap

How comparable apps behave (what strangers will compare against):

| App | First run | Recovery | Headless bootstrap |
|-----|-----------|----------|--------------------|
| Frigate | Generates an admin password and prints it in the log; `auth.reset_admin_password: true` prints a new one on next start | Config flag + restart (no exec needed) | Not needed (log password) |
| Grafana | Default admin account, forced change (training knowledge) | `grafana cli admin reset-admin-password <pw>` | `GF_SECURITY_ADMIN_PASSWORD`, applied on first DB creation only |
| Portainer | Setup page; if nobody claims admin within ~5 minutes the service stops until container restart (third-party sources) | Restart / documented reset | Password or password-file startup flag, or API call before admin exists |
| AdGuard Home | Install wizard on first run | Edit bcrypt hash in the YAML while stopped | Config file; `users: []` disables auth entirely |
| Uptime Kuma | Setup page on first visit | `npm run reset-password` via `docker exec` (third-party sources) | Has an auth-disable setting (anti-pattern, see below) |
| Paperless-ngx | Admin created by env vars or `createsuperuser` | `createsuperuser` / manage.py | `PAPERLESS_ADMIN_USER` / `PAPERLESS_ADMIN_PASSWORD` seed on container start |

Pattern: nobody ships a fixed default password any more; recovery is always something that needs host or container access (CLI, file edit, restart flag), never an in-app email flow; env-var bootstrap is seed-only.

#### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Setup gate: until a password exists, every page redirects to a setup page; only `/health`, the setup page and its assets are reachable | Grafana/Portainer/Uptime Kuma/AdGuard all do this; it is the "secure by default" promise | MEDIUM | Must also cover API routes (return 401/503-with-setup-hint for JSON, `HX-Redirect` for HTMX). Setup POST must be atomic and one-shot (reject if a hash already exists, to avoid a two-tab race). |
| Same gate for upgrading installs | Frigate added auth in a later release and upgraded users had to cope; ours has 53 open write endpoints today | LOW | No special path: "no hash on disk" == setup mode. Add one prominent startup log line ("open the UI to set the admin password") and a release-note entry. Existing HA/MQTT flow does not use HTTP, so only user scripts hitting `/api/*` break; call that out in the changelog. |
| Hash stored outside `config.yaml`, in a `/data` file with 0600 perms, using a real KDF | Constraint in PROJECT.md; config editor must never show it | LOW | scrypt from stdlib avoids a dependency; argon2/bcrypt is fine too (STACK.md decides). Never log the password or the hash. |
| Minimal password policy: length >= 8, confirm-password field, no composition rules | NIST-style; composition rules annoy single-user installs | LOW | Reject empty and whitespace-only. |
| Env-var bootstrap, seed-only: `WATERMETER_ADMIN_PASSWORD` (and `_FILE` variant for Docker secrets) used only when no hash exists | Paperless/Grafana/Portainer precedent; required for `debug_clean.sh`, integration fixture, CI | LOW | Hash it at once and drop it; if a hash already exists the variable is ignored (log at INFO, not the value). Oneshot CLI mode has no HTTP server and should not need it. |
| Documented recovery that needs only host/container access | Everyone gets locked out; support burden otherwise lands on GitHub issues | LOW | Two routes: `docker exec <container> python -m watermeter reset-password` (Grafana/Uptime Kuma style) and "stop container, delete the auth file in `/data`, start; setup page returns" (AdGuard style, works when exec is impossible). Both in README. |
| Change password in UI (requires current password) | Basic expectation after first setup | LOW | Changing it must invalidate all existing sessions. |
| Login, logout, and 401 contract for non-page requests | JS/HTMX must get 401 (never a login HTML page swapped into a fragment) | MEDIUM | Full pages: 303 to `/login?next=...`. HTMX: `HX-Redirect` header or 401 plus a global `htmx:responseError` handler. `fetch()`: 401 -> `location = /login`. Validate `next` is a same-origin relative path (open-redirect). |
| Logout is a POST (CSRF-protected), clears cookie, and is effective server-side | GET logout can be triggered cross-site | LOW | With stateless signed cookies "effective server-side" means a session epoch/version in the signed payload that is bumped on password change. |
| Session cookie: HttpOnly, SameSite=Lax, Secure when the request is HTTPS, signing secret persisted in `/data` | Frigate persists its JWT secret to `.jwt_secret` so restarts do not log everyone out | LOW | Secret regenerated if file missing. Only honour `X-Forwarded-Proto` from trusted proxies. |
| Sliding session expiry, default long (about 30 days), configurable | This is a dashboard people leave open on a wall tablet; Frigate default is 24 h, AdGuard and Grafana are days to weeks (LOW confidence on exact values) | LOW | HTMX polling counts as activity, so a kiosk tab stays logged in; that is desired. Short idle timeouts would kick kiosks to a login page. |
| Uniform failure behaviour: one generic "wrong password" message, constant-time compare | Standard | LOW | Single account, so no user enumeration issue, but keep the message generic anyway. |
| Failed-login log line including client IP | Lets fail2ban users and the owner see attempts | LOW | Log failures only, never the attempted password. |
| `/health` stays unauthenticated and leaks nothing | Compose healthcheck and CI smoke test | LOW | Already a constraint. Do not add version/config fields to it. |

#### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Instance-unique cookie name (e.g. suffix from a hash of the signing secret) | Cookies are scoped by host, not port. Prod and debug instances on the same host (common here: two ports) would overwrite each other's cookie and log each other out. Same for users running two meters. | LOW | Cheap and avoids a confusing support issue. |
| Login "next" deep-link and a visible "logged in / log out" affordance on every page | Small polish; kiosk users want to know state | LOW | Base template only. |
| Prominent first-run banner on the setup page: "anyone on your network who opens this page first can set the password" plus the env-var alternative | Honest about the claim race inherent in a setup page on upgrade | LOW | Costs a paragraph; avoids false sense of security. |
| Optional setup-claim token (printed in log, required on the setup form) when env `WATERMETER_REQUIRE_SETUP_TOKEN=1` | Closes the LAN first-claimer race for cautious users (Jenkins-style unlock) | MEDIUM | Opt-in only. Default must stay "no log reading required" per PROJECT.md decision. Defer. |
| Progressive delay on repeated failures (see Area 2) | Better than hard lockout for a single account | LOW | Listed in Area 2. |
| Notification of failed logins since last successful login shown after login | Home Assistant surfaces failed logins; gives the owner awareness | LOW | Counter + timestamp in the auth file. Nice to have. |

#### Anti-Features

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Auth-disabled / "open" mode toggle (AdGuard `users: []`, Uptime Kuma disable-auth) | Users behind an authenticating reverse proxy or on a "trusted" LAN ask for it, and it is the first thing desperate users flip when locked out | Silently reintroduces the 53-open-endpoints problem on a public image; one-line misconfiguration for strangers | Recovery commands (above). If proxy-auth demand appears, handle it as its own explicit decision (trusted-header auth is Out of Scope in PROJECT.md). |
| Generated or default password (admin/admin, or random password only in logs) | Zero-UI bootstrap | Invisible on upgrades; users who never read logs get locked out or run with a known password | Setup page plus env-var seed. |
| Env var that overrides the stored password on every start | "Config as code" | Makes UI password change meaningless and leaves the password sitting in compose files and `docker inspect` | Seed-only semantics. |
| HTTP Basic auth | Trivial to implement | No logout, no first-run setup, browsers cache it, awkward with HTMX/fetch and CSRF | Cookie session. |
| Multi-user accounts, roles, OIDC/OAuth, 2FA, email/SMS reset, security questions, API tokens | Standard "real app" features | Already Out of Scope; each adds large surface for a one-person device. Email reset needs SMTP the app does not have | Single admin + host-level recovery. |
| "Remember me" checkbox, session list/revoke UI | Familiar from SaaS | A single user with a sliding long session does not need the choice; session list is dead weight | Password change = log out everywhere. |
| Password complexity rules, CAPTCHA, password hints | Perceived security | Annoy owners, no gain against a rate-limited single account | Length minimum + throttling. |
| TLS termination in the app | Cookies over HTTP on LAN feel wrong | Out of Scope; cert management is a big surface | Document reverse proxy; set Secure flag when proxied HTTPS. |

---

### Area 2: Login throttling, lockout, session lifetime, logout

How comparable apps behave:
- Frigate: `failed_login_rate_limit` default `1/second;5/minute;20/hour`, in-memory (restart resets it), needs `trusted_proxies` behind a reverse proxy or all clients share the proxy IP. (official docs, MEDIUM)
- AdGuard Home: `auth_attempts` and `block_auth_min` block after N failures for M minutes; 0 disables. (official wiki, MEDIUM)
- Grafana: brute-force login protection locks a login after repeated failures for a short period (training knowledge, LOW).
- Uptime Kuma: has a login rate limiter; threshold unverified (LOW).

#### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Per-client-IP throttle on login, setup and change-password endpoints, in-memory sliding window | Frigate/AdGuard/Grafana all throttle; brute force is the main threat to a LAN device that gets port-forwarded | LOW | Start from Frigate's numbers: 1 failure/s, 5/min, 20/h. HTTP 429 with `Retry-After`; login form shows "try again in N s". Reset on restart is acceptable (Frigate does it). |
| Do not trust `X-Forwarded-For` by default | A spoofable header lets an attacker rotate "IPs" and bypass the limit | LOW | Use socket peer address. Behind a reverse proxy all users share the proxy IP, which is acceptable for a single-user device; offer an optional trusted-proxies setting only if demand appears. |
| Throttle only failures, never successes | A successful owner login must always work | LOW | Counter reset on success is fine. |
| Do not lock the account itself | Single account: an attacker could lock the owner out indefinitely | LOW | Throttle by source plus a small constant delay per failure; no per-username permanent lockout. |
| Server-side idempotent logout and expiry enforcement | Cookie expiry alone is client-side | LOW | Covered in Area 1 (session epoch). |
| Single-flight guard on compute-heavy endpoints (training, synthetic generation, trigger): 409 or 429 when already running, plus a minimum interval for `POST /api/trigger` | The listed endpoints are behind auth after this milestone, so the risk is runaway clients and double-clicks, not strangers | LOW | The pipeline already serializes on an asyncio lock; add explicit guards and clear UI error text. |

#### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Progressive backoff (delay grows per consecutive failure from one IP) | Smoother than hard blocks; protects against slow brute force | LOW | Cap at about 60 s. |
| Surface throttle state in the UI ("3 failed attempts from your address, retry in 20 s") | Reduces "is it broken?" confusion | LOW | Part of the 429 response body. |
| Configurable limits in a new config section | Power users behind proxies | LOW | Must go through `config_utils.py` schema; default to sane values so most users never touch it. |

#### Anti-Features

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Persistent bans, IP allow/deny lists, fail2ban/CrowdSec integration in-app | "Real" brute-force defence | State to persist and recover from; self-lockout; reverse-proxy or host firewall does this better | Log failures with IP (fail2ban can parse); document proxy/firewall. |
| Redis or external store for limiter state | Multi-worker correctness | Single process, single user; adds a service to a one-container app | In-memory limiter. |
| Email/push alert on every failed login | Awareness | No mail stack; alert fatigue | "Failed attempts since last login" line after login (Area 1 differentiator). |
| Per-account permanent lockout until admin unlocks | Strict policy | Only admin is the account being locked: guaranteed owner lockout | Timed throttle. |
| Throttling the polling endpoints the dashboard itself hits | "Protect everything" | Breaks HTMX polling and kiosks | Only login/setup and expensive actions. |

---

### Area 3: Write-only secrets in settings UI and API

How comparable apps behave:
- Grafana data-source API reports which secrets are set (`secureJsonFields`) but never returns values; an update replaces them (training knowledge, LOW).
- Node-RED editor shows a placeholder for stored credentials and treats the placeholder as "unchanged"; credentials live in a separate file from flows (training knowledge, LOW).
- ESPHome and Zigbee2MQTT keep secrets in a separate `secrets` file referenced from the main config (`!secret`) (established convention, MEDIUM).
- Frigate and Home Assistant never display stored passwords in the UI.

#### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| `/api/config` and every config read path return a "password is set" indicator (`password_set: true`) instead of the value | Core requirement (BL-48); the MQTT password is returned in plaintext today | MEDIUM | Do the redaction in one server-side function used by all readers so new routes cannot forget it. |
| Save semantics: omitted or sentinel value = keep existing; non-empty = replace; explicit "clear password" action = remove | Without this, saving any unrelated setting wipes the password (the classic bug) | MEDIUM | Needs a round-trip test: load config -> save unchanged -> password still works. |
| Raw YAML config editor: redact secret values when rendering, merge the original back on save | The editor shows the raw file, so redacting only the JSON API is not enough | MEDIUM-HIGH | Preserve ruamel comments; detect a sentinel in the saved text and restore the stored value. Highest-risk item in this area. |
| MQTT "Test connection" works with the stored password when the form field is left untouched | Otherwise users must retype the password to test, and the temptation is to make the API return it | MEDIUM | `routes/mqtt.py` currently takes the password from the request body and resolves `${ENV}` tokens; fall back to the stored value server-side. |
| Credentials supplied through env vars (`MQTT_USERNAME`/`MQTT_PASSWORD`) are shown as "set via environment", read-only, never echoed | Never expose the resolved value of an env reference | LOW | A literal `${VAR}` reference string in the file is not a secret and may be shown. |
| No secret in any other channel: startup/config dump logs, error messages, diagnostics or download endpoints, HTML `value=` attributes, browser autofill | Redacting one endpoint while another leaks is the common failure | MEDIUM | Audit grep for `password` showed it in `mqtt_publisher.py`, `watermeter_service.py`, `config_utils.py`, `routes/mqtt.py`, `static/mqtt-config.js`, `templates/roi_config.html`. Use `autocomplete="new-password"` on the field. |
| Credentials embedded in the image-source URL (camera `user:pass@host`, or token query parameters) are redacted in API/UI output | IP cameras commonly use URL credentials; the URL is displayed and returned today | MEDIUM | Verify whether config and `/api/roi/*` echo this URL; redact userinfo and well-known token params on read, keep on write via the same keep/replace semantics. Needs a check against the code before planning. |

#### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| `_FILE` suffix variants for secrets (`MQTT_PASSWORD_FILE`) | Docker/Swarm/Kubernetes secrets convention; keeps passwords out of `docker inspect` | LOW | Mirrors the admin-password `_FILE` option. |
| Secrets stored in a separate file (like `!secret`) so `config.yaml` can be pasted into GitHub issues safely | Public repo, public support threads: users will paste configs | MEDIUM-HIGH | Migration of existing installs and schema changes; defer unless cheap. Interim: redacted "copy config for support" is covered by redaction. |
| "Replace secret" UI affordance: field shows "password set" with a Change button | Clear mental model | LOW | Pairs with `password_set`. |

#### Anti-Features

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| "Show password" eye icon that fetches the real value | Convenience when debugging | Re-opens the leak; the value then appears in the browser, HAR files and screenshots | User retypes it; the stored one stays server-side. |
| Masked return of real data (`****` with real length or last 4 chars) | Looks like a compromise | Leaks length and tail | Boolean `password_set`. |
| Encrypting secrets at rest inside the container | Sounds more secure | The key sits next to the data; adds failure modes for no gain against anyone with volume access | File permissions plus write-only API. |
| Returning the admin hash or session secret anywhere | Debugging | Offline cracking / session forging | Never serialize the auth file. |
| Re-auth modal to reveal secrets | "Safer reveal" | Same exposure after re-auth, plus new UI | Do not offer reveal. |

---

### Area 4: Home Assistant visibility of a broken integration

How comparable integrations do it:

| Integration | Mechanism | Takeaway |
|-------------|-----------|----------|
| Zigbee2MQTT | Retained `zigbee2mqtt/bridge/state` = `{"state":"online"\|"offline"}`; `offline` is published right before the bridge stops; per-device availability is separate and opt-in (official docs, MEDIUM) | Bridge-level availability + explicit offline publish on graceful stop |
| Frigate | `frigate/available` online/offline through the MQTT Will Message, republished on every broker connection; per-camera status topics; docs advise debouncing with `for:` in automations because status can flap (official docs, MEDIUM) | LWT-based availability, republish on reconnect, debounce guidance |
| ESPHome | Birth message (retained `online`) and will message (`offline`) on `<prefix>/status`; per-component `availability` defaults to the global birth/will; if birth/will are empty or differ, availability is disabled (official docs, MEDIUM) | Same topic for birth and will, retained |
| AI-on-the-edge-device | Documents `Connection`, `main/status`, `main/error` ("no error" on success) and `main/timestamp` ("time of last valid reading"); no LWT or problem sensor documented (official docs, MEDIUM) | Closest peer offers an error text and last-valid-reading timestamp, so those are baseline expectations for this domain |
| Home Assistant itself | Entities with `availability` go unavailable on `offline`; birth message `homeassistant/status` should trigger discovery republish; `expire_after` turns state unavailable when no update arrives, and docs advise against retaining that state payload; `binary_sensor` `device_class: problem` shows ON as a problem (official docs, MEDIUM) | Native building blocks exist for everything below |

Facts about this repo (read from source, verify during planning):
- The shared state JSON is published retained with QoS 2 and contains `"mqtt_connected": True` and `"last_update": now()`. After a crash the retained `mqtt_connected: true` stays forever, so the current "MQTT Connected" binary_sensor lies after death. No `will_set` call exists; discovery payloads have no `availability`.
- `pipeline_status`, `consecutive_alignment_failures` and `last_alignment_error` are already in the payload, but only reach HA if the publish function runs on failure/rejection paths too. Check whether it does when readings stop arriving at all (the case that matters most).
- The service already subscribes to `homeassistant/status` for rediscovery.

#### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Availability topic (e.g. `<base>/status`) with retained `online` on every (re)connect and an MQTT Last Will of retained `offline`, set before connect | Frigate/Z2M/ESPHome all do it; this is what makes HA show "unavailable" when the container dies or the network drops | MEDIUM | Also publish `offline` explicitly on graceful shutdown: the MQTT spec does not send the Will on a clean DISCONNECT (Z2M does exactly this). Re-publish `online` in `on_connect` so broker restarts recover. |
| Every discovered entity carries `availability` pointing at that topic | Otherwise only some entities react | LOW-MEDIUM | Update the shared discovery builder. Publish `online` together with (or before) discovery so upgrading users do not see a window of "unavailable". Retained discovery configs are overwritten in place (same `unique_id`). |
| `Problem` binary_sensor via discovery (`device_class: problem`, `entity_category: diagnostic` is optional) that is ON when pipeline status is FAILED or STALE, or when last-good-reading age exceeds a threshold | The explicit goal of the milestone; no peer ships this, so it is the domain gap | MEDIUM | Make DEGRADED visible via the status enum/attribute rather than as a problem, to avoid flapping alerts (Frigate docs themselves recommend debouncing). Provide the reason via a JSON attributes topic (`reason`, `since`, `pipeline_status`, `last_good_reading_at`). |
| Last-good-reading timestamp sensor (`device_class: timestamp`) | AI-on-the-edge has `main/timestamp`; HA computes age natively | LOW | Distinguish "last accepted reading" from the current `last_update`, which is the time of a publish, not necessarily a good reading. Publish the timestamp, not an age: an age sensor changes every interval and bloats the HA recorder. |
| Periodic watchdog in the service that re-evaluates staleness | Time passing generates no event; a stale reading cannot detect itself from the reading path | LOW-MEDIUM | Hook into the existing stats loop. Publish only on state change. |
| Configurable staleness threshold with a sane default and a way to disable | Trigger modes differ (cyclic, MQTT-triggered, both); with MQTT-only triggering the expected interval is unknown | LOW | New config field through `config_utils.py` schema. Default derived from the cyclic interval when one exists. |
| Staleness defined as age of the last good reading, not as "value changed recently" | A meter legitimately shows no change for hours at night; fresh-but-unchanged must not alarm | LOW | Easy to get wrong; write it into the requirement. |
| One shared definition of "healthy" used for MQTT, the dashboard banner and the HTTP status API | If HA says problem and the dashboard says fine, the core value is broken | LOW-MEDIUM | A single function returning `{state, reason, since, age}`. |
| Fix the lying `mqtt_connected`: derive it from the availability topic or remove it from the payload | Retained `true` after death is exactly the "silently frozen" failure | LOW | Keep the entity id for compatibility but stop relying on the in-payload field. |
| README section with copy-paste HA automations: notify on problem ON and on `unavailable` for longer than N minutes | Strangers will not design this themselves; both signals are needed (see below) | LOW | Document debounce with `for:`. |

Why both signals: LWT/availability catches a dead or disconnected process. A hung process whose MQTT keepalive thread is still alive does not trip the Will. The problem sensor catches degraded pipelines, but only if the service's own watchdog is still running.

#### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Heartbeat with HA-side `expire_after` on the problem sensor (service republishes its state every N s, HA marks it unavailable if it stops) | Detects a hung container that LWT cannot, with no extra HA configuration | MEDIUM | Heartbeat must be non-retained, per the HA docs on `expire_after`. Costs a small amount of broker traffic. This is where the product beats Frigate and Z2M. |
| `expire_after` on the reading sensor itself | HA marks the value unavailable when stale, independent of the service | MEDIUM | Caveats: do not retain the state payload (it is retained today); if the service only publishes when a value is accepted, quiet periods or rejection streaks would flap it. Verify publish cadence first. Prefer the problem sensor as the primary mechanism. |
| Pipeline-status enum sensor (OK/DEGRADED/FAILED/STALE) with last alignment error as attribute | Debugging from HA without opening the dashboard | LOW | Data is already in the payload; check the discovery list covers it. |
| Reading entity stays available with last known value during pipeline problems (service still online) | HA statistics and the water dashboard keep continuity; the problem sensor carries the alarm | LOW | A design choice rather than code. Do not push `unavailable` for transient pipeline failures. |
| Re-notify policy for the existing STALE notification (once on entry, once on recovery) | Fewer duplicate alerts | LOW | Existing `_notify_stale` is one-shot; add a recovery message only if cheap. |

#### Anti-Features

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Marking the reading `unavailable` on every DEGRADED blip | Feels like "honest" data | Flaps, creates gaps, triggers automation noise | Problem sensor with threshold and `for:` debounce. |
| Publishing zero, null or the last value as if fresh when failing | Keeps graphs continuous | Silent wrongness, the exact thing the core value forbids | Keep last value, flag it. |
| Availability carried inside the state JSON (`mqtt_connected` style) | Easy to add | Cannot report its own death | Real LWT topic. |
| Per-entity availability topics | Finer-grained | More topics to keep consistent, no benefit for a single service | One service-level topic. |
| In-app push/email/Telegram alerting | "Notify me" | Duplicates HA's job, needs credentials and a mail/chat stack | HA automations, documented. |
| Deep Docker `/health` redesign now | Orchestrator restarts | Explicitly deferred (BL-41); also risks restart loops on a camera outage | Keep `/health` trivial; HA sensors for the real signal. |
| Retained staleness state combined with `expire_after` | Faster initial state | HA docs warn that replayed retained values defeat expiry | Non-retained heartbeat or no expiry. |

---

### Area 5: Dashboard "stale data" and "connection lost" banners

How comparable apps behave (judgement from product behaviour; no peer doc to cite for the exact UI):
- Home Assistant frontend and Uptime Kuma show a persistent, auto-clearing "connection lost, reconnecting" indicator and resync on reconnect (training knowledge, LOW).
- Grafana and Portainer mostly show per-panel or toast errors (training knowledge, LOW); those are easy to miss and are the pattern to avoid for a "never silent" goal.
- Frigate docs explicitly warn that status can flap and recommend waiting for it to hold steady.

#### Table Stakes

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Persistent connection-lost banner on every page (put in the base template), triggered by 2-3 consecutive failed polls (network error, timeout, 5xx) and cleared automatically on the first success | The server being down is the case where the page cannot tell you itself | MEDIUM | HTMX events `htmx:sendError`, `htmx:timeout`, `htmx:responseError` plus a small global `fetch` wrapper. Needs a dedicated lightweight heartbeat poll so pages without periodic requests are also covered. |
| Banner must separate "server unreachable" from "session expired" (401) and "server returned an error" | Without that, an expired session shows a misleading "connection lost" | LOW-MEDIUM | Depends on the 401 contract from Area 1. 401 -> redirect to login, not a banner. |
| Last-updated indicator ("Last updated 14:02:07, 3 min ago") and visibly dimmed or marked values while disconnected | Old numbers that look current are the actual failure | LOW | Show the age even when connected; it makes staleness self-evident. |
| Stale/pipeline-not-OK banner driven by server-supplied `pipeline_status`, `reason`, `since` and `last_good_reading_age_s` | Core requirement | LOW-MEDIUM | Use the server-computed age, not a comparison of timestamps against the browser clock (clock skew on tablets and kiosks). Severity: DEGRADED = warning, FAILED/STALE = error. Link to the relevant page (ROI/alignment). |
| Banner precedence and non-stacking: connection lost > session expired > pipeline problem | Avoid showing a "pipeline OK" fact that is itself stale | LOW | One banner region with one message at a time. |
| Recompute and re-poll immediately when a hidden tab becomes visible (`visibilitychange`) | Browsers throttle timers in background tabs; waking up otherwise produces false "connection lost" or falsely fresh state | LOW | Classic kiosk and tablet bug. |
| Do not rely on `navigator.onLine` | It only reflects the client's own link, not whether this server is reachable | LOW | Heartbeat result is the source of truth. |
| Accessible, non-dismissible-by-accident banner (`role="status"`/`aria-live`, uses existing CSS variables, no inline styles) | Matches repo frontend conventions | LOW | Dark/light variables already exist in `style.css`. |

#### Differentiators

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Server boot-id in the heartbeat response; reload the page automatically when it changes | After a container upgrade the open tab gets new templates and JS without a manual refresh | LOW-MEDIUM | Also avoids half-updated pages after the auth change ships. |
| Tab title/favicon prefix when not OK (e.g. "(!) Watermeter") | A tab or kiosk in the background still shows trouble | LOW | A few lines of JS. |
| Banner shows the specific reason in plain words ("alignment failed 12 times", "camera unreachable") | Users act faster than with a generic "FAILED" | LOW | Reuses `last_alignment_error` and the shared health function from Area 4. |
| Recovery message "Connection restored" shown briefly | Confirms the fix without stale-state ambiguity | LOW | Auto-clears after a few seconds. |

#### Anti-Features

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| WebSocket or SSE push transport for liveness | "Real-time" and instant disconnect detection | Violates the HTMX-polling, no-framework stack constraint; reconnect logic and proxy buffering issues | Short heartbeat poll. |
| Toasts that auto-dismiss | Less intrusive | Missed by exactly the people who weren't looking | Persistent banner that clears itself on recovery. |
| Modal blocking overlay | Forces attention | Hides the data users need to diagnose; annoying on kiosks | Top banner. |
| Service worker / PWA offline cache | "Works offline" | Serves stale pages that look live, the very failure we're preventing | None. |
| Browser-side age math from timestamps | Fewer fields in the API | Clock skew gives wrong "stale" banners | Server-supplied age. |
| Audible alarms, browser notifications | Awareness | Permissions and annoyance, and HA is the alerting channel | HA automations. |
| Dismissal state persisted across reloads | Reduce repeated nagging | A dismissed warning for a still-broken pipeline is a silent failure | Auto-clear only when the condition clears. |

---

## Feature Dependencies

```
Setup gate (no hash => setup mode)
    └──requires──> Auth file storage in /data + KDF hashing
                       └──requires──> Persistent session-signing secret

Login / logout / 401 contract
    └──requires──> Session cookie + session epoch
    └──enhances──> CSRF protection (cookie-authenticated writes)

Env-var bootstrap (seed-only) ──requires──> Auth file storage
    └──enables──> debug_clean.sh, integration fixture, CI smoke tests

Recovery CLI + "delete auth file" doc ──requires──> Auth file storage

Login rate limiting ──requires──> Correct client-IP handling (socket peer, no XFF trust)
Login rate limiting ──requires──> Login endpoint (cannot precede it)

Write-only secrets
    ├──requires──> Single redaction function used by all config readers
    ├──requires──> Keep/replace/clear save semantics
    └──requires──> MQTT test-connection falls back to stored secret
Raw YAML editor redaction ──requires──> ruamel comment-preserving merge-back

Availability + LWT ──requires──> MQTT client Will set before connect, birth in on_connect
    └──requires──> Availability field in the discovery builder
         └──enhances──> Problem binary_sensor (a dead service shows unavailable, a live broken one shows ON)

Problem binary_sensor ──requires──> Shared health function {state, reason, since, age}
                       ──requires──> Staleness watchdog + configurable threshold
Last-good-reading timestamp ──requires──> Distinguishing "last good" from "last publish"

Dashboard stale banner ──requires──> Shared health function (same as HA)
Dashboard connection-lost banner ──requires──> 401 contract (to tell expiry from outage)
Boot-id auto-reload ──enhances──> Connection-lost banner

Heartbeat + expire_after ──requires──> Non-retained heartbeat publish
                          ──conflicts──> Retained state payload on the same entity

Auth-disabled mode ──conflicts──> Secure-by-default for upgrades
Env var overriding stored password ──conflicts──> UI password change
```

### Dependency Notes

- **Setup gate requires auth storage and hashing:** "no hash on disk" is the single source of truth for setup mode, so upgrades and fresh installs take the same code path.
- **Rate limiting requires the login endpoint and client-IP policy:** build limiter and login together; limiting by a spoofable header is worse than none.
- **Write-only secrets are independent of auth but should land after it:** with the whole API gated, a redaction slip is a smaller incident, but redaction should still ship in the same milestone; the test-connection change is the one that is easy to forget.
- **Problem sensor and dashboard banner share one health definition:** if they diverge, the dashboard and HA disagree, which breaks the core value.
- **Connection-lost banner needs the auth contract:** the JS error handler must treat 401 as "go to login" before it can treat network failures as "server down".
- **Availability change touches every discovery payload:** do it once in the builder and publish `online` before/with discovery so upgraders never see a flash of unavailable.
- **Auth and reliability tracks are otherwise independent** and can be sequenced in either order or interleaved, except for the 401/banner link above.

## MVP Definition

### Launch With (this milestone)

- [ ] Setup gate, auth file with 0600 hash and KDF, persistent signing secret — secure-by-default on fresh and upgrading installs
- [ ] Login, logout (POST), 401/HX-Redirect contract, sliding session cookie — usable without breaking HTMX/JS
- [ ] Env-var seed (`WATERMETER_ADMIN_PASSWORD`, plus `_FILE`) — unblocks `debug_clean.sh`, integration tests, CI
- [ ] Documented recovery: reset CLI and "delete auth file" — avoids an unsupportable lockout
- [ ] Change password with session invalidation
- [ ] Per-IP failure throttle on login/setup/change-password with 429 and Retry-After — brute-force defence
- [ ] Single-flight guards on training, synthetic generation, trigger
- [ ] Write-only MQTT password across API, config editor, YAML editor, test-connection, logs; redaction of camera-URL credentials after a code check
- [ ] Availability topic with Will and birth, explicit offline on shutdown, `availability` on every discovery entity
- [ ] Problem binary_sensor with reason attributes, last-good-reading timestamp sensor, staleness watchdog, configurable threshold
- [ ] Shared health function feeding MQTT and the dashboard
- [ ] Connection-lost banner (consecutive-failure rule, 401 distinct) and stale/not-OK banner with server-computed age
- [ ] README: reverse proxy note, HA automation examples, recovery, upgrade notice

### Add After Validation (next iterations)

- [ ] Heartbeat plus `expire_after` hang detection — once real hangs are observed or requested
- [ ] Boot-id auto-reload and tab-title indicator — small polish after banners work
- [ ] Progressive backoff and "failed attempts since last login" notice
- [ ] Optional setup-claim token for cautious users
- [ ] Separate secrets file (`!secret`-style)

### Future Consideration (v2+)

- [ ] Trusted-proxy auth or auth delegation — explicitly Out of Scope now; reconsider if demand appears
- [ ] API tokens for HTTP machine clients — only if HTTP integrations appear
- [ ] Deeper `/health`, rate-history persistence, Docker resource limits — next reliability milestone

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Setup gate + hash storage | HIGH | MEDIUM | P1 |
| Login/logout/401 contract | HIGH | MEDIUM | P1 |
| Env-var seed (+ `_FILE`) | HIGH | LOW | P1 |
| Recovery CLI + file-delete doc | HIGH | LOW | P1 |
| Change password + session invalidation | MEDIUM | LOW | P1 |
| Login failure throttle | HIGH | LOW | P1 |
| Single-flight guards on heavy endpoints | MEDIUM | LOW | P1 |
| Write-only MQTT password (API + editors + test) | HIGH | MEDIUM-HIGH | P1 |
| Camera-URL credential redaction | MEDIUM | MEDIUM | P2 (verify scope first) |
| Availability topic + LWT + per-entity availability | HIGH | MEDIUM | P1 |
| Problem binary_sensor + reason attributes | HIGH | MEDIUM | P1 |
| Last-good-reading timestamp sensor | HIGH | LOW | P1 |
| Staleness watchdog + threshold | HIGH | LOW-MEDIUM | P1 |
| Shared health function | HIGH | LOW-MEDIUM | P1 |
| Connection-lost banner | HIGH | MEDIUM | P1 |
| Stale / pipeline banner | HIGH | LOW-MEDIUM | P1 |
| Instance-unique cookie name | MEDIUM | LOW | P2 |
| HA automation examples in README | MEDIUM | LOW | P1 |
| Visibility-change resync | MEDIUM | LOW | P1 (part of banner) |
| Heartbeat + `expire_after` | MEDIUM | MEDIUM | P2 |
| Boot-id auto-reload | MEDIUM | LOW-MEDIUM | P2 |
| Progressive backoff | LOW | LOW | P3 |
| Setup-claim token (opt-in) | LOW | MEDIUM | P3 |
| Separate secrets file | LOW | HIGH | P3 |

**Priority key:**
- P1: Must have for this milestone
- P2: Should have, add when possible
- P3: Nice to have, future consideration

## Competitor Feature Analysis

| Feature | Frigate | Zigbee2MQTT / ESPHome | Grafana / AdGuard / Portainer / Uptime Kuma | Our Approach |
|---------|---------|-----------------------|---------------------------------------------|--------------|
| First-run credential | Generated password in log | n/a | Setup page (Portainer, Uptime Kuma, AdGuard); default admin with forced change (Grafana) | Setup page; env seed for headless |
| Lockout recovery | Config flag prints new password | n/a | CLI (Grafana, Uptime Kuma), YAML hash edit (AdGuard), restart (Portainer) | CLI plus delete auth file |
| Login throttling | `1/second;5/minute;20/hour`, per IP, memory | n/a | Block after N attempts for M min (AdGuard); limiter (Grafana, Uptime Kuma) | Same numbers as Frigate, memory, no account lockout |
| Session length | 24 h default, JWT in cookie, secret persisted | n/a | Hours to weeks | Long sliding default for kiosks |
| Availability via LWT | `frigate/available` | `bridge/state`, `<prefix>/status` | n/a | Retained availability topic plus explicit offline on stop |
| Problem/health entity in HA | Per-role camera status topics, not a problem sensor | Z2M health check request/response | n/a | `problem` binary_sensor with reason attributes |
| Last valid reading timestamp | n/a | n/a | n/a | Timestamp sensor (peer AI-on-the-edge has `main/timestamp`) |
| Auth-disable option | Yes, for proxy-auth setups | n/a | AdGuard `users: []`, Uptime Kuma disable | Deliberately none |

## Sources

- Home Assistant MQTT integration docs (birth/will, discovery republish, retained discovery caveats): https://www.home-assistant.io/integrations/mqtt/ (MEDIUM)
- Home Assistant MQTT binary_sensor and sensor docs (availability options, `expire_after`, `device_class`, `entity_category`): https://www.home-assistant.io/integrations/binary_sensor.mqtt/ and https://www.home-assistant.io/integrations/sensor.mqtt/ (MEDIUM)
- Frigate authentication docs (generated admin password, `reset_admin_password`, `failed_login_rate_limit`, `session_length`, JWT secret file, `trusted_proxies`, unauthenticated internal port): https://docs.frigate.video/configuration/authentication (MEDIUM)
- Frigate MQTT docs (`frigate/available` via Will message, per-role status caveat): https://docs.frigate.video/integrations/mqtt (MEDIUM)
- Zigbee2MQTT MQTT topics (`bridge/state`, availability): https://www.zigbee2mqtt.io/guide/usage/mqtt_topics_and_messages.html (MEDIUM)
- ESPHome MQTT component (birth/will, availability defaults): https://esphome.io/components/mqtt/ (MEDIUM)
- AI-on-the-edge-device MQTT API docs (`Connection`, `main/status`, `main/error`, `main/timestamp`): https://jomjol.github.io/AI-on-the-edge-device-docs/MQTT-API/ (MEDIUM)
- Grafana CLI admin password reset: https://grafana.com/docs/grafana/latest/administration/cli/ (MEDIUM)
- AdGuard Home configuration wiki (bcrypt hash, `auth_attempts`, `block_auth_min`, session TTL, `users: []`, password reset by editing YAML; page marked outdated): https://github.com/AdguardTeam/AdGuardHome/wiki/Configuration (LOW-MEDIUM)
- Portainer first-run timeout, Uptime Kuma `npm run reset-password`, Paperless-ngx admin env vars: third-party tutorials and community pages found via web search; not confirmed against official docs (LOW)
- Node-RED credential placeholder and Grafana `secureJsonFields` write-only patterns, session-lifetime values, MQTT-spec behaviour that the Will is not sent on a clean DISCONNECT: prior knowledge, not re-verified this session (LOW)
- Repo observations (retained state payload with `mqtt_connected`, no `will_set`, `routes/mqtt.py` password handling, existing `homeassistant/status` subscription): read from `watermeter/mqtt_publisher.py` and `watermeter/routes/mqtt.py` this session (HIGH for what the code does, MEDIUM for implications; verify before planning)

---
*Feature research for: self-hosted single-user home-automation web app (security and reliability basics)*
*Researched: 2026-10-11*
