# MQTT Reconnect Fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the bug where MQTT disconnects but never reconnects, despite logging "will reconnect automatically".

**Architecture:** Add an explicit reconnect loop in the `on_disconnect` callback with exponential backoff. Paho's built-in `loop_start()` reconnect is unreliable because certain disconnect types set the internal state to `MQTT_CS_DISCONNECTED` (which causes the loop thread to exit) rather than `MQTT_CS_CONNECTION_LOST` (which would trigger reconnect). The fix is defensive: we don't trust paho's state machine and manage reconnection ourselves.

**Tech Stack:** paho-mqtt 2.x, threading, Python logging

---

## Root Cause

`on_disconnect()` at `watermeter/mqtt_publisher.py:475-480` logs a warning but does nothing. It trusts `loop_start()`'s internal reconnect, but paho 2.x's `loop_forever()` thread exits when `_state == MQTT_CS_DISCONNECTED`. Certain broker-initiated disconnects (keepalive timeout, broker restart, network loss) cause paho to set this state via `_loop_rc_handle()`, which means the background thread terminates permanently. No reconnect is ever attempted.

---

### Task 1: Write failing test for reconnect behavior

**Files:**
- Create: `tests/unit/test_mqtt_reconnect.py`

**Step 1: Write the failing test**

Test that `on_disconnect` with a non-zero reason code triggers a reconnect attempt:

