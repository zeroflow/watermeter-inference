# Persona-Based Project Evaluation -- Synthesis

**Date:** 2026-02-22
**Method:** 10 simulated personas evaluated the project from their unique perspectives
**Scope:** README, code, UI, docs, setup experience, feature completeness

---

## Executive Summary

- **Personas who would succeed** (setup rating >= 3): **8 / 10**
- **Personas who would give up** (setup rating < 3): **2 / 10** (HA Hobbyist, Non-Technical Homeowner)
- **Average setup rating:** 2.8 / 5
- **Top 3 cross-cutting themes:**
  1. **No authentication on any endpoint** -- flagged independently by 4 personas as a blocker or critical concern. Any device on the LAN can read credentials, delete models, and start training jobs.
  2. **Hardware requirements are undocumented** -- 6 personas could not determine what camera, compute platform, or prerequisite hardware is needed before investing setup time.
  3. **Onboarding assumes expert knowledge** -- the README speaks to developers who already understand OpenVINO, MQTT, Docker, and the upstream AI-on-the-edge project. The two personas representing the target audience (HA hobbyist, homeowner) both scored lowest.

The project is technically excellent -- every persona acknowledged the code quality, test coverage, and inference architecture. The gap is entirely in communication: telling users what they need, what to expect, and how to get started safely.

---

## Action Items (prioritized)

### Must-Fix (blocks adoption)

| # | Issue | Flagged by | Impact |
|---|-------|-----------|--------|
| 1 | **No authentication on HTTP API** -- all write endpoints (config save, model delete, training start, set-value) accept unauthenticated requests from any LAN host | Priya, Dave, Marcus, Sam | Anyone on the network can modify config, read MQTT credentials in plaintext via `/api/config`, delete models, or trigger CPU-intensive training. Blocks any deployment on a shared or semi-trusted network. |
| 2 | **`git clone <repository-url>` is a literal placeholder** in the README Quick Start | Klaus, Priya, Sam, Linda | New users cannot clone the project. Four personas independently flagged this as their first point of failure. |
| 3 | **No hardware requirements section** -- never states that an AI-on-the-edge ESP32-CAM device (or compatible IP camera) is required, or that the stack is Intel x86-64 only | Klaus, Linda, Yuki, Priya, Maya, Alex | Two personas would spend hours debugging before discovering the hardware dependency. Edge engineer notes the base image won't even pull on ARM. |
| 4 | **No offline/demo mode** -- setup requires a live camera feed; no way to evaluate, develop, or test against static images without hand-editing config | Priya, Sam, Alex, Jordan | Contributors cannot reproduce bugs, evaluators cannot assess the product, and the ROI wizard is unusable without live hardware. |
| 5 | **`/health` endpoint always returns 200 OK** regardless of MQTT state, model availability, or inference failures | Marcus, Dave | Orchestrators (Docker, Kubernetes, Home Assistant) will report the service as healthy when it is producing no readings. Silent failure in a monitoring tool is worse than a visible crash. |
| 6 | **Regression confidence heuristic is semantically incorrect** -- `abs(sigmoid - 0.5) * 2.0` conflates output magnitude with uncertainty; position 5.0 always reports low confidence | Alex, Yuki | Affects every regression prediction. A reading of exactly 5.0 on any dial is flagged as uncertain regardless of actual model certainty. Correctness bug in the core inference path. |

### Should-Fix (improves experience)

