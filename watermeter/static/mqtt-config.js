const MqttConfig = (function () {

    // ============================================================
    // Private helpers
    // ============================================================

    function _getVal(id) {
        const el = document.getElementById(id);
        return el ? el.value : '';
    }

    function _setVal(id, value) {
        const el = document.getElementById(id);
        if (el) el.value = (value !== null && value !== undefined) ? value : '';
    }

    function _setChecked(id, checked) {
        const el = document.getElementById(id);
        if (el) el.checked = !!checked;
    }

    function _show(id) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'block';
    }

    function _hide(id) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    }

    function _getCheckedRadio(name) {
        const radios = document.querySelectorAll(`input[name="${name}"]`);
        for (const r of radios) {
            if (r.checked) return r.value;
        }
        return null;
    }

    function _setCheckedRadio(name, value) {
        const radios = document.querySelectorAll(`input[name="${name}"]`);
        for (const r of radios) {
            r.checked = (r.value === value);
        }
    }

    // ============================================================
    // Trigger Mode Radios
    // ============================================================

    function _setupTriggerModeRadios() {
        const radios = document.querySelectorAll('input[name="trigger-mode"]');
        radios.forEach(function (radio) {
            radio.addEventListener('change', function () {
                _updateTriggerFields(this.value);
            });
        });
        // Set initial state from whatever is currently checked
        const current = _getCheckedRadio('trigger-mode');
        if (current) _updateTriggerFields(current);
    }

    function _updateTriggerFields(mode) {
        const showMqtt = (mode === 'mqtt' || mode === 'both');
        const showCyclic = (mode === 'cyclic' || mode === 'both');
        const mqttFields = document.getElementById('trigger-mqtt-fields');
        const cyclicFields = document.getElementById('trigger-cyclic-fields');
        if (mqttFields) mqttFields.style.display = showMqtt ? 'block' : 'none';
        if (cyclicFields) cyclicFields.style.display = showCyclic ? 'block' : 'none';
    }

    // ============================================================
    // HA Toggle
    // ============================================================

    function _setupHaToggle() {
        const toggle = document.getElementById('ha-enabled');
        if (!toggle) return;
        toggle.addEventListener('change', function () {
            const haFields = document.getElementById('ha-fields');
            if (haFields) haFields.style.display = this.checked ? 'block' : 'none';
        });
        // Set initial state
        const haFields = document.getElementById('ha-fields');
        if (haFields) haFields.style.display = toggle.checked ? 'block' : 'none';
    }

    // ============================================================
    // Password Toggle
    // ============================================================

    function _setupPasswordToggle() {
        const btn = document.getElementById('mqtt-password-toggle');
        if (!btn) return;
        btn.addEventListener('click', function () {
            const input = document.getElementById('mqtt-password');
            const eye = document.getElementById('mqtt-password-eye');
            if (!input) return;
            if (input.type === 'password') {
                input.type = 'text';
                if (eye) eye.textContent = '\uD83D\uDE48'; // see-no-evil as "hide" indicator
            } else {
                input.type = 'password';
                if (eye) eye.textContent = '\uD83D\uDC41\uFE0F';
            }
        });
    }

    // ============================================================
    // loadConfig
    // ============================================================

    async function loadConfig() {
        try {
            const resp = await fetch('/api/mqtt/config');
            const data = await resp.json();

            // MQTT broker settings
            const mqtt = data.mqtt || {};
            _setVal('mqtt-broker', mqtt.broker);
            _setVal('mqtt-port', mqtt.port !== undefined ? mqtt.port : 1883);
            _setVal('mqtt-username', mqtt.username);
            _setVal('mqtt-password', mqtt.password);
            _setVal('mqtt-client-id', mqtt.client_id);

            // Trigger mode
            const trigger = data.trigger || {};
            const triggerMode = trigger.mode || 'cyclic';
            _setCheckedRadio('trigger-mode', triggerMode);
            _updateTriggerFields(triggerMode);

            // Trigger topic — may be in mqtt.trigger_topic OR trigger.mqtt_topic
            const triggerTopic = mqtt.trigger_topic || trigger.mqtt_topic || '';
            _setVal('trigger-topic', triggerTopic);
            _setVal('trigger-payload', mqtt.trigger_payload || trigger.trigger_payload || '');
            _setVal('cyclic-interval', trigger.cyclic_interval !== undefined ? trigger.cyclic_interval : 60);

            // Home Assistant settings
            const ha = data.homeassistant || {};
            const haEnabled = !!(ha.enabled);
            _setChecked('ha-enabled', haEnabled);
            const haFields = document.getElementById('ha-fields');
            if (haFields) haFields.style.display = haEnabled ? 'block' : 'none';

            _setVal('ha-publish-topic', ha.publish_topic);
            _setVal('ha-discovery-prefix', ha.discovery_prefix !== undefined ? ha.discovery_prefix : 'homeassistant');
            // device_name may be in ha.device_name OR ha.device.name
            const deviceName = ha.device_name || (ha.device && ha.device.name) || '';
            _setVal('ha-device-name', deviceName);
            _setVal('ha-sensor-type', ha.sensor_type || 'sensor');
            _setVal('ha-unit', ha.unit || 'm3');
            _setVal('ha-update-interval', ha.update_interval !== undefined ? ha.update_interval : 60);

        } catch (e) {
            console.error('MqttConfig: failed to load config:', e);
        }
    }

    // ============================================================
    // save
    // ============================================================

    async function save() {
        const resultEl = document.getElementById('mqtt-save-result');

        // Clear previous errors
        ['mqtt-broker', 'mqtt-port'].forEach(function (id) {
            const el = document.getElementById(id);
            if (el) el.classList.remove('error');
        });
        if (resultEl) {
            resultEl.textContent = '';
            resultEl.className = '';
        }

        const triggerMode = _getCheckedRadio('trigger-mode') || 'cyclic';
        const broker = _getVal('mqtt-broker').trim();
        const portStr = _getVal('mqtt-port').trim();
        const port = parseInt(portStr, 10);

        // Validation
        const needsBroker = (triggerMode === 'mqtt' || triggerMode === 'both');
        let valid = true;

        if (needsBroker && !broker) {
            const el = document.getElementById('mqtt-broker');
            if (el) el.classList.add('error');
            if (resultEl) {
                resultEl.textContent = 'Broker address is required for MQTT trigger mode.';
                resultEl.className = 'error';
            }
            valid = false;
        }

        if (portStr && (isNaN(port) || port < 1 || port > 65535)) {
            const el = document.getElementById('mqtt-port');
            if (el) el.classList.add('error');
            if (resultEl) {
                const msg = 'Port must be between 1 and 65535.';
                resultEl.textContent = valid ? msg : resultEl.textContent + ' ' + msg;
                resultEl.className = 'error';
            }
            valid = false;
        }

        if (!valid) return;

        const haEnabled = !!(document.getElementById('ha-enabled') && document.getElementById('ha-enabled').checked);

        const payload = {
            mqtt: {
                broker: broker,
                port: port || 1883,
                username: _getVal('mqtt-username').trim(),
                password: _getVal('mqtt-password'),
                client_id: _getVal('mqtt-client-id').trim(),
                keepalive: 60,
                trigger_topic: _getVal('trigger-topic').trim(),
                trigger_payload: _getVal('trigger-payload').trim(),
                reset_topic: 'watermeter/reset'
            },
            trigger: {
                mode: triggerMode,
                cyclic_interval: parseInt(_getVal('cyclic-interval'), 10) || 60
            },
            homeassistant: {
                enabled: haEnabled,
                publish_topic: _getVal('ha-publish-topic').trim(),
                discovery_prefix: _getVal('ha-discovery-prefix').trim() || 'homeassistant',
                device_name: _getVal('ha-device-name').trim(),
                sensor_type: _getVal('ha-sensor-type') || 'sensor',
                unit: _getVal('ha-unit') || 'm3',
                update_interval: parseInt(_getVal('ha-update-interval'), 10) || 60
            }
        };

        const saveBtn = document.getElementById('mqtt-save-btn');
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
        }

        try {
            const resp = await fetch('/api/mqtt/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await resp.json();

            if (data.success) {
                _hide('mqtt-edit');
                _show('mqtt-saved');

                if (resultEl) {
                    resultEl.textContent = data.message || 'Settings saved.';
                    resultEl.className = 'success';
                }

                // In setup mode, show setup-complete
                if (window.setupMode) {
                    const el = document.getElementById('setup-complete');
                    if (el) el.style.display = 'block';
                }
            } else {
                if (resultEl) {
                    resultEl.textContent = data.message || 'Save failed.';
                    resultEl.className = 'error';
                }
            }
        } catch (e) {
            if (resultEl) {
                resultEl.textContent = 'Save failed: ' + e.message;
                resultEl.className = 'error';
            }
        } finally {
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save & Continue';
            }
        }
    }

    // ============================================================
    // edit
    // ============================================================

    function edit() {
        _show('mqtt-edit');
        _hide('mqtt-saved');
    }

    // ============================================================
    // testConnection
    // ============================================================

    async function testConnection() {
        const broker = _getVal('mqtt-broker').trim();
        const portStr = _getVal('mqtt-port').trim();
        const username = _getVal('mqtt-username').trim();
        const password = _getVal('mqtt-password');

        const resultEl = document.getElementById('mqtt-test-result');
        const btn = document.getElementById('mqtt-test-btn');

        if (!broker) {
            if (resultEl) {
                resultEl.textContent = '\u274c Broker address is required.';
                resultEl.className = 'error';
            }
            return;
        }

        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Testing...';
        }
        if (resultEl) {
            resultEl.textContent = 'Testing connection...';
            resultEl.className = '';
        }

        try {
            const resp = await fetch('/api/mqtt/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    broker: broker,
                    port: parseInt(portStr, 10) || 1883,
                    username: username,
                    password: password
                })
            });
            const data = await resp.json();

            if (data.success) {
                let msg = '\u2705 ' + (data.message || 'Connection successful.');
                if (data.warnings && data.warnings.length > 0) {
                    msg += ' Warnings: ' + data.warnings.join('; ');
                }
                if (resultEl) {
                    resultEl.textContent = msg;
                    resultEl.className = 'success';
                }
            } else {
                if (resultEl) {
                    resultEl.textContent = '\u274c ' + (data.message || 'Connection failed.');
                    resultEl.className = 'error';
                }
            }
        } catch (e) {
            if (resultEl) {
                resultEl.textContent = '\u274c Connection error: ' + e.message;
                resultEl.className = 'error';
            }
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Test Connection';
            }
        }
    }

    // ============================================================
    // init
    // ============================================================

    function init() {
        _setupTriggerModeRadios();
        _setupHaToggle();
        _setupPasswordToggle();
        loadConfig();
    }

    // ============================================================
    // Public API
    // ============================================================

    return { init, loadConfig, save, edit, testConnection };

})();

document.addEventListener('DOMContentLoaded', function () {
    if (document.getElementById('step-mqtt')) {
        MqttConfig.init();
    }
});
