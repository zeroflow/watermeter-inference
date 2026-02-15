# Backlog

Format: `BL-{id}` | status: `idea` → `done` → `in-progress` → `done`

## Data Quality

- **BL-01** `done` — **Label-candidate pruning**: deduplicate low-confidence images inline at save time
  - **Trigger**: in `save_low_confidence()`, before writing to disk, compare against existing images in the same ROI's input folder
  - **Scope**: per ROI position only (digit_1 vs digit_1, analog_2 vs analog_2)
  - **Method**: perceptual hash (e.g. average hash or dHash) — fast, works well for small grayscale crops
  - **Disposal**: silently skip saving if a near-duplicate already exists (no skipped folder)
  - **Motivation**: input folders grow to 200+ images, most near-identical — wastes significant labeling time
- **BL-02** `done` — **Ground-truth pruning**: remove near-duplicate images from ground truth to reduce dataset size and improve diversity
  - **Trigger**: UI button on the training page — shows preview of what would be removed, then confirm
  - **Scope**: per class folder (e.g. `digits/ground_truth/3/`, `arrows/ground_truth/4.5/`)
  - **Method**: perceptual hash (same as BL-01), cluster near-duplicates, keep the most distinct image from each cluster
  - **Min threshold**: never prune a class below the median class size — protects thin classes relative to the dataset distribution
  - **Disposal**: delete silently on confirm
  - **Current scale**: digits ~453 images (11 classes, 19–109 each), arrows ~4200 images (100 classes, 7–125 each)
- **BL-03** `done` — **Ground-truth rework**: benchmark-driven detection of mislabeled ground truth, bulk relabel
  - **Trigger**: UI button on training page (similar placement to BL-02 prune button) — "Find mislabeled"
  - **How it works**:
    1. Runs benchmark on current ground truth using the active model
    2. Collects per-image results: images where model prediction != folder label are suspects
    3. Shows gallery of suspect images with current label vs model prediction
    4. User confirms which to send back (select all / deselect individual)
    5. Selected images move from `ground_truth/{class}/` back to `input/`
  - **Pre-filled label**: encode original label in filename (e.g. `digit_1_20260214_label=3.jpg`) so the labeling UI can pre-fill it as suggestion
  - **Prerequisite**: requires a trained model to run benchmark — button disabled if no active model
  - **Depends on**: benchmark already computes predictions per image (training_manager.py:976-983), just needs to record image paths alongside results

## Algorithm / Inference

- **BL-04** `done` — **Value deduction from rules**: correct misread positions using temporal + spatial context
  - **Where**: new step between `calculate_total()` and `validate_plausibility()` in `process_reading()`
  - **Approach**: confidence-weighted correction
    - Low confidence → easy to override with contextual evidence
    - High confidence → needs strong disagreement from multiple signals to override
  - **Signals used**:
    1. **Previous value** — meter only goes up, so expected value ≥ previous
    2. **Expected rate** — average consumption from `rate_history` predicts approximate next value
    3. **Adjacent positions** — consistency check (e.g. digit at .5 → next position ≥ 5) narrows candidates
    4. **Model softmax** — use top-K predictions, not just argmax; if 2nd-best class fits context better, prefer it
  - **Transparency**: flag corrections as warnings on dashboard (e.g. "digit_3 corrected: 3→9 (prev=347, arrows~9, conf=0.42)")
  - **Safety**: never correct when all positions are high-confidence and consistent — only intervene when something doesn't add up
  - **API change**: add `predict_detailed()` to `Classifier` returning full softmax vector; keep `predict()` lean for normal use
  - **Depends on**: rate_history (already tracked)
- **BL-05** `done` — **Cross-arrow consistency**: use adjacent arrow readings to validate and correct each other
  - **Scope**: single-arrow precision first (per user), cross-validation is a later enhancement
  - **Core idea**: if arrow1 reads 1.x and arrow2 reads 6.2, they should be consistent — arrow1's sub-integer can be narrowed
  - **Cave**: parallax error skews images, especially on outer dial positions — may need tolerance
  - **Depends on**: BL-04 (rule engine) provides the framework for cross-position correction
  - **Deferred**: multi-model ensemble (running 1.0 + 0.5 + 0.1 step models) — too expensive for now
- **BL-08** `done` — **Arrow regression mode**: train arrows as regression (single 0.0–1.0 output) instead of classification
  - **Motivation**: reference project (AI-on-the-edge) uses regression; avoids class boundary issues; continuous output
  - **Implementation**: add "continuous" mode alongside current "discrete" mode in training config
    - Training: MSE/Huber loss, single output neuron, sigmoid → 0.0–1.0; ground truth normalized by dividing folder label by 10
    - Inference: multiply sigmoid output by 10 → dial position 0.0–9.9
    - New `Regressor` class in inference.py (similar to `Classifier` but no softmax/argmax)
  - **UI**: toggle between discrete/continuous in training form; model filename: `model_arrows_{arch}_continuous_r{res}`
  - **Compare**: benchmark both modes on same ground truth to see which is more accurate
  - **Priority**: implement after BL-05 since it's a bigger change to the training pipeline
