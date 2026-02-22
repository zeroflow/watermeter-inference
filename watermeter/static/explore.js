// --- State ---
var currentType = 'digits';
var currentClass = null;
var imageOffset = 0;
var imageLimit = 50;
var imageTotal = 0;
var classStats = null;

// --- Init ---
window.addEventListener('load', function() {
    loadStats();
});

function onTypeChange() {
    currentType = document.getElementById('explore-type').value;
    currentClass = null;
    imageOffset = 0;
    clearImageGrid();
    document.getElementById('image-panel-title').textContent = 'Select a class';
    document.getElementById('image-panel-page').textContent = '';
    document.getElementById('image-empty').style.display = 'block';
    document.getElementById('load-more-row').style.display = 'none';
    // Hide any tool results
    document.getElementById('mislabel-results-section').style.display = 'none';
    document.getElementById('prune-results-section').style.display = 'none';
    loadStats();
}

// --- Stats / Class List ---
async function loadStats() {
    try {
        var resp = await fetch('/api/training-data/stats');
        var data = await resp.json();
        if (!data.success) return;
        classStats = data;
        renderClassSidebar(data.ground_truth[currentType] || {});
    } catch (err) {
        console.error('Failed to load stats:', err);
    }
}

function renderClassSidebar(classCounts) {
    var sidebar = document.getElementById('class-sidebar');
    while (sidebar.firstChild) sidebar.removeChild(sidebar.firstChild);

    var keys;
    if (currentType === 'digits') {
        keys = ['0','1','2','3','4','5','6','7','8','9','NAN'];
    } else {
        keys = Object.keys(classCounts).sort(function(a, b) {
            return parseFloat(a) - parseFloat(b);
        });
    }

    keys.forEach(function(cls) {
        var count = classCounts[cls] || 0;
        var item = document.createElement('div');
        item.className = 'class-item' + (cls === currentClass ? ' active' : '');
        item.onclick = function() { selectClass(cls); };

        var nameSpan = document.createElement('span');
        nameSpan.textContent = cls;

        var countSpan = document.createElement('span');
        countSpan.className = 'class-count';
        countSpan.textContent = count;

        item.appendChild(nameSpan);
        item.appendChild(countSpan);
        sidebar.appendChild(item);
    });
}

// --- Image Grid ---
function selectClass(cls) {
    currentClass = cls;
    imageOffset = 0;
    clearImageGrid();
    document.getElementById('image-empty').style.display = 'none';

    // Update sidebar active state
    var items = document.querySelectorAll('.class-item');
    items.forEach(function(item) {
        var name = item.querySelector('span').textContent;
        if (name === cls) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });

    loadImages();
}

async function loadImages() {
    var titleEl = document.getElementById('image-panel-title');
    titleEl.textContent = 'Loading...';

    try {
        var url = '/api/training-data/images?type=' + encodeURIComponent(currentType) +
                  '&class_name=' + encodeURIComponent(currentClass) +
                  '&offset=' + imageOffset + '&limit=' + imageLimit;

        var resp = await fetch(url);
        var data = await resp.json();

        if (!data.success) {
            titleEl.textContent = 'Error loading images';
            return;
        }

        imageTotal = data.total;
        var loaded = imageOffset + data.filenames.length;
        titleEl.textContent = 'Class "' + currentClass + '" (' + imageTotal + ' images)';
        document.getElementById('image-panel-page').textContent =
            'Showing ' + Math.min(loaded, imageTotal) + ' of ' + imageTotal;

        var grid = document.getElementById('image-grid');
        data.filenames.forEach(function(filename) {
            var img = document.createElement('img');
            img.src = '/api/training-data/image/' + encodeURIComponent(currentType) + '/' +
                      encodeURIComponent(currentClass) + '/' + encodeURIComponent(filename);
            img.alt = filename;
            img.title = filename;
            img.loading = 'lazy';
            grid.appendChild(img);
        });

        // Show/hide Load More
        var moreBtn = document.getElementById('load-more-row');
        if (loaded < imageTotal) {
            moreBtn.style.display = 'block';
        } else {
            moreBtn.style.display = 'none';
        }

        if (data.filenames.length === 0 && imageOffset === 0) {
            document.getElementById('image-empty').style.display = 'block';
            document.getElementById('image-empty').textContent = 'No images in this class.';
        }
    } catch (err) {
        titleEl.textContent = 'Network error';
    }
}

