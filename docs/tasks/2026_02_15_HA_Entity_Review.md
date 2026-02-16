# HA Entity Review: Comprehensive MQTT Integration Audit

**Created:** 2026-02-15
**Status:** complete
**Purpose:** Trace every HA entity from discovery to state publication, flag issues

---

## 1. Entity Registry

All entities are defined in `_HA_ENTITIES` (module-level list) at **`watermeter_service.py` L48-232**.

There are **21 entities** total: 14 sensors + 3 binary_sensors + 4 training stats sensors.

---

## 2. Entity-by-Entity Analysis

### MQTT Topics

| Topic | Purpose | Published by |
|-------|---------|-------------|
| `homeassistant/sensor/watermeter/state` | Main state (JSON) | `publish_to_mqtt()` L1867, `set_manual_value()` L1965 |
| `homeassistant/sensor/watermeter/state/training_stats` | Training stats (JSON) | `publish_training_stats()` L2054 |
| `homeassistant/{component}/watermeter_ai/{object_id}/config` | Discovery (per entity) | `publish_discovery()` L2127 |

### Discovery Message Structure (L2155-2185)

For every entity, discovery publishes:
```json
{
  "name": "...",
  "unique_id": "watermeter_ai_{object_id}",
  "state_topic": "{main_state_topic or training_stats_topic}",
  "value_template": "{{ value_json.{object_id} }}",
  "device": { "identifiers": ["watermeter_ai"], ... },
  // + optional: device_class, state_class, unit_of_measurement, icon, entity_category, options
  // binary_sensor adds: payload_on: true, payload_off: false
}
```

**Key design principle:** The `value_template` uses `{{ value_json.{object_id} }}` where `{object_id}` matches the JSON key in the published payload. This means the payload key names MUST match the `object_id` fields exactly.

---

### Entity #1: Water Usage

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `water_usage` / `watermeter_ai_water_usage` |
| device_class | `water` |
| state_class | `total_increasing` |
| unit | `m³` |
| icon | `mdi:water` |
| state_topic | `homeassistant/sensor/watermeter/state` |
| value_template | `{{ value_json.water_usage }}` |
| Payload key | `"water_usage": round(value, 4)` (L1917) |
| Published in | `publish_to_mqtt()` L1917, `set_manual_value()` L2019 |
| **Status** | **OK** -- payload key matches object_id |

---

### Entity #2: Water Usage Raw

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `water_usage_raw` / `watermeter_ai_water_usage_raw` |
| device_class | `water` |
| state_class | `total_increasing` |
| unit | `m³` |
| icon | `mdi:water-outline` |
| state_topic | `homeassistant/sensor/watermeter/state` |
| value_template | `{{ value_json.water_usage_raw }}` |
| Payload key | `"water_usage_raw": round(raw_value, 6) if raw_value is not None else round(value, 4)` (L1918) |
| Published in | `publish_to_mqtt()` L1918, `set_manual_value()` L2020 |

#### Raw Value Flow Analysis

The `raw_value` parameter flows through these paths:

**Path 1: Normal reading (process_reading L1783-1789)**
```python
raw_total = self._compute_raw_total(raw_values)
await self.publish_to_mqtt(total_value, ..., raw_value=raw_total)
```
- `raw_values` comes from `calculate_total()` L680 which returns `{"digits": [...], "arrows": [...]}`
- `_compute_raw_total()` L1852-1865 uses continuous arrow values (not floored) for higher precision
- This path provides a valid `raw_value`

**Path 2: Confirmation confirm (L1107-1121)**
```python
raw_total = self._compute_raw_total(pending["raw_values"]) if pending.get("raw_values") else None
await self.publish_to_mqtt(pending["value"], ..., raw_value=raw_total)
```
- `pending["raw_values"]` is stored at L1767 in `process_reading()`
- This path also provides a valid `raw_value`

**Path 3: Confirmation correct (L1141-1177)**
```python
await self.publish_to_mqtt(corrected_value, ..., raw_value=corrected_value)
```
- Uses the corrected value as raw value (makes sense since user overrode it)

