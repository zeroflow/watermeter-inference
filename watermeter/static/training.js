// HTML escaping to prevent XSS when inserting dynamic text
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// State
let currentJobId = null;
let currentBenchmarkJobId = null;
let pollingInterval = null;
let allModels = { digits: [], arrows: [] };
let activeModels = { digits: null, arrows: null };
let currentFilter = 'all';
let sortColumn = localStorage.getItem('modelSortCol') || 'accuracy';
let sortAsc = localStorage.getItem('modelSortAsc') === 'true';

// Initialize
window.addEventListener('load', function() {
    loadTrainingStats();
    loadModels();
    pollTrainingStatus();

    // Start polling (only when tab is visible)
    pollingInterval = setInterval(pollTrainingStatus, 2000);

    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            clearInterval(pollingInterval);
            pollingInterval = null;
        } else {
            pollTrainingStatus();
            pollingInterval = setInterval(pollTrainingStatus, 2000);
        }
    });
});

// Show message
function showMessage(text, type) {
    const message = document.getElementById('message');
    message.textContent = text;
    message.className = 'message ' + type;

    setTimeout(() => {
        message.className = 'message';
    }, 5000);
}

// Model type change handler
function onModelTypeChange() {
    const modelType = document.getElementById('model-type').value;
    const stepSizeGroup = document.getElementById('step-size-group');
    const trainingModeGroup = document.getElementById('training-mode-group');
    stepSizeGroup.style.display = modelType === 'arrows' ? '' : 'none';
    trainingModeGroup.style.display = modelType === 'arrows' ? '' : 'none';
}

// Architecture combobox
const CURATED_MODELS = [
    { group: 'Lightweight', models: [
        { value: 'resnet18', label: 'ResNet-18', params: '12M' },
        { value: 'efficientnet_lite0', label: 'EfficientNet-Lite0', params: '4.7M' },
        { value: 'mobilenetv3_small_100', label: 'MobileNetV3 Small', params: '2.5M' },
    ]},
    { group: 'Medium', models: [
        { value: 'efficientnetv2_rw_t', label: 'EfficientNetV2-RW Tiny', params: '14M' },
        { value: 'efficientnetv2_rw_s', label: 'EfficientNetV2-RW Small', params: '24M' },
        { value: 'convnext_nano', label: 'ConvNeXt Nano', params: '16M' },
    ]},
    { group: 'Heavy', models: [
        { value: 'resnext50_32x4d', label: 'ResNeXt-50 32×4d', params: '25M' },
        { value: 'convnextv2_tiny', label: 'ConvNeXt-V2 Tiny', params: '29M' },
        { value: 'efficientnetv2_rw_m', label: 'EfficientNetV2-RW Medium', params: '53M' },
    ]},
];

// Build a lookup from value -> label for curated models
const CURATED_LABELS = {};
CURATED_MODELS.forEach(g => g.models.forEach(m => CURATED_LABELS[m.value] = m.label));

// Convert a timm model name to a human-friendly display name
// e.g. "convnextv2_base.fcmae_ft_in22k_in1k" -> "ConvNeXtV2 Base"
function prettifyModelName(timmName) {
    // Check curated labels first
    if (CURATED_LABELS[timmName]) return CURATED_LABELS[timmName];

    // Strip training recipe suffix (everything after first dot)
    let name = timmName.split('.')[0];

    // Known family mappings (order matters — check longer prefixes first)
    const families = [
        ['efficientnetv2_rw_', 'EfficientNetV2-RW '],
        ['efficientnet_lite', 'EfficientNet-Lite'],
        ['efficientnetv2_', 'EfficientNetV2-'],
        ['efficientnet_', 'EfficientNet-'],
        ['mobilenetv3_', 'MobileNetV3 '],
        ['mobilenetv2_', 'MobileNetV2 '],
        ['mobileone_', 'MobileOne '],
        ['convnextv2_', 'ConvNeXt-V2 '],
        ['convnext_', 'ConvNeXt '],
        ['resnext50_', 'ResNeXt-50 '],
        ['resnext101_', 'ResNeXt-101 '],
        ['resnetv2_', 'ResNetV2-'],
        ['resnet', 'ResNet-'],
        ['regnetx_', 'RegNetX-'],
        ['regnety_', 'RegNetY-'],
        ['densenet', 'DenseNet-'],
        ['ghostnet_', 'GhostNet '],
        ['edgenext_', 'EdgeNeXt '],
        ['lcnet_', 'LCNet '],
        ['tinynet_', 'TinyNet-'],
        ['mnasnet_', 'MnasNet '],
        ['seresnext', 'SE-ResNeXt-'],
    ];

    for (const [prefix, label] of families) {
        if (name.startsWith(prefix)) {
            let suffix = name.slice(prefix.length);
            // Capitalize first letter of suffix
            suffix = suffix.charAt(0).toUpperCase() + suffix.slice(1);
            // Replace underscores with spaces
            suffix = suffix.replace(/_/g, ' ');
            return label + suffix;
        }
    }

    // Fallback: capitalize first char, replace underscores with spaces
    name = name.replace(/_/g, ' ');
    return name.charAt(0).toUpperCase() + name.slice(1);
}

