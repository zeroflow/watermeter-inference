# Watermeter Inference

## What This Is

A self-hosted AI water meter reader. A single Docker container fetches snapshots from an ESP32-CAM (AI-on-the-edge), reads the digits and dial arrows with OpenVINO models, checks the reading for plausibility, and publishes it to Home Assistant via MQTT. The same container hosts a dashboard, a labelling UI, and in-process model training. The repo is public and ships as `ghcr.io/zeroflow/watermeter-inference`, so other people run it on their own home networks.

## Core Value

The value Home Assistant shows is either correct and current, or visibly flagged as not. It is never silently wrong or frozen.

## Current Milestone: Reliability & Security Basics

Other people now run this image on LANs we can't assume are trusted, and the app is wide open: 53 unauthenticated write endpoints, the MQTT password returned in plaintext by `/api/config`, and a server-side URL fetch with no guard. On the reliability side, a stale or failing pipeline isn't visible to Home Assistant (no availability topic, `/health` always 200) or to a dashboard user once the server drops away.

**Goal:** secure by default for new and upgrading installs, and stale or broken readings are never silent in Home Assistant or the dashboard.

## Requirements

### Validated

<!-- Inferred from the existing codebase (see .planning/codebase/). -->

- ✓ Fetch camera snapshots (whole image or per-ROI) on MQTT trigger, cyclic loop, or `POST /api/trigger`. Existing.
- ✓ Fisheye/rotation correction, feature (AKAZE) or marker alignment that fails closed, ROI crop. Existing.
- ✓ Digit classification (11-class incl. `NAN`) and arrow reading (classifier, regressor, OpenCV, or calibrated mode). Existing.
- ✓ Carry-aware total, cross-arrow consistency, optional correction, plausibility (max change, reverse flow, leak), optional MQTT confirmation flow. Existing.
- ✓ Pipeline status OK → DEGRADED → FAILED → STALE, with an MQTT notification on STALE. Existing.
- ✓ MQTT publish to Home Assistant with discovery. Existing.
- ✓ Dashboard, ROI setup, config editor, labelling UI, calibration tab (Jinja2 + HTMX). Existing.
- ✓ In-process training (timm/PyTorch → ONNX → OpenVINO IR) and model hot-reload. Existing.
- ✓ Comment-preserving config via `config_utils.py` (ruamel.yaml) with a validation schema. Existing.
- ✓ Docker image with first-run config copy, PUID/PGID remap, pretrained model install. Existing.

### Active

**Security:**
- [ ] Single admin password protects the whole UI and API (session cookie)
- [ ] First-run setup page: until a password is set, every page redirects to setup (Grafana/Portainer style). Applies to upgrades too.
- [ ] CSRF protection for cookie-authenticated writes
- [ ] Security headers (X-Frame-Options / CSP frame-ancestors, nosniff, referrer policy) (BL-62)
- [ ] Secrets are write-only: `/api/config` and the config editor never return the MQTT password (BL-48)
- [ ] SSRF guard on the image-source URL fetch: http(s) only; block loopback, link-local, and metadata ranges (supersedes AD-02)
- [ ] Rate limiting on login (brute force) and on compute-heavy endpoints: training, synthetic generation, trigger (BL-53)
- [ ] `docs/decisions.md`: AD-01 and AD-02 superseded by the new decisions

**Reliability (visibility of bad/stale readings):**
- [ ] MQTT availability topic with Last Will, so HA marks entities unavailable when the service dies or disconnects
- [ ] HA-visible problem state (e.g. a binary_sensor via discovery) when the pipeline is degraded/failed/stale or the last good reading is too old
- [ ] Dashboard banner when the shown reading is stale or the pipeline is not OK
- [ ] Dashboard banner when the browser loses connection to the server (BL-54)

### Out of Scope

- Multi-user accounts / roles: one admin is enough for a home device.
- HTTPS/TLS termination in the app: leave it to a reverse proxy; document it instead.
- API tokens for machine clients: today only MQTT and the browser talk to the app; revisit if HTTP integrations appear.
- Auth delegated to a proxy (trusted header): not chosen; built-in password is the default.
- Reset/reading race and concurrent config-write locking: real but separate (see CONCERNS.md); next milestone.
- Docker resource limits (BL-58), restart persistence of rate history (BL-28), deeper `/health` (BL-41): deferred to the next reliability milestone.
- MQTT TLS/auth to the broker (BL-37): broker-side concern, deferred.
- Formal tagged release for this milestone: "done" means running on main/prod; release later.

## Context

- Brownfield: about 670 unit tests, CI runs ruff and `pytest tests/unit` on push. Codebase map in `.planning/codebase/`.
- The frontend is Jinja2 + HTMX polling + vanilla JS with no framework. Auth must work for full pages, HTMX fragments, and `fetch()` calls from the static JS (HTMX/JS calls should get 401 and redirect, not an HTML login page swapped into a fragment).
- `/health` is used by the compose healthcheck and the CI smoke test. It must stay unauthenticated.
- `oneshot` CLI mode, `debug_clean.sh` (:8003) and the integration-test fixture start fresh containers. First-run setup must not break them (they need a way to set the password non-interactively, e.g. an env var).
- MQTT credentials can already come from `MQTT_USERNAME`/`MQTT_PASSWORD` env vars, and secrets live in a gitignored `.env`.
- Existing pipeline status machine and `_notify_stale` (`watermeter/watermeter_service.py`) are the natural source for the HA problem state.

## Constraints

- **Production**: port 8001 / container `watermeter-dashboard-prod` must never be stopped, restarted, rebuilt, or exec'd into during development. Verify on debug (:8002).
- **Compatibility**: existing installs upgrade by pulling `:latest`/`:dev`; config and `/data` must keep working without manual edits beyond setting the password on first visit.
- **Public repo**: no real IPs, hostnames, credentials, or home paths in commits.
- **Tech stack**: stay within FastAPI/Starlette, Jinja2, and HTMX; add minimal dependencies (a password hash library is fine).
- **Config**: new config fields go through `config_utils.py` and its validation schema; the password hash should not live in the user-editable `config.yaml` (it would show in the config editor).
- **Git**: commit directly to `main` with the `claude: ` prefix; push to both `origin` and `gitea`.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Built-in single admin password (session cookie) | Other users run it on untrusted LANs; simplest model that is secure by default | — Pending |
| First-run setup page on upgrade, not a generated password or open mode | Secure by default without relying on users reading logs | — Pending |
| Supersede AD-01/AD-02 | Their premise (trusted LAN, single operator) no longer holds for a public image | — Pending |
| HA problem sensor + availability over richer `/health` for this milestone | User needs to notice in HA and the dashboard; Docker-level health is secondary | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-10-11 after initialization*