function loadMoreImages() {
    imageOffset += imageLimit;
    loadImages();
}

function clearImageGrid() {
    var grid = document.getElementById('image-grid');
    while (grid.firstChild) grid.removeChild(grid.firstChild);
}

// --- Toast ---
function showMessage(text, type) {
    var el = document.getElementById('message');
    el.textContent = text;
    el.className = 'message ' + type;
    setTimeout(function() { el.className = 'message'; }, 5000);
}

// --- Mislabel Detection ---

async function startMislabelScan() {
    var type = document.getElementById('explore-type').value;
    var btn = document.getElementById('mislabel-scan-btn');
    var sectionEl = document.getElementById('mislabel-results-section');
    var statusEl = document.getElementById('mislabel-status');
    var resultsEl = document.getElementById('mislabel-results');

    btn.disabled = true;
    btn.textContent = 'Scanning...';
    sectionEl.style.display = 'block';
    resultsEl.style.display = 'none';

    statusEl.className = 'tool-status scanning';
    statusEl.textContent = 'Scanning ground truth against the active model...';

    try {
        var resp = await fetch('/api/training-data/mislabel/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: type })
        });
        var data = await resp.json();

        if (!resp.ok || !data.success) {
            statusEl.className = 'tool-status error';
            statusEl.textContent = data.detail || data.message || 'Scan failed';
            return;
        }

        statusEl.className = 'tool-status complete';
        statusEl.textContent = 'Scanned ' + data.total_scanned + ' images. Found ' + data.total_suspects + ' suspect(s).';

        if (data.suspects.length > 0) {
            renderMislabelResults(data.suspects);
        }
    } catch (err) {
        statusEl.className = 'tool-status error';
        statusEl.textContent = 'Network error: ' + err.message;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Scan for Mislabels';
    }
}

function renderMislabelResults(suspects) {
    var grid = document.getElementById('mislabel-grid');
    var resultsEl = document.getElementById('mislabel-results');
    var countEl = document.getElementById('mislabel-count');

    while (grid.firstChild) grid.removeChild(grid.firstChild);
    countEl.textContent = suspects.length + ' suspect(s)';

    suspects.forEach(function(s, i) {
        var card = document.createElement('div');
        card.className = 'mislabel-card selected';
        card.dataset.path = s.path;
        card.onclick = function() { card.classList.toggle('selected'); };

        var img = document.createElement('img');
        img.src = 'data:image/jpeg;base64,' + s.image_base64;
        img.alt = s.filename;

        var labels = document.createElement('div');
        labels.className = 'mislabel-labels';

        var cur = document.createElement('div');
        cur.className = 'label-current';
        cur.textContent = 'Was: ' + s.current_label;

        var pred = document.createElement('div');
        pred.className = 'label-predicted';
        pred.textContent = 'Model: ' + s.predicted_label;

        var conf = document.createElement('div');
        conf.style.cssText = 'font-size:0.7rem;color:var(--text-light)';
        conf.textContent = (s.confidence * 100).toFixed(1) + '%';

        labels.appendChild(cur);
        labels.appendChild(pred);
        labels.appendChild(conf);
        card.appendChild(img);
        card.appendChild(labels);
        grid.appendChild(card);
    });

    resultsEl.style.display = 'block';
}

function toggleAllMislabels(select) {
    document.querySelectorAll('.mislabel-card').forEach(function(card) {
        if (select) card.classList.add('selected');
        else card.classList.remove('selected');
    });
}