function initArchCombobox() {
    const input = document.getElementById('arch-search');
    const hidden = document.getElementById('architecture');
    const dropdown = document.getElementById('arch-dropdown');
    let debounceTimer = null;

    function showCurated(filter = '') {
        let html = '';
        const lf = filter.toLowerCase();
        CURATED_MODELS.forEach(group => {
            const filtered = lf
                ? group.models.filter(m => m.label.toLowerCase().includes(lf) || m.value.includes(lf))
                : group.models;
            if (filtered.length === 0) return;
            html += `<div class="arch-group-label">${group.group}</div>`;
            filtered.forEach(m => {
                const sub = m.params ? `${m.params} · ${m.value}` : m.value;
                html += `<div class="arch-option" data-value="${m.value}" data-label="${m.label}"><span class="arch-label">${m.label}</span><span class="arch-sub">${sub}</span></div>`;
            });
        });
        if (!html && lf) return false; // no curated matches
        dropdown.innerHTML = html;
        dropdown.classList.add('open');
        return true;
    }

    async function searchTimm(query) {
        try {
            const resp = await fetch(`/api/models/architectures?q=${encodeURIComponent(query)}`);
            const models = await resp.json();
            if (input.value !== query) return; // stale response
            let html = '';
            // Show curated matches first
            const lf = query.toLowerCase();
            let curatedHtml = '';
            CURATED_MODELS.forEach(group => {
                const filtered = group.models.filter(m => m.label.toLowerCase().includes(lf) || m.value.includes(lf));
                if (filtered.length === 0) return;
                curatedHtml += `<div class="arch-group-label">${group.group}</div>`;
                filtered.forEach(m => {
                    const sub = m.params ? `${m.params} · ${m.value}` : m.value;
                    curatedHtml += `<div class="arch-option" data-value="${m.value}" data-label="${m.label}"><span class="arch-label">${m.label}</span><span class="arch-sub">${sub}</span></div>`;
                });
            });
            if (curatedHtml) html += curatedHtml;
            // Then show timm results (excluding curated ones)
            const curatedValues = new Set(Object.keys(CURATED_LABELS));
            const timmOnly = models.filter(m => !curatedValues.has(m));
            if (timmOnly.length > 0) {
                html += `<div class="arch-search-info">${models.length} timm models found${models.length >= 50 ? ' (showing first 50)' : ''}</div>`;
                timmOnly.forEach(m => {
                    const pretty = prettifyModelName(m);
                    html += `<div class="arch-option" data-value="${m}" data-label="${pretty}"><span class="arch-label">${pretty}</span><span class="arch-sub">${m}</span></div>`;
                });
            }
            if (!html) html = `<div class="arch-search-info">No models found</div>`;
            dropdown.innerHTML = html;
            dropdown.classList.add('open');
        } catch(e) {
            console.error('Architecture search failed:', e);
        }
    }

    input.addEventListener('focus', () => {
        if (!input.value || input.value === (CURATED_LABELS[hidden.value] || prettifyModelName(hidden.value))) {
            input.select();
            showCurated();
        } else {
            showCurated(input.value);
        }
    });

    input.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        const val = input.value.trim();
        // If text matches current selection label, show all curated (not filtered)
        const currentLabel = CURATED_LABELS[hidden.value] || prettifyModelName(hidden.value);
        if (!val || val === currentLabel) {
            showCurated();
            return;
        }
        // Filter curated immediately
        const hasCurated = showCurated(val);
        // Also search timm after debounce
        if (val.length >= 2) {
            debounceTimer = setTimeout(() => searchTimm(val), 300);
        }
    });

    dropdown.addEventListener('click', (e) => {
        const opt = e.target.closest('.arch-option');
        if (!opt) return;
        hidden.value = opt.dataset.value;
        input.value = opt.dataset.label;
        dropdown.classList.remove('open');
    });

    document.addEventListener('click', (e) => {
        if (!e.target.closest('.arch-combobox')) {
            dropdown.classList.remove('open');
            // Reset display if value was not changed
            if (hidden.value) {
                input.value = CURATED_LABELS[hidden.value] || prettifyModelName(hidden.value);
            }
        }
    });
}

initArchCombobox();

// ============================================================================
// Matrix Training Mode
// ============================================================================

let matrixArchitectures = []; // [{value: 'resnet18', label: 'ResNet-18'}, ...]

function onTrainingModeToggle(mode) {
    const isSingle = mode === 'single';
    // Show/hide single-mode elements
    document.querySelectorAll('.single-only').forEach(el => el.style.display = isSingle ? '' : 'none');
    // Show/hide matrix-mode elements
    document.querySelectorAll('.matrix-only').forEach(el => el.style.display = isSingle ? 'none' : '');
    // Show/hide matrix summary
    document.getElementById('matrix-summary').style.display = isSingle ? 'none' : '';
    // Update arrows-specific fields visibility for matrix mode
    if (!isSingle) {
        onMatrixModelTypeChange();
        updateMatrixSummary();
    } else {
        // Restore single-mode arrows field visibility
        onModelTypeChange();
    }
}

function onMatrixModelTypeChange() {
    const arrowsChecked = document.querySelector('#matrix-model-types input[value="arrows"]').checked;
    const stepSizeGroup = document.getElementById('step-size-group');
    const trainingModeGroup = document.getElementById('training-mode-group');
    const mode = document.querySelector('input[name="training-mode-select"]:checked').value;
    if (mode === 'matrix') {
        stepSizeGroup.style.display = arrowsChecked ? '' : 'none';
        trainingModeGroup.style.display = arrowsChecked ? '' : 'none';
    }
}

function matrixAddArch(value, label) {
    if (matrixArchitectures.find(a => a.value === value)) return; // no dupes
    matrixArchitectures.push({value, label});
    renderMatrixArchTags();
    updateMatrixSummary();
}

function matrixRemoveArch(value) {
    matrixArchitectures = matrixArchitectures.filter(a => a.value !== value);
    renderMatrixArchTags();
    updateMatrixSummary();
}

function matrixAddGroup(group) {
    const groups = group === 'all' ? CURATED_MODELS : CURATED_MODELS.filter(g => g.group === group);
    groups.forEach(g => g.models.forEach(m => matrixAddArch(m.value, m.label)));
}

function renderMatrixArchTags() {
    const container = document.getElementById('matrix-arch-tags');
    const tags = matrixArchitectures.map(a => {
        const tag = document.createElement('span');
        tag.className = 'arch-tag';
        tag.textContent = a.label + ' ';
        const removeBtn = document.createElement('span');
        removeBtn.className = 'arch-tag-remove';
        removeBtn.textContent = '\u00d7';
        removeBtn.addEventListener('click', () => matrixRemoveArch(a.value));
        tag.appendChild(removeBtn);
        return tag;
    });
    container.replaceChildren(...tags);
}

function updateMatrixSummary() {
    const mode = document.querySelector('input[name="training-mode-select"]:checked').value;
    if (mode !== 'matrix') return;

    const types = [...document.querySelectorAll('#matrix-model-types input:checked')].map(i => i.value);
    const resolutions = [...document.querySelectorAll('#matrix-resolutions input:checked')].map(i => parseInt(i.value));
    const archs = matrixArchitectures.length;
    const seedsStr = document.getElementById('seeds').value;
    const seeds = seedsStr.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n));

    const jobCount = types.length * archs * resolutions.length;
    document.getElementById('matrix-count').textContent = jobCount;
    document.getElementById('matrix-detail').textContent =
        types.length + ' types \u00d7 ' + archs + ' arch \u00d7 ' + resolutions.length + ' res';
    document.getElementById('matrix-seeds').textContent = seeds.length;
}

