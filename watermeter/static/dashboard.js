function toggleAutoRefresh(checkbox) {
    const main = document.querySelector('main');
    if (checkbox.checked) {
        // Enable HTMX polling
        main.setAttribute('hx-trigger', 'load, every 5s');
        htmx.process(main); // Re-process HTMX attributes
        console.log('Auto-refresh aktiviert');
    } else {
        // Disable HTMX polling
        main.setAttribute('hx-trigger', 'load');
        htmx.process(main); // Re-process HTMX attributes
        console.log('Auto-refresh deaktiviert');
    }
}

// Status-Message nach 5 Sekunden ausblenden
function hideStatusMessage() {
    setTimeout(() => {
        const msg = document.getElementById('status-message');
        if (msg) msg.textContent = '';
    }, 5000);
}

// Display status message
function showStatusMessage(message) {
    const msg = document.getElementById('status-message');
    if (msg) {
        msg.textContent = message;
        hideStatusMessage();
    }
}

// Set meter value manually
function setMeterValue() {
    const input = document.getElementById('meter-value-input');
    const value = parseFloat(input.value);
    if (isNaN(value) || value < 0) {
        showStatusMessage('Please enter a valid positive number');
        return;
    }
    if (!confirm(`Set meter to ${value.toFixed(4)} m³?`)) return;
    fetch('/api/set-value', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({value: value})
    })
    .then(response => {
        if (!response.ok) throw new Error(`Status ${response.status}`);
        return response.json();
    })
    .then(data => {
        showStatusMessage(data.message || `Meter set to ${value.toFixed(4)} m³`);
        input.value = '';
    })
    .catch(error => showStatusMessage('Error: ' + error.message));
}

// HTMX Event Listener
document.body.addEventListener('htmx:afterSwap', function(evt) {
    if (evt.detail.target.id === 'status-message') {
        hideStatusMessage();
    }
});

// Submit image for training
function submitForTraining(id, imageBase64, model, nextImageBase64 = null) {
    const payload = {
        id: id,
        image_base64: imageBase64,
        model: model
    };
    // Include next dial image for arrows (helps with annotation)
    if (nextImageBase64) {
        payload.next_image_base64 = nextImageBase64;
    }
    fetch('/api/submit-training', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
    })
    .then(response => response.json())
    .then(data => {
        const msg = document.getElementById('status-message');
        if (data.success) {
            msg.textContent = `✅ ${data.message}`;
        } else {
            msg.textContent = `⚠️ ${data.message}`;
        }
        hideStatusMessage();
    })
    .catch(error => {
        const msg = document.getElementById('status-message');
        msg.textContent = `❌ Fehler beim Einreichen: ${error}`;
        hideStatusMessage();
        console.error('Error submitting for training:', error);
    });
}

// --- Pipeline Health metrics poller (Issue #4) ---
// Polls /api/metrics every 30s, renders into #pipeline-health-body using
// textContent + createElement only. No innerHTML, no template strings into DOM.
(function() {
    const POLL_INTERVAL_MS = 30000;

    function makeRow(label, value) {
        const row = document.createElement('div');
        row.className = 'metric';
        const labelEl = document.createElement('span');
        labelEl.textContent = label;
        const valueEl = document.createElement('strong');
        valueEl.textContent = value;
        row.appendChild(labelEl);
        row.appendChild(valueEl);
        return row;
    }

    function fmtPct(x) {
        if (x === null || x === undefined) return '—';
        return (x * 100).toFixed(1) + '%';
    }
    function fmtFloat(x, digits) {
        if (x === null || x === undefined) return '—';
        return Number(x).toFixed(digits);
    }

    function renderMetrics(body, data) {
        // Clear via DOM, not innerHTML
        while (body.firstChild) body.removeChild(body.firstChild);

        const okCount = data.readings_total.ok;
        const failCount = data.readings_total.alignment_failed + data.readings_total.inference_failed;
        body.appendChild(makeRow('Readings OK / Failed', okCount + ' / ' + failCount));
        body.appendChild(makeRow('Failure rate (1h)', fmtPct(data.failure_rate_1h)));
        body.appendChild(makeRow('Failure rate (24h)', fmtPct(data.failure_rate_24h)));

        ['M1', 'M2'].forEach(function(m) {
            const c = data.marker_match_confidence[m] || { count: 0 };
            if (c.count > 0) {
                body.appendChild(makeRow(
                    m + ' confidence (median / min)',
                    fmtFloat(c.median, 3) + ' / ' + fmtFloat(c.min, 3)
                ));
            }
            body.appendChild(makeRow(
                m + ' failures total',
                String(data.marker_detection_failures_total[m] || 0)
            ));
        });
    }

    function pollOnce() {
        const body = document.getElementById('pipeline-health-body');
        if (!body) return;
        const url = body.dataset.metricsUrl || '/api/metrics';
        fetch(url, { credentials: 'same-origin' })
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (data) renderMetrics(body, data);
            })
            .catch(function() { /* silent — keep last good render */ });
    }

    // Re-attach poller after every HTMX swap of the status fragment, since the
    // <details id="pipeline-health"> element gets replaced.
    function startPolling() {
        pollOnce();
        if (window.__pipelineHealthInterval) clearInterval(window.__pipelineHealthInterval);
        window.__pipelineHealthInterval = setInterval(pollOnce, POLL_INTERVAL_MS);
    }

    document.addEventListener('DOMContentLoaded', startPolling);
    document.body.addEventListener('htmx:afterSwap', startPolling);
})();
