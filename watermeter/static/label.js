let currentImage = null;
let initialCounts = { digits: 0, arrows: 0, total: 0 };

async function loadNextImage() {
    try {
        const response = await fetch('/api/label/next-image');
        const data = await response.json();

        if (!data.has_images) {
            document.getElementById('labeling-interface').style.display = 'none';
            document.getElementById('no-images').style.display = 'block';
            // Show 100% completion
            updateProgress(0, 0, 0);
            return;
        }

        currentImage = data;

        // Track initial counts on first load
        if (initialCounts.total === 0) {
            initialCounts = {
                digits: data.remaining.digits,
                arrows: data.remaining.arrows,
                total: data.remaining.total
            };
        }

        // Update stats and progress bars
        updateProgress(data.remaining.digits, data.remaining.arrows, data.remaining.total);

        // Display image
        document.getElementById('current-image').src = 'data:image/jpeg;base64,' + data.image_base64;

        // Display next dial reference image if available
        const nextImageContainer = document.getElementById('next-image-container');
        const nextImage = document.getElementById('next-image');
        if (data.next_image_base64) {
            nextImage.src = 'data:image/jpeg;base64,' + data.next_image_base64;
            nextImageContainer.style.display = 'flex';
        } else {
            nextImageContainer.style.display = 'none';
        }

        // Update model type badge
        const badge = document.getElementById('model-type-badge');
        const instructions = document.getElementById('instructions');
        const input = document.getElementById('label-input');

        if (data.model_type === 'digits') {
            badge.textContent = 'DIGIT';
            badge.className = 'model-type-badge model-type-digits';
            instructions.innerHTML = '<strong>Digits:</strong> Enter 0-9 for the digit, or N for NAN (not a number)';
            input.placeholder = '0-9 or N';
            input.inputMode = 'numeric';
        } else {
            badge.textContent = 'ARROW';
            badge.className = 'model-type-badge model-type-arrows';
            instructions.innerHTML = '<strong>Arrows:</strong> Enter value 0,0 to 9,9 (e.g., 1,2 or 6,9 or 3,3 or just 3 for 3,0). Next dial shows the smaller position for reference.';
            input.placeholder = 'e.g. 3,3 or 6,9';
            input.inputMode = 'decimal';
        }

        // Show/hide NAN button (only for digits)
        const nanBtn = document.getElementById('btn-nan');
        if (nanBtn) {
            nanBtn.style.display = data.model_type === 'digits' ? '' : 'none';
        }

        // Show interface
        document.getElementById('labeling-interface').style.display = 'block';
        document.getElementById('no-images').style.display = 'none';

        input.value = '';
        input.focus();

        // Hide message
        const message = document.getElementById('message');
        message.style.display = 'none';

    } catch (error) {
        console.error('Error loading next image:', error);
        showMessage('Error loading image: ' + error.message, 'error');
    }
}

function updateProgress(digitsRemaining, arrowsRemaining, totalRemaining) {
    // Update counts
    document.getElementById('remaining-digits').textContent = digitsRemaining;
    document.getElementById('remaining-arrows').textContent = arrowsRemaining;
    document.getElementById('remaining-total').textContent = totalRemaining;

    // Calculate and update progress bars (showing completion, not remaining)
    const digitsProgress = initialCounts.digits > 0
        ? ((initialCounts.digits - digitsRemaining) / initialCounts.digits * 100)
        : 100;
    const arrowsProgress = initialCounts.arrows > 0
        ? ((initialCounts.arrows - arrowsRemaining) / initialCounts.arrows * 100)
        : 100;
    const totalProgress = initialCounts.total > 0
        ? ((initialCounts.total - totalRemaining) / initialCounts.total * 100)
        : 100;

    document.getElementById('progress-digits').style.width = digitsProgress + '%';
    document.getElementById('progress-arrows').style.width = arrowsProgress + '%';
    document.getElementById('progress-total').style.width = totalProgress + '%';
}

