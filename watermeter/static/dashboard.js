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
