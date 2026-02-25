        // ============================================================
        // Setup Mode: Image Source (Step 0)
        // ============================================================
        async function testImageSource() {
            const url = document.getElementById('image-source-url').value.trim();
            const status = document.getElementById('image-source-status');
            const btn = document.getElementById('test-image-btn');

            if (!url) {
                status.textContent = 'Please enter a URL.';
                status.style.color = 'var(--danger)';
                return;
            }

            btn.disabled = true;
            btn.textContent = 'Testing...';
            status.textContent = 'Connecting to device...';
            status.style.color = 'var(--text-light)';

            try {
                const resp = await fetch('/api/roi/image-source', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({url})
                });
                const data = await resp.json();

                if (data.success) {
                    status.textContent = data.message;
                    status.style.color = 'var(--success)';
                    // Hide step 0, reload image onto canvas
                    document.getElementById('step-image-source').style.display = 'none';
                    fetchAndReload();
                } else {
                    status.textContent = data.message;
                    status.style.color = 'var(--danger)';
                }
            } catch (e) {
                status.textContent = 'Connection failed: ' + e.message;
                status.style.color = 'var(--danger)';
            } finally {
                btn.disabled = false;
                btn.textContent = 'Test & Save';
            }
        }

        // ============================================================
        // Canvas & Image State
        // ============================================================
        const canvas = document.getElementById('roi-canvas');
        const ctx = canvas.getContext('2d');
        const loadingIndicator = document.getElementById('loading-indicator');
        const reloadBtn = document.getElementById('reload-btn');

        let image = null;
        let imageLoaded = false;

        // ============================================================
        // Overlay System
        // ============================================================
        const overlays = {
            mode: null,  // 'rotation', 'marker_select', 'digit_select', 'analog_select', null
            mouse: { x: null, y: null, active: false },
            crosshair: { x: null, y: null },

            // Marker selection state
            selectingMarkerIndex: null,
            dragStart: null,
            dragEnd: null,

            // Marker boxes (normalized 0-1)
            markers: [
                { x: 0, y: 0, width: 0.1, height: 0.1, defined: false },
                { x: 0, y: 0, width: 0.1, height: 0.1, defined: false }
            ],

            // Digit selection state
            selectingDigitIndex: null,
            digits: [],  // Array of { x, y, width, height, defined, prediction, confidence }

            // Analog selection state
            selectingAnalogIndex: null,
            analogs: []  // Array of { x, y, width, height, defined, prediction, confidence }
        };

        function setOverlayMode(mode) {
            overlays.mode = mode;
            canvas.style.cursor = mode ? 'crosshair' : 'default';
            render();
        }

        // Round to nearest 0.005
        function roundToStep(value, step = 0.001) {
            return Math.round(value / step) * step;
        }

        function getCanvasCoordinates(event) {
            const rect = canvas.getBoundingClientRect();
            const scaleX = canvas.width / rect.width;
            const scaleY = canvas.height / rect.height;
            return {
                x: (event.clientX - rect.left) * scaleX,
                y: (event.clientY - rect.top) * scaleY
            };
        }

        // Canvas mouse events
        canvas.addEventListener('mousemove', function(event) {
            if (!overlays.mode) return;
            const coords = getCanvasCoordinates(event);
            overlays.mouse.x = coords.x;
            overlays.mouse.y = coords.y;
            overlays.mouse.active = true;

            // Update drag end if dragging
            if ((overlays.mode === 'marker_select' || overlays.mode === 'digit_select' || overlays.mode === 'analog_select') && overlays.dragStart) {
                overlays.dragEnd = { x: coords.x, y: coords.y };
            }

            render();
        });

        canvas.addEventListener('mouseleave', function() {
            overlays.mouse.active = false;
            render();
        });

        canvas.addEventListener('mousedown', function(event) {
            if (overlays.mode !== 'marker_select' && overlays.mode !== 'digit_select' && overlays.mode !== 'analog_select') return;
            const coords = getCanvasCoordinates(event);
            overlays.dragStart = { x: coords.x, y: coords.y };
            overlays.dragEnd = { x: coords.x, y: coords.y };
        });

        canvas.addEventListener('mouseup', function(event) {
            if (!overlays.dragStart) return;
            if (overlays.mode !== 'marker_select' && overlays.mode !== 'digit_select' && overlays.mode !== 'analog_select') return;

            const coords = getCanvasCoordinates(event);
            overlays.dragEnd = coords;

            // Calculate normalized box
            const x1 = Math.min(overlays.dragStart.x, overlays.dragEnd.x);
            const y1 = Math.min(overlays.dragStart.y, overlays.dragEnd.y);
            const x2 = Math.max(overlays.dragStart.x, overlays.dragEnd.x);
            const y2 = Math.max(overlays.dragStart.y, overlays.dragEnd.y);

            const width = x2 - x1;
            const height = y2 - y1;

            let currentMode = overlays.mode;
            let currentIdx = null;

            // Only accept if box is larger than 10px
            if (width > 10 && height > 10) {
                // Calculate and round normalized coordinates
                const normX = roundToStep(x1 / canvas.width);
                const normY = roundToStep(y1 / canvas.height);
                const normW = roundToStep(width / canvas.width);
                const normH = roundToStep(height / canvas.height);

                if (overlays.mode === 'marker_select') {
                    currentIdx = overlays.selectingMarkerIndex;
                    overlays.markers[currentIdx] = {
                        x: normX,
                        y: normY,
                        width: normW,
                        height: normH,
                        defined: true
                    };
                    updateMarkerInputs(currentIdx);
                    updateMarkerPreview(currentIdx);
                    overlays.selectingMarkerIndex = null;
                } else if (overlays.mode === 'digit_select') {
                    currentIdx = overlays.selectingDigitIndex;
                    overlays.digits[currentIdx] = {
                        ...overlays.digits[currentIdx],
                        x: normX,
                        y: normY,
                        width: normW,
                        height: normH,
                        defined: true
                    };
                    updateDigitInputs(currentIdx);
                    runDigitInference(currentIdx);
                    overlays.selectingDigitIndex = null;
                } else if (overlays.mode === 'analog_select') {
                    currentIdx = overlays.selectingAnalogIndex;
                    overlays.analogs[currentIdx] = {
                        ...overlays.analogs[currentIdx],
                        x: normX,
                        y: normY,
                        width: normW,
                        height: normH,
                        defined: true
                    };
                    updateAnalogInputs(currentIdx);
                    runAnalogInference(currentIdx);
                    overlays.selectingAnalogIndex = null;
                }
            }

            overlays.dragStart = null;
            overlays.dragEnd = null;

            // Auto-advance to next undefined ROI
            if (currentIdx !== null) {
                if (currentMode === 'marker_select') {
                    // Find next undefined marker
                    const nextIdx = overlays.markers.findIndex((m, i) => i > currentIdx && !m.defined);
                    if (nextIdx !== -1) {
                        selectMarkerInPicture(nextIdx);
                        return;
                    }
                } else if (currentMode === 'digit_select') {
                    // Find next undefined digit
                    const nextIdx = overlays.digits.findIndex((d, i) => i > currentIdx && !d.defined);
                    if (nextIdx !== -1) {
                        selectDigitInPicture(nextIdx);
                        return;
                    }
                } else if (currentMode === 'analog_select') {
                    // Find next undefined analog
                    const nextIdx = overlays.analogs.findIndex((a, i) => i > currentIdx && !a.defined);
                    if (nextIdx !== -1) {
                        selectAnalogInPicture(nextIdx);
                        return;
                    }
                }
            }

            setOverlayMode(null);
        });

        canvas.addEventListener('click', function(event) {
            if (overlays.mode !== 'rotation') return;
            const coords = getCanvasCoordinates(event);
            overlays.crosshair.x = coords.x;
            overlays.crosshair.y = coords.y;
            render();
        });

        // ============================================================
        // Drawing Functions
        // ============================================================
        function drawCrosshair(x, y, color, lineWidth, dashed, showCircle = true) {
            ctx.save();
            ctx.strokeStyle = color;
            ctx.lineWidth = lineWidth;
            if (dashed) ctx.setLineDash([5, 5]);

            ctx.beginPath();
            ctx.moveTo(0, y);
            ctx.lineTo(canvas.width, y);
            ctx.stroke();

            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, canvas.height);
            ctx.stroke();

            if (showCircle) {
                ctx.beginPath();
                ctx.arc(x, y, 10, 0, 2 * Math.PI);
                ctx.stroke();
            }

            ctx.restore();
        }

        function drawBox(x, y, w, h, color, lineWidth, label, drawAlignmentHelpers = false) {
            ctx.save();
            ctx.strokeStyle = color;
            ctx.lineWidth = lineWidth;
            ctx.strokeRect(x, y, w, h);

            if (label) {
                ctx.fillStyle = color;
                ctx.font = 'bold 14px sans-serif';
                ctx.fillText(label, x + 4, y + 16);
            }

            // Draw alignment helpers (circle + crosshair) for analog ROIs
            if (drawAlignmentHelpers) {
                const cx = x + w / 2;
                const cy = y + h / 2;
                const radiusX = w / 2 - 2;
                const radiusY = h / 2 - 2;

                ctx.setLineDash([3, 3]);
                ctx.lineWidth = 1;

                // Draw ellipse inscribed in ROI box
                ctx.beginPath();
                ctx.ellipse(cx, cy, radiusX, radiusY, 0, 0, 2 * Math.PI);
                ctx.stroke();

                // Draw crosshair inside box
                ctx.beginPath();
                ctx.moveTo(x + 2, cy);
                ctx.lineTo(x + w - 2, cy);
                ctx.stroke();

                ctx.beginPath();
                ctx.moveTo(cx, y + 2);
                ctx.lineTo(cx, y + h - 2);
                ctx.stroke();
            }

            ctx.restore();
        }

        function render() {
            if (!imageLoaded) return;

            const rotation = getActiveRotation() * Math.PI / 180;

            // Expand canvas to fit full rotated image (no corner clipping)
            const cos_a = Math.abs(Math.cos(rotation));
            const sin_a = Math.abs(Math.sin(rotation));
            const new_w = Math.ceil(image.width * cos_a + image.height * sin_a);
            const new_h = Math.ceil(image.height * cos_a + image.width * sin_a);

            canvas.width = new_w;
            canvas.height = new_h;

            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.save();
            ctx.translate(canvas.width / 2, canvas.height / 2);
            ctx.rotate(rotation);
            ctx.translate(-image.width / 2, -image.height / 2);
            ctx.drawImage(image, 0, 0);
            ctx.restore();

            // Draw rotation overlays
            if (overlays.mode === 'rotation') {
                if (overlays.crosshair.x !== null && overlays.crosshair.y !== null) {
                    drawCrosshair(overlays.crosshair.x, overlays.crosshair.y, '#00ff00', 2, false);
                }
                if (overlays.mouse.active) {
                    drawCrosshair(overlays.mouse.x, overlays.mouse.y, 'rgba(0, 255, 0, 0.5)', 1, true);
                }
            }

            // Draw crosshair on mouseover in selection modes (no circle)
            if ((overlays.mode === 'marker_select' || overlays.mode === 'digit_select' || overlays.mode === 'analog_select') && overlays.mouse.active && !overlays.dragStart) {
                drawCrosshair(overlays.mouse.x, overlays.mouse.y, 'rgba(255, 255, 255, 0.6)', 1, true, false);
            }

            // Draw marker boxes
            const colors = ['#ff6600', '#0066ff'];
            for (let i = 0; i < 2; i++) {
                const m = overlays.markers[i];
                if (m.defined) {
                    const px = m.x * canvas.width;
                    const py = m.y * canvas.height;
                    const pw = m.width * canvas.width;
                    const ph = m.height * canvas.height;
                    drawBox(px, py, pw, ph, colors[i], 2, `M${i + 1}`);
                }
            }

            // Draw drag selection for markers
            if (overlays.mode === 'marker_select' && overlays.dragStart && overlays.dragEnd) {
                const x1 = Math.min(overlays.dragStart.x, overlays.dragEnd.x);
                const y1 = Math.min(overlays.dragStart.y, overlays.dragEnd.y);
                const w = Math.abs(overlays.dragEnd.x - overlays.dragStart.x);
                const h = Math.abs(overlays.dragEnd.y - overlays.dragStart.y);

                ctx.save();
                ctx.strokeStyle = colors[overlays.selectingMarkerIndex];
                ctx.lineWidth = 2;
                ctx.setLineDash([5, 5]);
                ctx.strokeRect(x1, y1, w, h);
                ctx.restore();
            }

            // Draw digit boxes
            const digitColors = ['#22c55e', '#16a34a', '#15803d', '#166534', '#14532d', '#052e16'];
            for (let i = 0; i < overlays.digits.length; i++) {
                const d = overlays.digits[i];
                if (d.defined) {
                    const px = d.x * canvas.width;
                    const py = d.y * canvas.height;
                    const pw = d.width * canvas.width;
                    const ph = d.height * canvas.height;
                    const color = digitColors[i % digitColors.length];
                    drawBox(px, py, pw, ph, color, 2, `D${i + 1}`);
                }
            }

            // Draw drag selection for digits
            if (overlays.mode === 'digit_select' && overlays.dragStart && overlays.dragEnd) {
                const x1 = Math.min(overlays.dragStart.x, overlays.dragEnd.x);
                const y1 = Math.min(overlays.dragStart.y, overlays.dragEnd.y);
                const w = Math.abs(overlays.dragEnd.x - overlays.dragStart.x);
                const h = Math.abs(overlays.dragEnd.y - overlays.dragStart.y);

                ctx.save();
                ctx.strokeStyle = digitColors[overlays.selectingDigitIndex % digitColors.length];
                ctx.lineWidth = 2;
                ctx.setLineDash([5, 5]);
                ctx.strokeRect(x1, y1, w, h);
                ctx.restore();
            }

            // Draw analog boxes with alignment helpers (circle + crosshair)
            const analogColors = ['#f97316', '#ea580c', '#c2410c', '#9a3412', '#7c2d12', '#431407'];
            for (let i = 0; i < overlays.analogs.length; i++) {
                const a = overlays.analogs[i];
                if (a.defined) {
                    const px = a.x * canvas.width;
                    const py = a.y * canvas.height;
                    const pw = a.width * canvas.width;
                    const ph = a.height * canvas.height;
                    const color = analogColors[i % analogColors.length];
                    drawBox(px, py, pw, ph, color, 2, `A${i + 1}`, true);
                }
            }

            // Draw drag selection for analogs
            if (overlays.mode === 'analog_select' && overlays.dragStart && overlays.dragEnd) {
                const x1 = Math.min(overlays.dragStart.x, overlays.dragEnd.x);
                const y1 = Math.min(overlays.dragStart.y, overlays.dragEnd.y);
                const w = Math.abs(overlays.dragEnd.x - overlays.dragStart.x);
                const h = Math.abs(overlays.dragEnd.y - overlays.dragStart.y);

                ctx.save();
                ctx.strokeStyle = analogColors[overlays.selectingAnalogIndex % analogColors.length];
                ctx.lineWidth = 2;
                ctx.setLineDash([5, 5]);
                ctx.strokeRect(x1, y1, w, h);
                ctx.restore();
            }
        }

        // ============================================================
        // Marker Functions
        // ============================================================
        function selectMarkerInPicture(index) {
            overlays.selectingMarkerIndex = index;
            overlays.dragStart = null;
            overlays.dragEnd = null;
            setOverlayMode('marker_select');
        }

        function updateMarkerInputs(index) {
            const m = overlays.markers[index];
            document.getElementById(`marker-${index + 1}-x`).value = m.x.toFixed(4);
            document.getElementById(`marker-${index + 1}-y`).value = m.y.toFixed(4);
            document.getElementById(`marker-${index + 1}-w`).value = m.width.toFixed(4);
            document.getElementById(`marker-${index + 1}-h`).value = m.height.toFixed(4);
        }

        function updateMarkerFromInput(index) {
            const m = overlays.markers[index];
            m.x = roundToStep(parseFloat(document.getElementById(`marker-${index + 1}-x`).value) || 0);
            m.y = roundToStep(parseFloat(document.getElementById(`marker-${index + 1}-y`).value) || 0);
            m.width = roundToStep(parseFloat(document.getElementById(`marker-${index + 1}-w`).value) || 0.1);
            m.height = roundToStep(parseFloat(document.getElementById(`marker-${index + 1}-h`).value) || 0.1);
            m.defined = true;
            updateMarkerInputs(index);  // Update display with rounded values
            updateMarkerPreview(index);
            render();
        }

        function updateMarkerPreview(index) {
            const m = overlays.markers[index];
            const preview = document.getElementById(`marker-${index + 1}-preview`);

            if (!m.defined || !imageLoaded) {
                preview.innerHTML = '<span class="no-selection">No selection</span>';
                return;
            }

            // Create a temp canvas for the cropped image
            const tempCanvas = document.createElement('canvas');
            const tempCtx = tempCanvas.getContext('2d');

            const rotation = getActiveRotation() * Math.PI / 180;

            // Draw rotated image with expanded canvas (same logic as render())
            const cos_a = Math.abs(Math.cos(rotation));
            const sin_a = Math.abs(Math.sin(rotation));
            const new_w = Math.ceil(image.width * cos_a + image.height * sin_a);
            const new_h = Math.ceil(image.height * cos_a + image.width * sin_a);

            tempCanvas.width = new_w;
            tempCanvas.height = new_h;
            tempCtx.translate(new_w / 2, new_h / 2);
            tempCtx.rotate(rotation);
            tempCtx.translate(-image.width / 2, -image.height / 2);
            tempCtx.drawImage(image, 0, 0);

            // Extract crop using expanded canvas dimensions
            const px = m.x * new_w;
            const py = m.y * new_h;
            const pw = m.width * new_w;
            const ph = m.height * new_h;

            const cropCanvas = document.createElement('canvas');
            cropCanvas.width = pw;
            cropCanvas.height = ph;
            const cropCtx = cropCanvas.getContext('2d');
            cropCtx.drawImage(tempCanvas, px, py, pw, ph, 0, 0, pw, ph);

            preview.innerHTML = `<img src="${cropCanvas.toDataURL()}" alt="Marker ${index + 1}">`;
        }

        async function saveMarkers() {
            // Check both markers are defined
            if (!overlays.markers[0].defined || !overlays.markers[1].defined) {
                alert('Please define both markers first');
                return;
            }

            const btn = document.getElementById('save-markers-btn');
            btn.disabled = true;
            btn.textContent = 'Saving...';

            try {
                const response = await fetch('/api/roi/markers', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        markers: overlays.markers.map(m => ({
                            x: m.x,
                            y: m.y,
                            width: m.width,
                            height: m.height
                        }))
                    })
                });
                const data = await response.json();

                if (data.success) {
                    showMarkersSavedMode();
                } else {
                    alert('Error: ' + data.message);
                }
            } catch (error) {
                alert('Error: ' + error.message);
            } finally {
                btn.disabled = false;
                btn.textContent = 'Save Markers';
            }
        }

        async function restartMarkers() {
            try {
                const response = await fetch('/api/roi/markers', { method: 'DELETE' });
                const data = await response.json();

                if (data.success) {
                    showMarkersEditMode();
                }
            } catch (error) {
                alert('Error: ' + error.message);
            }
        }

        // Change function - keeps previous settings and enters edit mode
        function changeMarkers() {
            // Keep current marker values
            document.getElementById('markers-edit').style.display = 'block';
            document.getElementById('markers-saved').style.display = 'none';
            document.getElementById('markers-disabled').style.display = 'none';
            document.getElementById('step-markers').style.display = 'block';
            document.getElementById('step-markers').classList.remove('roi-step-disabled');
            // Update input fields with current marker values
            for (let i = 0; i < 2; i++) {
                if (overlays.markers[i].defined) {
                    updateMarkerInputs(i);
                    updateMarkerPreview(i);
                }
            }
            updateCompletedSteps();
            render();
        }

        function showMarkersEditMode() {
            document.getElementById('markers-edit').style.display = 'block';
            document.getElementById('markers-saved').style.display = 'none';
            document.getElementById('markers-disabled').style.display = 'none';
            document.getElementById('step-markers').style.display = 'block';
            document.getElementById('step-markers').classList.remove('roi-step-disabled');
            updateCompletedSteps();

            // Auto-activate selection for first undefined marker
            const firstUndefined = overlays.markers.findIndex(m => !m.defined);
            if (firstUndefined !== -1) {
                setTimeout(() => selectMarkerInPicture(firstUndefined), 100);
            }
        }

        function showMarkersSavedMode() {
            document.getElementById('markers-edit').style.display = 'none';
            document.getElementById('markers-saved').style.display = 'none';
            document.getElementById('markers-disabled').style.display = 'none';
            document.getElementById('step-markers').style.display = 'none';
            document.getElementById('step-markers').classList.remove('roi-step-disabled');

            // Enable digits step
            showDigitsEditMode();
            updateCompletedSteps();
        }

        function showMarkersDisabledMode() {
            document.getElementById('markers-edit').style.display = 'none';
            document.getElementById('markers-saved').style.display = 'none';
            document.getElementById('markers-disabled').style.display = 'block';
            document.getElementById('step-markers').style.display = 'block';
            document.getElementById('step-markers').classList.add('roi-step-disabled');
            showDigitsDisabledMode();
            updateCompletedSteps();
        }

        // ============================================================
        // Digit Functions
        // ============================================================
        function getDigitCount() {
            return parseInt(document.getElementById('digits-count').value) || 3;
        }

        function updateDigitBoxes() {
            const count = getDigitCount();
            const container = document.getElementById('digits-container');

            // Resize digits array
            while (overlays.digits.length < count) {
                overlays.digits.push({ x: 0, y: 0, width: 0.1, height: 0.1, defined: false, prediction: null, confidence: null });
            }
            overlays.digits.length = count;

            // Generate HTML for each digit box
            let html = '';
            for (let i = 0; i < count; i++) {
                const placeValue = Math.pow(10, count - 1 - i);
                html += `
                <div class="digit-box" id="digit-${i + 1}-box">
                    <div class="digit-header">
                        <span class="digit-label">Digit ${i + 1} <small>(×${placeValue})</small></span>
                        <button class="btn btn-small btn-secondary" onclick="selectDigitInPicture(${i})">Select in Picture</button>
                    </div>
                    <div class="digit-inputs">
                        <div class="input-row">
                            <label>X:</label>
                            <input type="number" id="digit-${i + 1}-x" min="0" max="1" step="0.005" value="0" onchange="updateDigitFromInput(${i})">
                            <label>Y:</label>
                            <input type="number" id="digit-${i + 1}-y" min="0" max="1" step="0.005" value="0" onchange="updateDigitFromInput(${i})">
                        </div>
                        <div class="input-row">
                            <label>W:</label>
                            <input type="number" id="digit-${i + 1}-w" min="0" max="1" step="0.005" value="0.1" onchange="updateDigitFromInput(${i})">
                            <label>H:</label>
                            <input type="number" id="digit-${i + 1}-h" min="0" max="1" step="0.005" value="0.1" onchange="updateDigitFromInput(${i})">
                        </div>
                    </div>
                    <div class="digit-preview" id="digit-${i + 1}-preview">
                        <span class="no-selection">No selection</span>
                    </div>
                    <div class="digit-result" id="digit-${i + 1}-result" style="display: none;">
                        <span class="result-label">detected</span>
                        <span class="digit-prediction">?</span>
                        <span class="digit-confidence">0%</span>
                    </div>
                </div>`;
            }
            container.innerHTML = html;

            // Update inputs from existing data
            for (let i = 0; i < count; i++) {
                if (overlays.digits[i].defined) {
                    updateDigitInputs(i);
                    updateDigitPreviewFromOverlay(i);
                }
            }

            render();

            // Auto-activate selection for first undefined digit
            const firstUndefined = overlays.digits.findIndex(d => !d.defined);
            if (firstUndefined !== -1) {
                setTimeout(() => selectDigitInPicture(firstUndefined), 100);
            }
        }

        function selectDigitInPicture(index) {
            overlays.selectingDigitIndex = index;
            overlays.dragStart = null;
            overlays.dragEnd = null;
            setOverlayMode('digit_select');
        }

        function updateDigitInputs(index) {
            const d = overlays.digits[index];
            document.getElementById(`digit-${index + 1}-x`).value = d.x.toFixed(4);
            document.getElementById(`digit-${index + 1}-y`).value = d.y.toFixed(4);
            document.getElementById(`digit-${index + 1}-w`).value = d.width.toFixed(4);
            document.getElementById(`digit-${index + 1}-h`).value = d.height.toFixed(4);
        }

        function updateDigitFromInput(index) {
            const d = overlays.digits[index];
            d.x = roundToStep(parseFloat(document.getElementById(`digit-${index + 1}-x`).value) || 0);
            d.y = roundToStep(parseFloat(document.getElementById(`digit-${index + 1}-y`).value) || 0);
            d.width = roundToStep(parseFloat(document.getElementById(`digit-${index + 1}-w`).value) || 0.1);
            d.height = roundToStep(parseFloat(document.getElementById(`digit-${index + 1}-h`).value) || 0.1);
            d.defined = true;
            updateDigitInputs(index);  // Update display with rounded values
            runDigitInference(index);
            render();
        }

        function updateDigitPreviewFromOverlay(index) {
            const d = overlays.digits[index];
            const preview = document.getElementById(`digit-${index + 1}-preview`);
            const result = document.getElementById(`digit-${index + 1}-result`);

            if (!d.defined || !imageLoaded) {
                preview.innerHTML = '<span class="no-selection">No selection</span>';
                result.style.display = 'none';
                return;
            }

            // Create a temp canvas for the cropped image
            const tempCanvas = document.createElement('canvas');
            const tempCtx = tempCanvas.getContext('2d');

            const rotation = getActiveRotation() * Math.PI / 180;

            // Draw rotated image
            tempCanvas.width = canvas.width;
            tempCanvas.height = canvas.height;
            tempCtx.translate(canvas.width / 2, canvas.height / 2);
            tempCtx.rotate(rotation);
            tempCtx.translate(-canvas.width / 2, -canvas.height / 2);
            tempCtx.drawImage(image, 0, 0);

            // Extract crop
            const px = d.x * canvas.width;
            const py = d.y * canvas.height;
            const pw = d.width * canvas.width;
            const ph = d.height * canvas.height;

            const cropCanvas = document.createElement('canvas');
            cropCanvas.width = pw;
            cropCanvas.height = ph;
            const cropCtx = cropCanvas.getContext('2d');
            cropCtx.drawImage(tempCanvas, px, py, pw, ph, 0, 0, pw, ph);

            preview.innerHTML = `<img src="${cropCanvas.toDataURL()}" alt="Digit ${index + 1}">`;

            // Show result if available
            if (d.prediction !== null) {
                result.style.display = 'flex';
                result.querySelector('.digit-prediction').textContent = d.prediction;
                const conf = (d.confidence * 100).toFixed(0);
                result.querySelector('.digit-confidence').textContent = `${conf}%`;
                result.querySelector('.digit-confidence').className = `digit-confidence ${d.confidence >= 0.8 ? 'high' : d.confidence >= 0.5 ? 'medium' : 'low'}`;
            }
        }

        async function runDigitInference(index) {
            const d = overlays.digits[index];
            if (!d.defined) return;

            const result = document.getElementById(`digit-${index + 1}-result`);
            result.style.display = 'flex';
            result.querySelector('.digit-prediction').textContent = '...';
            result.querySelector('.digit-confidence').textContent = '';

            try {
                const response = await fetch('/api/roi/digit-preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        x: d.x,
                        y: d.y,
                        width: d.width,
                        height: d.height
                    })
                });
                const data = await response.json();

                if (data.success) {
                    // Update preview with server-rendered image
                    const preview = document.getElementById(`digit-${index + 1}-preview`);
                    preview.innerHTML = `<img src="data:image/jpeg;base64,${data.image_base64}" alt="Digit ${index + 1}">`;

                    if (data.no_model) {
                        // No model loaded - show neutral "No model" indicator
                        d.prediction = null;
                        d.confidence = null;
                        result.querySelector('.digit-prediction').textContent = 'No model';
                        result.querySelector('.digit-confidence').textContent = '';
                        result.className = 'digit-result no-model';
                    } else {
                        // Model loaded - show prediction and confidence
                        d.prediction = data.prediction;
                        d.confidence = data.confidence;
                        result.querySelector('.digit-prediction').textContent = data.prediction;
                        const conf = (data.confidence * 100).toFixed(0);
                        const confClass = data.confidence >= 0.8 ? 'high' : data.confidence >= 0.5 ? 'medium' : 'low';
                        result.querySelector('.digit-confidence').textContent = `${conf}%`;
                        result.querySelector('.digit-confidence').className = `digit-confidence ${confClass}`;
                        result.className = `digit-result ${confClass}`;
                    }
                } else {
                    result.querySelector('.digit-prediction').textContent = '!';
                    result.querySelector('.digit-confidence').textContent = 'Error';
                    result.className = 'digit-result low';
                }
            } catch (error) {
                console.error('Inference error:', error);
                result.querySelector('.digit-prediction').textContent = '!';
                result.querySelector('.digit-confidence').textContent = 'Error';
            }
        }

        async function saveDigits() {
            const count = getDigitCount();

            // Check all digits are defined
            for (let i = 0; i < count; i++) {
                if (!overlays.digits[i].defined) {
                    alert(`Please define Digit ${i + 1} first`);
                    return;
                }
            }

            const btn = document.getElementById('save-digits-btn');
            btn.disabled = true;
            btn.textContent = 'Saving...';

            try {
                const response = await fetch('/api/roi/digits', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        count: count,
                        rois: overlays.digits.slice(0, count).map(d => ({
                            x: d.x,
                            y: d.y,
                            width: d.width,
                            height: d.height
                        }))
                    })
                });
                const data = await response.json();

                if (data.success) {
                    showDigitsSavedMode();
                } else {
                    alert('Error: ' + data.message);
                }
            } catch (error) {
                alert('Error: ' + error.message);
            } finally {
                btn.disabled = false;
                btn.textContent = 'Save Digits';
            }
        }

        async function restartDigits() {
            try {
                const response = await fetch('/api/roi/digits', { method: 'DELETE' });
                const data = await response.json();

                if (data.success) {
                    overlays.digits = [];
                    showDigitsEditMode();
                }
            } catch (error) {
                alert('Error: ' + error.message);
            }
        }

        // Change function - keeps previous settings and enters edit mode
        function changeDigits() {
            // Keep current digit values
            document.getElementById('digits-edit').style.display = 'block';
            document.getElementById('digits-saved').style.display = 'none';
            document.getElementById('digits-disabled').style.display = 'none';
            document.getElementById('step-digits').style.display = 'block';
            document.getElementById('step-digits').classList.remove('roi-step-disabled');
            // Set the count selector to match current digits
            document.getElementById('digits-count').value = overlays.digits.length;
            updateDigitBoxes();
            updateCompletedSteps();
            render();
        }

        function showDigitsEditMode() {
            document.getElementById('digits-edit').style.display = 'block';
            document.getElementById('digits-saved').style.display = 'none';
            document.getElementById('digits-disabled').style.display = 'none';
            document.getElementById('step-digits').style.display = 'block';
            document.getElementById('step-digits').classList.remove('roi-step-disabled');
            updateDigitBoxes();
            updateCompletedSteps();
        }

        function showDigitsSavedMode() {
            document.getElementById('digits-edit').style.display = 'none';
            document.getElementById('digits-saved').style.display = 'none';
            document.getElementById('digits-disabled').style.display = 'none';
            document.getElementById('step-digits').style.display = 'none';
            document.getElementById('step-digits').classList.remove('roi-step-disabled');
            // Enable analog ROIs step
            showAnalogsEditMode();
            updateCompletedSteps();
        }

        function showDigitsDisabledMode() {
            document.getElementById('digits-edit').style.display = 'none';
            document.getElementById('digits-saved').style.display = 'none';
            document.getElementById('digits-disabled').style.display = 'block';
            document.getElementById('step-digits').style.display = 'block';
            document.getElementById('step-digits').classList.add('roi-step-disabled');
            showAnalogsDisabledMode();
            updateCompletedSteps();
        }

        // ============================================================
        // Completed Steps Container
        // ============================================================
        function updateCompletedSteps() {
            const container = document.getElementById('completed-steps');
            let html = '';

            // Step 1: Image Correction
            if (savedRotation !== null) {
                html += `
                <div class="completed-step">
                    <div class="completed-step-header">
                        <span class="completed-step-title">Step 1: Image Correction</span>
                        <button class="btn btn-small btn-secondary" onclick="changeRotation()">Change</button>
                    </div>
                    <div class="completed-step-data">
                        <span class="completed-value">Lens: ${(savedFisheye || 0).toFixed(2)}, Rotation: ${savedRotation.toFixed(1)}°</span>
                    </div>
                </div>`;
            }

            // Step 2: Markers
            const markersCompleted = overlays.markers[0].defined && overlays.markers[1].defined &&
                                      document.getElementById('step-markers').style.display === 'none';
            if (markersCompleted) {
                const t = Date.now();
                html += `
                <div class="completed-step">
                    <div class="completed-step-header">
                        <span class="completed-step-title">Step 2: Markers</span>
                        <button class="btn btn-small btn-secondary" onclick="changeMarkers()">Change</button>
                    </div>
                    <div class="completed-step-data completed-step-images">
                        <img src="/api/roi/marker-image/1?t=${t}" alt="M1" onerror="this.style.display='none'">
                        <img src="/api/roi/marker-image/2?t=${t}" alt="M2" onerror="this.style.display='none'">
                    </div>
                </div>`;
            }

            // Step 3: Digits
            const digitsCompleted = overlays.digits.length > 0 &&
                                    overlays.digits.every(d => d.defined) &&
                                    document.getElementById('step-digits').style.display === 'none';
            if (digitsCompleted) {
                const count = overlays.digits.length;
                const t = Date.now();
                let digitsHtml = '';
                for (let i = 0; i < count; i++) {
                    const d = overlays.digits[i];
                    digitsHtml += `
                    <div class="completed-digit">
                        <img src="/api/roi/digit-image/${i + 1}?t=${t}" alt="D${i + 1}" onerror="this.style.display='none'">
                        <span class="completed-digit-value">${d.prediction || '?'}</span>
                    </div>`;
                }
                html += `
                <div class="completed-step">
                    <div class="completed-step-header">
                        <span class="completed-step-title">Step 3: Digits</span>
                        <button class="btn btn-small btn-secondary" onclick="changeDigits()">Change</button>
                    </div>
                    <div class="completed-step-data completed-step-digits">
                        ${digitsHtml}
                    </div>
                </div>`;
            }

            // Step 4: Analogs
            const analogsCompleted = overlays.analogs.length > 0 &&
                                     overlays.analogs.every(a => a.defined) &&
                                     document.getElementById('step-analogs').style.display === 'none';
            if (analogsCompleted) {
                const count = overlays.analogs.length;
                const t = Date.now();
                let analogsHtml = '';
                for (let i = 0; i < count; i++) {
                    const a = overlays.analogs[i];
                    analogsHtml += `
                    <div class="completed-analog">
                        <img src="/api/roi/analog-image/${i + 1}?t=${t}" alt="A${i + 1}" onerror="this.style.display='none'">
                        <span class="completed-analog-value">${a.prediction || '?'}</span>
                    </div>`;
                }
                html += `
                <div class="completed-step completed-step-analog">
                    <div class="completed-step-header">
                        <span class="completed-step-title">Step 4: Analogs</span>
                        <button class="btn btn-small btn-secondary" onclick="changeAnalogs()">Change</button>
                    </div>
                    <div class="completed-step-data completed-step-analogs">
                        ${analogsHtml}
                    </div>
                </div>`;
            }

            container.innerHTML = html;
            container.style.display = html ? 'flex' : 'none';
        }

        // ============================================================
        // Analog Functions
        // ============================================================
        function getAnalogCount() {
            return parseInt(document.getElementById('analogs-count').value) || 4;
        }

        function updateAnalogBoxes() {
            const count = getAnalogCount();
            const container = document.getElementById('analogs-container');

            // Resize analogs array
            while (overlays.analogs.length < count) {
                overlays.analogs.push({ x: 0, y: 0, width: 0.1, height: 0.1, defined: false, prediction: null, confidence: null });
            }
            overlays.analogs.length = count;

            // Generate HTML for each analog box
            let html = '';
            for (let i = 0; i < count; i++) {
                const placeValue = Math.pow(10, -(i + 1));
                const placeLabel = '×0.' + '0'.repeat(i) + '1';
                html += `
                <div class="analog-box" id="analog-${i + 1}-box">
                    <div class="analog-header">
                        <span class="analog-label">Analog ${i + 1} <small>(${placeLabel})</small></span>
                        <button class="btn btn-small btn-secondary" onclick="selectAnalogInPicture(${i})">Select in Picture</button>
                    </div>
                    <div class="analog-inputs">
                        <div class="input-row">
                            <label>X:</label>
                            <input type="number" id="analog-${i + 1}-x" min="0" max="1" step="0.005" value="0" onchange="updateAnalogFromInput(${i})">
                            <label>Y:</label>
                            <input type="number" id="analog-${i + 1}-y" min="0" max="1" step="0.005" value="0" onchange="updateAnalogFromInput(${i})">
                        </div>
                        <div class="input-row">
                            <label>W:</label>
                            <input type="number" id="analog-${i + 1}-w" min="0" max="1" step="0.005" value="0.1" onchange="updateAnalogFromInput(${i})">
                            <label>H:</label>
                            <input type="number" id="analog-${i + 1}-h" min="0" max="1" step="0.005" value="0.1" onchange="updateAnalogFromInput(${i})">
                        </div>
                    </div>
                    <div class="analog-preview" id="analog-${i + 1}-preview">
                        <span class="no-selection">No selection</span>
                    </div>
                    <div class="analog-result" id="analog-${i + 1}-result" style="display: none;">
                        <span class="result-label">detected</span>
                        <span class="analog-prediction">?</span>
                        <span class="analog-confidence">0%</span>
                    </div>
                </div>`;
            }
            container.innerHTML = html;

            // Update inputs from existing data
            for (let i = 0; i < count; i++) {
                if (overlays.analogs[i].defined) {
                    updateAnalogInputs(i);
                    updateAnalogPreviewFromOverlay(i);
                }
            }

            render();

            // Auto-activate selection for first undefined analog
            const firstUndefined = overlays.analogs.findIndex(a => !a.defined);
            if (firstUndefined !== -1) {
                setTimeout(() => selectAnalogInPicture(firstUndefined), 100);
            }
        }

        function selectAnalogInPicture(index) {
            overlays.selectingAnalogIndex = index;
            overlays.dragStart = null;
            overlays.dragEnd = null;
            setOverlayMode('analog_select');
        }

        function updateAnalogInputs(index) {
            const a = overlays.analogs[index];
            document.getElementById(`analog-${index + 1}-x`).value = a.x.toFixed(4);
            document.getElementById(`analog-${index + 1}-y`).value = a.y.toFixed(4);
            document.getElementById(`analog-${index + 1}-w`).value = a.width.toFixed(4);
            document.getElementById(`analog-${index + 1}-h`).value = a.height.toFixed(4);
        }

        function updateAnalogFromInput(index) {
            const a = overlays.analogs[index];
            a.x = roundToStep(parseFloat(document.getElementById(`analog-${index + 1}-x`).value) || 0);
            a.y = roundToStep(parseFloat(document.getElementById(`analog-${index + 1}-y`).value) || 0);
            a.width = roundToStep(parseFloat(document.getElementById(`analog-${index + 1}-w`).value) || 0.1);
            a.height = roundToStep(parseFloat(document.getElementById(`analog-${index + 1}-h`).value) || 0.1);
            a.defined = true;
            updateAnalogInputs(index);  // Update display with rounded values
            runAnalogInference(index);
            render();
        }

        function updateAnalogPreviewFromOverlay(index) {
            const a = overlays.analogs[index];
            const preview = document.getElementById(`analog-${index + 1}-preview`);
            const result = document.getElementById(`analog-${index + 1}-result`);

            if (!a.defined || !imageLoaded) {
                preview.innerHTML = '<span class="no-selection">No selection</span>';
                result.style.display = 'none';
                return;
            }

            // Create a temp canvas for the cropped image
            const tempCanvas = document.createElement('canvas');
            const tempCtx = tempCanvas.getContext('2d');

            const rotation = getActiveRotation() * Math.PI / 180;

            // Draw rotated image
            tempCanvas.width = canvas.width;
            tempCanvas.height = canvas.height;
            tempCtx.translate(canvas.width / 2, canvas.height / 2);
            tempCtx.rotate(rotation);
            tempCtx.translate(-canvas.width / 2, -canvas.height / 2);
            tempCtx.drawImage(image, 0, 0);

            // Extract crop
            const px = a.x * canvas.width;
            const py = a.y * canvas.height;
            const pw = a.width * canvas.width;
            const ph = a.height * canvas.height;

            const cropCanvas = document.createElement('canvas');
            cropCanvas.width = pw;
            cropCanvas.height = ph;
            const cropCtx = cropCanvas.getContext('2d');
            cropCtx.drawImage(tempCanvas, px, py, pw, ph, 0, 0, pw, ph);

            preview.innerHTML = `<img src="${cropCanvas.toDataURL()}" alt="Analog ${index + 1}">`;

            // Show result if available
            if (a.prediction !== null) {
                result.style.display = 'flex';
                result.querySelector('.analog-prediction').textContent = a.prediction;
                const conf = (a.confidence * 100).toFixed(0);
                result.querySelector('.analog-confidence').textContent = `${conf}%`;
                result.querySelector('.analog-confidence').className = `analog-confidence ${a.confidence >= 0.8 ? 'high' : a.confidence >= 0.5 ? 'medium' : 'low'}`;
            }
        }

        async function runAnalogInference(index) {
            const a = overlays.analogs[index];
            if (!a.defined) return;

            const result = document.getElementById(`analog-${index + 1}-result`);
            result.style.display = 'flex';
            result.querySelector('.analog-prediction').textContent = '...';
            result.querySelector('.analog-confidence').textContent = '';

            try {
                const response = await fetch('/api/roi/analog-preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        x: a.x,
                        y: a.y,
                        width: a.width,
                        height: a.height
                    })
                });
                const data = await response.json();

                if (data.success) {
                    // Update preview with server-rendered image
                    const preview = document.getElementById(`analog-${index + 1}-preview`);
                    preview.innerHTML = `<img src="data:image/jpeg;base64,${data.image_base64}" alt="Analog ${index + 1}">`;

                    if (data.no_model) {
                        // No model loaded - show neutral "No model" indicator
                        a.prediction = null;
                        a.confidence = null;
                        result.querySelector('.analog-prediction').textContent = 'No model';
                        result.querySelector('.analog-confidence').textContent = '';
                        result.className = 'analog-result no-model';
                    } else {
                        // Model loaded - show prediction and confidence
                        a.prediction = data.prediction;
                        a.confidence = data.confidence;
                        result.querySelector('.analog-prediction').textContent = data.prediction;
                        const conf = (data.confidence * 100).toFixed(0);
                        const confClass = data.confidence >= 0.8 ? 'high' : data.confidence >= 0.5 ? 'medium' : 'low';
                        result.querySelector('.analog-confidence').textContent = `${conf}%`;
                        result.querySelector('.analog-confidence').className = `analog-confidence ${confClass}`;
                        result.className = `analog-result ${confClass}`;
                    }
                } else {
                    result.querySelector('.analog-prediction').textContent = '!';
                    result.querySelector('.analog-confidence').textContent = 'Error';
                    result.className = 'analog-result low';
                }
            } catch (error) {
                console.error('Inference error:', error);
                result.querySelector('.analog-prediction').textContent = '!';
                result.querySelector('.analog-confidence').textContent = 'Error';
            }
        }

        async function saveAnalogs() {
            const count = getAnalogCount();

            // Check all analogs are defined
            for (let i = 0; i < count; i++) {
                if (!overlays.analogs[i].defined) {
                    alert(`Please define Analog ${i + 1} first`);
                    return;
                }
            }

            const btn = document.getElementById('save-analogs-btn');
            btn.disabled = true;
            btn.textContent = 'Saving...';

            try {
                const response = await fetch('/api/roi/analogs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        count: count,
                        rois: overlays.analogs.slice(0, count).map(a => ({
                            x: a.x,
                            y: a.y,
                            width: a.width,
                            height: a.height
                        }))
                    })
                });
                const data = await response.json();

                if (data.success) {
                    showAnalogsSavedMode();
                } else {
                    alert('Error: ' + data.message);
                }
            } catch (error) {
                alert('Error: ' + error.message);
            } finally {
                btn.disabled = false;
                btn.textContent = 'Save Analogs';
            }
        }

        async function restartAnalogs() {
            try {
                const response = await fetch('/api/roi/analogs', { method: 'DELETE' });
                const data = await response.json();

                if (data.success) {
                    overlays.analogs = [];
                    showAnalogsEditMode();
                }
            } catch (error) {
                alert('Error: ' + error.message);
            }
        }

        // Change function - keeps previous settings and enters edit mode
        function changeAnalogs() {
            // Keep current analog values
            document.getElementById('analogs-edit').style.display = 'block';
            document.getElementById('analogs-saved').style.display = 'none';
            document.getElementById('analogs-disabled').style.display = 'none';
            document.getElementById('step-analogs').style.display = 'block';
            document.getElementById('step-analogs').classList.remove('roi-step-disabled');
            // Set the count selector to match current analogs
            document.getElementById('analogs-count').value = overlays.analogs.length;
            updateAnalogBoxes();
            updateCompletedSteps();
            render();
        }

        function showAnalogsEditMode() {
            document.getElementById('analogs-edit').style.display = 'block';
            document.getElementById('analogs-saved').style.display = 'none';
            document.getElementById('analogs-disabled').style.display = 'none';
            document.getElementById('step-analogs').style.display = 'block';
            document.getElementById('step-analogs').classList.remove('roi-step-disabled');
            updateAnalogBoxes();
            updateCompletedSteps();
        }

        function showAnalogsSavedMode() {
            document.getElementById('analogs-edit').style.display = 'none';
            document.getElementById('analogs-saved').style.display = 'none';
            document.getElementById('analogs-disabled').style.display = 'none';
            document.getElementById('step-analogs').style.display = 'none';
            document.getElementById('step-analogs').classList.remove('roi-step-disabled');
            updateCompletedSteps();

            // In setup mode, show Step 5 (MQTT/HA config) next
            if (window.setupMode) {
                const el = document.getElementById('step-mqtt');
                if (el) el.style.display = 'block';
            }
        }

        function showAnalogsDisabledMode() {
            document.getElementById('analogs-edit').style.display = 'none';
            document.getElementById('analogs-saved').style.display = 'none';
            document.getElementById('analogs-disabled').style.display = 'block';
            document.getElementById('step-analogs').style.display = 'block';
            document.getElementById('step-analogs').classList.add('roi-step-disabled');
            updateCompletedSteps();
        }

        // ============================================================
        // Rotation Controls
        // ============================================================
        const rotationCoarse = document.getElementById('rotation-coarse');
        const rotationFine = document.getElementById('rotation-fine');
        const totalRotationDisplay = document.getElementById('total-rotation');
        const rotationEdit = document.getElementById('rotation-edit');
        const rotationSaved = document.getElementById('rotation-saved');
        const savedRotationValue = document.getElementById('saved-rotation-value');

        let savedRotation = null;

        // Fisheye correction
        const fisheyeSlider = document.getElementById('fisheye-slider');
        const fisheyeValueDisplay = document.getElementById('fisheye-value');
        const savedFisheyeDisplay = document.getElementById('saved-fisheye-value');

        let savedFisheye = null;
        let fisheyeDebounceTimer = null;

        function getTotalRotation() {
            const coarse = parseFloat(rotationCoarse.value) || 0;
            const fine = parseFloat(rotationFine.value) || 0;
            return coarse + fine;
        }

        function getActiveRotation() {
            if (savedRotation !== null) return savedRotation;
            return getTotalRotation();
        }

        function getActiveFisheye() {
            if (savedFisheye !== null) return savedFisheye;
            return parseFloat(fisheyeSlider.value) || 0;
        }

        function updateFisheyeValue() {
            const val = parseFloat(fisheyeSlider.value) || 0;
            fisheyeValueDisplay.textContent = val.toFixed(2);

            // Debounced server-side preview
            clearTimeout(fisheyeDebounceTimer);
            fisheyeDebounceTimer = setTimeout(() => fetchFisheyePreview(val), 300);
        }

        async function fetchFisheyePreview(k1) {
            try {
                const response = await fetch('/api/roi/fisheye-preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ fisheye_correction: k1 })
                });
                const data = await response.json();
                if (data.success && data.image) {
                    const newImg = new Image();
                    newImg.onload = () => {
                        image = newImg;
                        imageLoaded = true;
                        render();
                    };
                    newImg.src = data.image;
                }
            } catch (error) {
                console.error('Fisheye preview error:', error);
            }
        }

        function resetFisheyeSlider() {
            fisheyeSlider.value = 0;
            updateFisheyeValue();
        }

        function updateTotalRotation() {
            const total = getTotalRotation();
            totalRotationDisplay.textContent = total.toFixed(1);
            render();
        }

        function showRotationEditMode() {
            rotationEdit.style.display = 'block';
            rotationSaved.style.display = 'none';
            document.getElementById('step-rotation').style.display = 'block';
            savedRotation = null;
            setOverlayMode('rotation');
            showMarkersDisabledMode();
            updateCompletedSteps();
        }

        function showRotationSavedMode() {
            rotationEdit.style.display = 'none';
            rotationSaved.style.display = 'none';
            document.getElementById('step-rotation').style.display = 'none';
            savedRotationValue.textContent = savedRotation.toFixed(1);
            if (savedFisheyeDisplay) {
                savedFisheyeDisplay.textContent = (savedFisheye || 0).toFixed(2);
            }
            setOverlayMode(null);
            render();
            updateCompletedSteps();
        }

        async function saveRotation() {
            const total = getTotalRotation();
            const fisheye = parseFloat(fisheyeSlider.value) || 0;
            const btn = document.getElementById('save-rotation-btn');
            btn.disabled = true;
            btn.textContent = 'Saving...';

            try {
                // Save fisheye if non-zero, or delete any previously stored value
                if (fisheye !== 0) {
                    const fisheyeResp = await fetch('/api/roi/fisheye', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ fisheye_correction: fisheye })
                    });
                    const fisheyeData = await fisheyeResp.json();
                    if (!fisheyeData.success) {
                        alert('Error saving fisheye: ' + fisheyeData.message);
                        return;
                    }
                } else {
                    // Remove any previously stored fisheye value
                    await fetch('/api/roi/fisheye', { method: 'DELETE' });
                }

                // Save rotation
                const response = await fetch('/api/roi/rotation', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ rotation: total })
                });
                const data = await response.json();

                if (data.success) {
                    savedRotation = total;
                    savedFisheye = fisheye;
                    showRotationSavedMode();
                    showMarkersEditMode();
                } else {
                    alert('Error: ' + data.message);
                }
            } catch (error) {
                alert('Error: ' + error.message);
            } finally {
                btn.disabled = false;
                btn.textContent = 'Save';
            }
        }

        async function restartRotation() {
            try {
                // Delete both fisheye and rotation
                await fetch('/api/roi/fisheye', { method: 'DELETE' });
                const response = await fetch('/api/roi/rotation', { method: 'DELETE' });
                const data = await response.json();

                if (data.success) {
                    const total = savedRotation || 0;
                    rotationCoarse.value = Math.trunc(total);
                    rotationFine.value = (total - Math.trunc(total)).toFixed(1);
                    updateTotalRotation();

                    // Reset fisheye slider to zero (clear stale savedFisheye state)
                    savedFisheye = null;
                    fisheyeSlider.value = 0;
                    fisheyeValueDisplay.textContent = '0.00';

                    // Reload original image (no fisheye applied)
                    const origImg = new Image();
                    origImg.onload = () => {
                        image = origImg;
                        imageLoaded = true;
                        render();
                    };
                    origImg.src = '/api/roi/reference-image?' + Date.now();

                    showRotationEditMode();
                }
            } catch (error) {
                alert('Error: ' + error.message);
            }
        }

        // Change function - keeps previous settings and enters edit mode
        function changeRotation() {
            const total = savedRotation || 0;
            rotationCoarse.value = Math.trunc(total);
            rotationFine.value = (total - Math.trunc(total)).toFixed(1);
            savedRotation = null;
            updateTotalRotation();

            // Restore fisheye slider
            fisheyeSlider.value = savedFisheye || 0;
            savedFisheye = null;
            updateFisheyeValue();

            rotationEdit.style.display = 'block';
            rotationSaved.style.display = 'none';
            document.getElementById('step-rotation').style.display = 'block';
            setOverlayMode('rotation');
            // Keep markers/digits/analogs visible but will need to re-save rotation first
            updateCompletedSteps();
        }

        rotationCoarse.addEventListener('input', updateTotalRotation);
        rotationFine.addEventListener('input', updateTotalRotation);
        fisheyeSlider.addEventListener('input', updateFisheyeValue);

        // ============================================================
        // Image Loading
        // ============================================================
        async function fetchAndReload() {
            reloadBtn.disabled = true;
            reloadBtn.textContent = 'Loading...';
            loadingIndicator.style.display = 'block';
            loadingIndicator.textContent = 'Fetching image from remote...';
            loadingIndicator.style.color = 'white';

            try {
                const response = await fetch('/api/roi/fetch-image', { method: 'POST' });
                const data = await response.json();

                if (data.success) {
                    loadImage();
                } else {
                    loadingIndicator.textContent = 'Error: ' + data.message;
                    loadingIndicator.style.color = 'var(--danger)';
                }
            } catch (error) {
                loadingIndicator.textContent = 'Error: ' + error.message;
                loadingIndicator.style.color = 'var(--danger)';
            } finally {
                reloadBtn.disabled = false;
                reloadBtn.textContent = 'Reload Image';
            }
        }

        function loadImage() {
            loadingIndicator.style.display = 'block';
            loadingIndicator.textContent = 'Loading image...';
            loadingIndicator.style.color = 'white';

            image = new Image();

            image.onload = function() {
                imageLoaded = true;
                loadingIndicator.style.display = 'none';
                canvas.width = image.width;
                canvas.height = image.height;
                // Re-apply fisheye correction if saved
                const activeFisheye = getActiveFisheye();
                if (activeFisheye !== 0) {
                    fetchFisheyePreview(activeFisheye);
                } else {
                    render();
                }
            };

            image.onerror = function() {
                loadingIndicator.textContent = 'No image available. Click "Reload Image" to fetch.';
                loadingIndicator.style.color = 'var(--warning)';
            };

            image.src = '/api/roi/reference-image?t=' + Date.now();
        }

        // ============================================================
        // Config Loading
        // ============================================================
        async function loadConfig() {
            try {
                const response = await fetch('/api/roi/config');
                const data = await response.json();

                // Fisheye correction
                if (data.fisheye_correction !== null && data.fisheye_correction !== undefined) {
                    savedFisheye = data.fisheye_correction;
                    fisheyeSlider.value = data.fisheye_correction;
                    fisheyeValueDisplay.textContent = data.fisheye_correction.toFixed(2);
                }

                // Rotation
                if (data.rotation !== null && data.rotation !== undefined) {
                    savedRotation = data.rotation;
                    showRotationSavedMode();

                    // Markers
                    if (data.markers && data.markers.length === 2) {
                        overlays.markers = data.markers.map(m => ({ ...m, defined: true }));

                        // Digits
                        if (data.digits && data.digits.rois && data.digits.rois.length > 0) {
                            document.getElementById('digits-count').value = data.digits.count;
                            overlays.digits = data.digits.rois.map(r => ({
                                ...r,
                                defined: true,
                                prediction: null,
                                confidence: null
                            }));

                            // Analogs
                            if (data.analogs && data.analogs.rois && data.analogs.rois.length > 0) {
                                document.getElementById('analogs-count').value = data.analogs.count;
                                overlays.analogs = data.analogs.rois.map(r => ({
                                    ...r,
                                    defined: true,
                                    prediction: null,
                                    confidence: null
                                }));
                                showMarkersSavedMode();
                                showDigitsSavedMode();
                                showAnalogsSavedMode();

                                // Run inference on all digits and analogs
                                for (let i = 0; i < overlays.digits.length; i++) {
                                    runDigitInference(i);
                                }
                                for (let i = 0; i < overlays.analogs.length; i++) {
                                    runAnalogInference(i);
                                }
                            } else {
                                showMarkersSavedMode();
                                showDigitsSavedMode();

                                // Run inference on all digits
                                for (let i = 0; i < overlays.digits.length; i++) {
                                    runDigitInference(i);
                                }
                            }
                        } else {
                            showMarkersSavedMode();
                        }
                    } else {
                        showMarkersEditMode();
                    }
                } else {
                    showRotationEditMode();
                }
            } catch (error) {
                console.error('Error loading config:', error);
                showRotationEditMode();
            }

            // Step 5: MQTT is always visible in normal mode
            var stepMqtt = document.getElementById('step-mqtt');
            if (stepMqtt) {
                stepMqtt.style.display = 'block';
            }
        }

        // ============================================================
        // Initialize
        // ============================================================

        // In setup mode, if Step 0 is visible (no image source yet),
        // hide the canvas and subsequent steps until Step 0 completes.
        if (window.setupMode) {
            const step0 = document.getElementById('step-image-source');
            if (step0 && step0.style.display !== 'none') {
                // Step 0 is showing - hide canvas and wizard steps until image source is set
                document.querySelector('.canvas-container').style.display = 'none';
                document.getElementById('step-rotation').style.display = 'none';
                document.getElementById('step-markers').style.display = 'none';
                document.getElementById('step-digits').style.display = 'none';
                document.getElementById('step-analogs').style.display = 'none';
                document.getElementById('step-mqtt').style.display = 'none';

                // Override fetchAndReload to also reveal the wizard steps after image loads
                const _originalFetchAndReload = fetchAndReload;
                fetchAndReload = async function() {
                    await _originalFetchAndReload();
                    document.querySelector('.canvas-container').style.display = '';
                    document.getElementById('step-rotation').style.display = '';
                    document.getElementById('step-markers').style.display = '';
                    document.getElementById('step-digits').style.display = '';
                    document.getElementById('step-analogs').style.display = '';
                    loadConfig();
                };
            }
        }

        loadConfig();
        loadImage();