**Path 4: Manual set_manual_value (L1965-2046)**
```python
payload = {
    "water_usage_raw": round(value, 4),
    ...
}
```
- Inline payload construction; raw = rounded value (correct for manual set)

**Path 5: Reading rejected (L1790-1815)**
- **NO publish_to_mqtt() call.** Rejected readings are never published to MQTT.
- This is correct behavior -- HA should not see rejected values.

| **Status** | **OK** -- raw_value is always provided. The fallback `round(value, 4)` in L1918 handles the edge case. |

#### BUT: Potential Timing Issue with Raw Value

Looking more carefully at `publish_to_mqtt()` signature (L1867-1875):
```python
async def publish_to_mqtt(self, value, warnings, predictions, *,
                          leak_warning=False, raw_value=None):
```

The `raw_value` defaults to `None`. In `publish_to_mqtt()` L1918:
```python
"water_usage_raw": round(raw_value, 6) if raw_value is not None else round(value, 4),
```

This means if `raw_value=None` is passed, the raw entity just mirrors the rounded value. This is a fallback, not a bug.

| **Status** | **OK** -- all publish paths provide raw_value or have a sensible fallback |

---

### Entity #3: Leak Warning

| Field | Value |
|-------|-------|
| Type | `binary_sensor` |
| object_id / unique_id | `leak_warning` / `watermeter_ai_leak_warning` |
| device_class | `moisture` |
| icon | `mdi:water-alert` |
| state_topic | `homeassistant/sensor/watermeter/state` |
| value_template | `{{ value_json.leak_warning }}` |
| payload_on | `True` (Python bool, serialized as JSON `true`) |
| payload_off | `False` (Python bool, serialized as JSON `false`) |
| Payload key | `"leak_warning": leak_warning` (L1919, bool) |

#### ISSUE: Binary sensor value_template vs payload_on/payload_off mismatch

The discovery message sets:
```json
{
  "payload_on": true,
  "payload_off": false,
  "value_template": "{{ value_json.leak_warning }}"
}
```

In HA MQTT binary_sensor, the flow is:
1. State message arrives as JSON string
2. `value_template` is rendered by Jinja2 -- `{{ value_json.leak_warning }}` renders Python's `True` as the **string** `"True"`
3. HA compares the rendered string against `payload_on` and `payload_off`
4. `payload_on` is `true` (JSON boolean), but HA treats it as the **string** `"True"` after JSON deserialization

Actually, looking more carefully at HA's MQTT binary sensor documentation: when `payload_on` / `payload_off` are set to boolean `true`/`false` in the discovery config (JSON), HA will compare the `value_template` output against the **string representation**. The Jinja2 template `{{ value_json.leak_warning }}` with a JSON boolean input produces `"True"` (capital T). HA's `payload_on: true` gets stringified to `"True"` as well.

**VERDICT:** This actually works in practice because HA's comparison is case-insensitive for booleans when using `payload_on`/`payload_off`. However, the **cleaner approach** would be to use `value_template: "{{ value_json.leak_warning | lower }}"` or set `payload_on: "True"` / `payload_off: "False"` explicitly.

| **Status** | **LIKELY OK** but fragile -- depends on HA's internal bool-to-string coercion |

---

### Entity #4: Minimum Confidence

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `min_confidence` / `watermeter_ai_min_confidence` |
| state_class | `measurement` |
| unit | `%` |
| icon | `mdi:percent-circle` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.min_confidence }}` |
| Payload key | `"min_confidence": min_conf` (L1920) |
| Value computation | L1900-1904: `min(p["confidence"] for p in predictions.values()) * 100`, rounded to 1 decimal |

| **Status** | **OK** -- but publishes `None` when `predictions` is empty (L1904). HA will show "unknown". |

---

### Entity #5: Status

| Field | Value |
|-------|-------|
| Type | `sensor` (enum) |
| object_id / unique_id | `status` / `watermeter_ai_status` |
| device_class | `enum` |
| options | `["idle", "ok", "warning", "error", "no_models", "pending_confirmation", "processing"]` |
| icon | `mdi:information-outline` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.status }}` |
| Payload key | `"status": self.current_state.get("status", "idle")` (L1921) |

