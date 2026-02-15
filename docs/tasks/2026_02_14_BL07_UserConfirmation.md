# BL-07: User Confirmation via Home Assistant

## Goal

Route uncertain meter readings through Home Assistant for user verification.
Publish confirmation requests via MQTT; subscribe for user responses (confirm / reject / correct).
Readings that trigger confirmation are held in a pending state until the user responds or a timeout fires.

## Architecture

### Publish side
In `process_reading()`, after a reading is accepted but has warnings or low confidence:
1. Check trigger conditions (configurable thresholds)
2. If triggered, publish a confirmation request to `watermeter/confirmation_request`
3. Hold the reading in `_pending_confirmation` (don't publish to HA yet)
4. Start a `threading.Timer` for auto-reject on timeout

### Subscribe side
Subscribe to `watermeter/confirmation_response` on MQTT connect.
Handle payloads:
- `confirm` -- accept the pending reading, publish to HA, update `previous_value`
- `reject` -- discard the reading, revert `previous_value`
- `correct:{value}` -- override with user-supplied value, publish corrected reading

### Config (`confirmation:` section in config.yaml)
- `enabled: false` (opt-in)
- `request_topic`, `response_topic`
- `timeout_minutes: 5`
- `min_warnings: 1`
- `min_low_confidence_positions: 2`
- `max_rate_jump_factor: 3.0`

### State
- `self._pending_confirmation: Optional[Dict]`
- `self._confirmation_timer: Optional[threading.Timer]`

### API
- `GET /api/confirmation/status` -- returns pending confirmation details

## Work Packages

- WP-1: Config + schema -- DONE
- WP-2: Core confirmation logic in watermeter_service.py -- DONE
- WP-3: API endpoint in routes/service.py -- DONE
- WP-4: Unit tests (36 tests) -- DONE
- WP-5: Verification (305/305 tests pass) -- DONE

## Progress Log

- 2026-02-15: Started implementation
- 2026-02-15: All work packages complete. 305/305 tests pass.

## Done Criteria

- [x] Confirmation flow works end-to-end in unit tests
- [x] Config defaults added to config.yaml
- [x] Schema added to config_utils.py
- [x] API endpoint returns pending confirmation
- [x] All existing tests still pass (305/305)
- [x] Disabled by default -- zero behavioral change unless opted in