function initMatrixArchCombobox() {
    const input = document.getElementById('matrix-arch-search');
    const dropdown = document.getElementById('matrix-arch-dropdown');
    let debounceTimer = null;

    function showCurated(filter) {
        const lf = (filter || '').toLowerCase();
        dropdown.replaceChildren();
        CURATED_MODELS.forEach(group => {
            const filtered = lf
                ? group.models.filter(m => m.label.toLowerCase().includes(lf) || m.value.includes(lf))
                : group.models;
            if (filtered.length === 0) return;
            const groupLabel = document.createElement('div');
            groupLabel.className = 'arch-group-label';
            groupLabel.textContent = group.group;
            dropdown.appendChild(groupLabel);
            filtered.forEach(m => {
                const opt = document.createElement('div');
                opt.className = 'arch-option';
                opt.dataset.value = m.value;
                opt.dataset.label = m.label;
                const labelSpan = document.createElement('span');
                labelSpan.className = 'arch-label';
                labelSpan.textContent = m.label;
                const subSpan = document.createElement('span');
                subSpan.className = 'arch-sub';
                subSpan.textContent = m.params ? (m.params + ' \u00b7 ' + m.value) : m.value;
                opt.appendChild(labelSpan);
                opt.appendChild(subSpan);
                dropdown.appendChild(opt);
            });
        });
        if (dropdown.children.length === 0 && lf) return false;
        dropdown.classList.add('open');
        return true;
    }

    async function searchTimm(query) {
        try {
            const resp = await fetch('/api/models/architectures?q=' + encodeURIComponent(query));
            const models = await resp.json();
            if (input.value !== query) return;
            dropdown.replaceChildren();
            const lf = query.toLowerCase();
            // Curated matches first
            CURATED_MODELS.forEach(group => {
                const filtered = group.models.filter(m => m.label.toLowerCase().includes(lf) || m.value.includes(lf));
                if (filtered.length === 0) return;
                const groupLabel = document.createElement('div');
                groupLabel.className = 'arch-group-label';
                groupLabel.textContent = group.group;
                dropdown.appendChild(groupLabel);
                filtered.forEach(m => {
                    const opt = document.createElement('div');
                    opt.className = 'arch-option';
                    opt.dataset.value = m.value;
                    opt.dataset.label = m.label;
                    const labelSpan = document.createElement('span');
                    labelSpan.className = 'arch-label';
                    labelSpan.textContent = m.label;
                    const subSpan = document.createElement('span');
                    subSpan.className = 'arch-sub';
                    subSpan.textContent = m.params ? (m.params + ' \u00b7 ' + m.value) : m.value;
                    opt.appendChild(labelSpan);
                    opt.appendChild(subSpan);
                    dropdown.appendChild(opt);
                });
            });
            // Then timm results
            const curatedValues = new Set(Object.keys(CURATED_LABELS));
            const timmOnly = models.filter(m => !curatedValues.has(m));
            if (timmOnly.length > 0) {
                const info = document.createElement('div');
                info.className = 'arch-search-info';
                info.textContent = models.length + ' timm models found' + (models.length >= 50 ? ' (showing first 50)' : '');
                dropdown.appendChild(info);
                timmOnly.forEach(m => {
                    const pretty = prettifyModelName(m);
                    const opt = document.createElement('div');
                    opt.className = 'arch-option';
                    opt.dataset.value = m;
                    opt.dataset.label = pretty;
                    const labelSpan = document.createElement('span');
                    labelSpan.className = 'arch-label';
                    labelSpan.textContent = pretty;
                    const subSpan = document.createElement('span');
                    subSpan.className = 'arch-sub';
                    subSpan.textContent = m;
                    opt.appendChild(labelSpan);
                    opt.appendChild(subSpan);
                    dropdown.appendChild(opt);
                });
            }
            if (dropdown.children.length === 0) {
                const info = document.createElement('div');
                info.className = 'arch-search-info';
                info.textContent = 'No models found';
                dropdown.appendChild(info);
            }
            dropdown.classList.add('open');
        } catch(e) {
            console.error('Matrix architecture search failed:', e);
        }
    }

    input.addEventListener('focus', () => {
        input.select();
        showCurated();
    });

    input.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        const val = input.value.trim();
        if (!val) {
            showCurated();
            return;
        }
        showCurated(val);
        if (val.length >= 2) {
            debounceTimer = setTimeout(() => searchTimm(val), 300);
        }
    });

    dropdown.addEventListener('click', (e) => {
        const opt = e.target.closest('.arch-option');
        if (!opt) return;
        matrixAddArch(opt.dataset.value, opt.dataset.label);
        input.value = '';
        dropdown.classList.remove('open');
    });

    document.addEventListener('click', (e) => {
        if (!e.target.closest('#matrix-arch-combobox')) {
            dropdown.classList.remove('open');
            if (input.value) input.value = '';
        }
    });
}

initMatrixArchCombobox();