| **Status** | **OK** |

---

### Entity #6: Consecutive Rejections

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `consecutive_rejections` / `watermeter_ai_consecutive_rejections` |
| state_class | `measurement` |
| icon | `mdi:close-octagon-outline` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.consecutive_rejections }}` |
| Payload key | `"consecutive_rejections": self.consecutive_rejections` (L1922) |

| **Status** | **OK** |

---

### Entity #7: Unlabeled Images (Digits)

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `unlabeled_digits` / `watermeter_ai_unlabeled_digits` |
| state_class | `measurement` |
| unit | `images` |
| icon | `mdi:image-edit-outline` |
| entity_category | `diagnostic` |
| state_topic_key | `training_stats` |
| state_topic | `homeassistant/sensor/watermeter/state/training_stats` |
| value_template | `{{ value_json.unlabeled_digits }}` |
| Payload key | `"unlabeled_digits": count` (L2077-2079) |
| Published by | `publish_training_stats()` L2054, every 300s via `_stats_loop()` |

| **Status** | **OK** |

---

### Entity #8: Unlabeled Images (Arrows)

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `unlabeled_arrows` / `watermeter_ai_unlabeled_arrows` |
| state_class | `measurement` |
| unit | `images` |
| icon | `mdi:image-edit-outline` |
| entity_category | `diagnostic` |
| state_topic_key | `training_stats` |
| state_topic | `homeassistant/sensor/watermeter/state/training_stats` |
| value_template | `{{ value_json.unlabeled_arrows }}` |
| Payload key | `"unlabeled_arrows": count` (L2080-2082) |

| **Status** | **OK** |

---

### Entity #9: Training Images (Digits)

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `training_digits` / `watermeter_ai_training_digits` |
| state_class | `total_increasing` |
| unit | `images` |
| icon | `mdi:image-check` |
| entity_category | `diagnostic` |
| state_topic_key | `training_stats` |
| value_template | `{{ value_json.training_digits }}` |
| Payload key | `"training_digits": count` (L2083-2087) |

| **Status** | **OK** |

---

### Entity #10: Training Images (Arrows)

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `training_arrows` / `watermeter_ai_training_arrows` |
| state_class | `total_increasing` |
| unit | `images` |
| icon | `mdi:image-check` |
| entity_category | `diagnostic` |
| state_topic_key | `training_stats` |
| value_template | `{{ value_json.training_arrows }}` |
| Payload key | `"training_arrows": count` (L2088-2092) |

| **Status** | **OK** |

---

### Entity #11: Last Rejected Value

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `last_rejected_value` / `watermeter_ai_last_rejected_value` |
| device_class | `water` |
| unit | `m³` |
| icon | `mdi:water-remove` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.last_rejected_value }}` |
| Payload key | `"last_rejected_value": self.current_state.get("last_rejected_value")` (L1923) |

#### Note on behavior
- This value is set when a reading is rejected (L1797) and cleared when a reading is accepted (L1751) or manually set (L2008).
- It is published in the NEXT successful reading's payload (since rejected readings don't trigger `publish_to_mqtt()`).
- Between rejection and next successful publish, HA still shows the old value from the last successful publish.

| **Status** | **OK** -- but note HA only sees this value on the next successful publish, not immediately on rejection |

---

### Entity #12: Last Rejected Reason

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `last_rejected_reason` / `watermeter_ai_last_rejected_reason` |
| icon | `mdi:alert-circle-outline` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.last_rejected_reason }}` |
| Payload key | `"last_rejected_reason": last_rejected_reason` (L1924) |
| Computation | L1910-1914: joins `last_rejected_reasons` list with `"; "`, or empty string |

| **Status** | **OK** |

---

### Entity #13: Average Rate

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `average_rate` / `watermeter_ai_average_rate` |
| state_class | `measurement` |
| unit | `m³/h` |
| icon | `mdi:speedometer` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.average_rate }}` |
| Payload key | `"average_rate": round(avg_rate, 4) if avg_rate is not None else None` (L1925) |

