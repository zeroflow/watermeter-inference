# Requirements: Watermeter Inference — Reliability & Security Basics

**Defined:** 2026-10-11
**Core Value:** The value Home Assistant shows is either correct and current, or visibly flagged as not. It is never silently wrong or frozen.

## v1 Requirements

### Authentication (AUTH)

- [x] **AUTH-01**: On a fresh or upgraded install with no admin password, every page redirects to a setup page where the owner sets the password (min. 8 chars, confirm field). Only `/health`, the setup page and static assets are reachable until then.
- [x] **AUTH-02**: Once a password is set, the setup page is permanently closed (a second setup attempt is refused, including concurrent submissions).
- [x] **AUTH-03**: Owner can log in with the admin password and stays logged in across browser restarts (sliding session, default about 30 days).
- [x] **AUTH-04**: Owner can log out from any page (POST), which ends that session.
- [x] **AUTH-05**: Owner can change the password in the UI (current password required); every other existing session is logged out.
- [x] **AUTH-06**: Every route except the explicit allowlist (`/health`, `/login`, `/setup` while unconfigured, `/logout`, `/static/*`) rejects unauthenticated requests, including `/docs`/`/openapi.json` and routes added later (enforced by a route-inventory test).
- [x] **AUTH-07**: An unauthenticated or expired session never shows login HTML inside a page fragment: full pages redirect to login and return to the original page afterwards; HTMX and `fetch()` calls get a 401 that sends the browser to login.
- [x] **AUTH-08**: A cross-site page cannot perform state-changing requests with the owner's cookie (Origin check plus required custom header on writes, works over plain HTTP on a LAN).
- [x] **AUTH-09**: Operator can pre-seed the admin password via `WATERMETER_ADMIN_PASSWORD` (or `_FILE`). It is used only when no password exists yet and is ignored afterwards.
- [x] **AUTH-10**: A locked-out owner can reset the password with host access only (`python -m watermeter set-password` in the container, or deleting the auth file and restarting), documented in the README.
- [x] **AUTH-11**: Repeated failed logins from one client are throttled (429 with Retry-After), without ever locking the owner's account permanently; failures are logged with client IP, never the attempted password.
- [x] **AUTH-12**: Prod and debug instances on the same host keep separate login sessions (per-instance cookie name).
- [x] **AUTH-13**: Session cookie is HttpOnly and SameSite=Lax, and Secure only when the request is HTTPS, so login works on plain-HTTP LANs and behind a TLS proxy. The signing secret persists in `/data` across restarts.
- [x] **AUTH-14**: Operator can opt in (env flag) to requiring a one-time setup token, printed in the container log, on the setup page.
- [x] **AUTH-15**: Unit tests, integration tests, CI smoke test, `debug_clean.sh` and `oneshot_test.sh` pass with auth enabled, without any auth-disable switch in production code.

### Hardening (HARD)

- [ ] **HARD-01**: The MQTT password is never returned by any endpoint (`/api/config`, YAML config editor, `/api/mqtt/config`). The UI shows "password set" and saving an unrelated setting keeps the stored password.
- [ ] **HARD-02**: Owner can replace or clear the MQTT password explicitly; the YAML editor round trip keeps comments and the stored secret intact.
- [ ] **HARD-03**: "Test connection" on the MQTT page works with the stored password without re-typing it, and the stored password is only sent to the saved broker host/port.
- [ ] **HARD-04**: Credentials embedded in the camera URL (`user:pass@`, token params) are redacted in API responses, the UI and logs.
- [ ] **HARD-05**: Server-side URL fetches (image source, ROI fetch, MQTT test, pipeline fetch) refuse loopback, link-local/metadata, multicast and reserved targets, including encoded/IPv6-mapped forms, and do not follow redirects, while LAN (RFC 1918/CGNAT) cameras keep working.
- [ ] **HARD-06**: A camera URL blocked by the SSRF guard is rejected when saved, and on an existing install it shows a distinct visible error instead of a silently stale pipeline.
- [ ] **HARD-07**: Compute-heavy actions (training, synthetic generation, trigger, mislabel scan) refuse a second concurrent run (409) and are rate limited, with a visible message in the UI.
- [ ] **HARD-08**: All responses carry security headers (nosniff, referrer policy, light CSP, configurable frame-ancestors so HA iframe embedding stays possible).
- [ ] **HARD-09**: htmx is served from the app itself (no CDN), so the dashboard works on offline LANs.
- [ ] **HARD-10**: `docs/decisions.md` records AD-01 and AD-02 as superseded, plus the accepted SSRF DNS-rebinding gap.

### Home Assistant Visibility (HAV)

