# Roadmap: Watermeter Inference

## Overview

The Reliability & Security Basics milestone turns a wide-open, trusted-LAN app into one that is safe to run from a public Docker image, and makes a stale or broken reading impossible to miss. Phase 1 puts the whole UI and API behind a single admin password, with a first-run setup page that also catches upgrading installs, and brings the tests, CI and helper scripts along in the same step so nothing goes red. Phase 2 hardens what sits behind that gate: write-only secrets, an SSRF guard on camera fetches, limits on heavy jobs, security headers and a self-hosted htmx. Phase 3 makes Home Assistant see failures through MQTT availability, a problem sensor and a last-good-reading timestamp, all driven by one shared health definition. Phase 4 shows the same health on the dashboard and adds a connection-lost banner, so the value on screen is either correct and current, or visibly flagged as not.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Auth Core and Test/Script Plumbing** - Single admin password gates the whole UI and API, with first-run setup, sessions, CSRF, login throttle, and tests/CI/scripts green
- [ ] **Phase 2: Hardening Behind Auth** - Write-only secrets, SSRF guard, single-flight limits on heavy jobs, security headers, vendored htmx, decisions recorded
- [ ] **Phase 3: Home Assistant Visibility** - MQTT availability with Last Will, a problem sensor and last-good-reading timestamp driven by one shared health definition
- [ ] **Phase 4: Dashboard Banners** - Stale / not-OK banner, reading age, connection-lost banner, background-tab resync, auto-reload on new version

## Phase Details

### Phase 1: Auth Core and Test/Script Plumbing

**Goal**: Every page and API on a fresh or upgraded install is protected by one admin password that the owner sets on first visit, and the existing tests, CI and helper scripts keep working with auth on.
**Depends on**: Nothing (first phase)
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, AUTH-06, AUTH-07, AUTH-08, AUTH-09, AUTH-10, AUTH-11, AUTH-12, AUTH-13, AUTH-14, AUTH-15
**Success Criteria** (what must be TRUE):
  1. On a fresh or upgraded install with no password, every page redirects to a setup page where the owner sets a password (min. 8 chars, confirm field), only `/health`, the setup page and static assets are reachable until then, and once set the setup page is permanently closed (a second or concurrent attempt is refused).
  2. Owner can log in, stays logged in across browser restarts, can log out from any page, and can change the password (current password required) which logs out every other session. Login works over plain HTTP on a LAN, and prod and debug instances on the same host keep separate sessions.
  3. A request without a valid session to anything outside the allowlist (including `/docs`, `/openapi.json` and routes added later) is refused. Full pages redirect to login and return to the original page afterwards, HTMX and `fetch()` calls get a 401 that sends the browser to login (no login HTML ever appears inside a page fragment), and a cross-site page cannot perform a state-changing request with the owner's cookie.
  4. Repeated failed logins from one client get a 429 with Retry-After without ever locking the owner out permanently. A locked-out owner can recover with host access only (`python -m watermeter set-password` or deleting the auth file), an operator can pre-seed the password with `WATERMETER_ADMIN_PASSWORD` (or `_FILE`) and optionally require a one-time setup token, and the README documents recovery and the upgrade notice.
  5. Unit tests, integration tests, the CI smoke test, `debug_clean.sh` and `oneshot_test.sh` all pass with auth enabled, and no auth-disable switch exists in production code.

**Plans**: 5 plans

Plans:
**Wave 1**
- [ ] 01-01-PLAN.md — Auth core: credential store (scrypt, 0600 atomic auth.json), session tokens, throttle, set-password CLI (unwired)

**Wave 2** *(blocked on Wave 1 completion)*
- [ ] 01-02-PLAN.md — Default-deny AuthMiddleware (CSRF, 401 contract, sliding refresh) and auth.js shim, proven on a harness app
- [ ] 01-03-PLAN.md — Setup, login, logout, account and change-password routes with themed pages, proven on a harness app

**Wave 3** *(blocked on Wave 2 completion)*
- [ ] 01-04-PLAN.md — The switch: wire gate and routers, disable docs, shim and nav on every page, logged-in fixtures, route inventory, startup seed, CI smoke

**Wave 4** *(blocked on Wave 3 completion)*
- [ ] 01-05-PLAN.md — Integration fixtures, debug/clean scripts, README/DOCKER docs, live verification on :8002/:8003 and one-shot