| **Status** | **OK** -- `None` becomes `null` in JSON, HA shows "unknown" |

---

### Entity #14: Last Update

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `last_update` / `watermeter_ai_last_update` |
| device_class | `timestamp` |
| icon | `mdi:clock-outline` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.last_update }}` |
| Payload key | `"last_update": datetime.now().isoformat()` (L1926) |

| **Status** | **OK** -- ISO format is what HA expects for `device_class: timestamp` |

---

### Entity #15: MQTT Connected

| Field | Value |
|-------|-------|
| Type | `binary_sensor` |
| object_id / unique_id | `mqtt_connected` / `watermeter_ai_mqtt_connected` |
| device_class | `connectivity` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.mqtt_connected }}` |
| payload_on | `True` (bool) |
| payload_off | `False` (bool) |
| Payload key | `"mqtt_connected": True` (L1927, always True since we're publishing) |

| **Status** | **OK** -- always True when publishing (by definition). Same bool coercion note as Entity #3. |

---

### Entity #16: Processing

| Field | Value |
|-------|-------|
| Type | `binary_sensor` |
| object_id / unique_id | `processing` / `watermeter_ai_processing` |
| device_class | `running` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.processing }}` |
| payload_on | `True` (bool) |
| payload_off | `False` (bool) |
| Payload key | `"processing": False` (L1928, always False since publish happens after processing) |

| **Status** | **OK** -- always False at publish time. Same bool coercion note as Entity #3. |

---

### Entity #17: Confirmation Pending

| Field | Value |
|-------|-------|
| Type | `binary_sensor` |
| object_id / unique_id | `confirmation_pending` / `watermeter_ai_confirmation_pending` |
| icon | `mdi:human-greeting-proximity` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.confirmation_pending }}` |
| payload_on | `True` (bool) |
| payload_off | `False` (bool) |
| Payload key | `"confirmation_pending": self._pending_confirmation is not None` (L1929) |

| **Status** | **OK** -- same bool coercion note as Entity #3 |

---