- [ ] **HAV-01**: When the service stops, crashes or loses its broker connection, all its HA entities become unavailable (retained availability topic with Last Will, explicit offline on clean stop, online on every reconnect).
- [ ] **HAV-02**: A `problem` binary_sensor in HA turns on when the pipeline is FAILED/STALE, when every reading has been rejected for a while, or when no accepted reading arrived within a configurable threshold. Its attributes give the reason.
- [ ] **HAV-03**: HA shows a last-good-reading timestamp sensor (time of the last accepted reading, not of the last publish).
- [ ] **HAV-04**: Staleness is re-evaluated on a timer independent of readings, with a startup grace period and a trigger-mode-aware default threshold (no false alarms when the meter value simply doesn't change).
- [ ] **HAV-05**: The retained state no longer claims `mqtt_connected: true` after the service has died.
- [ ] **HAV-06**: HA shows a pipeline-status sensor (OK/DEGRADED/FAILED/STALE) with the last error as an attribute.
- [ ] **HAV-07**: A hung (alive but not working) service is detected by HA: the health topic is republished periodically and the problem sensor uses `expire_after`.
- [ ] **HAV-08**: After a STALE notification, a recovery notification is sent once the pipeline is OK again.
- [ ] **HAV-09**: Prod and debug instances use distinct availability/health topics, so stopping debug never marks prod entities unavailable.
- [ ] **HAV-10**: README has copy-paste HA automation examples (notify on problem on, and on unavailable for N minutes).

### Dashboard (DASH)

- [ ] **DASH-01**: The dashboard shows a banner with a plain-language reason when the pipeline is not OK or the last good reading is too old (same health definition as HA).
- [ ] **DASH-02**: The dashboard shows the age of the displayed reading ("last updated 3 min ago"), computed by the server.
- [ ] **DASH-03**: Every page shows a "connection lost" banner after a few consecutive failed polls, which clears itself on recovery; an expired session goes to login instead of showing "connection lost".
- [ ] **DASH-04**: Coming back to a background tab re-polls immediately instead of showing a false "connection lost" or stale state.
- [ ] **DASH-05**: Open tabs reload automatically when the container restarts with a new version (boot ID in the heartbeat).

## v2 Requirements

### Reliability

- **REL-01**: Reset / in-flight reading race fixed (reset goes through the processing lock; MQTT reset marshalled onto the event loop)
- **REL-02**: Concurrent config writes serialized (file lock in `config_utils.py`)
- **REL-03**: Rate history persisted across restarts (BL-28)
- **REL-04**: Docker resource limits for training vs inference (BL-58)
- **REL-05**: Deeper `/health` for orchestrators (BL-41)

### Security

- **SEC-01**: SSRF connection pinning to the validated IP (closes the DNS-rebinding gap)
- **SEC-02**: `MQTT_PASSWORD_FILE` / `_FILE` variants for other secrets
- **SEC-03**: Secrets in a separate file so `config.yaml` can be shared safely
- **SEC-04**: MQTT TLS/auth to the broker (BL-37)
- **SEC-05**: Strict CSP (remove inline handlers/styles; vendor Monaco)

### UX

- **UX-01**: "Failed login attempts since last login" notice
- **UX-02**: Tab-title problem indicator
- **UX-03**: "Connection restored" flash

## Out of Scope

| Feature | Reason |
|---------|--------|
| Auth-disabled / open mode | First thing desperate users flip; reintroduces the wide-open default |
| Multi-user accounts, roles, OIDC, 2FA, email reset | One admin is enough for a home device; large surface |
| Generated password printed in logs, or default credentials | Invisible on upgrades; setup page chosen instead |
| Env var overriding the stored password on every start | Makes UI password change meaningless; seed-only instead |
| HTTP Basic auth | No logout, awkward with HTMX/fetch |
| API tokens for machine clients | Only MQTT and the browser use the app today |
| Trusted-header proxy auth | Not chosen; built-in password is the default |
| TLS termination in the app | Reverse proxy's job; document instead |
| Persistent IP bans / fail2ban integration in-app | Self-lockout risk; host firewall does it better |
| "Show password" reveal for secrets | Reopens the leak |
| WebSocket/SSE liveness, service worker | Violates the HTMX-polling stack; offline cache would show stale pages as live |
| In-app push/email alerts | HA automations are the alerting channel |
| Tagged release as part of this milestone | Done = running on main/prod; release later |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| AUTH-01 | Phase 1 | Complete |
| AUTH-02 | Phase 1 | Complete |
| AUTH-03 | Phase 1 | Complete |
| AUTH-04 | Phase 1 | Complete |
| AUTH-05 | Phase 1 | Complete |
| AUTH-06 | Phase 1 | Complete |
| AUTH-07 | Phase 1 | Complete |
| AUTH-08 | Phase 1 | Complete |
| AUTH-09 | Phase 1 | Complete |
| AUTH-10 | Phase 1 | Complete |
| AUTH-11 | Phase 1 | Complete |
| AUTH-12 | Phase 1 | Complete |
| AUTH-13 | Phase 1 | Complete |
| AUTH-14 | Phase 1 | Complete |
| AUTH-15 | Phase 1 | Complete |
| HARD-01 | Phase 2 | Pending |
| HARD-02 | Phase 2 | Pending |
| HARD-03 | Phase 2 | Pending |
| HARD-04 | Phase 2 | Pending |
| HARD-05 | Phase 2 | Pending |
| HARD-06 | Phase 2 | Pending |
| HARD-07 | Phase 2 | Pending |
| HARD-08 | Phase 2 | Pending |
| HARD-09 | Phase 2 | Pending |
| HARD-10 | Phase 2 | Pending |
| HAV-01 | Phase 3 | Pending |
| HAV-02 | Phase 3 | Pending |
| HAV-03 | Phase 3 | Pending |
| HAV-04 | Phase 3 | Pending |
| HAV-05 | Phase 3 | Pending |
| HAV-06 | Phase 3 | Pending |
| HAV-07 | Phase 3 | Pending |
| HAV-08 | Phase 3 | Pending |
| HAV-09 | Phase 3 | Pending |
| HAV-10 | Phase 3 | Pending |
| DASH-01 | Phase 4 | Pending |
| DASH-02 | Phase 4 | Pending |
| DASH-03 | Phase 4 | Pending |
| DASH-04 | Phase 4 | Pending |
| DASH-05 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 40 total
- Mapped to phases: 40
- Unmapped: 0

---
*Requirements defined: 2026-10-11*
*Last updated: 2026-10-11 after roadmap creation*
