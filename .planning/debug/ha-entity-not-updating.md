---
status: resolved
trigger: "warum Updatet die entitiy in Home-Assistant seit ein paar Tagen nicht mehr?"
created: 2026-10-26
updated: 2026-10-26
---

# Debug: HA entity not updating

## Symptoms

- expected: `sensor.diy_ai_water_meter_water_usage_2` follows the meter
- actual: stuck at 57.3685; status `rejected`, 1273 consecutive rejections; no MQTT updates since 2026-10-24 06:34 UTC
- trigger mode: cyclic (internal)
- user believed prod runs on this host (claude-env LXC)

## Current Focus

hypothesis: two stacked causes (see Resolution)
next_action: user decision on remediation (restart + baseline reset, forward re-anchor design)

## Evidence

- timestamp: 2026-10-26 — `docker ps -a`: no `watermeter-dashboard-prod` on this host, no prod data dirs, nothing answering :8001 in the /24
- timestamp: 2026-10-26 — `watermeter-dashboard-debug` (restart policy `no`, binds `config_debug`/`data_debug`) publishes as "DIY AI Water Meter" to the real broker; it is the de-facto production instance
- timestamp: 2026-10-26 — `last reboot`: PVE node rebooted 2026-10-24 06:38 UTC; debug container FinishedAt 06:34:44 UTC; other containers (`unless-stopped`) came back, debug did not
- timestamp: 2026-10-26 — HA history: raw values stop at 2026-10-24 06:34 UTC, afterwards only retained values re-appear on HA restarts
- timestamp: 2026-10-26 — `data_debug/state.json`: previous_value 57.3685, last_update_time 2026-10-11T04:25 (last accepted reading)
- timestamp: 2026-10-26 — HA raw readings 2026-10-24: ~58.52 m³, consistently; last_rejected_reason "Change per reading too high: 1.1538 m³ (max: 0.15)"
- timestamp: 2026-10-26 — container was recreated 2026-10-23 08:46 UTC (phase 01 auth testing); 1273 rejections ≈ one per minute since then
- timestamp: 2026-10-26 — `plausibility.py` evaluate: `max_rate_per_reading` rejects any forward jump > 0.15 regardless of elapsed time; `ReanchorTracker` only runs on reverse readings, so a forward gap never recovers on its own
- timestamp: 2026-10-26 — raw values also flip between ~58.51 and ~58.61 (0.1-dial jitter), which would also defeat a 0.01-spread re-anchor run

## Eliminated

- hypothesis: auth gate (phase 01) blocks the pipeline — cyclic trigger and MQTT publishing are not HTTP; readings were produced, then rejected by plausibility

## Resolution

root_cause: |
  1. Since 2026-10-24 06:38 UTC nothing is published: the instance feeding HA is the debug container, started without a restart policy; it did not come back after the PVE reboot.
  2. Since 2026-10-11 the value was already frozen: ~1.15 m³ were used while the service was down (2026-10-11 → 2026-10-23, ~96 L/day). After the restart, every reading exceeds `max_rate_per_reading` (0.15 m³), and forward jumps have no re-anchor path, so it was rejected forever.
fix: |
  Operational (2026-10-26): `docker update --restart unless-stopped watermeter-dashboard-debug`, started it, sampled 5 readings (59.306 → 59.316, consistent), then `POST /api/reset`; HA shows 59.3163 again.
  Code: `PlausibilityChecker` re-anchors forward too. After `reanchor_after` consecutive readings above `max_rate_per_reading` that agree with each other (steps ≤ `max_rate_per_reading`), and only when the gap fits `max_rate_per_hour` × hours since the last accepted reading, it adopts the run minimum as baseline.
verification: unit + regression suite 1288 passed; new tests fail without the fix; ruff clean
files_changed: [watermeter/plausibility.py, watermeter/config_utils.py, config.yaml, tests/unit/test_plausibility.py, tests/unit/test_service_reading_recovery.py]
open:
  - the instance feeding HA is the debug container (`debug.sh` rebuilds it, so the restart policy is lost on the next rebuild); a real prod deployment via docker-compose is missing on this host
  - HA template helper `sensor.watermeter_total` reads the nonexistent `sensor.watermeter_value` (dead since early October)
  - the new code is not yet deployed to the running container
