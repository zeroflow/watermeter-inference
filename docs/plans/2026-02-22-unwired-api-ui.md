# Unwired API Endpoints — UI Integration Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire 3 backend-complete features into the training page UI: deduplication, pruning, and model archiving.

**Architecture:** All 3 APIs exist and are tested (prune) or partially tested (archive manager-level, dedup untested). The work is purely frontend + thin endpoint tests. Dedup is a one-click action added to the Training Data Stats footer. Prune is a two-step preview/confirm workflow in its own section (like mislabel detection). Archive is a per-model button in the Models table. No HTMX — all pages use plain `fetch()` + DOM manipulation.

**Tech Stack:** Vanilla JS, CSS variables from `style.css`, FastAPI endpoints already in `watermeter/routes/models.py`

---

## UI Placement

```
Training Data Stats  ← dedup button in summary footer
Mislabel Detection   ← existing
Data Pruning         ← NEW section (preview/confirm workflow)
Start Training       ← existing
...
Models               ← archive button per model row
```

---

## Task 1: Dedup Button — Endpoint Test

**Files:**
- Modify: `tests/unit/test_api_routes.py`

**Step 1: Write failing test for dedup endpoint**

Add a new test class after the existing label tests:

```python
class TestDedupEndpoint:
    """Tests for POST /api/training-data/dedup."""

    def test_dedup_returns_results(self, test_client, tmp_path):
        """Dedup endpoint returns per-type counts."""
        # Create input dirs with a duplicate pair
        digits_input = tmp_path / "digits" / "input"
        digits_input.mkdir(parents=True)
        arrows_input = tmp_path / "arrows" / "input"
        arrows_input.mkdir(parents=True)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {
                    "save_path": str(tmp_path),
                    "dedup_threshold": 10,
                    "dedup_scope": "input",
                }
            }
            with patch("watermeter.routes.models.image_hash") as mock_hash:
                mock_hash.purge_duplicates.return_value = 3
                resp = test_client.post(
                    "/api/training-data/dedup",
                    json={},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "digits" in data["results"]
        assert "arrows" in data["results"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_api_routes.py::TestDedupEndpoint -v`
Expected: FAIL or PASS (endpoint exists, test validates it works).

**Step 3: Commit**

```bash
git add tests/unit/test_api_routes.py
git commit -m "claude: add endpoint test for training-data dedup"
```

---

## Task 2: Dedup Button — UI

**Files:**
- Modify: `watermeter/templates/training.html` (~L1422, stats summary footer area)
- Modify: `watermeter/static/training.js` (append function)

**Step 1: Add dedup button HTML to Training Data Stats section**

Find the `.stats-summary` div in `training.html` (contains `#total-labeled` and `#total-unlabeled`). After it (but still inside the `stats-section`), add:

```html
<div class="stats-actions">
    <button class="btn btn-sm" id="dedup-btn" onclick="runDedup()" title="Remove near-duplicate images from the input queue">
        Deduplicate Input Queue
    </button>
    <span id="dedup-status" class="dedup-status"></span>
</div>
```

**Step 2: Add CSS for stats-actions**

In the `<style>` block of `training.html`:

```css
.stats-actions {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--border, #e2e8f0);
}

.dedup-status {
    font-size: 0.85rem;
    color: var(--text-light);
}
```

**Step 3: Add JS function to training.js**

Append to `training.js`:

```javascript
// --- Deduplication ---

async function runDedup() {
    const btn = document.getElementById('dedup-btn');
    const statusEl = document.getElementById('dedup-status');

    btn.disabled = true;
    btn.textContent = 'Deduplicating...';
    statusEl.textContent = '';

    try {
        const resp = await fetch('/api/training-data/dedup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        const data = await resp.json();

        if (!resp.ok || !data.success) {
            showMessage(data.message || 'Dedup failed', 'error');
            return;
        }

        const d = data.results.digits || 0;
        const a = data.results.arrows || 0;
        const total = d + a;
        if (total === 0) {
            statusEl.textContent = 'No duplicates found.';
        } else {
            statusEl.textContent = 'Removed ' + total + ' duplicate(s) (' + d + ' digits, ' + a + ' arrows).';
            loadTrainingStats();
        }
    } catch (err) {
        showMessage('Network error: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Deduplicate Input Queue';
    }
}
```

**Step 4: Bump training.js version**

Update the script tag version in `training.html` from `?v=2` to `?v=3`.

**Step 5: Commit**

```bash
git add watermeter/templates/training.html watermeter/static/training.js
git commit -m "claude: add dedup button to training data stats section"
```

---

## Task 3: Prune UI — HTML + CSS

**Files:**
- Modify: `watermeter/templates/training.html` (insert new section after Mislabel Detection, before Start Training)

**Step 1: Add Data Pruning section HTML**

