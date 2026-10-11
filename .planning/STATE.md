---
gsd_state_version: '1.0'
status: planning
progress:
  total_phases: 4
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-10-11)

**Core value:** The value Home Assistant shows is either correct and current, or visibly flagged as not. It is never silently wrong or frozen.
**Current focus:** Phase 1 - Auth Core and Test/Script Plumbing

## Current Position

Phase: 1 of 4 (Auth Core and Test/Script Plumbing)
Plan: 0 of TBD in current phase
Status: Ready to plan
Last activity: 2026-10-11 - Roadmap created (4 phases, 40/40 v1 requirements mapped)

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

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: 4 phases (auth core + test/script plumbing, hardening, HA visibility, dashboard banners). AUTH-15 lives in Phase 1 with the gate so tests, CI and scripts never go red.
- [Research]: Default-deny pure-ASGI auth gate, custom stdlib-`hmac` signed cookie, `hashlib.scrypt` password hash, secret and hash in `/data/auth.json` (not `config.yaml`).
- [Research]: CSRF via Origin check plus required custom header (no token). SSRF guard is check-then-fetch with redirects off; the DNS-rebinding gap is accepted and recorded in `docs/decisions.md`.
- [Research]: One pure `health.evaluate()` feeds the HA problem sensor, the dashboard banner and `/api/status`, so HA and the dashboard cannot disagree.

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

Last session: 2026-10-11
Stopped at: Roadmap created; ready to plan Phase 1
Resume file: None
