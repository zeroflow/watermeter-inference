---
gsd_state_version: "1.0"
current_phase: 01
current_phase_name: Auth Core and Test/Script Plumbing
status: verifying
stopped_at: Completed 01-05-PLAN.md
last_updated: "2026-10-23T20:03:44.108Z"
last_activity: 2026-10-23
last_activity_desc: Phase 01 execution started
state_head: eba8b186dc3207d1767b0fa0b99bf76f683556b2
progress:
  total_phases: 4
  completed_phases: 0
  total_plans: 5
  completed_plans: 5
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-10-11)

**Core value:** The value Home Assistant shows is either correct and current, or visibly flagged as not. It is never silently wrong or frozen.
**Current focus:** Phase 01 — Auth Core and Test/Script Plumbing

## Current Position

Phase: 01 (Auth Core and Test/Script Plumbing) — EXECUTING
Plan: 5 of 5
Status: Phase complete — ready for verification
Last activity: 2026-10-23 — Phase 01 execution started

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: - min
- Total execution time: 0.0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
**Per-Plan Metrics:**

| Plan | Duration | Tasks | Files |
|------|----------|-------|-------|
| Phase 01 P01 | 9 min | 3 tasks | 7 files |
| Phase 01 P02 | 7 min | 3 tasks | 5 files |
| Phase 01 P03 | 5 min | 3 tasks | 7 files |
| Phase 01 P04 | 14 min | 3 tasks | 12 files |
| Phase 01 P05 | 60 min | 3 tasks | 9 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: 4 phases (auth core + test/script plumbing, hardening, HA visibility, dashboard banners). AUTH-15 lives in Phase 1 with the gate so tests, CI and scripts never go red.
- [Research]: Default-deny pure-ASGI auth gate, custom stdlib-`hmac` signed cookie, `hashlib.scrypt` password hash, secret and hash in `/data/auth.json` (not `config.yaml`).
- [Research]: CSRF via Origin check plus required custom header (no token). SSRF guard is check-then-fetch with redirects off; the DNS-rebinding gap is accepted and recorded in `docs/decisions.md`.
- [Research]: One pure `health.evaluate()` feeds the HA problem sensor, the dashboard banner and `/api/status`, so HA and the dashboard cannot disagree.
- [Phase 01]: [01-01] auth.json that exists but is damaged, invalid or unreadable is UNAVAILABLE (fail closed), never UNCONFIGURED; cache key is stat identity plus mode, uid and gid
- [Phase 01]: [01-01] Login throttle is per client key only, in memory, no global backstop; set-password CLI keeps secret and instance_id and bumps epoch
- [Phase 01]: 01-02: next= values in Location and HX-Redirect are fully percent-encoded (safe='')
- [Phase 01]: 01-02: CSRF check runs before the allowlist; Origin compared by netloc only, X-Forwarded-Host never read, empty netloc refused
- [Phase 01]: 01-02: sliding refresh skipped when the response already sets the session cookie (logout and password change stay effective)
- [Phase 01]: 01-03: setup token is checked before password validation; throttle keys are <ip>, setup:<ip>, pw:<ip>
- [Phase 01]: 01-03: wrong current password on /api/auth/password answers 403 (auth.js treats 401 as expired session); same-password change allowed and bumps epoch
- [Phase 01]: 01-04: AuthMiddleware is installed unconditionally with no bypass flag; tests authenticate through a real auth.json and a minted cookie
- [Phase 01]: 01-04: _auth_startup never raises and logs only the name of the seed variable, never the value; the volume warning is skipped when a seed variable is set
- [Phase 01]: 01-04: CI smoke logic lives in scripts/auth_smoke.sh and runs before the GHCR push, so a broken gate blocks :dev
- [Phase 01]: 01-05: debug_clean.sh passes PUID/PGID so the throwaway container's bind-mounted config dir stays removable by the caller
- [Phase 01]: 01-05: full integration run classified, not hidden: 3 training tests fail identically on the pre-phase image (CPU too slow for the 300 s limit), benchmark cancel is a timing race; none is an auth regression

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 1]: Spike needed before locking the 401 contract: confirm `HX-Redirect` on a 401 with the pinned htmx version (`auth.js` XHR-level redirect is the fallback), scrypt `maxmem` on the image's OpenSSL, and real-browser behavior of the Origin-only CSRF path over plain HTTP.
- [Phase 1]: No persistent `/data` volume means setup reopens on every container recreate. Warn at startup and recommend `WATERMETER_ADMIN_PASSWORD`.
- [Phase 2]: Raw-YAML secret round trip with ruamel is the highest-risk item (mask must never be written back). Check which endpoints and logs echo `images.src` credentials before sizing redaction work.
- [Phase 3]: Decide the problem-condition design and the stale threshold default in `mqtt` trigger mode (generous, configurable) during discuss-phase. Ensure the shipped debug config uses a distinct availability topic or no HA publishing.
- [Phase 3]: Production port 8001 must never be touched; verify everything on debug (:8002).

## Deferred Items

Items acknowledged and deferred at milestone close, most recent first:

| Category | Item | Status | Deferred At | Milestone |
|----------|------|--------|-------------|-----------|
| *(none)* | | | | |

## Session Continuity

Last session: 2026-10-23T20:03:44.084Z
Stopped at: Completed 01-05-PLAN.md
Resume file: None