- **BL-06** `done` — **Continuous consumption warning**: detect sustained high water usage (leak detection)
  - **What it detects**: consumption rate stays elevated over a prolonged period — likely a running toilet, dripping pipe, or open valve
  - **How**: check `rate_history` — if average rate exceeds a threshold for N consecutive readings, trigger warning
  - **Output**: both dashboard banner + MQTT attribute
    - Dashboard: warning banner with current sustained rate and duration
    - MQTT: add `leak_warning` attribute to published payload so HA automations can fire notifications (e.g. night-time alerts)
  - **Config**: thresholds in `config.yaml` under `plausibility` section
    - `sustained_rate_threshold: 0.05` — m³/h above which consumption is considered "high"
    - `sustained_rate_readings: 3` — number of consecutive readings above threshold before warning
  - **Existing infra**: `rate_history` already tracks (value, timestamp) tuples; `_calculate_average_rate_per_hour()` exists

## User Interaction

- **BL-07** `done` — **User confirmation via Home Assistant**: route uncertain readings through HA for user verification and response
  - **Not Telegram-native** — use MQTT to publish actionable events, let HA handle notification channel (Telegram, push, email, etc.)
  - **One-way (publish)**:
    - New MQTT topic `watermeter/confirmation_request` with payload: value, confidence, image (base64 or URL), reason for doubt
    - HA automation picks this up → sends notification via user's preferred channel
  - **Two-way (subscribe)**:
    - New MQTT topic `watermeter/confirmation_response` — user replies via HA (e.g. inline Telegram buttons)
    - Payloads: `confirm` (accept the reading), `reject` (discard it), `correct:{value}` (override with manual value)
    - Watermeter subscribes and processes the response: update `previous_value`, publish corrected reading
  - **Trigger conditions**: reading accepted but with warnings, low confidence across multiple positions, large jump from previous
  - **Timeout**: if no response within N minutes, auto-reject (don't publish uncertain readings; wait for next cycle)
  - **Depends on**: existing MQTT infra (already has publish + subscribe); BL-06 stale detection could also trigger confirmation requests

- **BL-14** `done` — **Show rejected reading on dashboard**: when the last cycle was rejected (plausibility check), show both the last published value and the rejected reading so the user can compare
  - **Display**: only when they differ — if last reading was accepted, show only the current value as before
  - **What to show**: "Last sent: 34.6808 m³ (15:32)" vs "Last read: 34.8961 m³ (15:37) — rejected: Change per reading too high"
  - **Where**: below or next to the current meter reading card on the dashboard
  - **Data source**: `current_state` already has `total_value`, `previous_value`, `status`, `errors` — may need to also store `last_published_value` + `last_published_timestamp` separately
  - **MQTT**: no changes needed, this is display-only
- **BL-15** `done` — **Manual meter reading input**: replace the Reset button with a manual input field that sets the current meter value, stores it as `previous_value`, and publishes to HA via MQTT
  - **UI**: text input field + "Set" button (replaces the current "Reset" button)
  - **Behavior on submit**:
    1. Validate input is a valid positive number
    2. Update `previous_value` to the entered value
    3. Publish the value to MQTT/HA as a normal reading
    4. Reset rejection counter and error state
    5. Show confirmation on dashboard
  - **Use case**: meter was misread for several cycles, user reads the physical meter and enters the correct value to re-sync
  - **Safety**: confirmation dialog before applying ("Set meter to X.XXXX m³?")
  - **API**: `POST /api/set-value` with `{"value": 34.8961}`

## Internationalization

- **BL-11** `done` — **Translate UI to English**: replace remaining German text in templates and backend with English
  - **Examples**: "Aktueller Zählerstand", button labels, status messages, tooltips, section headers
  - **Scope**: all templates (`templates/*.html`), flash messages and error strings in `app.py`, any hardcoded German in JS, MQTT topic names, config keys
  - **Not in scope**: comments in code (can stay mixed), log messages (already English)
  - **Breaking changes OK**: app is unreleased, so MQTT topics and config keys can change freely
  - **Terminology**: "Zählerstand" → "Meter Reading", hardcoded English (no i18n framework)
  - **Approach**: systematic sweep — grep for common German patterns (umlauts, known German words), replace with English equivalents
  - **Assigned**: `frontend` (templates/CSS) + `junior-dev` (backend strings + config keys + MQTT topics)

## UI / Frontend

- **BL-12** `done` — **Fix console errors**: resolve the 3 browser console errors on the dashboard
  - `/api/status` returns 500 Internal Server Error → find root cause and fix
  - `dashboard.js:93` JSON parse error (consequence of the 500 above)
  - `/favicon.ico` 404 → add a favicon
- **BL-13** `done` — **UI beautification**: visual polish across all pages, focus on dashboard
  - **Primary**: the status/error badge on the dashboard looks ugly → restyle
  - **Scope**: all pages (dashboard, label, ROI config, config editor, training) — light touch, no redesign
  - **Keep**: existing color scheme (CSS variables), overall layout
  - **Approach**: send `frontend` agent with Playwright to screenshot each page, identify rough spots, propose improvements

## Release Preparation

- **BL-17** `done` — **Secrets scan**: audit git history for leaked credentials
  - Scan for: API keys, tokens (HF_TOKEN), MQTT passwords, IP addresses, private URLs
  - Tools: `git log -p | grep -i`, trufflehog, or manual grep
  - If found: clean with BFG Repo Cleaner + force push
- **BL-18** `done` — **Add LICENSE file**: choose and add an open source license
  - License: **AGPL-3.0** (user preference)
  - Add `LICENSE` file to repo root
  - Add license header reference in README
- **BL-19** `done` — **README**: create a proper README.md for the project
  - Sections: project description, screenshot, features, setup (Docker Compose), configuration, API overview, development, license
  - Include dashboard screenshot
  - Example config (`config.example.yaml`) without real credentials
  - Link to upstream project (jomjol/AI-on-the-edge-device) for context
- **BL-20** `done` — **.gitignore audit**: ensure all generated/private files are excluded
  - Check: model binaries (`*.bin`), `data_debug/`, `config_debug/`, `models_debug/`, `.env`, `__pycache__/`, `*.pyc`, `.playwright-mcp/`
  - Verify no large binaries are tracked in git history
- **BL-21** `done` — **Docker Compose for end users**: single `docker-compose.yml` for easy deployment
  - Services: watermeter app, optional MQTT broker (mosquitto)
  - Volume mounts for config, models, training data
  - Environment variables for HF_TOKEN, MQTT credentials
  - `.env.example` template

## Code Quality

- **BL-22** `done` — **OpenAPI / Swagger cleanup**: improve auto-generated API docs
  - Add descriptions and tags to all FastAPI endpoints
  - Group endpoints by category (status, training, models, config, etc.)
  - Verify `/docs` is accessible and useful
  - Add request/response examples where helpful
- **BL-23** `done` — **Linting & formatting**: enforce consistent code style
  - Run ruff + black across entire codebase
  - Fix all findings
  - Add `pyproject.toml` config for ruff/black settings
  - Single pass — no pre-commit hook yet (that's CI's job)
- **BL-24** `done` — **GitHub Actions CI**: automated testing on push
  - Workflow: pytest (unit tests only, no Docker/integration)
  - Workflow: ruff lint check
  - Trigger: push to main, PRs
  - Prepare for both GitHub Actions and Gitea Actions
  - Badge in README

## Tech Debt

- **BL-16** `done` — **Test suite audit**: analyze the full test suite for unnecessary, incorrect, or broken tests
  - **Scope**: all tests in `tests/` (unit, integration, etc.)
  - **Find**: tests that are redundant, test wrong behavior, always pass trivially, mock too much to be useful, or are flaky/broken
  - **Output**: report with recommendations (delete, fix, rewrite) per finding
  - **Not in scope**: writing new tests — this is analysis only
- **BL-09** `done` — **Fix Pydantic `schema` field shadow**: `SetupRequest` in `inference.py:267` uses field name `schema` which shadows `BaseModel.schema()` → renamed to `label_schema` with `alias="schema"` for wire compatibility

## Legal / Licensing

- **BL-10** `done` — **Handle upstream data license**: training data from jomjol's repos has no explicit license ([issue #4041](https://github.com/jomjol/AI-on-the-edge-device/issues/4041))
  - **Solution**: ship our own data, add a script to pull his — models are trained locally by the user, no derivative works distributed
  - **What ships**: our self-collected meter photos + ground truth, pre-trained models from our data only
  - **Script**: `scripts/fetch_upstream_data.sh` — downloads jomjol's training data into the right directory structure on the user's machine
  - **Action items**:
    1. Separate our ground truth from upstream-derived data (document provenance)
    2. Write fetch script that pulls from jomjol's repos (digits + analog needles)
    3. Track upstream issue #4041 — if license is clarified, can simplify