// Load training data stats
async function loadTrainingStats() {
    try {
        const response = await fetch('/api/training-data/stats');
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.message);
        }

        // Render digits stats
        const digitsContainer = document.getElementById('digits-stats');
        const digitsStats = data.ground_truth.digits || {};
        const digitsClasses = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'NAN'];
        const maxDigits = Math.max(...Object.values(digitsStats), 1);

        let digitsHtml = '';
        let totalDigits = 0;
        for (const cls of digitsClasses) {
            const count = digitsStats[cls] || 0;
            totalDigits += count;
            const width = (count / maxDigits * 100).toFixed(1);
            digitsHtml += `
                <div class="class-bar">
                    <span class="label">${cls}</span>
                    <div class="bar-container">
                        <div class="bar-fill digits" style="width: ${width}%"></div>
                    </div>
                    <span class="count">${count}</span>
                </div>
            `;
        }
        digitsContainer.innerHTML = digitsHtml || '<div class="empty-state"><p>No data</p></div>';

        // Render arrows stats - grouped by integer (0.x, 1.x, ... 9.x)
        const arrowsContainer = document.getElementById('arrows-stats');
        const arrowsStats = data.ground_truth.arrows || {};

        // Group by integer part
        const arrowGroups = {};
        let totalArrows = 0;
        for (const [cls, count] of Object.entries(arrowsStats)) {
            const intPart = Math.floor(parseFloat(cls));
            if (!arrowGroups[intPart]) {
                arrowGroups[intPart] = { total: 0, classes: {} };
            }
            arrowGroups[intPart].total += count;
            arrowGroups[intPart].classes[cls] = count;
            totalArrows += count;
        }

        // Find max for bar scaling
        const maxGroupTotal = Math.max(...Object.values(arrowGroups).map(g => g.total), 1);

        let arrowsHtml = '';
        for (let i = 0; i <= 9; i++) {
            const group = arrowGroups[i] || { total: 0, classes: {} };
            const groupTotal = group.total;
            const width = (groupTotal / maxGroupTotal * 100).toFixed(1);

            // Sort sub-classes
            const subClasses = Object.keys(group.classes).sort((a, b) => parseFloat(a) - parseFloat(b));
            const maxSubCount = Math.max(...Object.values(group.classes), 1);

            let subBarsHtml = '';
            for (const cls of subClasses) {
                const count = group.classes[cls];
                const subWidth = (count / maxSubCount * 100).toFixed(1);
                subBarsHtml += `
                    <div class="class-bar">
                        <span class="label">${cls}</span>
                        <div class="bar-container">
                            <div class="bar-fill arrows" style="width: ${subWidth}%"></div>
                        </div>
                        <span class="count">${count}</span>
                    </div>
                `;
            }

            arrowsHtml += `
                <details class="arrow-group">
                    <summary>
                        <span class="expand-icon">▶</span>
                        <span class="label">${i}.x</span>
                        <div class="bar-container">
                            <div class="bar-fill" style="width: ${width}%"></div>
                        </div>
                        <span class="count">${groupTotal}</span>
                    </summary>
                    <div class="sub-bars">
                        ${subBarsHtml || '<div class="empty-state"><p>No data</p></div>'}
                    </div>
                </details>
            `;
        }
        arrowsContainer.innerHTML = arrowsHtml || '<div class="empty-state"><p>No data</p></div>';

        // Update summary
        document.getElementById('total-labeled').textContent = totalDigits + totalArrows;
        const unlabeled = (data.unlabeled.digits || 0) + (data.unlabeled.arrows || 0);
        document.getElementById('total-unlabeled').textContent = unlabeled;

        // Auto-collapse "Getting Started" when all classes have data; expand if classes are missing
        const hasEmptyClasses = checkForEmptyClasses(digitsStats, arrowsStats, totalDigits, totalArrows);
        const gettingStartedDetails = document.getElementById('getting-started-details');
        if (gettingStartedDetails) {
            if (hasEmptyClasses) {
                gettingStartedDetails.setAttribute('open', '');
            } else {
                gettingStartedDetails.removeAttribute('open');
            }
        }

    } catch (error) {
        console.error('Error loading training stats:', error);
    }
}

// Check if there are any classes with 0 images or if there's no data at all
function checkForEmptyClasses(digitsStats, arrowsStats, totalDigits, totalArrows) {
    // Show if no data at all (fresh install)
    if (totalDigits === 0 && totalArrows === 0) {
        return true;
    }

    // Show if any digit class has 0 images (but only check if there's some digit data)
    if (totalDigits > 0) {
        const digitsClasses = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'NAN'];
        for (const cls of digitsClasses) {
            if (!digitsStats[cls] || digitsStats[cls] === 0) {
                return true;
            }
        }
    }

    // Show if any arrow class has 0 images (but only check if there's some arrow data)
    if (totalArrows > 0) {
        const arrowClasses = [];
        for (let i = 0; i < 10; i++) {
            for (let j = 0; j < 10; j++) {
                arrowClasses.push(`${i}.${j}`);
            }
        }
        for (const cls of arrowClasses) {
            if (!arrowsStats[cls] || arrowsStats[cls] === 0) {
                return true;
            }
        }
    }

    return false;
}

// Poll training and benchmark status
async function pollTrainingStatus() {
    try {
        const response = await fetch('/api/training/status');
        const data = await response.json();

        // Handle training status
        const trainingStatus = data.training;
        const progressSection = document.getElementById('progress-section');
        const statusBadge = document.getElementById('training-status-badge');
        const startBtn = document.getElementById('start-training-btn');

        const queue = data.queue || [];
        const isTrainingRunning = trainingStatus && trainingStatus.status === 'running';

        if (isTrainingRunning) {
            // Training is running
            currentJobId = trainingStatus.job_id;
            progressSection.classList.add('visible');
            statusBadge.textContent = 'Training: Running';
            statusBadge.className = 'badge badge-running';
            startBtn.disabled = false;
            startBtn.textContent = 'Add to Queue';

            // Show model info
            const cfg = trainingStatus.config || {};
            const infoEl = document.getElementById('progress-model-info');
            infoEl.innerHTML = `<strong>${escapeHtml(cfg.architecture || '?')}</strong> &middot; ${escapeHtml(cfg.model_type || '?')} &middot; ${escapeHtml(String(cfg.resolution || '?'))}px &middot; Seeds: ${escapeHtml((cfg.seeds || []).join(', '))} &middot; ${escapeHtml(String(cfg.epochs || '?'))} epochs`;

            // Update progress
            const progress = trainingStatus.progress || {};
            updateProgress(progress);

            // Load logs
            loadLogs(trainingStatus.job_id);

        } else if (trainingStatus && (trainingStatus.status === 'completed' || trainingStatus.status === 'failed' || trainingStatus.status === 'cancelled')) {
            // Training just finished
            if (currentJobId) {
                const status = trainingStatus.status;
                if (status === 'completed') {
                    showMessage('Training completed successfully!', 'success');
                } else if (status === 'cancelled') {
                    showMessage('Training was cancelled.', 'error');
                } else {
                    showMessage('Training failed: ' + (trainingStatus.error || 'Unknown error'), 'error');
                }
                currentJobId = null;
                loadModels(); // Refresh model list
            }
            progressSection.classList.remove('visible');
            hideLogSectionIfIdle();
            statusBadge.textContent = 'Training: Idle';
            statusBadge.className = 'badge badge-idle';
            startBtn.disabled = false;
            startBtn.textContent = 'Start Training';

        } else {
            // No training
            progressSection.classList.remove('visible');
            hideLogSectionIfIdle();
            statusBadge.textContent = 'Training: Idle';
            statusBadge.className = 'badge badge-idle';
            startBtn.disabled = false;
            startBtn.textContent = 'Start Training';
        }

        // Render training queue
        renderQueue(queue);

        // Handle benchmark status
        const benchmarkStatus = data.benchmark;
        const benchmarkSection = document.getElementById('benchmark-section');

        if (benchmarkStatus && benchmarkStatus.status === 'running') {
            currentBenchmarkJobId = benchmarkStatus.job_id;
            benchmarkSection.classList.add('visible');
            updateBenchmarkProgress(benchmarkStatus.progress || {});
            loadBenchmarkLogs(benchmarkStatus.job_id);
            // Disable benchmark buttons
            document.querySelectorAll('.btn-benchmark').forEach(btn => btn.disabled = true);

        } else if (benchmarkStatus && (benchmarkStatus.status === 'completed' || benchmarkStatus.status === 'failed' || benchmarkStatus.status === 'cancelled')) {
            if (currentBenchmarkJobId) {
                const status = benchmarkStatus.status;
                if (status === 'completed') {
                    showMessage('Benchmark completed!', 'success');
                    loadModels();
                } else if (status === 'cancelled') {
                    showMessage('Benchmark was cancelled.', 'error');
                } else {
                    showMessage('Benchmark failed: ' + (benchmarkStatus.error || 'Unknown error'), 'error');
                }
                currentBenchmarkJobId = null;
            }
            benchmarkSection.classList.remove('visible');
            hideLogSectionIfIdle();
            document.querySelectorAll('.btn-benchmark').forEach(btn => btn.disabled = false);

        } else {
            benchmarkSection.classList.remove('visible');
            hideLogSectionIfIdle();
            document.querySelectorAll('.btn-benchmark').forEach(btn => btn.disabled = false);
        }

    } catch (error) {
        console.error('Error polling status:', error);
    }
}