```python
"""Unit tests for MQTT reconnection logic."""

import threading
import time
from unittest.mock import MagicMock, patch


class TestMqttReconnect:
    """Tests for MQTT reconnect-on-disconnect behavior."""

    def _make_publisher(self):
        """Create an MqttPublisher with minimal mocked dependencies."""
        from watermeter.mqtt_publisher import MqttPublisher

        config = {
            "mqtt": {
                "broker": "localhost",
                "port": 1883,
                "client_id": "test",
                "keepalive": 60,
                "trigger_topic": "trigger",
                "trigger_payload": "go",
                "reset_topic": "reset",
            },
            "trigger": {"mode": "mqtt"},
            "homeassistant": {"enabled": False},
        }
        publisher = MqttPublisher(
            config=config,
            meter_state=MagicMock(),
            rate_tracker=MagicMock(),
            confirmation_manager=MagicMock(get_config=MagicMock(return_value={"enabled": False})),
            on_trigger=MagicMock(),
            on_reset=MagicMock(),
            get_active_model_fn=MagicMock(return_value=("none", "none")),
            get_inference_duration_fn=MagicMock(return_value=None),
            get_processing_duration_fn=MagicMock(return_value=None),
        )
        publisher.mqtt_client = MagicMock()
        return publisher

    def test_unclean_disconnect_schedules_reconnect(self):
        """on_disconnect with reason_code != 0 must attempt reconnect."""
        publisher = self._make_publisher()
        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: False  # non-zero
        reason_code.__str__ = lambda self: "Unspecified error"

        publisher.on_disconnect(
            client=publisher.mqtt_client,
            userdata=None,
            disconnect_flags=MagicMock(),
            reason_code=reason_code,
            properties=None,
        )

        # Give the reconnect thread a moment to start
        time.sleep(0.1)

        # reconnect() must have been called (or at least attempted)
        publisher.mqtt_client.reconnect.assert_called()

    def test_clean_disconnect_does_not_reconnect(self):
        """on_disconnect with reason_code == 0 must NOT attempt reconnect."""
        publisher = self._make_publisher()
        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: other == 0  # is zero
        reason_code.__str__ = lambda self: "Success"

        publisher.on_disconnect(
            client=publisher.mqtt_client,
            userdata=None,
            disconnect_flags=MagicMock(),
            reason_code=reason_code,
            properties=None,
        )

        time.sleep(0.1)
        publisher.mqtt_client.reconnect.assert_not_called()

    def test_reconnect_retries_on_failure(self):
        """Reconnect loop retries with backoff when reconnect() raises."""
        publisher = self._make_publisher()
        # First call fails, second succeeds
        publisher.mqtt_client.reconnect.side_effect = [OSError("refused"), None]

        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: False
        reason_code.__str__ = lambda self: "Unspecified error"

        # Patch the sleep to speed up the test
        with patch("watermeter.mqtt_publisher.time.sleep"):
            publisher.on_disconnect(
                client=publisher.mqtt_client,
                userdata=None,
                disconnect_flags=MagicMock(),
                reason_code=reason_code,
                properties=None,
            )
            time.sleep(0.3)

        assert publisher.mqtt_client.reconnect.call_count >= 2

    def test_reconnect_uses_exponential_backoff(self):
        """Reconnect delays double on each failure up to max."""
        publisher = self._make_publisher()
        sleep_calls = []

        # Fail 4 times, then succeed
        publisher.mqtt_client.reconnect.side_effect = [
            OSError("fail1"),
            OSError("fail2"),
            OSError("fail3"),
            OSError("fail4"),
            None,  # success
        ]

        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: False
        reason_code.__str__ = lambda self: "error"

        def mock_sleep(secs):
            sleep_calls.append(secs)

        with patch("watermeter.mqtt_publisher.time.sleep", side_effect=mock_sleep):
            publisher.on_disconnect(
                client=publisher.mqtt_client,
                userdata=None,
                disconnect_flags=MagicMock(),
                reason_code=reason_code,
                properties=None,
            )
            time.sleep(0.3)

        # Backoff: 5, 10, 20, 40
        assert len(sleep_calls) >= 4
        assert sleep_calls[0] == 5
        assert sleep_calls[1] == 10
        assert sleep_calls[2] == 20
        assert sleep_calls[3] == 40

    def test_reconnect_caps_at_120_seconds(self):
        """Backoff is capped at 120 seconds."""
        publisher = self._make_publisher()
        sleep_calls = []

        # Fail many times
        publisher.mqtt_client.reconnect.side_effect = [OSError("fail")] * 10 + [None]

        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: False
        reason_code.__str__ = lambda self: "error"

        def mock_sleep(secs):
            sleep_calls.append(secs)

        with patch("watermeter.mqtt_publisher.time.sleep", side_effect=mock_sleep):
            publisher.on_disconnect(
                client=publisher.mqtt_client,
                userdata=None,
                disconnect_flags=MagicMock(),
                reason_code=reason_code,
                properties=None,
            )
            time.sleep(0.5)

        # After 5→10→20→40→80→120, should cap at 120
        assert max(sleep_calls) <= 120

    def test_no_concurrent_reconnect_loops(self):
        """Multiple disconnect events must not spawn multiple reconnect loops."""
        publisher = self._make_publisher()
        reconnect_count = []

        original_reconnect = publisher.mqtt_client.reconnect

        def slow_reconnect():
            reconnect_count.append(1)
            time.sleep(0.2)  # simulate slow reconnect
            return original_reconnect()

        publisher.mqtt_client.reconnect = MagicMock(side_effect=slow_reconnect)

        reason_code = MagicMock()
        reason_code.__eq__ = lambda self, other: False
        reason_code.__str__ = lambda self: "error"

        with patch("watermeter.mqtt_publisher.time.sleep"):
            # Fire disconnect twice quickly
            publisher.on_disconnect(
                client=publisher.mqtt_client,
                userdata=None,
                disconnect_flags=MagicMock(),
                reason_code=reason_code,
                properties=None,
            )
            publisher.on_disconnect(
                client=publisher.mqtt_client,
                userdata=None,
                disconnect_flags=MagicMock(),
                reason_code=reason_code,
                properties=None,
            )
            time.sleep(0.5)

        # Should not have spawned two parallel reconnect loops
        # At most 2 calls (one per loop is fine, but not 2 concurrent loops)
        assert publisher.mqtt_client.reconnect.call_count <= 2
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_mqtt_reconnect.py -v`
Expected: FAIL — `on_disconnect` currently does not call `reconnect()`

**Step 3: Commit test**

```bash
git add tests/unit/test_mqtt_reconnect.py
git commit -m "claude: add failing tests for MQTT reconnect-on-disconnect"
```

---

### Task 2: Implement the reconnect loop