### Entity #18: Inference Duration

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `inference_duration` / `watermeter_ai_inference_duration` |
| device_class | `duration` |
| state_class | `measurement` |
| unit | `ms` |
| icon | `mdi:timer-outline` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.inference_duration }}` |
| Payload key | `"inference_duration": self._last_inference_duration_ms` (L1930) |
| Initialized as | `None` (L327) |
| Set by | `process_reading()` L1642 |

| **Status** | **OK** -- `None` on first manual-set before any inference; HA shows "unknown" |

---

### Entity #19: Processing Duration

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `processing_duration` / `watermeter_ai_processing_duration` |
| device_class | `duration` |
| state_class | `measurement` |
| unit | `s` |
| icon | `mdi:timer` |
| entity_category | `diagnostic` |
| value_template | `{{ value_json.processing_duration }}` |
| Payload key | `"processing_duration": self._last_processing_duration_s` (L1931) |
| Initialized as | `None` (L328) |
| Set by | `process_reading()` L1838 |

| **Status** | **OK** -- same as Entity #18 |

---

### Entity #20: Active Digits Model

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `active_digits_model` / `watermeter_ai_active_digits_model` |
| icon | `mdi:brain` |
| entity_category | `config` |
| value_template | `{{ value_json.active_digits_model }}` |
| Payload key | `"active_digits_model": self._get_active_model_name("digits")` (L1932) |

| **Status** | **OK** -- returns model directory name or `None` |

---

### Entity #21: Active Arrows Model

| Field | Value |
|-------|-------|
| Type | `sensor` |
| object_id / unique_id | `active_arrows_model` / `watermeter_ai_active_arrows_model` |
| icon | `mdi:brain` |
| entity_category | `config` |
| value_template | `{{ value_json.active_arrows_model }}` |
| Payload key | `"active_arrows_model": self._get_active_model_name("arrows")` (L1933) |

| **Status** | **OK** |

---

## 3. Payload Key Alignment Verification

The `_HA_ENTITIES` registry uses `object_id` as both the discovery topic component AND the `value_template` key (L2168: `"{{ value_json." + object_id + " }}"`). The `publish_to_mqtt()` payload (L1916-1934) must use the exact same keys.

| Entity object_id | Payload key in `publish_to_mqtt()` | Match? |
|------------------|-------------------------------------|--------|
| `water_usage` | `"water_usage"` (L1917) | YES |
| `water_usage_raw` | `"water_usage_raw"` (L1918) | YES |
| `leak_warning` | `"leak_warning"` (L1919) | YES |
| `min_confidence` | `"min_confidence"` (L1920) | YES |
| `status` | `"status"` (L1921) | YES |
| `consecutive_rejections` | `"consecutive_rejections"` (L1922) | YES |
| `last_rejected_value` | `"last_rejected_value"` (L1923) | YES |
| `last_rejected_reason` | `"last_rejected_reason"` (L1924) | YES |
| `average_rate` | `"average_rate"` (L1925) | YES |
| `last_update` | `"last_update"` (L1926) | YES |
| `mqtt_connected` | `"mqtt_connected"` (L1927) | YES |
| `processing` | `"processing"` (L1928) | YES |
| `confirmation_pending` | `"confirmation_pending"` (L1929) | YES |
| `inference_duration` | `"inference_duration"` (L1930) | YES |
| `processing_duration` | `"processing_duration"` (L1931) | YES |
| `active_digits_model` | `"active_digits_model"` (L1932) | YES |
| `active_arrows_model` | `"active_arrows_model"` (L1933) | YES |

Training stats topic entities:

| Entity object_id | Payload key in `publish_training_stats()` | Match? |
|------------------|---------------------------------------------|--------|
| `unlabeled_digits` | `"unlabeled_digits"` (L2077) | YES |
| `unlabeled_arrows` | `"unlabeled_arrows"` (L2080) | YES |
| `training_digits` | `"training_digits"` (L2083) | YES |
| `training_arrows` | `"training_arrows"` (L2088) | YES |

**All 21 payload keys match their discovery `value_template` keys.**

---

## 4. `set_manual_value()` Payload Alignment Check

`set_manual_value()` (L1965-2046) builds its own inline payload at L2018-2036 instead of calling `publish_to_mqtt()`. Let me verify every key matches:

| Key | `publish_to_mqtt()` | `set_manual_value()` | Match? |
|-----|---------------------|----------------------|--------|
| `water_usage` | L1917 | L2019 | YES |
| `water_usage_raw` | L1918 | L2020 | YES |
| `leak_warning` | L1919 | L2021 | YES |
| `min_confidence` | L1920 | L2023 (`None`) | YES |
| `status` | L1921 | L2023 | YES |
| `consecutive_rejections` | L1922 | L2024 | YES |
| `last_rejected_value` | L1923 | L2025 | YES |
| `last_rejected_reason` | L1924 | L2026 | YES |
| `average_rate` | L1925 | L2027 | YES |
| `last_update` | L1926 | L2028 | YES |
| `mqtt_connected` | L1927 | L2029 | YES |
| `processing` | L1928 | L2030 | YES |
| `confirmation_pending` | L1929 | L2031 | YES |
| `inference_duration` | L1930 | L2032 | YES |
| `processing_duration` | L1931 | L2033 | YES |
| `active_digits_model` | L1932 | L2034 | YES |
| `active_arrows_model` | L1933 | L2035 | YES |

**All keys align between both publish paths.**

---

## 5. Publication Flow Diagram

```
process_reading() L1615
    ├─ is_valid=True, no confirmation needed → publish_to_mqtt() L1786
    ├─ is_valid=True, confirmation needed → _publish_confirmation_request() L1775
    │   └─ User confirms → publish_to_mqtt() L1112
    │   └─ User corrects → publish_to_mqtt() L1168
    │   └─ User rejects → NO publish
    │   └─ Timeout → NO publish
    └─ is_valid=False → NO publish (rejected reading)

set_manual_value() L1965
    └─ Inline MQTT publish L2014-2038

publish_training_stats() L2054
    └─ Published on: connect (L2217), every 300s (_stats_loop L2099)