**UI hint**: yes

### Phase 2: Hardening Behind Auth

**Goal**: Secrets never leave the server, the server cannot be turned against internal addresses, heavy jobs cannot be stacked, and the app serves itself safely on offline LANs.
**Depends on**: Phase 1
**Requirements**: HARD-01, HARD-02, HARD-03, HARD-04, HARD-05, HARD-06, HARD-07, HARD-08, HARD-09, HARD-10
**Success Criteria** (what must be TRUE):
  1. The MQTT password is never returned by any endpoint or shown in the config editor: the UI shows "password set", saving an unrelated setting or round-tripping the YAML editor keeps the stored password and the comments intact, the owner can explicitly replace or clear it, and "Test connection" works with the stored password without re-typing it. Credentials embedded in the camera URL are redacted in API responses, the UI and logs.
  2. A camera URL pointing at loopback, link-local/metadata, multicast or reserved targets (including encoded and IPv6-mapped forms) is rejected when saved, redirects are not followed, and LAN cameras (RFC 1918/CGNAT) keep working. On an existing install with a blocked URL the dashboard shows a distinct visible error instead of a silently stale pipeline.
  3. Starting training, synthetic generation, trigger or a mislabel scan while one is already running is refused (409), and rapid repeats are rate limited, each with a visible message in the UI.
  4. Every response carries security headers (nosniff, referrer policy, light CSP, configurable frame-ancestors so Home Assistant iframe embedding stays possible), and the dashboard works on an offline LAN because htmx is served from the app itself.
  5. `docs/decisions.md` records AD-01 and AD-02 as superseded and notes the accepted SSRF DNS-rebinding gap.

**Plans**: TBD
**UI hint**: yes

### Phase 3: Home Assistant Visibility

**Goal**: Home Assistant always knows whether the service is alive and whether the reading it shows is trustworthy, and it flags the cases where it is not.
**Depends on**: Phase 2
**Requirements**: HAV-01, HAV-02, HAV-03, HAV-04, HAV-05, HAV-06, HAV-07, HAV-08, HAV-09, HAV-10
**Success Criteria** (what must be TRUE):
  1. When the service stops, crashes or loses its broker connection, all of its HA entities become unavailable, and they come back on reconnect. The retained state no longer claims `mqtt_connected: true` after the service has died, and stopping the debug instance never marks prod entities unavailable.
  2. A `problem` binary_sensor in HA turns on when the pipeline is FAILED/STALE, when every reading has been rejected for a while, or when no accepted reading arrived within a configurable threshold, and its attributes give the reason. It raises no false alarm during the startup grace period or when the meter value simply does not change in `mqtt` trigger mode.
  3. HA shows a last-good-reading timestamp (time of the last accepted reading, not the last publish) and a pipeline-status sensor (OK/DEGRADED/FAILED/STALE) with the last error as an attribute.
  4. A hung service (alive but not working) is detected by HA because the health topic is republished periodically and the problem sensor expires. After a STALE notification, a recovery notification is sent once the pipeline is OK again.
  5. The README has copy-paste HA automation examples for "notify on problem on" and "notify on unavailable for N minutes".

**Plans**: TBD

### Phase 4: Dashboard Banners

**Goal**: A dashboard user can tell at a glance whether the displayed reading is current and whether the server is reachable, using the same health definition as Home Assistant.
**Depends on**: Phase 3 (also builds on the Phase 1 401 contract)
**Requirements**: DASH-01, DASH-02, DASH-03, DASH-04, DASH-05
**Success Criteria** (what must be TRUE):
  1. The dashboard shows a banner with a plain-language reason whenever the pipeline is not OK or the last good reading is too old, using the same health definition as HA, and shows the age of the displayed reading ("last updated 3 min ago") computed by the server.
  2. Every page shows a "connection lost" banner after a few consecutive failed polls and clears it by itself on recovery, while an expired session sends the user to login instead of showing "connection lost".
  3. Returning to a background tab re-polls immediately instead of showing a false "connection lost" or stale state.
  4. Open tabs reload automatically after the container restarts with a new version.

**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Auth Core and Test/Script Plumbing | 0/5 | Planned | - |
| 2. Hardening Behind Auth | 0/TBD | Not started | - |
| 3. Home Assistant Visibility | 0/TBD | Not started | - |
| 4. Dashboard Banners | 0/TBD | Not started | - |
