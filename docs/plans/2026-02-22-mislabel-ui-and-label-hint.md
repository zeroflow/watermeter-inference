# Mislabel Detection UI + Label Hint Pre-fill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the missing UI for mislabel detection on the training page and implement label hint pre-fill (BL-29) in the labeling UI.

**Architecture:** Two independent features. (1) A new "Mislabel Detection" section in `training.html` with JS in `training.js` that calls the existing `POST /api/training-data/mislabel/scan` and `/confirm` endpoints. (2) Backend parsing of `_label=X` from filenames in `GET /api/label/next-image` + frontend pre-fill in `label.js`. No HTMX — both pages use plain `fetch()`.

**Tech Stack:** Python/FastAPI (backend), Jinja2 templates, vanilla JS, CSS variables from `style.css`

---

## Task 1: Label Hint Backend — Failing Tests

**Files:**
- Modify: `tests/unit/test_api_routes.py` (add new test class after `TestLabelValidation` ~L266)

**Step 1: Write failing tests for label hint parsing**

Add to `tests/unit/test_api_routes.py` after the `TestLabelValidation` class:

```python
class TestNextImageLabelHint:
    """Tests for _label=X hint parsing in GET /api/label/next-image."""

    def test_returns_label_hint_from_filename(self, test_client, tmp_path):
        """When filename contains _label=X, response includes label_hint."""
        digits_input = tmp_path / "digits" / "input"
        digits_input.mkdir(parents=True)
        (digits_input / "img001_label=5.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        with patch("watermeter.routes.label.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {"training": {"path": str(tmp_path)}}
            resp = test_client.get("/api/label/next-image")

        assert resp.status_code == 200
        data = resp.json()
        assert data["has_images"] is True
        assert data["label_hint"] == "5"

    def test_no_label_hint_when_absent(self, test_client, tmp_path):
        """When filename has no _label=X pattern, label_hint is null."""
        digits_input = tmp_path / "digits" / "input"
        digits_input.mkdir(parents=True)
        (digits_input / "img001.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        with patch("watermeter.routes.label.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {"training": {"path": str(tmp_path)}}
            resp = test_client.get("/api/label/next-image")

        assert resp.status_code == 200
        data = resp.json()
        assert data["has_images"] is True
        assert data["label_hint"] is None

    def test_label_hint_decimal_for_arrows(self, test_client, tmp_path):
        """Arrow images with _label=4.5 return decimal hint."""
        arrows_input = tmp_path / "arrows" / "input"
        arrows_input.mkdir(parents=True)
        (arrows_input / "dial_label=4.5.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        with patch("watermeter.routes.label.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {"training": {"path": str(tmp_path)}}
            resp = test_client.get("/api/label/next-image")

        assert resp.status_code == 200
        data = resp.json()
        assert data["label_hint"] == "4.5"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_api_routes.py::TestNextImageLabelHint -v`
Expected: FAIL — either `label_hint` key missing from response or value mismatch.

**Step 3: Commit failing tests**

```bash
git add tests/unit/test_api_routes.py
git commit -m "claude: add failing tests for label hint parsing in next-image endpoint (BL-29)"
```

---

## Task 2: Label Hint Backend — Implementation

**Files:**
- Modify: `watermeter/routes/label.py:74-88` (the `response_data` dict construction)

**Step 1: Add label hint parsing to `GET /api/label/next-image`**

In `watermeter/routes/label.py`, add a `re` import at the top (if not already present), then modify the response construction block around L74-88.

Before the `response_data` dict is built, add:

```python
import re  # at top of file

# Inside get_next_unlabeled_image(), before response_data construction:
label_hint_match = re.search(r'_label=([^_.]+(?:\.\d+)?)', image_path.stem)
label_hint = label_hint_match.group(1) if label_hint_match else None
```

Then add `"label_hint": label_hint` to the `response_data` dict.

The regex `_label=([^_.]+(?:\.\d+)?)` captures:
- `_label=5` → `"5"` (digit)
- `_label=4.5` → `"4.5"` (arrow decimal)
- `_label=NAN` → `"NAN"` (not-a-number class)