publish_discovery() L2127
    └─ Called on: MQTT connect (L2216), HA online (L2254)
```

---

## 6. Issues Found

### Issue A: Binary Sensor Bool Coercion (Minor)

**Entities affected:** #3 (leak_warning), #15 (mqtt_connected), #16 (processing), #17 (confirmation_pending)

**Problem:** Discovery sets `payload_on: true` (JSON bool) and `payload_off: false` (JSON bool), but the `value_template` renders the JSON boolean as the Jinja2 string `"True"` / `"False"` (capital T/F). HA compares the rendered string against the stringified `payload_on`/`payload_off`.

**Severity:** Low. HA handles this correctly in practice because it stringifies `true` -> `"True"` for comparison. But it would be more robust to either:
- Set `payload_on: "True"` and `payload_off: "False"` (strings), or
- Use `value_template: "{{ value_json.leak_warning | lower }}"` with `payload_on: "true"` / `payload_off: "false"`

### Issue B: Duplicate Payload Construction in `set_manual_value()` (Code Smell)

**Location:** L2018-2036

**Problem:** `set_manual_value()` constructs its own MQTT payload inline instead of calling `publish_to_mqtt()`. If a new entity is added to `publish_to_mqtt()`, the developer must remember to update `set_manual_value()` as well. This is error-prone.

**Recommendation:** Refactor `set_manual_value()` to call `publish_to_mqtt()` directly:
```python
await self.publish_to_mqtt(value, [], {}, leak_warning=False, raw_value=value)
```
Note: `set_manual_value()` is currently synchronous, so this would require making it async or using `asyncio.run_coroutine_threadsafe()`.

### Issue C: `mqtt_connected` Always True (Semantic Question)

**Entity:** #15

**Problem:** `mqtt_connected` is hardcoded to `True` in both publish paths (L1927, L2029). This is technically correct (if we're publishing, we're connected), but the entity is misleading -- it can never show `False` because when disconnected, no message is published.

**Recommendation:** This entity only has value for HA availability tracking. Consider using MQTT last-will-and-testament (LWT) instead, which would properly set the entity to `False` on disconnect.

### Issue D: `processing` Always False (Semantic Question)

**Entity:** #16

**Problem:** `processing` is hardcoded to `False` in both publish paths (L1928, L2030). Since MQTT publish only happens after processing completes, this entity can never show `True`.

**Recommendation:** To make this entity useful, you'd need to publish a "processing started" message at the beginning of `process_reading()` with `processing: True`, then the final message with `processing: False`. Currently this entity is always `False`.

### Issue E: No Immediate MQTT Publish on Rejection

**Entities affected:** #6 (consecutive_rejections), #11 (last_rejected_value), #12 (last_rejected_reason)

**Problem:** When a reading is rejected (L1790-1815), `publish_to_mqtt()` is NOT called. The rejection count, last rejected value, and reason are stored in `current_state` but only published to MQTT on the NEXT successful reading. This means HA is always one step behind for these diagnostic values.

**Severity:** Medium. If multiple consecutive rejections occur, HA will not reflect the updated `consecutive_rejections` count until the next successful reading.

### Issue F: Training Stats Not Published Immediately on Startup

**Location:** `_stats_loop()` L2099-2110

**Problem:** The stats loop sleeps 300 seconds BEFORE publishing for the first time. However, `publish_training_stats()` IS called on MQTT connect (L2217), so the initial publish does happen. The 300s delay is only for subsequent updates. This is fine.

**Status:** Not an issue.

---

## 7. Specific Investigation: Raw Value Entity

The user reports that `water_usage_raw` does NOT work.

### Complete trace of raw_value publication:

1. **`process_reading()` L1645:** `total_value, raw_values = self.calculate_total(predictions)` -- `raw_values` = `{"digits": [int, ...], "arrows": [float, ...]}`

2. **`process_reading()` L1785:** `raw_total = self._compute_raw_total(raw_values)` -- computes unrounded total using continuous arrow values

3. **`process_reading()` L1786-1788:** `await self.publish_to_mqtt(total_value, ..., raw_value=raw_total)` -- passes raw_total to publish

4. **`publish_to_mqtt()` L1918:** `"water_usage_raw": round(raw_value, 6) if raw_value is not None else round(value, 4)` -- includes in payload

5. **Discovery (L2168):** `value_template: "{{ value_json.water_usage_raw }}"` -- extracts from payload

### Potential Issues with Raw Value:

**A. Value might be identical to `water_usage`:**
- `_compute_raw_total()` (L1852-1865) sums `digits * 10^n + arrows * 10^(-n)` using continuous arrow values
- `calculate_total()` (L736-738) uses `int(arrow) * 10^(-n)` (floored arrows)
- The difference is only in the fractional part from arrows. If arrows happen to predict integer values (e.g., 3.0 instead of 3.7), both values will be identical.
- This could make the user think raw_value "doesn't work" when it actually does but produces the same number.

**B. Rounding:**
- `water_usage` is `round(value, 4)` -- 4 decimal places
- `water_usage_raw` is `round(raw_value, 6)` -- 6 decimal places
- If the raw value is, say, `123.4567891`, it becomes `123.456789`. But `water_usage` would be `123.4567`.
- The difference is only in decimal places 5 and 6, which may not be visible in HA's default display.

**C. The `state_class: total_increasing` might cause HA to reject decreasing raw values:**
- If raw_value fluctuates (e.g., 123.456789 -> 123.456123 on next reading), HA's `total_increasing` state class will ignore the decrease and keep showing the old value.
- This is a real possibility since the continuous arrow predictions are noisier than the floored ones.

**VERDICT on raw_value:** The code itself is correct -- `water_usage_raw` IS published with every reading. The most likely reasons for it appearing "broken" are:
1. **`total_increasing` state class** causes HA to silently drop readings where the raw value decreased (arrow noise)
2. **Values are very similar** to `water_usage`, making it seem like nothing new is published
3. **The entity might need HA restart** after initial discovery to pick up correctly

---

## 8. Summary

| # | Entity | Type | Status |
|---|--------|------|--------|
| 1 | Water Usage | sensor | OK |
| 2 | Water Usage Raw | sensor | OK (but see total_increasing issue) |
| 3 | Leak Warning | binary_sensor | OK (minor: bool coercion) |
| 4 | Min Confidence | sensor | OK |
| 5 | Status | sensor (enum) | OK |
| 6 | Consecutive Rejections | sensor | OK (delayed on rejection) |
| 7 | Unlabeled Digits | sensor | OK |
| 8 | Unlabeled Arrows | sensor | OK |
| 9 | Training Digits | sensor | OK |
| 10 | Training Arrows | sensor | OK |
| 11 | Last Rejected Value | sensor | OK (delayed on rejection) |
| 12 | Last Rejected Reason | sensor | OK (delayed on rejection) |
| 13 | Average Rate | sensor | OK |
| 14 | Last Update | sensor | OK |
| 15 | MQTT Connected | binary_sensor | OK (always True) |
| 16 | Processing | binary_sensor | OK (always False) |
| 17 | Confirmation Pending | binary_sensor | OK (bool coercion) |
| 18 | Inference Duration | sensor | OK |
| 19 | Processing Duration | sensor | OK |
| 20 | Active Digits Model | sensor | OK |
| 21 | Active Arrows Model | sensor | OK |

### Recommended Fixes (prioritized):

1. **Raw Value: Change `state_class` from `total_increasing` to `measurement`** -- This is the most likely cause of the reported "doesn't work" issue. Arrow noise causes small decreases in the raw value, which `total_increasing` silently drops.

2. **Add rejection-time MQTT publish** -- Publish a state update when readings are rejected so diagnostic entities (#6, #11, #12) update immediately.

3. **Refactor `set_manual_value()` to call `publish_to_mqtt()`** -- Eliminate duplicated payload construction.

4. **Consider LWT for `mqtt_connected`** -- Make the connectivity entity actually useful.

5. **Consider `processing: True` publish at start of reading** -- Make the processing entity actually useful.