**Files:**
- Modify: `watermeter/mqtt_publisher.py:1` (add `import time` at top)
- Modify: `watermeter/mqtt_publisher.py:266-267` (add `_reconnecting` flag in `__init__`)
- Modify: `watermeter/mqtt_publisher.py:475-480` (replace `on_disconnect`)
- Add new method `_reconnect_loop` after `on_disconnect`

**Step 1: Add `import time` and `import threading` to imports**

Check existing imports at top of file. `threading` may already be imported. Add `time` if missing.

Add at `watermeter/mqtt_publisher.py:266-267`, after `self.loop = None`:
```python
self._reconnecting = False  # guard against concurrent reconnect loops
```

**Step 2: Replace `on_disconnect` at lines 475-480**

```python
def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
    """MQTT disconnect callback (paho v2 API)."""
    if reason_code == 0:
        logger.info("Disconnected from MQTT broker (clean)")
        return

    logger.warning(f"Disconnected from MQTT broker: {reason_code}")

    # Guard: only one reconnect loop at a time
    if self._reconnecting:
        logger.debug("Reconnect loop already active — skipping")
        return

    self._reconnecting = True
    thread = threading.Thread(target=self._reconnect_loop, daemon=True)
    thread.start()
```

**Step 3: Add `_reconnect_loop` method after `on_disconnect`**

```python
def _reconnect_loop(self):
    """Reconnect to broker with exponential backoff (5s → 120s cap)."""
    delay = 5
    try:
        while True:
            time.sleep(delay)
            try:
                self.mqtt_client.reconnect()
                logger.info("Reconnected to MQTT broker")
                return
            except (OSError, ConnectionRefusedError) as exc:
                logger.warning(f"MQTT reconnect failed ({exc}), retrying in {delay}s")
                delay = min(delay * 2, 120)
    finally:
        self._reconnecting = False
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_mqtt_reconnect.py -v`
Expected: ALL PASS

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest --tb=short -q`
Expected: All 317+ tests pass, no regressions

**Step 6: Commit**

```bash
git add watermeter/mqtt_publisher.py
git commit -m "claude: fix MQTT reconnect — add explicit reconnect loop with backoff"
```

---

### Task 3: Add `reconnect_delay_set()` belt-and-suspenders

**Files:**
- Modify: `watermeter/mqtt_publisher.py:569-570` (add reconnect_delay_set before loop_start)

**Step 1: Write a test that `reconnect_delay_set` is called during start**

Add to `tests/unit/test_mqtt_reconnect.py`:

```python
def test_start_configures_reconnect_delay(self):
    """start() must call reconnect_delay_set on the paho client."""
    publisher = self._make_publisher()
    publisher.mqtt_client = None  # will be created in start()

    with patch("watermeter.mqtt_publisher.mqtt.Client") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        mock_instance.connect.return_value = None

        publisher.start()

        mock_instance.reconnect_delay_set.assert_called_once_with(
            min_delay=5, max_delay=120
        )
```

**Step 2: Run test — expect FAIL**

Run: `.venv/bin/python -m pytest tests/unit/test_mqtt_reconnect.py::TestMqttReconnect::test_start_configures_reconnect_delay -v`

**Step 3: Add to `start()` method, before `loop_start()`**

At `watermeter/mqtt_publisher.py:569`, add before `self.mqtt_client.loop_start()`:
```python
# Configure paho's built-in reconnect backoff as belt-and-suspenders
self.mqtt_client.reconnect_delay_set(min_delay=5, max_delay=120)
```

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest --tb=short -q`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add watermeter/mqtt_publisher.py tests/unit/test_mqtt_reconnect.py
git commit -m "claude: add reconnect_delay_set as belt-and-suspenders for paho reconnect"
```

---

## Commit Strategy

3 commits on `claude/main`:
1. `claude: add failing tests for MQTT reconnect-on-disconnect` — test file only
2. `claude: fix MQTT reconnect — add explicit reconnect loop with backoff` — implementation
3. `claude: add reconnect_delay_set as belt-and-suspenders for paho reconnect` — defense in depth