async function confirmMislabels() {
    var type = document.getElementById('explore-type').value;
    var selected = Array.from(document.querySelectorAll('.mislabel-card.selected'))
        .map(function(card) { return card.dataset.path; });

    if (selected.length === 0) { showMessage('No images selected', 'error'); return; }

    var btn = document.getElementById('mislabel-confirm-btn');
    btn.disabled = true;
    btn.textContent = 'Moving...';

    try {
        var resp = await fetch('/api/training-data/mislabel/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: type, selected: selected })
        });
        var data = await resp.json();

        if (!resp.ok || !data.success) {
            showMessage(data.message || 'Confirm failed', 'error');
            return;
        }

        showMessage('Moved ' + data.moved_count + ' image(s) to input queue.', 'success');
        document.getElementById('mislabel-results-section').style.display = 'none';
        loadStats();
        if (currentClass) { imageOffset = 0; clearImageGrid(); loadImages(); }
    } catch (err) {
        showMessage('Network error: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Move Selected to Input Queue';
    }
}

// --- Data Pruning ---

async function startPrunePreview() {
    var type = document.getElementById('explore-type').value;
    var btn = document.getElementById('prune-preview-btn');
    var sectionEl = document.getElementById('prune-results-section');
    var statusEl = document.getElementById('prune-status');
    var resultsEl = document.getElementById('prune-results');

    btn.disabled = true;
    btn.textContent = 'Scanning...';
    sectionEl.style.display = 'block';
    resultsEl.style.display = 'none';

    statusEl.className = 'tool-status scanning';
    statusEl.textContent = 'Analyzing ground truth for near-duplicate clusters...';

    try {
        var resp = await fetch('/api/training-data/prune/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: type })
        });
        var data = await resp.json();

        if (!resp.ok || !data.success) {
            statusEl.className = 'tool-status error';
            statusEl.textContent = data.message || 'Preview failed';
            return;
        }

        if (data.total_removable === 0) {
            statusEl.className = 'tool-status complete';
            statusEl.textContent = 'Dataset balanced. No pruning needed (' + data.total_before + ' images).';
            return;
        }

        statusEl.className = 'tool-status complete';
        statusEl.textContent = 'Found ' + data.total_removable + ' removable out of ' + data.total_before + '.';
        renderPrunePreview(data);
    } catch (err) {
        statusEl.className = 'tool-status error';
        statusEl.textContent = 'Network error: ' + err.message;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Preview Pruning';
    }
}

function renderPrunePreview(data) {
    var summaryEl = document.getElementById('prune-summary');
    var listEl = document.getElementById('prune-class-list');
    var resultsEl = document.getElementById('prune-results');

    summaryEl.textContent = data.total_before + ' \u2192 ' + data.total_after + ' (\u2212' + data.total_removable + ')';
    while (listEl.firstChild) listEl.removeChild(listEl.firstChild);

    var keys = Object.keys(data.classes).sort();
    keys.forEach(function(cls) {
        var info = data.classes[cls];
        if (info.removable === 0) return;
        var item = document.createElement('div');
        item.className = 'prune-class-item';
        var nameSpan = document.createElement('span');
        nameSpan.textContent = cls + ': ' + info.before + ' \u2192 ' + info.after;
        var removeSpan = document.createElement('span');
        removeSpan.className = 'prune-removable';
        removeSpan.textContent = '\u2212' + info.removable;
        item.appendChild(nameSpan);
        item.appendChild(removeSpan);
        listEl.appendChild(item);
    });

    resultsEl.style.display = 'block';
}

async function confirmPrune() {
    var type = document.getElementById('explore-type').value;
    var btn = document.getElementById('prune-confirm-btn');
    btn.disabled = true;
    btn.textContent = 'Deleting...';

    try {
        var resp = await fetch('/api/training-data/prune/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: type })
        });
        var data = await resp.json();

        if (!resp.ok || !data.success) {
            showMessage(data.message || 'Prune failed', 'error');
            return;
        }

        showMessage('Pruned ' + data.total_deleted + ' image(s).', 'success');
        document.getElementById('prune-results-section').style.display = 'none';
        loadStats();
        if (currentClass) { imageOffset = 0; clearImageGrid(); loadImages(); }
    } catch (err) {
        showMessage('Network error: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Confirm & Delete';
    }
}