Insert after the Mislabel Detection `</section>` (after `#mislabel-section`) and before the Start Training section (`#training-form-section`):

```html
<!-- Data Pruning -->
<section class="section" id="prune-section">
    <h2>Data Pruning</h2>
    <p class="section-description">Remove near-duplicate images from over-represented ground truth classes to balance the dataset.</p>

    <div class="prune-controls">
        <div class="prune-type-select">
            <label for="prune-type">Model type:</label>
            <select id="prune-type">
                <option value="digits">Digits</option>
                <option value="arrows">Arrows</option>
            </select>
        </div>
        <button class="btn btn-primary" id="prune-preview-btn" onclick="startPrunePreview()">
            Preview Pruning
        </button>
    </div>

    <div id="prune-status" class="prune-status" style="display: none;"></div>

    <div id="prune-results" class="prune-results" style="display: none;">
        <div class="prune-summary" id="prune-summary"></div>
        <div id="prune-class-list" class="prune-class-list"></div>
        <div class="prune-actions">
            <button class="btn btn-primary" id="prune-confirm-btn" onclick="confirmPrune()">
                Confirm &amp; Delete
            </button>
        </div>
    </div>
</section>
```

**Step 2: Add CSS for prune section**

Add to the `<style>` block in `training.html`:

```css
/* Data Pruning */
.prune-controls {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 16px;
}

.prune-type-select {
    display: flex;
    align-items: center;
    gap: 8px;
}

.prune-type-select select {
    padding: 6px 12px;
    border-radius: 6px;
    border: 1px solid var(--border-input, #cbd5e1);
    background: var(--bg-input, #fff);
    color: var(--text-dark);
    font-size: 0.9rem;
}

.prune-status {
    padding: 12px 16px;
    border-radius: 8px;
    margin-bottom: 16px;
    font-size: 0.9rem;
}

.prune-status.scanning {
    background: var(--info-bg, #dbeafe);
    color: var(--text-dark);
}

.prune-status.complete {
    background: var(--success-bg, #d1fae5);
    color: var(--text-dark);
}

.prune-status.error {
    background: var(--danger-bg, #fee2e2);
    color: var(--text-dark);
}

.prune-summary {
    margin-bottom: 12px;
    font-size: 0.95rem;
    font-weight: 600;
}

.prune-class-list {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 8px;
    margin-bottom: 16px;
    max-height: 300px;
    overflow-y: auto;
}

.prune-class-item {
    display: flex;
    justify-content: space-between;
    padding: 6px 12px;
    border-radius: 6px;
    background: var(--bg-surface, #fff);
    border: 1px solid var(--border, #e2e8f0);
    font-size: 0.85rem;
}

.prune-class-item .prune-removable {
    color: var(--danger, #dc2626);
    font-weight: 600;
}

.prune-actions {
    display: flex;
    justify-content: flex-end;
}
```

**Step 3: Commit**

```bash
git add watermeter/templates/training.html
git commit -m "claude: add data pruning section HTML/CSS to training page"
```

---

## Task 4: Prune UI — JavaScript

**Files:**
- Modify: `watermeter/static/training.js` (append functions)

**Step 1: Add prune preview/confirm functions**

Append to `training.js`. Use safe DOM construction (no innerHTML):

```javascript
// --- Data Pruning ---

async function startPrunePreview() {
    const type = document.getElementById('prune-type').value;
    const btn = document.getElementById('prune-preview-btn');
    const statusEl = document.getElementById('prune-status');
    const resultsEl = document.getElementById('prune-results');

    btn.disabled = true;
    btn.textContent = 'Scanning...';
    resultsEl.style.display = 'none';

    statusEl.style.display = 'block';
    statusEl.className = 'prune-status scanning';
    statusEl.textContent = 'Analyzing ground truth for near-duplicate clusters...';

    try {
        const resp = await fetch('/api/training-data/prune/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: type })
        });
        const data = await resp.json();

        if (!resp.ok || !data.success) {
            statusEl.className = 'prune-status error';
            statusEl.textContent = data.message || 'Preview failed';
            return;
        }

        if (data.total_removable === 0) {
            statusEl.className = 'prune-status complete';
            statusEl.textContent = 'Dataset is balanced. No pruning needed (' + data.total_before + ' images).';
            return;
        }

        statusEl.className = 'prune-status complete';
        statusEl.textContent = 'Found ' + data.total_removable + ' removable image(s) out of ' + data.total_before + '.';

        renderPrunePreview(data);
    } catch (err) {
        statusEl.className = 'prune-status error';
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

    summaryEl.textContent = data.total_before + ' images \u2192 ' + data.total_after + ' after pruning (\u2212' + data.total_removable + ')';

    // Clear previous
    while (listEl.firstChild) listEl.removeChild(listEl.firstChild);

    var classes = data.classes;
    var keys = Object.keys(classes).sort();
    keys.forEach(function(cls) {
        var info = classes[cls];
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
    var type = document.getElementById('prune-type').value;
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

        showMessage('Pruned ' + data.total_deleted + ' image(s) from ' + type + ' ground truth.', 'success');

        document.getElementById('prune-results').style.display = 'none';
        document.getElementById('prune-status').style.display = 'none';
        if (typeof loadTrainingStats === 'function') {
            loadTrainingStats();
        }
    } catch (err) {
        showMessage('Network error: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Confirm & Delete';
    }
}
```