**Step 2: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_api_routes.py::TestNextImageLabelHint -v`
Expected: All 3 PASS.

**Step 3: Run full test suite to check for regressions**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: All pass (no regressions).

**Step 4: Commit**

```bash
git add watermeter/routes/label.py
git commit -m "claude: parse _label=X hint from filename in next-image endpoint (BL-29)"
```

---

## Task 3: Label Hint Frontend

**Files:**
- Modify: `watermeter/static/label.js` (in `loadNextImage()` success handler)
- Modify: `watermeter/templates/label.html` (add hint indicator element + CSS)

**Step 1: Add hint indicator HTML to `label.html`**

After the `<input id="label-input">` element (around L418-425), add a small hint indicator:

```html
<div id="label-hint" class="label-hint" style="display: none;">
    Suggested: <span id="label-hint-value"></span>
    <button type="button" class="hint-accept-btn" onclick="acceptLabelHint()" title="Accept suggestion (Tab)">Use</button>
</div>
```

Add CSS in the `<style>` block of `label.html`:

```css
.label-hint {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 4px;
    padding: 6px 12px;
    background: var(--info-bg, #dbeafe);
    border: 1px solid var(--primary, #2563eb);
    border-radius: 6px;
    font-size: 0.9rem;
    color: var(--text-dark);
}

.hint-accept-btn {
    padding: 2px 10px;
    background: var(--primary, #2563eb);
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.85rem;
}

.hint-accept-btn:hover {
    opacity: 0.85;
}
```

**Step 2: Update `label.js` to show/pre-fill hint**

In `label.js`, in the `loadNextImage()` function where the response data is processed, add after setting the image:

```javascript
// Show label hint if present
const hintEl = document.getElementById('label-hint');
const hintValueEl = document.getElementById('label-hint-value');
if (data.label_hint) {
    hintValueEl.textContent = data.label_hint;
    hintEl.style.display = 'flex';
    // Pre-fill the input
    document.getElementById('label-input').value = data.label_hint;
} else {
    hintEl.style.display = 'none';
    hintValueEl.textContent = '';
}
```

Add the `acceptLabelHint()` function:

```javascript
function acceptLabelHint() {
    const hint = document.getElementById('label-hint-value').textContent;
    const input = document.getElementById('label-input');
    input.value = hint;
    input.focus();
}
```

Add Tab key handler to accept hint (inside the existing keydown handler or as new one):

```javascript
document.getElementById('label-input').addEventListener('keydown', function(e) {
    if (e.key === 'Tab' && document.getElementById('label-hint').style.display !== 'none') {
        e.preventDefault();
        acceptLabelHint();
    }
});
```

**Step 3: Bump label.js version**

In `label.html`, update the script tag version from `?v=4` to `?v=5`.

**Step 4: Test manually on debug container**

Launch debug container if not running: `./debug.sh --detach`
Navigate to `http://localhost:8002/label`
Place a test image with `_label=X` in filename in the digits input dir and verify the hint appears.

**Step 5: Commit**

```bash
git add watermeter/static/label.js watermeter/templates/label.html
git commit -m "claude: add label hint pre-fill in labeling UI (BL-29)"
```

---

## Task 4: Mislabel Detection UI — HTML + CSS

**Files:**
- Modify: `watermeter/templates/training.html` (insert new section at ~L1299)

**Step 1: Add the Mislabel Detection section HTML**

Insert after the Training Data Stats section (after L1298, before the Start Training section at ~L1301):

```html
<!-- Mislabel Detection -->
<section class="section" id="mislabel-section">
    <h2>Mislabel Detection</h2>
    <p class="section-description">Scan ground truth images against the active model to find potential mislabels.</p>

    <div class="mislabel-controls">
        <div class="mislabel-type-select">
            <label for="mislabel-type">Model type:</label>
            <select id="mislabel-type">
                <option value="digits">Digits</option>
                <option value="arrows">Arrows</option>
            </select>
        </div>
        <button class="btn btn-primary" id="mislabel-scan-btn" onclick="startMislabelScan()">
            Scan for Mislabels
        </button>
    </div>

    <div id="mislabel-status" class="mislabel-status" style="display: none;"></div>

    <div id="mislabel-results" class="mislabel-results" style="display: none;">
        <div class="mislabel-results-header">
            <span id="mislabel-count"></span>
            <div class="mislabel-select-controls">
                <button class="btn btn-sm" onclick="toggleAllMislabels(true)">Select All</button>
                <button class="btn btn-sm" onclick="toggleAllMislabels(false)">Deselect All</button>
            </div>
        </div>
        <div id="mislabel-grid" class="mislabel-grid"></div>
        <div class="mislabel-actions">
            <button class="btn btn-primary" id="mislabel-confirm-btn" onclick="confirmMislabels()">
                Move Selected to Input Queue
            </button>
        </div>
    </div>
</section>
```

**Step 2: Add CSS for the mislabel section**

Add to the `<style>` block in `training.html` (alongside other section styles):

```css
/* Mislabel Detection */
.mislabel-controls {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 16px;
}

.mislabel-type-select {
    display: flex;
    align-items: center;
    gap: 8px;
}

.mislabel-type-select select {
    padding: 6px 12px;
    border-radius: 6px;
    border: 1px solid var(--border-input, #cbd5e1);
    background: var(--bg-input, #fff);
    color: var(--text-dark);
    font-size: 0.9rem;
}

.mislabel-status {
    padding: 12px 16px;
    border-radius: 8px;
    margin-bottom: 16px;
    font-size: 0.9rem;
}

.mislabel-status.scanning {
    background: var(--info-bg, #dbeafe);
    color: var(--text-dark);
}

.mislabel-status.complete {
    background: var(--success-bg, #d1fae5);
    color: var(--text-dark);
}

.mislabel-status.error {
    background: var(--danger-bg, #fee2e2);
    color: var(--text-dark);
}

.mislabel-results-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
}

.mislabel-select-controls {
    display: flex;
    gap: 8px;
}

.btn-sm {
    padding: 4px 10px;
    font-size: 0.8rem;
    border-radius: 4px;
    border: 1px solid var(--border-input, #cbd5e1);
    background: var(--bg-surface, #fff);
    color: var(--text-dark);
    cursor: pointer;
}

.btn-sm:hover {
    background: var(--bg-alt, #f1f5f9);
}

.mislabel-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 16px;
    max-height: 500px;
    overflow-y: auto;
}

.mislabel-card {
    border: 2px solid var(--border, #e2e8f0);
    border-radius: 8px;
    padding: 8px;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
    text-align: center;
}

.mislabel-card:hover {
    border-color: var(--primary, #2563eb);
}

.mislabel-card.selected {
    border-color: var(--primary, #2563eb);
    background: var(--info-bg, #dbeafe);
}

.mislabel-card img {
    width: 100%;
    height: 120px;
    object-fit: contain;
    border-radius: 4px;
    background: var(--bg-inset, #f1f5f9);
    margin-bottom: 6px;
}

.mislabel-card .mislabel-labels {
    font-size: 0.8rem;
    line-height: 1.4;
}

.mislabel-card .label-current {
    color: var(--danger, #dc2626);
    text-decoration: line-through;
}

.mislabel-card .label-predicted {
    color: var(--success, #059669);
    font-weight: 600;
}

.mislabel-actions {
    display: flex;
    justify-content: flex-end;
}
```

**Step 3: Commit**

```bash
git add watermeter/templates/training.html
git commit -m "claude: add mislabel detection section HTML/CSS to training page"
```

---

## Task 5: Mislabel Detection UI — JavaScript

**Files:**
- Modify: `watermeter/static/training.js` (add functions at end of file)

**Step 1: Add mislabel scan function**

Append to `training.js`. IMPORTANT: Use safe DOM construction (no innerHTML) to prevent XSS:

```javascript
// --- Mislabel Detection ---

async function startMislabelScan() {
    const type = document.getElementById('mislabel-type').value;
    const btn = document.getElementById('mislabel-scan-btn');
    const statusEl = document.getElementById('mislabel-status');
    const resultsEl = document.getElementById('mislabel-results');

    btn.disabled = true;
    btn.textContent = 'Scanning...';
    resultsEl.style.display = 'none';

    statusEl.style.display = 'block';
    statusEl.className = 'mislabel-status scanning';
    statusEl.textContent = 'Scanning ground truth images against the active model...';

    try {
        const resp = await fetch('/api/training-data/mislabel/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type })
        });
        const data = await resp.json();

        if (!resp.ok || !data.success) {
            statusEl.className = 'mislabel-status error';
            statusEl.textContent = data.detail || data.message || 'Scan failed';
            return;
        }

        statusEl.className = 'mislabel-status complete';
        statusEl.textContent = 'Scanned ' + data.total_scanned + ' images. Found ' + data.total_suspects + ' suspect(s).';

        if (data.suspects.length === 0) {
            return;
        }

        renderMislabelResults(data.suspects, type);
    } catch (err) {
        statusEl.className = 'mislabel-status error';
        statusEl.textContent = 'Network error: ' + err.message;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Scan for Mislabels';
    }
}

function renderMislabelResults(suspects, type) {
    const grid = document.getElementById('mislabel-grid');
    const resultsEl = document.getElementById('mislabel-results');
    const countEl = document.getElementById('mislabel-count');

    // Clear previous results safely
    while (grid.firstChild) grid.removeChild(grid.firstChild);
    countEl.textContent = suspects.length + ' suspect(s) found';

    suspects.forEach(function(s, i) {
        const card = document.createElement('div');
        card.className = 'mislabel-card selected';
        card.dataset.path = s.path;
        card.dataset.index = i;
        card.onclick = function() { card.classList.toggle('selected'); };

        // Build card contents with safe DOM methods
        const img = document.createElement('img');
        img.src = 'data:image/jpeg;base64,' + s.image_base64;
        img.alt = s.filename;

        const labelsDiv = document.createElement('div');
        labelsDiv.className = 'mislabel-labels';

        const currentDiv = document.createElement('div');
        currentDiv.className = 'label-current';
        currentDiv.textContent = 'Was: ' + s.current_label;

        const predictedDiv = document.createElement('div');
        predictedDiv.className = 'label-predicted';
        predictedDiv.textContent = 'Model: ' + s.predicted_label;

        const confDiv = document.createElement('div');
        confDiv.style.cssText = 'font-size:0.75rem;color:var(--text-light)';
        confDiv.textContent = (s.confidence * 100).toFixed(1) + '%';

        labelsDiv.appendChild(currentDiv);
        labelsDiv.appendChild(predictedDiv);
        labelsDiv.appendChild(confDiv);

        card.appendChild(img);
        card.appendChild(labelsDiv);

        grid.appendChild(card);
    });

    resultsEl.style.display = 'block';
}

function toggleAllMislabels(select) {
    document.querySelectorAll('.mislabel-card').forEach(function(card) {
        if (select) {
            card.classList.add('selected');
        } else {
            card.classList.remove('selected');
        }
    });
}

async function confirmMislabels() {
    const type = document.getElementById('mislabel-type').value;
    const selected = Array.from(document.querySelectorAll('.mislabel-card.selected'))
        .map(function(card) { return card.dataset.path; });

    if (selected.length === 0) {
        showMessage('No images selected', 'error');
        return;
    }

    const btn = document.getElementById('mislabel-confirm-btn');
    btn.disabled = true;
    btn.textContent = 'Moving...';

    try {
        const resp = await fetch('/api/training-data/mislabel/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: type, selected: selected })
        });
        const data = await resp.json();

        if (!resp.ok || !data.success) {
            showMessage(data.detail || data.message || 'Confirm failed', 'error');
            return;
        }

        showMessage('Moved ' + data.moved_count + ' image(s) to input queue for re-labeling', 'success');

        // Hide results and refresh stats
        document.getElementById('mislabel-results').style.display = 'none';
        document.getElementById('mislabel-status').style.display = 'none';
        if (typeof loadTrainingStats === 'function') {
            loadTrainingStats();
        }
    } catch (err) {
        showMessage('Network error: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Move Selected to Input Queue';
    }
}
```

**Step 2: Bump training.js version**

In `training.html`, update the `<script src="/static/training.js">` tag to include a version query param (e.g., `?v=2`).

**Step 3: Test manually on debug container**

Launch debug container: `./debug.sh --detach`
Navigate to `http://localhost:8002/training`
1. Verify the "Mislabel Detection" section appears between Training Data Stats and Start Training.
2. Select "Digits" and click "Scan for Mislabels" — verify the scan runs and shows results (or "0 suspects" if all labels are correct).
3. If suspects found: verify card display, select/deselect, confirm flow.

**Step 4: Commit**

```bash
git add watermeter/static/training.js watermeter/templates/training.html
git commit -m "claude: add mislabel detection JS to training page"
```

---

## Task 6: Update Backlog

**Files:**
- Modify: `backlog.md`

**Step 1: Update BL-29 status**

Change BL-29 status from `idea` to `done`.

**Step 2: Commit**

```bash
git add backlog.md
git commit -m "claude: mark BL-29 done — label hint pre-fill implemented"
```

---

## Task Summary

| Task | Agent | What | Depends On |
|------|-------|------|------------|
| 1 | tester | Failing tests for label hint parsing | — |
| 2 | dev | Implement label hint parsing in `label.py` | Task 1 |
| 3 | frontend | Label hint UI in `label.html` + `label.js` | Task 2 |
| 4 | frontend | Mislabel detection HTML/CSS in `training.html` | — |
| 5 | frontend | Mislabel detection JS in `training.js` | Task 4 |
| 6 | dev | Update backlog | Tasks 1-5 |

**Parallel opportunities:** Tasks 1+4 can run in parallel (independent features). Tasks 3+5 can run in parallel after their dependencies.