async function submitLabel(label) {
    if (!currentImage) return;

    try {
        const response = await fetch('/api/label/submit', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                filename: currentImage.filename,
                model_type: currentImage.model_type,
                label: label
            })
        });

        const data = await response.json();

        if (data.success) {
            showMessage(`✓ Labeled as ${data.label}`, 'success');
            // Load next image after a short delay
            setTimeout(() => {
                loadNextImage();
            }, 500);
        } else {
            showMessage('Error: ' + data.message, 'error');
            document.getElementById('label-input').focus();
        }

    } catch (error) {
        console.error('Error submitting label:', error);
        showMessage('Error: ' + error.message, 'error');
    }
}

function showMessage(text, type) {
    const message = document.getElementById('message');
    message.textContent = text;
    message.className = 'message ' + type;
    message.style.display = 'block';

    if (type === 'success') {
        setTimeout(() => {
            message.style.display = 'none';
        }, 2000);
    }
}

async function deleteImage() {
    if (!currentImage) return;

    try {
        const response = await fetch('/api/label/delete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                filename: currentImage.filename,
                model_type: currentImage.model_type
            })
        });

        const data = await response.json();

        if (data.success) {
            showMessage('Deleted', 'success');
            setTimeout(() => {
                loadNextImage();
            }, 300);
        } else {
            showMessage('Error: ' + data.message, 'error');
        }

    } catch (error) {
        console.error('Error deleting image:', error);
        showMessage('Error: ' + error.message, 'error');
    }
}

function validateAndSubmit(value) {
    if (!value) return;

    if (currentImage.model_type === 'digits') {
        // Normalize input for digits
        value = value.toUpperCase().trim();

        // Accept 0-9 or N
        if (value === 'N' || (value.length === 1 && value >= '0' && value <= '9')) {
            submitLabel(value === 'N' ? 'NAN' : value);
        } else {
            showMessage('Invalid input. Enter 0-9 or N for NAN', 'error');
        }
    } else {
        // Accept decimal format with comma or dot: 0,0 to 9,9 (or 0.0 to 9.9)
        // Also accept integers: 0-9 (interpreted as X.0)
        value = value.trim();

        // Replace comma with dot for parsing
        const normalizedValue = value.replace(',', '.');

        // Parse as float
        const num = parseFloat(normalizedValue);

        // Validate: must be a number between 0.0 and 9.9
        if (!isNaN(num) && num >= 0.0 && num <= 9.9) {
            // Format to one decimal place with dot (e.g., "3.3")
            const formattedValue = num.toFixed(1);
            submitLabel(formattedValue);
        } else {
            showMessage('Invalid input. Enter 0,0 to 9,9 (e.g., 1,2 or 6,9 or 3)', 'error');
        }
    }
}

// Event listener for Enter key
document.getElementById('label-input').addEventListener('keypress', function(e) {
    if (e.key === 'Enter') {
        validateAndSubmit(this.value);
    }
});

// Global keyboard shortcut for Delete (D key when input is empty)
document.addEventListener('keydown', function(e) {
    const input = document.getElementById('label-input');
    if (e.key.toLowerCase() === 'd' && input.value === '' && document.activeElement === input) {
        e.preventDefault();
        deleteImage();
    }
});

// Detect mobile keyboard open/close via visualViewport
if (window.visualViewport) {
    let baseHeight = window.visualViewport.height;
    window.visualViewport.addEventListener('resize', function() {
        const isOpen = window.visualViewport.height < baseHeight * 0.75;
        document.body.classList.toggle('keyboard-open', isOpen);
        // Lock body to visible height so no whitespace appears behind keyboard
        if (isOpen) {
            document.body.style.height = window.visualViewport.height + 'px';
        } else {
            document.body.style.height = '';
        }
    });
}

// Load first image on page load
window.addEventListener('load', function() {
    loadNextImage();
});