// Update benchmark progress display
function updateBenchmarkProgress(progress) {
    const currentClass = progress.current_class || 0;
    const totalClasses = progress.total_classes || 1;
    const processedImages = progress.processed_images || 0;
    const totalImages = progress.total_images || 1;

    const classPercent = (currentClass / totalClasses * 100).toFixed(0);
    document.getElementById('benchmark-class-text').textContent = `${currentClass} / ${totalClasses}`;
    document.getElementById('benchmark-class-bar').style.width = classPercent + '%';

    const imagesPercent = (processedImages / totalImages * 100).toFixed(0);
    document.getElementById('benchmark-images-text').textContent = `${processedImages} / ${totalImages}`;
    document.getElementById('benchmark-images-bar').style.width = imagesPercent + '%';

    document.getElementById('benchmark-message').textContent = progress.message || 'Running benchmark...';
}

// Load benchmark logs into the unified log section
async function loadBenchmarkLogs(jobId) {
    try {
        const response = await fetch(`/api/training/logs/${jobId}`);
        const data = await response.json();

        if (data.success && data.logs) {
            const logSection = document.getElementById('log-section');
            const logViewer = document.getElementById('log-viewer');
            logSection.classList.add('visible');

            const wasAtBottom = logViewer.scrollHeight - logViewer.scrollTop <= logViewer.clientHeight + 50;

            logViewer.innerHTML = data.logs.map(log => `<div class="log-line">${escapeHtml(log)}</div>`).join('');

            if (wasAtBottom) {
                logViewer.scrollTop = logViewer.scrollHeight;
            }
        }
    } catch (error) {
        console.error('Error loading benchmark logs:', error);
    }
}


// Start benchmark
async function startBenchmark(modelType, modelId) {
    try {
        const response = await fetch(`/api/models/${modelType}/${modelId}/benchmark`, {
            method: 'POST'
        });

        const data = await response.json();

        if (data.success) {
            showMessage('Benchmark started!', 'success');
            currentBenchmarkJobId = data.job_id;
        } else {
            showMessage('Failed to start benchmark: ' + data.message, 'error');
        }

    } catch (error) {
        console.error('Error starting benchmark:', error);
        showMessage('Error: ' + error.message, 'error');
    }
}

// Cancel benchmark
async function cancelBenchmark() {
    if (!currentBenchmarkJobId) return;

    if (!confirm('Are you sure you want to cancel the benchmark?')) return;

    try {
        const response = await fetch(`/api/benchmark/cancel?job_id=${currentBenchmarkJobId}`, {
            method: 'POST'
        });

        const data = await response.json();

        if (data.success) {
            showMessage('Benchmark cancellation requested.', 'success');
        } else {
            showMessage('Failed to cancel: ' + data.message, 'error');
        }

    } catch (error) {
        console.error('Error cancelling benchmark:', error);
        showMessage('Error: ' + error.message, 'error');
    }
}

// Update progress display
function updateProgress(progress) {
    const currentEpoch = progress.current_epoch || 0;
    const totalEpochs = progress.total_epochs || 20;
    const currentConfig = progress.current_config || 1;
    const totalConfigs = progress.total_configs || 1;

    // Epoch progress
    const epochPercent = (currentEpoch / totalEpochs * 100).toFixed(0);
    document.getElementById('epoch-progress-text').textContent = `${currentEpoch} / ${totalEpochs}`;
    document.getElementById('epoch-progress-bar').style.width = epochPercent + '%';

    // Config progress
    const configPercent = (currentConfig / totalConfigs * 100).toFixed(0);
    document.getElementById('config-progress-text').textContent = `${currentConfig} / ${totalConfigs}`;
    document.getElementById('config-progress-bar').style.width = configPercent + '%';

    // Metrics
    const loss = progress.train_loss;
    const accuracy = progress.val_accuracy;
    document.getElementById('metric-loss').textContent = loss !== null && loss !== undefined ? loss.toFixed(4) : '-';
    document.getElementById('metric-accuracy').textContent = accuracy !== null && accuracy !== undefined ? accuracy.toFixed(2) + '%' : '-';

    // Epoch duration & ETA (from backend)
    const durationEl = document.getElementById('metric-epoch-duration');
    const etaEl = document.getElementById('metric-eta');
    const epochDur = progress.epoch_duration;

    if (epochDur) {
        const epochMs = epochDur * 1000;
        durationEl.textContent = formatDuration(epochMs);

        const epochsLeftThisConfig = totalEpochs - currentEpoch;
        const configsLeft = totalConfigs - currentConfig;
        const totalEpochsLeft = epochsLeftThisConfig + configsLeft * totalEpochs;
        const remainingMs = epochMs * totalEpochsLeft;

        const etaDate = new Date(Date.now() + remainingMs);
        const etaTime = etaDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        etaEl.textContent = `${formatDuration(remainingMs)} (${etaTime})`;
    } else {
        durationEl.textContent = '-';
        etaEl.textContent = '-';
    }

    // Learning rate
    const lrEl = document.getElementById('metric-lr');
    const lr = progress.learning_rate;
    if (lr !== null && lr !== undefined) {
        lrEl.textContent = lr.toExponential(2);
    } else {
        lrEl.textContent = '-';
    }
}

function formatDuration(ms) {
    const totalSec = Math.round(ms / 1000);
    if (totalSec < 60) return `${totalSec}s`;
    const min = Math.floor(totalSec / 60);
    const sec = totalSec % 60;
    if (min < 60) return `${min}m ${sec}s`;
    const hr = Math.floor(min / 60);
    return `${hr}h ${min % 60}m`;
}

// Hide log section when neither training nor benchmark is active
function hideLogSectionIfIdle() {
    if (!currentJobId && !currentBenchmarkJobId) {
        document.getElementById('log-section').classList.remove('visible');
    }
}