| # | Issue | Flagged by | Impact |
|---|-------|-----------|--------|
| 7 | **No CONTRIBUTING.md or PR process documentation** | Sam, Priya | External contributors have no guidance on code style, test expectations, or review process. The backlog is rich but the on-ramp is missing. |
| 8 | **No rate limiting on compute-heavy endpoints** -- `/api/training/start`, `/api/synthetic/generate`, `/api/trigger` can be called without throttle | Dave, Priya | A single curl loop can saturate CPU and OOM the host. Training and inference share one container with no resource limits. |
| 9 | **HTMX silent failure -- no error/reconnect banner** | Maya, Marcus | If the server becomes unreachable, the dashboard stops updating with no visible indication. Users see stale data and assume the system is working. |
| 10 | **German-language strings in production JavaScript** | Maya | `'Auto-refresh aktiviert'` in console, `'Fehler beim Einreichen'` shown to users on error. Breaks the English UI for non-German speakers. |
| 11 | **Monaco editor loaded from CDN** -- fails silently in LAN-only deployments | Maya, Klaus | The config editor is a key feature; it breaks entirely without internet access, which is the expected deployment environment. |
| 12 | **Mislabel scan is synchronous and unbounded** -- blocks the API thread on large datasets | Jordan, Alex | Scales poorly; a dataset with thousands of images will timeout or hang the UI with no progress feedback. |
| 13 | **No Docker resource limits** -- training and inference share one container with no memory ceiling | Marcus, Dave | A training job can OOM the host and take down the inference service. Resource limits are commented out in compose. |
| 14 | **`debug.sh` has hardcoded `/home/thomas/.cache/huggingface`** path | Marcus, Sam | Any developer who is not Thomas gets a silent bind-mount failure, re-downloading model weights on every container start. |
| 15 | **Rename `aiote` to `camera` throughout config and UI** | Klaus, Priya, Linda, Maya | "aiote" is an abbreviation of the upstream project name that no user recognizes. Four personas were confused by it. |
| 16 | **No `HEALTHCHECK` in the Dockerfile** | Marcus | Only compose deployments get health checks. Raw `docker run`, Kubernetes, and other orchestrators see no health metadata. |
| 17 | **No CORS policy or security headers** (X-Frame-Options, CSP) | Dave | Clickjacking via iframe is possible. No Content-Security-Policy means injected scripts would execute. |
| 18 | **Training pipeline lacks learning rate warmup and early stopping** | Alex | Pretrained weights receive full learning rate from epoch 1, risking feature extractor destabilization. No mechanism to stop when validation plateaus. |
| 19 | **No log rotation in docker-compose** | Marcus | Long-running instances accumulate unbounded stdout logs. `json-file` driver with `max-size`/`max-file` should be the default. |

### Nice-to-Have (delights users)

| # | Issue | Flagged by | Impact |
|---|-------|-----------|--------|
| 20 | **INT8 quantization export path** | Yuki | 2-4x inference speedup on CPU, mandatory for peak iGPU throughput. OpenVINO supports it natively. |
| 21 | **Class balance visualization and confidence histograms per class** | Jordan | Guides labeling effort and reveals which classes need more data. Currently requires manual API calls. |
| 22 | **Per-class accuracy breakdown / confusion matrix in benchmarks** | Alex, Jordan | Aggregate accuracy hides systematic failures (e.g., "1" vs "7" confusion). |
| 23 | **ONNX Runtime backend for ARM deployments** | Yuki, Priya | ONNX files are already exported but unused. Would unlock Raspberry Pi, Jetson, and non-Intel platforms. |
| 24 | **Radial dial selector for arrow labeling** | Jordan | Typing "6.3" is error-prone for a 100-class problem. A visual click-to-set widget would reduce labeling errors. |
| 25 | **Multi-meter / fleet management support** | Priya | Currently single-meter only. Multiple meters require separate Docker containers with no aggregation view. |
| 26 | **Auto-train trigger when label count crosses a threshold** | Jordan | The active learning loop is manual at every step. An automatic trigger would close the feedback loop. |
| 27 | **Webhook / Prometheus metrics endpoint** | Priya | The only outbound channel is MQTT to Home Assistant. Non-HA users have no observability path beyond polling. |
| 28 | **Accessibility improvements** -- skip-to-content link, focus-visible styles, accessible ROI canvas, non-color-only confidence indicators | Maya | Multiple WCAG failures (2.4.1, 1.4.1, 4.1.3). Affects keyboard and screen reader users. |
| 29 | **Mobile-friendly dashboard** | Klaus, Linda | Homeowners checking the meter on their phone is a primary use case. |
| 30 | **Data versioning / soft-delete for prune and mislabel operations** | Jordan | Destructive operations are permanent with no recovery path. |
| 31 | **Ship a working `mosquitto.conf` template** | Klaus | The mosquitto service is in compose but the config file must be created manually with no example provided. |

---

## Per-Persona Highlights

