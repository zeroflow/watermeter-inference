// Account page: change the admin password through the JSON API.
(function () {
    const form = document.getElementById('change-password-form');
    const message = document.getElementById('account-message');
    if (!form || !message) return;

    function show(text, ok) {
        message.textContent = text;
        message.classList.toggle('auth-success', ok);
        message.classList.toggle('auth-error', !ok);
    }

    form.addEventListener('submit', async function (event) {
        event.preventDefault();
        const payload = {
            current: form.elements['current'].value,
            new: form.elements['new'].value,
            confirm: form.elements['confirm'].value,
        };
        try {
            const response = await fetch('/api/auth/password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            let data = {};
            try {
                data = await response.json();
            } catch (e) {
                // Non-JSON body: fall through to the generic message below.
            }
            if (response.status === 429) {
                const wait = response.headers.get('Retry-After');
                show('Too many attempts, try again in ' + wait + ' s', false);
            } else if (response.ok && data.success) {
                show(data.message || 'Password changed.', true);
                form.reset();
            } else {
                show(data.message || 'Could not change the password (HTTP ' + response.status + ')', false);
            }
        } catch (err) {
            show('Request failed: ' + err.message, false);
        }
    });
})();