// Regex to filter redundant epoch/loss/accuracy lines from training logs
const epochLogPattern = /Epoch \d+\/\d+ - Loss: .* - Val Acc: /;

// Load logs into the unified log section
async function loadLogs(jobId) {
    try {
        const response = await fetch(`/api/training/logs/${jobId}`);
        const data = await response.json();

        if (data.success && data.logs) {
            const logSection = document.getElementById('log-section');
            const logViewer = document.getElementById('log-viewer');
            logSection.classList.add('visible');

            const wasAtBottom = logViewer.scrollHeight - logViewer.scrollTop <= logViewer.clientHeight + 50;

            // Filter out redundant epoch lines (already shown in progress metrics)
            const filtered = data.logs.filter(log => !epochLogPattern.test(log));
            logViewer.innerHTML = filtered.map(log => `<div class="log-line">${escapeHtml(log)}</div>`).join('');

            if (wasAtBottom) {
                logViewer.scrollTop = logViewer.scrollHeight;
            }
        }
    } catch (error) {
        console.error('Error loading logs:', error);
    }
}

// Start training
async function startTraining(event) {
    event.preventDefault();

    const mode = document.querySelector('input[name="training-mode-select"]:checked').value;

    if (mode === 'matrix') {
        await startMatrixTraining();
        return;
    }

    const modelType = document.getElementById('model-type').value;
    const architecture = document.getElementById('architecture').value;
    const resolution = parseInt(document.getElementById('resolution').value);
    const seedsStr = document.getElementById('seeds').value;
    const epochs = parseInt(document.getElementById('epochs').value);
    const learningRate = parseFloat(document.getElementById('learning-rate').value);
    const stepSize = parseFloat(document.getElementById('step-size').value);
    const notes = document.getElementById('notes').value;

    // Parse seeds
    const seeds = seedsStr.split(',').map(s => parseInt(s.trim())).filter(s => !isNaN(s));
    if (seeds.length === 0) {
        showMessage('Please enter at least one valid seed.', 'error');
        return;
    }

    const autoBenchmark = document.getElementById('auto-benchmark').checked;

    const config = {
        model_type: modelType,
        architecture: architecture,
        resolution: resolution,
        seeds: seeds,
        epochs: epochs,
        learning_rate: learningRate,
        batch_size: 16,
        notes: notes,
        auto_benchmark: autoBenchmark
    };

    if (modelType === 'arrows') {
        config.step_size = stepSize;
        const trainingMode = document.getElementById('training-mode').value;
        config.training_mode = trainingMode;
    }

    try {
        const response = await fetch('/api/training/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const data = await response.json();

        if (data.success) {
            if (data.queued) {
                showMessage(`Training queued (position ${data.queue_position})`, 'success');
            } else {
                showMessage('Training started!', 'success');
                currentJobId = data.job_id;
            }
        } else {
            showMessage('Failed to start training: ' + data.message, 'error');
        }

    } catch (error) {
        console.error('Error starting training:', error);
        showMessage('Error starting training: ' + error.message, 'error');
    }
}

// Start matrix training (multiple combinatorial jobs)
async function startMatrixTraining() {
    const types = [...document.querySelectorAll('#matrix-model-types input:checked')].map(i => i.value);
    const resolutions = [...document.querySelectorAll('#matrix-resolutions input:checked')].map(i => parseInt(i.value));
    const archs = matrixArchitectures.map(a => a.value);

    if (types.length === 0 || archs.length === 0 || resolutions.length === 0) {
        showMessage('Select at least one option for each matrix dimension', 'error');
        return;
    }

    const epochs = parseInt(document.getElementById('epochs').value);
    const learningRate = parseFloat(document.getElementById('learning-rate').value);
    const seedsStr = document.getElementById('seeds').value;
    const seeds = seedsStr.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n));
    if (seeds.length === 0) {
        showMessage('Please enter at least one valid seed.', 'error');
        return;
    }
    const notes = document.getElementById('notes').value;
    const stepSize = parseFloat(document.getElementById('step-size').value);
    const trainingMode = document.getElementById('training-mode') ? document.getElementById('training-mode').value : 'discrete';

    // Build all combinations
    const jobs = [];
    for (const type of types) {
        for (const arch of archs) {
            for (const res of resolutions) {
                const config = {
                    model_type: type,
                    architecture: arch,
                    resolution: res,
                    seeds: seeds,
                    epochs: epochs,
                    batch_size: 16,
                    learning_rate: learningRate,
                    notes: notes ? '[Matrix] ' + notes : '[Matrix]',
                    auto_benchmark: false
                };
                if (type === 'arrows') {
                    config.step_size = stepSize;
                    config.training_mode = trainingMode;
                }
                jobs.push(config);
            }
        }
    }

    // Last job gets auto_benchmark
    jobs[jobs.length - 1].auto_benchmark = true;

    // Disable start button during submission
    const startBtn = document.getElementById('start-training-btn');
    startBtn.disabled = true;
    startBtn.textContent = 'Queuing...';

    // Submit all jobs
    let queued = 0;
    for (const config of jobs) {
        try {
            const resp = await fetch('/api/training/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(config)
            });
            if (resp.ok) queued++;
        } catch(e) {
            console.error('Failed to queue job:', e);
        }
    }

    startBtn.disabled = false;
    startBtn.textContent = 'Start Training';

    showMessage('Matrix: ' + queued + '/' + jobs.length + ' jobs queued', 'success');
    pollTrainingStatus();
}

// Cancel training
async function cancelTraining() {
    if (!currentJobId) return;

    // Check if there are queued items
    const queueSection = document.getElementById('queue-section');
    const hasQueue = queueSection.style.display !== 'none';

    let clearQueue = false;
    if (hasQueue) {
        const choice = prompt('Cancel current training.\n\nType "all" to also clear the queue, or press OK to keep queued jobs:');
        if (choice === null) return; // user pressed Cancel
        clearQueue = choice.trim().toLowerCase() === 'all';
    } else {
        if (!confirm('Cancel current training?')) return;
    }

    try {
        const response = await fetch(`/api/training/cancel?job_id=${currentJobId}&clear_queue=${clearQueue}`, {
            method: 'POST'
        });

        const data = await response.json();

        if (data.success) {
            showMessage(clearQueue ? 'Training cancelled, queue cleared.' : 'Training cancelled, queue will continue.', 'success');
        } else {
            showMessage('Failed to cancel: ' + data.message, 'error');
        }

    } catch (error) {
        console.error('Error cancelling training:', error);
        showMessage('Error cancelling: ' + error.message, 'error');
    }
}