### Klaus -- Home Assistant Hobbyist (Setup: 2/5)
- **Verdict:** Would use it, but only with a friend helping set up. Would recommend on r/homeassistant with a caveat.
- **#1 fix:** "Replace the Configuration section with a Getting Started guide that answers: what camera do I need, how do I point it at my meter, and how do I see it in Home Assistant."
- **Unique insight:** The README never states "you DON'T need to train models -- pre-trained models are included." That single sentence would calm the majority of hobbyist fears.

### Priya -- IoT Startup Developer (Setup: 3/5)
- **Verdict:** Would not deploy in a commercial product (AGPL-3.0 license blocker), but would evaluate seriously for a dual-license arrangement. Best-engineered option in its class.
- **#1 fix:** "Add HTTP Basic Auth to all write endpoints before recommending this to anyone on a shared network."
- **Unique insight:** The API response envelope (`{"success": bool}`) is inconsistently applied -- some endpoints return raw dicts, `/api/trigger` returns an HTML fragment. No API versioning means integrations break silently on upgrade.

### Alex -- ML Engineer (Setup: 3/5)
- **Verdict:** Conditionally yes -- best open-source option for meter reading on Intel edge hardware. Caveats on regression confidence and training pipeline gaps.
- **#1 fix:** "Replace the sigmoid-distance confidence heuristic with a statistically grounded uncertainty estimate."
- **Unique insight:** Validation set leakage -- the stratified split is re-drawn when synthetic data is added between runs, meaning the same real images may shift between train and val sets. The split should be fixed by filename hash.

### Marcus -- DevOps Engineer (Setup: 4/5)
- **Verdict:** Yes, for the homelab audience. Production-ready in the sense that it won't corrupt itself on a crash.
- **#1 fix:** "Add resource limits to docker-compose and a HEALTHCHECK to the Dockerfile."
- **Unique insight:** The `_cyclic_loop` in `scheduling.py` catches `CancelledError` but not arbitrary exceptions from `process_reading()`. If the processing function raises, the entire polling loop dies silently -- no log, no restart, no alert.

### Sam -- Open Source Contributor (Setup: 3/5)
- **Verdict:** Would use it, would contribute reluctantly. The code quality and backlog are excellent, but the project signals "internal tool that happens to be public" rather than "welcoming external contributors."
- **#1 fix:** "Add a CONTRIBUTING.md and clean the root directory of screenshot noise and internal planning documents."
- **Unique insight:** `CLAUDE.md` in the project root is an AI agent orchestration spec that is disorienting for human contributors. The boundary between human and AI contribution workflows is unclear.

### Yuki -- Embedded/Edge Engineer (Setup: 3/5, or 1/5 on non-Intel)
- **Verdict:** Yes on Intel mini-PCs. Non-starter for the broader embedded world (Pi, Jetson, Coral) without an ONNX Runtime backend.
- **#1 fix:** "Add a hardware requirements section and an INT8 quantization export path."
- **Unique insight:** `cv2.matchTemplate` for template-based alignment is a latency wildcard on slow CPUs -- no documentation of expected overhead. Could dominate inference time on low-power hardware.

### Linda -- Non-Technical Homeowner (Setup: 1/5)
- **Verdict:** Barely, and only if someone sat next to her. The app once running seems genuinely useful, but the setup is a developer-only obstacle course.
- **#1 fix:** "Add a prominent 'What you need before you start' section and replace the bash Quick Start with a Docker Desktop GUI walkthrough."
- **Unique insight:** "ROI" means "Return on Investment" to non-technical users. A water meter app with a "ROI" tab is confusing. The nav terminology assumes domain knowledge that homeowners do not have.

### Dave -- Security Auditor (Setup: 3/5)
- **Verdict:** Yes for home LAN with explicit warnings. No for any deployment with untrusted network access. An attacker who reaches port 8001 gets full control.
- **#1 fix:** "Add a startup warning banner stating the dashboard is unauthenticated and bound to all interfaces."
- **Unique insight:** `GET /api/config` returns the full raw YAML including plaintext MQTT credentials. Combined with `0.0.0.0` default binding and no auth, credentials are readable by any host that can reach the port.