**Step 2: Bump training.js version**

Update the script tag version in `training.html` from `?v=3` to `?v=4`.

**Step 3: Commit**

```bash
git add watermeter/static/training.js watermeter/templates/training.html
git commit -m "claude: add data pruning JS to training page"
```

---

## Task 5: Archive Button in Models Table

**Files:**
- Modify: `watermeter/static/training.js` (modify `renderModels()`, add `archiveModel()`)
- Modify: `watermeter/templates/training.html` (bump version)

**Step 1: Add endpoint test for archive route**

Add to `tests/unit/test_api_routes.py`:

```python
class TestArchiveEndpoint:
    """Tests for POST /api/models/{type}/{id}/archive."""

    def test_archive_success(self, test_client):
        """Archive endpoint returns success when model exists."""
        with patch("watermeter.routes.models.model_manager") as mock_mm:
            mock_mm.archive_model.return_value = True
            resp = test_client.post("/api/models/digits/test-model/archive")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_archive_not_found(self, test_client):
        """Archive endpoint returns 404 when model not found."""
        with patch("watermeter.routes.models.model_manager") as mock_mm:
            mock_mm.archive_model.return_value = False
            resp = test_client.post("/api/models/digits/nonexistent/archive")

        assert resp.status_code == 404
        data = resp.json()
        assert data["success"] is False
```

Run: `.venv/bin/python -m pytest tests/unit/test_api_routes.py::TestArchiveEndpoint -v`

**Step 2: Add archive button to renderModels()**

In `training.js`, find `renderModels()` (~L1238). Inside the normal (non-failed) model button section (~L1354-1377), find where the delete button is added. Before the delete button, add an archive button:

The existing code builds buttons as HTML strings concatenated into a variable. Add this alongside the existing buttons (before the delete button line):

```javascript
'<button class="btn-archive" onclick="archiveModel(\'' + m.type + '\', \'' + m.id + '\')"' +
(m.status === 'archived' ? ' disabled title="Already archived"' : '') +
'>Archive</button>' +
```

Note: `renderModels()` uses innerHTML for the entire table — this is the existing pattern. Add the button in the same style.

**Step 3: Add archiveModel() function**

Append to `training.js`:

```javascript
// --- Model Archive ---

async function archiveModel(modelType, modelId) {
    if (!confirm('Archive model ' + modelId + '? It will be hidden from the active list.')) {
        return;
    }

    try {
        var resp = await fetch('/api/models/' + encodeURIComponent(modelType) + '/' + encodeURIComponent(modelId) + '/archive', {
            method: 'POST'
        });
        var data = await resp.json();

        if (!resp.ok || !data.success) {
            showMessage(data.message || 'Archive failed', 'error');
            return;
        }

        showMessage('Model ' + modelId + ' archived.', 'success');
        loadModels();
    } catch (err) {
        showMessage('Network error: ' + err.message, 'error');
    }
}
```

**Step 4: Add CSS for archive button**

In `training.html` `<style>` block, near the existing model button styles:

```css
.btn-archive {
    padding: 4px 10px;
    font-size: 0.8rem;
    border-radius: 4px;
    border: 1px solid var(--border-input, #cbd5e1);
    background: var(--bg-surface, #fff);
    color: var(--text-light);
    cursor: pointer;
}

.btn-archive:hover {
    background: var(--bg-alt, #f1f5f9);
}

.btn-archive:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}
```

**Step 5: Bump training.js version**

Update the script tag version in `training.html` from `?v=4` to `?v=5`.

**Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: All pass.

**Step 7: Commit**

```bash
git add watermeter/static/training.js watermeter/templates/training.html tests/unit/test_api_routes.py
git commit -m "claude: add archive button to models table"
```

---

## Task Summary

| Task | What | Depends On |
|------|------|------------|
| 1 | Dedup endpoint test | — |
| 2 | Dedup button UI (HTML + CSS + JS) | Task 1 |
| 3 | Prune section HTML + CSS | — |
| 4 | Prune JS (preview + confirm) | Task 3 |
| 5 | Archive button + test + JS + CSS | — |

**Parallel opportunities:** Tasks 1+3+5 can all run in parallel (independent features). Tasks 2+4 can run after their dependencies.