// Render training queue
function renderQueue(queue) {
    const section = document.getElementById('queue-section');
    const container = document.getElementById('queue-container');
    const badge = document.getElementById('queue-count-badge');

    if (!queue || queue.length === 0) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';
    badge.textContent = queue.length;

    let html = '';
    for (let i = 0; i < queue.length; i++) {
        const item = queue[i];
        html += `
            <div class="queue-item">
                <div class="queue-info">
                    <span><strong>${item.architecture}</strong></span>
                    <span>${item.model_type}</span>
                    <span>${item.resolution}px</span>
                    <span>Seeds: ${(item.seeds || []).join(', ')}</span>
                    <span>${item.epochs} epochs</span>
                    ${item.auto_benchmark ? '<span>+ benchmark</span>' : ''}
                </div>
                <button class="btn-remove" onclick="removeFromQueue(${i})">Remove</button>
            </div>
        `;
    }
    container.innerHTML = html;
}

// Remove item from queue
async function removeFromQueue(index) {
    try {
        const response = await fetch(`/api/training/queue/${index}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.success) {
            showMessage('Removed from queue', 'success');
        } else {
            showMessage('Failed: ' + data.message, 'error');
        }
    } catch (error) {
        showMessage('Error: ' + error.message, 'error');
    }
}

// Clear entire queue
async function clearQueue() {
    if (!confirm('Remove all queued training jobs?')) return;
    try {
        const response = await fetch('/api/training/queue', { method: 'DELETE' });
        const data = await response.json();
        if (data.success) {
            showMessage('Queue cleared', 'success');
        }
    } catch (error) {
        showMessage('Error: ' + error.message, 'error');
    }
}

// Load models
async function loadModels() {
    try {
        const response = await fetch('/api/models');
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.message);
        }

        allModels = data.models;

        // Get active models from API response
        activeModels = data.active_models || {};

        renderModels();

    } catch (error) {
        console.error('Error loading models:', error);
        document.getElementById('models-container').innerHTML =
            '<div class="empty-state"><p>Error loading models</p></div>';
    }
}

// Filter models
function filterModels(type) {
    currentFilter = type;

    // Update tab styles
    document.querySelectorAll('.tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.type === type);
    });

    renderModels();
}

// Sort models by column
function sortModels(column) {
    if (sortColumn === column) {
        sortAsc = !sortAsc;
    } else {
        sortColumn = column;
        sortAsc = column === 'name' || column === 'type' || column === 'arch';
    }
    localStorage.setItem('modelSortCol', sortColumn);
    localStorage.setItem('modelSortAsc', sortAsc);
    renderModels();
}

// Render models table
function renderModels() {
    const container = document.getElementById('models-container');
    let models = [];

    if (currentFilter === 'all') {
        models = [
            ...allModels.digits.map(m => ({ ...m, model_type: 'digits' })),
            ...allModels.arrows.map(m => ({ ...m, model_type: 'arrows' }))
        ];
    } else if (currentFilter === 'digits') {
        models = allModels.digits.map(m => ({ ...m, model_type: 'digits' }));
    } else {
        models = allModels.arrows.map(m => ({ ...m, model_type: 'arrows' }));
    }

    if (models.length === 0) {
        container.innerHTML = '<div class="empty-state"><p>No models found</p></div>';
        return;
    }

    // Sort: failed always at bottom, then by selected column
    models.sort((a, b) => {
        const aFailed = a.status === 'failed' ? 1 : 0;
        const bFailed = b.status === 'failed' ? 1 : 0;
        if (aFailed !== bFailed) return aFailed - bFailed;

        let cmp = 0;
        const dir = sortAsc ? 1 : -1;
        switch (sortColumn) {
            case 'name':
                cmp = (a.id || '').localeCompare(b.id || '');
                break;
            case 'type':
                cmp = (a.model_type || '').localeCompare(b.model_type || '');
                break;
            case 'arch':
                cmp = (a.architecture || '').localeCompare(b.architecture || '');
                break;
            case 'res':
                cmp = (a.resolution || 0) - (b.resolution || 0);
                break;
            case 'accuracy':
                cmp = ((a.benchmark?.accuracy ?? a.benchmark?.within_half_pct ?? -1)) - ((b.benchmark?.accuracy ?? b.benchmark?.within_half_pct ?? -1));
                break;
            case 'inftime':
                cmp = ((a.benchmark?.inference_time_ms ?? 9999)) - ((b.benchmark?.inference_time_ms ?? 9999));
                break;
            case 'created':
            default:
                cmp = (a.created_at || '').localeCompare(b.created_at || '');
                break;
        }
        return cmp * dir;
    });

    const cols = [
        { key: 'name', label: 'Name' },
        { key: 'type', label: 'Type' },
        { key: 'arch', label: 'Architecture' },
        { key: 'res', label: 'Res' },
        { key: 'accuracy', label: 'Accuracy' },
        { key: 'inftime', label: 'Inf. Time' },
        { key: 'created', label: 'Created' },
    ];
    const thHtml = cols.map(c => {
        const icon = sortColumn === c.key ? (sortAsc ? '&#9650;' : '&#9660;') : '&#9660;';
        const cls = sortColumn === c.key ? ' class="sorted"' : '';
        return `<th${cls} onclick="sortModels('${c.key}')">${c.label}<span class="sort-icon">${icon}</span></th>`;
    }).join('') + '<th>Actions</th>';

    let html = `
        <div class="model-table-wrap"><table class="model-table">
            <thead><tr>${thHtml}</tr></thead>
            <tbody>
    `;

    for (const model of models) {
        const modelType = model.model_type;
        const isActive = activeModels[modelType] === model.id;
        const isFailed = model.status === 'failed';
        const createdAt = model.created_at ? new Date(model.created_at).toLocaleString() : '-';
        const bm = model.benchmark;

        // Accuracy cell
        let accuracyHtml = '-';
        if (bm && bm.accuracy !== undefined) {
            const accVal = bm.accuracy;
            const accClass = accVal >= 90 ? 'good' : accVal >= 70 ? 'warning' : 'bad';
            const tooltip = bm.mae !== undefined ? ` title="MAE: ${bm.mae}, RMSE: ${bm.rmse}"` : '';
            accuracyHtml = `<span class="accuracy-cell ${accClass}"${tooltip}>${accVal}%</span>`;
        } else if (bm && bm.within_half_pct !== undefined) {
            const accVal = bm.within_half_pct;
            const accClass = accVal >= 90 ? 'good' : accVal >= 70 ? 'warning' : 'bad';
            accuracyHtml = `<span class="accuracy-cell ${accClass}" title="MAE: ${bm.mae}, RMSE: ${bm.rmse}">${accVal}%</span>`;
        }

        // Inference time cell
        const infTimeHtml = bm && bm.inference_time_ms !== undefined ? `${bm.inference_time_ms}ms` : '-';

        let actionsHtml;
        if (isFailed) {
            actionsHtml = `
                <button class="btn-viewlog" onclick="viewModelLog('${modelType}', '${model.id}')">Log</button>
                <button class="btn-delete" onclick="deleteModel('${modelType}', '${model.id}')">Delete</button>
            `;
        } else {
            // Benchmark button: show accuracy if already benchmarked
            let benchBtnHtml;
            const bmValue = bm?.accuracy ?? bm?.within_half_pct;
            if (bm && bmValue !== undefined) {
                benchBtnHtml = `<button class="btn-benchmark benchmarked" onclick="startBenchmark('${modelType}', '${model.id}')">${bmValue}%</button>`;
            } else {
                benchBtnHtml = `<button class="btn-benchmark" onclick="startBenchmark('${modelType}', '${model.id}')">Benchmark</button>`;
            }

            actionsHtml = `
                <button class="btn-activate" onclick="activateModel('${modelType}', '${model.id}')" ${isActive ? 'disabled' : ''}>
                    ${isActive ? 'Active' : 'Activate'}
                </button>
                ${benchBtnHtml}
                <button class="btn-delete" onclick="deleteModel('${modelType}', '${model.id}')" ${isActive ? 'disabled' : ''}>Delete</button>
            `;
        }

        html += `
            <tr${isFailed ? ' style="opacity: 0.7"' : ''}>
                <td class="model-name" title="Seed: ${model.seed || 'N/A'}">${model.id}</td>
                <td><span class="model-type ${modelType}">${modelType}</span></td>
                <td>${model.architecture || '-'}</td>
                <td>${model.resolution || '-'}px</td>
                <td>${accuracyHtml}</td>
                <td>${infTimeHtml}</td>
                <td>${createdAt}</td>
                <td class="model-actions">
                    ${actionsHtml}
                </td>
            </tr>
        `;
    }

    html += '</tbody></table></div>';
    container.innerHTML = html;
}

// Activate model
async function activateModel(modelType, modelId) {
    try {
        const response = await fetch(`/api/models/${modelType}/${modelId}/activate`, {
            method: 'POST'
        });

        const data = await response.json();

        if (data.success) {
            showMessage(`Model ${modelId} activated!`, 'success');
            loadModels();
        } else {
            showMessage('Failed to activate: ' + data.message, 'error');
        }

    } catch (error) {
        console.error('Error activating model:', error);
        showMessage('Error: ' + error.message, 'error');
    }
}

// View model training log
async function viewModelLog(modelType, modelId) {
    const overlay = document.getElementById('log-modal-overlay');
    const body = document.getElementById('log-modal-body');
    const title = document.getElementById('log-modal-title');

    title.textContent = `Training Log: ${modelId}`;
    body.innerHTML = '<div class="log-line">Loading...</div>';
    overlay.classList.add('visible');

    try {
        const response = await fetch(`/api/models/${modelType}/${modelId}/logs`);
        const data = await response.json();

        if (data.success && data.logs) {
            body.innerHTML = data.logs.map(line => `<div class="log-line">${escapeHtml(line)}</div>`).join('');
            body.scrollTop = body.scrollHeight;
        } else {
            body.innerHTML = '<div class="log-line">No training log available for this model.</div>';
        }
    } catch (error) {
        body.innerHTML = `<div class="log-line">Error loading log: ${escapeHtml(error.message)}</div>`;
    }
}

function closeLogModal(event) {
    if (event && event.target !== event.currentTarget) return;
    document.getElementById('log-modal-overlay').classList.remove('visible');
}

// Delete model
async function deleteModel(modelType, modelId) {
    if (!confirm(`Are you sure you want to delete model "${modelId}"? This cannot be undone.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/models/${modelType}/${modelId}`, {
            method: 'DELETE'
        });

        const data = await response.json();

        if (data.success) {
            showMessage(`Model ${modelId} deleted.`, 'success');
            loadModels();
        } else {
            showMessage('Failed to delete: ' + data.message, 'error');
        }

    } catch (error) {
        console.error('Error deleting model:', error);
        showMessage('Error: ' + error.message, 'error');
    }
}

// ============================================================================
// Training Data Upload
// ============================================================================

function uploadTrainingData() {
    const fileInput = document.getElementById('training-data-zip');
    const dataType = document.getElementById('data-type-select').value;
    const statusEl = document.getElementById('upload-status');
    const progressEl = document.getElementById('upload-progress');
    const progressBar = document.getElementById('upload-progress-bar');
    const progressText = document.getElementById('upload-progress-text');
    const btn = document.getElementById('upload-data-btn');

    if (!fileInput.files.length) {
        statusEl.textContent = 'Please select a ZIP file.';
        statusEl.style.color = 'var(--danger)';
        return;
    }

    const file = fileInput.files[0];
    if (!file.name.endsWith('.zip')) {
        statusEl.textContent = 'Only ZIP files are supported.';
        statusEl.style.color = 'var(--danger)';
        return;
    }

    const formData = new FormData();
    formData.append('file', file);
    formData.append('type', dataType);

    btn.disabled = true;
    btn.textContent = 'Uploading...';
    statusEl.textContent = '';
    progressEl.style.display = 'flex';

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/training-data/upload');

    xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100);
            progressBar.value = pct;
            progressText.textContent = `${pct}% uploaded`;
        }
    };

    xhr.onload = () => {
        progressEl.style.display = 'none';
        btn.disabled = false;
        btn.textContent = 'Upload ZIP';

        try {
            const data = JSON.parse(xhr.responseText);
            if (xhr.status === 200) {
                statusEl.textContent = data.message;
                statusEl.style.color = 'var(--success)';
                loadTrainingStats();  // Refresh dataset stats
                fileInput.value = '';  // Clear file input
            } else {
                statusEl.textContent = 'Error: ' + (data.detail || 'Upload failed');
                statusEl.style.color = 'var(--danger)';
            }
        } catch (e) {
            statusEl.textContent = 'Error: Invalid response from server';
            statusEl.style.color = 'var(--danger)';
        }
    };

    xhr.onerror = () => {
        progressEl.style.display = 'none';
        btn.disabled = false;
        btn.textContent = 'Upload ZIP';
        statusEl.textContent = 'Upload failed.';
        statusEl.style.color = 'var(--danger)';
    };

    xhr.send(formData);
}