### Maya -- UX/Frontend Developer (Setup: 3/5)
- **Verdict:** Yes, with enthusiasm and caveats. The dashboard is far above typical hobbyist home-automation UI. Dark mode, navigation, and the ROI wizard show genuine frontend craft.
- **#1 fix:** "Add an `htmx:sendError` handler that shows a 'Dashboard offline -- retrying...' banner."
- **Unique insight:** The labeling interface has excellent keyboard ergonomics (`Enter` to confirm, `D` to delete, autofocus, nav-bar hide on mobile keyboard), but the instructions block is hidden at 768px -- mobile users labeling arrow dials lose the 0.00-0.99 scale reference entirely.

### Jordan -- Data Scientist (Setup: 3/5)
- **Verdict:** Yes, with caveats. The capture-deduplicate-label-prune-mislabel pipeline is more complete than most hobby ML projects. Stops just short of a closed active learning loop.
- **#1 fix:** "Make the mislabel scan async with a job queue -- running inference on thousands of images synchronously is a reliability problem at scale."
- **Unique insight:** The `synth_` filename prefix cleanly separates synthetic from real data, but the stats API counts all images together. A training run silently dominated by synthetic data could perform poorly on real images with no warning surfaced to the user.

---

## Cross-Cutting Analysis

### Documentation Gaps

Six personas independently noted the absence of hardware requirements documentation. The project never clearly states:

- What camera hardware is required (AI-on-the-edge device, IP camera, or USB camera)
- That the stack is Intel x86-64 only (ARM containers do not exist)
- What "aiote" means or its relationship to the upstream jomjol project
- That pre-trained models ship with the container (no training required to start)
- What minimum CPU/RAM is needed for acceptable inference latency

Additional documentation gaps flagged by multiple personas: no CONTRIBUTING.md (Sam, Priya), no SECURITY.md or hardening guide (Dave, Marcus), and no architecture selection guide for the 16 available model architectures (Alex, Yuki).

### Onboarding Friction

The two lowest-rated personas (Klaus at 2/5, Linda at 1/5) represent the project's stated target audience: Home Assistant users and homeowners. Their failure points cluster at three stages:

1. **Before cloning** -- placeholder URL, no hardware prerequisites, jargon-heavy first paragraph
2. **After container start** -- "No Models Loaded" with no guidance, MQTT disconnected warnings, `aiote` config section with no explanation
3. **Camera configuration** -- ROI wizard requires a live feed, no offline fallback, fisheye/barrel terminology unexplained

The technically capable personas (Marcus at 4/5, Priya/Alex/Sam at 3/5) all succeeded but noted the same pattern: the happy path works, but any deviation from it (no camera, non-Intel hardware, Windows host) has no recovery documentation.

### Feature Priorities

Features requested by 3 or more personas, ranked by frequency:

1. **Authentication** (4 personas) -- even HTTP Basic Auth would be sufficient
2. **Hardware requirements documentation** (6 personas) -- the cheapest fix with the widest impact
3. **Offline/demo mode** (4 personas) -- static test image support for evaluation and development
4. **ARM / non-Intel support** (3 personas) -- ONNX Runtime backend using existing exported files
5. **Inference benchmarks and latency documentation** (3 personas) -- no baseline numbers exist anywhere

### Security and Operations

Dave's audit found the threat model is documented (AD-01, AD-02 in `docs/decisions.md`) but not communicated to users. The key operational risks:

- **Credential exposure**: `/api/config` serves plaintext MQTT credentials to any requester
- **Unauthenticated mutation**: config overwrite, model deletion, training initiation all require zero credentials
- **No rate limiting**: compute-heavy endpoints can be called in unbounded loops
- **Silent monitoring failure**: `/health` returns 200 even when the service is non-functional
- **No HTTPS**: credentials travel in cleartext on the LAN

Marcus's operational concerns compound with Dave's: no resource limits on the shared training/inference container, no log rotation, and a cyclic polling loop that dies silently on unhandled exceptions.

The combination of these issues means a long-running deployment is likely to either (a) accumulate unbounded logs, (b) OOM during training, or (c) silently stop producing readings after an exception -- with the health check reporting "ok" throughout.

---

*This evaluation was conducted by simulating 10 distinct user personas, each evaluating the project independently from their professional perspective. Action items were deduplicated and prioritized by cross-persona frequency and severity.*
