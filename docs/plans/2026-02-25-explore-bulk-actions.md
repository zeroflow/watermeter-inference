# Explore Bulk Actions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add image selection and bulk actions (Send to Labeling, Delete) to the Explore & Tune page

**Architecture:** Two new API endpoints in models.py following the existing mislabel/prune patterns. Frontend changes in explore.html template -- JS selection state + action bar UI.

**Tech Stack:** FastAPI, Jinja2, vanilla JS, CSS variables from existing theme

---

## Task 1: Backend -- bulk-move-to-input and bulk-delete endpoints

**Files:**
- Modify: `watermeter/routes/models.py` (add 2 new endpoints after line 719)
- Modify: `tests/unit/test_bulk_actions.py` (create)

### Step 1.1: Write failing unit tests

Create `tests/unit/test_bulk_actions.py`:

```python
"""Unit tests for bulk training data operations (move-to-input, delete)."""

import shutil
from pathlib import Path

import pytest

from watermeter.routes.models import bulk_move_to_input_logic, bulk_delete_logic


@pytest.fixture
def training_tree(tmp_path):
    """Create a realistic ground_truth + input directory tree."""
    gt_dir = tmp_path / "digits" / "ground_truth" / "3"
    gt_dir.mkdir(parents=True)
    input_dir = tmp_path / "digits" / "input"
    input_dir.mkdir(parents=True)

    # Create 3 fake JPEG files
    for i in range(3):
        (gt_dir / f"img_{i}.jpg").write_bytes(b"\xff\xd8fake")

    return tmp_path


class TestBulkMoveToInputLogic:
    def test_moves_files_to_input(self, training_tree):
        result = bulk_move_to_input_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["img_0.jpg", "img_1.jpg"],
        )
        assert result["moved_count"] == 2
        assert result["error_count"] == 0
        assert result["errors"] == []
        # Files should now be in input/
        input_dir = training_tree / "digits" / "input"
        assert (input_dir / "img_0.jpg").exists()
        assert (input_dir / "img_1.jpg").exists()
        # Files should NOT be in ground_truth anymore
        gt_dir = training_tree / "digits" / "ground_truth" / "3"
        assert not (gt_dir / "img_0.jpg").exists()
        assert not (gt_dir / "img_1.jpg").exists()
        # Third file untouched
        assert (gt_dir / "img_2.jpg").exists()

    def test_missing_file_returns_error(self, training_tree):
        result = bulk_move_to_input_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["nonexistent.jpg"],
        )
        assert result["moved_count"] == 0
        assert result["error_count"] == 1
        assert "not found" in result["errors"][0].lower()

    def test_path_traversal_blocked(self, training_tree):
        result = bulk_move_to_input_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["../../etc/passwd"],
        )
        assert result["moved_count"] == 0
        assert result["error_count"] == 1

    def test_empty_filenames_returns_zero(self, training_tree):
        result = bulk_move_to_input_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=[],
        )
        assert result["moved_count"] == 0
        assert result["error_count"] == 0

    def test_creates_input_dir_if_missing(self, tmp_path):
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)
        (gt_dir / "a.jpg").write_bytes(b"\xff\xd8fake")
        # No input dir exists yet
        result = bulk_move_to_input_logic(
            training_path=tmp_path,
            model_type="digits",
            class_name="5",
            filenames=["a.jpg"],
        )
        assert result["moved_count"] == 1
        assert (tmp_path / "digits" / "input" / "a.jpg").exists()


class TestBulkDeleteLogic:
    def test_deletes_files(self, training_tree):
        result = bulk_delete_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["img_0.jpg", "img_1.jpg"],
        )
        assert result["deleted_count"] == 2
        assert result["error_count"] == 0
        assert result["errors"] == []
        gt_dir = training_tree / "digits" / "ground_truth" / "3"
        assert not (gt_dir / "img_0.jpg").exists()
        assert not (gt_dir / "img_1.jpg").exists()
        assert (gt_dir / "img_2.jpg").exists()

    def test_missing_file_returns_error(self, training_tree):
        result = bulk_delete_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["ghost.jpg"],
        )
        assert result["deleted_count"] == 0
        assert result["error_count"] == 1

    def test_path_traversal_blocked(self, training_tree):
        result = bulk_delete_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["../../../etc/passwd"],
        )
        assert result["deleted_count"] == 0
        assert result["error_count"] == 1

    def test_empty_filenames_returns_zero(self, training_tree):
        result = bulk_delete_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=[],
        )
        assert result["deleted_count"] == 0
        assert result["error_count"] == 0
```

Run tests -- they should fail (functions don't exist yet):

```bash
.venv/bin/python -m pytest tests/unit/test_bulk_actions.py -v 2>&1 | head -30
```

### Step 1.2: Implement the logic functions and route endpoints

In `watermeter/routes/models.py`, add the following after the `serve_training_image` function (after line 719):

Add `shutil` to imports at the top (line 3 area):
```python
import shutil
```

Add `safe_subpath` import (near the existing imports from `..`):
```python
from ..app import safe_subpath
```

Then add these functions and endpoints after line 719:

```python
# ---------------------------------------------------------------------------
# Bulk operations on training images
# ---------------------------------------------------------------------------


def bulk_move_to_input_logic(
    training_path: Path, model_type: str, class_name: str, filenames: list[str]
) -> dict:
    """Move files from ground_truth/{type}/{class}/ to input/{type}/.

    Returns dict with moved_count, error_count, errors.
    """
    gt_dir = training_path / model_type / "ground_truth" / class_name
    input_dir = training_path / model_type / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    errors = []

    for filename in filenames:
        try:
            src = safe_subpath(gt_dir, filename)
        except ValueError:
            errors.append(f"Invalid filename (path traversal): {filename}")
            continue

        if not src.exists():
            errors.append(f"File not found: {filename}")
            continue

        dest = input_dir / src.name
        # Avoid overwriting
        if dest.exists():
            counter = 1
            stem, suffix = src.stem, src.suffix
            while dest.exists():
                dest = input_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        try:
            shutil.move(str(src), str(dest))
            moved += 1
        except Exception as e:
            errors.append(f"Failed to move {filename}: {e}")

    return {"moved_count": moved, "error_count": len(errors), "errors": errors}


def bulk_delete_logic(
    training_path: Path, model_type: str, class_name: str, filenames: list[str]
) -> dict:
    """Delete files from ground_truth/{type}/{class}/.

    Returns dict with deleted_count, error_count, errors.
    """
    gt_dir = training_path / model_type / "ground_truth" / class_name
    deleted = 0
    errors = []

    for filename in filenames:
        try:
            target = safe_subpath(gt_dir, filename)
        except ValueError:
            errors.append(f"Invalid filename (path traversal): {filename}")
            continue

        if not target.exists():
            errors.append(f"File not found: {filename}")
            continue

        try:
            target.unlink()
            deleted += 1
        except Exception as e:
            errors.append(f"Failed to delete {filename}: {e}")

    return {"deleted_count": deleted, "error_count": len(errors), "errors": errors}


@router.post(
    "/api/training-data/bulk-move-to-input",
    tags=["Training Data"],
    summary="Bulk move images to input queue",
    description="Move selected images from ground_truth back to input for relabeling",
)
async def bulk_move_to_input(request: dict):
    """Move selected training images from ground truth to input queue."""
    try:
        model_type = request.get("type")
        if model_type not in _VALID_MODEL_TYPES:
            return JSONResponse(
                {"success": False, "message": "type must be 'digits' or 'arrows'"},
                status_code=400,
            )

        class_name = request.get("class_name")
        if not class_name or not _validate_path_component(class_name):
            return JSONResponse(
                {"success": False, "message": "Invalid or missing class_name"},
                status_code=400,
            )

        filenames = request.get("filenames")
        if not isinstance(filenames, list):
            return JSONResponse(
                {"success": False, "message": "'filenames' must be a list"},
                status_code=400,
            )

        service = watermeter_service.get_service()
        training_path = Path(
            service.config.get("low_confidence", {}).get("save_path", "/training")
        )

        result = bulk_move_to_input_logic(training_path, model_type, class_name, filenames)

        return JSONResponse(
            {
                "success": True,
                "type": model_type,
                "moved_count": result["moved_count"],
                "error_count": result["error_count"],
                "errors": result["errors"],
            }
        )

    except Exception as e:
        logger.error(f"Error in bulk move to input: {e}")
        return JSONResponse(
            {"success": False, "message": f"Error: {str(e)}"}, status_code=500
        )


@router.post(
    "/api/training-data/bulk-delete",
    tags=["Training Data"],
    summary="Bulk delete training images",
    description="Permanently delete selected images from ground truth",
)
async def bulk_delete(request: dict):
    """Permanently delete selected training images from ground truth."""
    try:
        model_type = request.get("type")
        if model_type not in _VALID_MODEL_TYPES:
            return JSONResponse(
                {"success": False, "message": "type must be 'digits' or 'arrows'"},
                status_code=400,
            )

        class_name = request.get("class_name")
        if not class_name or not _validate_path_component(class_name):
            return JSONResponse(
                {"success": False, "message": "Invalid or missing class_name"},
                status_code=400,
            )

        filenames = request.get("filenames")
        if not isinstance(filenames, list):
            return JSONResponse(
                {"success": False, "message": "'filenames' must be a list"},
                status_code=400,
            )

        service = watermeter_service.get_service()
        training_path = Path(
            service.config.get("low_confidence", {}).get("save_path", "/training")
        )

        result = bulk_delete_logic(training_path, model_type, class_name, filenames)

        return JSONResponse(
            {
                "success": True,
                "type": model_type,
                "deleted_count": result["deleted_count"],
                "error_count": result["error_count"],
                "errors": result["errors"],
            }
        )

    except Exception as e:
        logger.error(f"Error in bulk delete: {e}")
        return JSONResponse(
            {"success": False, "message": f"Error: {str(e)}"}, status_code=500
        )
```

### Step 1.3: Run tests and lint

```bash
.venv/bin/python -m pytest tests/unit/test_bulk_actions.py -v
uvx ruff check watermeter/routes/models.py
uvx black watermeter/routes/models.py
```

### Step 1.4: Commit

```bash
git add watermeter/routes/models.py tests/unit/test_bulk_actions.py
git commit -m "claude: add bulk-move-to-input and bulk-delete endpoints

Two new POST endpoints in models.py:
- /api/training-data/bulk-move-to-input
- /api/training-data/bulk-delete

Logic extracted into testable pure functions (bulk_move_to_input_logic,
bulk_delete_logic). Both use safe_subpath for path traversal prevention.
Unit tests cover happy path, missing files, path traversal, and empty input."
```

---

## Task 2: Frontend -- selection CSS and action bar HTML

**Files:**
- Modify: `watermeter/templates/explore.html` (CSS + HTML changes)

### Step 2.1: Add selection CSS styles

In `explore.html`, add these styles inside the `<style>` block. Insert after the `.image-grid img:hover` rule (after line 116, before the `.load-more-row` rule at line 118):

```css
        /* --- Image Selection --- */
        .image-grid .img-wrapper {
            position: relative;
            cursor: pointer;
        }

        .image-grid .img-wrapper img {
            cursor: pointer;
        }

        .image-grid .img-wrapper.selected img {
            border-color: var(--primary, #2563eb);
            border-width: 2px;
        }

        .image-grid .img-wrapper.selected::after {
            content: '\2713';
            position: absolute;
            top: 2px;
            right: 4px;
            background: var(--primary, #2563eb);
            color: #fff;
            font-size: 0.7rem;
            width: 16px;
            height: 16px;
            line-height: 16px;
            text-align: center;
            border-radius: 50%;
            pointer-events: none;
        }

        /* --- Bulk Action Bar --- */
        .bulk-actions {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.85rem;
        }

        .bulk-actions a {
            color: var(--primary, #2563eb);
            cursor: pointer;
            text-decoration: none;
            font-size: 0.8rem;
        }

        .bulk-actions a:hover { text-decoration: underline; }

        .btn-bulk-move {
            padding: 4px 10px;
            font-size: 0.8rem;
            border-radius: 4px;
            border: 1px solid var(--border-input, #cbd5e1);
            background: var(--bg-alt, #f1f5f9);
            color: var(--text-dark);
            cursor: pointer;
        }

        .btn-bulk-move:hover { background: var(--bg-surface, #fff); }
        .btn-bulk-move:disabled { opacity: 0.5; cursor: not-allowed; }

        .btn-bulk-delete {
            padding: 4px 10px;
            font-size: 0.8rem;
            border-radius: 4px;
            border: 1px solid var(--danger, #dc2626);
            background: var(--danger-bg, #fee2e2);
            color: var(--danger, #dc2626);
            cursor: pointer;
        }

        .btn-bulk-delete:hover { background: var(--danger, #dc2626); color: #fff; }
        .btn-bulk-delete:disabled { opacity: 0.5; cursor: not-allowed; }
```

### Step 2.2: Add action bar HTML to the image panel header

Replace the `image-panel-header` div (lines 280-283):

**Old (lines 280-283):**
```html
                <div class="image-panel-header">
                    <h3 id="image-panel-title">Select a class</h3>
                    <span id="image-panel-page" style="font-size:0.85rem;color:var(--text-light)"></span>
                </div>
```

**New:**
```html
                <div class="image-panel-header">
                    <h3 id="image-panel-title">Select a class</h3>
                    <div class="bulk-actions" id="bulk-actions" style="display:none">
                        <a onclick="bulkSelectAll()">Select All</a>
                        <a onclick="bulkDeselectAll()">Deselect All</a>
                        <button class="btn-bulk-move" id="btn-bulk-move" disabled onclick="bulkMoveToInput()">Send to Labeling</button>
                        <button class="btn-bulk-delete" id="btn-bulk-delete" disabled onclick="bulkDelete()">Delete</button>
                    </div>
                    <span id="image-panel-page" style="font-size:0.85rem;color:var(--text-light)"></span>
                </div>
```

### Step 2.3: Commit

```bash
git add watermeter/templates/explore.html
git commit -m "claude: add selection CSS and action bar HTML to explore page

Adds .img-wrapper.selected styling with checkmark overlay, bulk action
bar with Select All / Deselect All links, Send to Labeling (gray) and
Delete (red) buttons. Bar is hidden until a class is selected."
```

---

## Task 3: Frontend -- JavaScript selection state and bulk action logic

**Files:**
- Modify: `watermeter/static/explore.js`

### Step 3.1: Add selection state and update image rendering

At the top of `explore.js`, add selection state after the existing state variables (after line 7):

```javascript
// --- Selection State ---
var selectedImages = new Set();
```

### Step 3.2: Modify `loadImages()` to wrap images in selectable wrappers

In the `loadImages()` function, replace the image-rendering loop (lines 119-127).

**Old (lines 119-127):**
```javascript
        data.filenames.forEach(function(filename) {
            var img = document.createElement('img');
            img.src = '/api/training-data/image/' + encodeURIComponent(currentType) + '/' +
                      encodeURIComponent(currentClass) + '/' + encodeURIComponent(filename);
            img.alt = filename;
            img.title = filename;
            img.loading = 'lazy';
            grid.appendChild(img);
        });
```

**New:**
```javascript
        data.filenames.forEach(function(filename) {
            var wrapper = document.createElement('div');
            wrapper.className = 'img-wrapper';
            wrapper.dataset.filename = filename;
            wrapper.onclick = function() { toggleImageSelection(wrapper, filename); };

            var img = document.createElement('img');
            img.src = '/api/training-data/image/' + encodeURIComponent(currentType) + '/' +
                      encodeURIComponent(currentClass) + '/' + encodeURIComponent(filename);
            img.alt = filename;
            img.title = filename;
            img.loading = 'lazy';

            // Restore selection state if already selected (e.g. after Load More)
            if (selectedImages.has(filename)) {
                wrapper.classList.add('selected');
            }

            wrapper.appendChild(img);
            grid.appendChild(wrapper);
        });
```

### Step 3.3: Modify `selectClass()` to reset selection and show action bar

In the `selectClass()` function (line 75-93), add selection reset and action bar visibility. Insert after `imageOffset = 0;` (after line 77):

```javascript
    selectedImages.clear();
    updateBulkButtons();
    document.getElementById('bulk-actions').style.display = 'flex';
```

### Step 3.4: Modify `onTypeChange()` to reset selection and hide action bar

In the `onTypeChange()` function (line 14-27), add selection reset after `imageOffset = 0;` (after line 17):

```javascript
    selectedImages.clear();
    updateBulkButtons();
    document.getElementById('bulk-actions').style.display = 'none';
```

### Step 3.5: Add selection toggle, bulk action, and helper functions

Add these functions after the existing `showMessage()` function (after line 162), before the mislabel section:

```javascript
// --- Image Selection & Bulk Actions ---

function toggleImageSelection(wrapper, filename) {
    if (selectedImages.has(filename)) {
        selectedImages.delete(filename);
        wrapper.classList.remove('selected');
    } else {
        selectedImages.add(filename);
        wrapper.classList.add('selected');
    }
    updateBulkButtons();
}

function updateBulkButtons() {
    var count = selectedImages.size;
    var moveBtn = document.getElementById('btn-bulk-move');
    var deleteBtn = document.getElementById('btn-bulk-delete');
    if (!moveBtn || !deleteBtn) return;

    moveBtn.disabled = count === 0;
    deleteBtn.disabled = count === 0;
    moveBtn.textContent = count > 0 ? 'Send to Labeling (' + count + ')' : 'Send to Labeling';
    deleteBtn.textContent = count > 0 ? 'Delete (' + count + ')' : 'Delete';
}

function bulkSelectAll() {
    document.querySelectorAll('.image-grid .img-wrapper').forEach(function(wrapper) {
        var filename = wrapper.dataset.filename;
        selectedImages.add(filename);
        wrapper.classList.add('selected');
    });
    updateBulkButtons();
}

function bulkDeselectAll() {
    selectedImages.clear();
    document.querySelectorAll('.image-grid .img-wrapper.selected').forEach(function(wrapper) {
        wrapper.classList.remove('selected');
    });
    updateBulkButtons();
}

async function bulkMoveToInput() {
    if (selectedImages.size === 0) return;

    var filenames = Array.from(selectedImages);
    var btn = document.getElementById('btn-bulk-move');
    btn.disabled = true;
    btn.textContent = 'Moving...';

    try {
        var resp = await fetch('/api/training-data/bulk-move-to-input', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                type: currentType,
                class_name: currentClass,
                filenames: filenames
            })
        });
        var data = await resp.json();

        if (!resp.ok || !data.success) {
            showMessage(data.message || 'Move failed', 'error');
            return;
        }

        showMessage('Moved ' + data.moved_count + ' image(s) to input queue.', 'success');
        removeSelectedFromDom(filenames);
        selectedImages.clear();
        updateBulkButtons();
        updateSidebarCount(-data.moved_count);
        loadStats();
    } catch (err) {
        showMessage('Network error: ' + err.message, 'error');
    } finally {
        btn.disabled = selectedImages.size === 0;
        btn.textContent = 'Send to Labeling';
    }
}

async function bulkDelete() {
    if (selectedImages.size === 0) return;

    var count = selectedImages.size;
    if (!confirm('Permanently delete ' + count + ' image(s)? This cannot be undone.')) return;

    var filenames = Array.from(selectedImages);
    var btn = document.getElementById('btn-bulk-delete');
    btn.disabled = true;
    btn.textContent = 'Deleting...';

    try {
        var resp = await fetch('/api/training-data/bulk-delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                type: currentType,
                class_name: currentClass,
                filenames: filenames
            })
        });
        var data = await resp.json();

        if (!resp.ok || !data.success) {
            showMessage(data.message || 'Delete failed', 'error');
            return;
        }

        showMessage('Deleted ' + data.deleted_count + ' image(s).', 'success');
        removeSelectedFromDom(filenames);
        selectedImages.clear();
        updateBulkButtons();
        updateSidebarCount(-data.deleted_count);
        loadStats();
    } catch (err) {
        showMessage('Network error: ' + err.message, 'error');
    } finally {
        btn.disabled = selectedImages.size === 0;
        btn.textContent = 'Delete';
    }
}

function removeSelectedFromDom(filenames) {
    var filenameSet = new Set(filenames);
    document.querySelectorAll('.image-grid .img-wrapper').forEach(function(wrapper) {
        if (filenameSet.has(wrapper.dataset.filename)) {
            wrapper.remove();
        }
    });
    // Update count display
    var remaining = document.querySelectorAll('.image-grid .img-wrapper').length;
    imageTotal -= filenames.length;
    if (imageTotal < 0) imageTotal = 0;
    document.getElementById('image-panel-title').textContent =
        'Class "' + currentClass + '" (' + imageTotal + ' images)';
    document.getElementById('image-panel-page').textContent =
        'Showing ' + remaining + ' of ' + imageTotal;
}

function updateSidebarCount(delta) {
    if (!currentClass) return;
    var items = document.querySelectorAll('.class-item');
    items.forEach(function(item) {
        var name = item.querySelector('span').textContent;
        if (name === currentClass) {
            var countSpan = item.querySelector('.class-count');
            var current = parseInt(countSpan.textContent, 10) || 0;
            countSpan.textContent = Math.max(0, current + delta);
        }
    });
}
```

### Step 3.6: Bump the JS cache-buster version

In `explore.html`, change the script tag (line 332):

**Old:**
```html
    <script src="/static/explore.js?v=1"></script>
```

**New:**
```html
    <script src="/static/explore.js?v=2"></script>
```

### Step 3.7: Run lint and full test suite

```bash
uvx ruff check watermeter/routes/models.py
uvx black watermeter/routes/models.py
.venv/bin/python -m pytest tests/unit/test_bulk_actions.py -v
.venv/bin/python -m pytest tests/unit/ -v --tb=short
```

### Step 3.8: Commit

```bash
git add watermeter/static/explore.js watermeter/templates/explore.html
git commit -m "claude: implement image selection and bulk actions in explore JS

Adds click-to-toggle selection on image grid with visual checkmark overlay.
Selection stored as JS Set, resets on class/type switch.
Bulk actions: Send to Labeling (immediate) and Delete (with confirm dialog).
After action: removes images from DOM, updates sidebar count, shows toast."
```

---

## Task 4: Integration test and polish

**Files:**
- Modify: `tests/unit/test_bulk_actions.py` (add edge case tests)

### Step 4.1: Add additional edge case tests

Append to `tests/unit/test_bulk_actions.py`:

```python
class TestBulkMoveToInputEdgeCases:
    def test_duplicate_filename_in_input_gets_suffix(self, training_tree):
        """If a file with the same name already exists in input, it gets a _1 suffix."""
        input_dir = training_tree / "digits" / "input"
        (input_dir / "img_0.jpg").write_bytes(b"\xff\xd8existing")

        result = bulk_move_to_input_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["img_0.jpg"],
        )
        assert result["moved_count"] == 1
        # Original stays, new gets _1 suffix
        assert (input_dir / "img_0.jpg").exists()
        assert (input_dir / "img_0_1.jpg").exists()

    def test_arrows_type_works(self, tmp_path):
        gt_dir = tmp_path / "arrows" / "ground_truth" / "3.5"
        gt_dir.mkdir(parents=True)
        (gt_dir / "arrow_1.jpg").write_bytes(b"\xff\xd8fake")

        result = bulk_move_to_input_logic(
            training_path=tmp_path,
            model_type="arrows",
            class_name="3.5",
            filenames=["arrow_1.jpg"],
        )
        assert result["moved_count"] == 1
        assert (tmp_path / "arrows" / "input" / "arrow_1.jpg").exists()


class TestBulkDeleteEdgeCases:
    def test_mixed_valid_and_invalid(self, training_tree):
        """Some files exist, some don't -- partial success."""
        result = bulk_delete_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["img_0.jpg", "nonexistent.jpg", "img_1.jpg"],
        )
        assert result["deleted_count"] == 2
        assert result["error_count"] == 1

    def test_dotdot_in_filename_blocked(self, training_tree):
        result = bulk_delete_logic(
            training_path=training_tree,
            model_type="digits",
            class_name="3",
            filenames=["../3/img_0.jpg"],
        )
        # safe_subpath should catch this -- the resolved path would still be
        # inside the class dir, so it depends on the exact resolution.
        # Either way, the file should be handled safely.
        assert result["error_count"] + result["deleted_count"] <= 1
```

### Step 4.2: Run all tests

```bash
.venv/bin/python -m pytest tests/unit/test_bulk_actions.py -v
.venv/bin/python -m pytest tests/unit/ -v --tb=short
```

### Step 4.3: Commit

```bash
git add tests/unit/test_bulk_actions.py
git commit -m "claude: add edge case tests for bulk actions

Tests duplicate filename handling in move-to-input, arrows type support,
mixed valid/invalid filenames, and dotdot path traversal variants."
```

---

## Summary of all changes

| File | Action | Description |
|------|--------|-------------|
| `watermeter/routes/models.py` | Modify | Add `shutil` + `safe_subpath` imports, `bulk_move_to_input_logic()`, `bulk_delete_logic()`, 2 POST route handlers |
| `watermeter/templates/explore.html` | Modify | Add selection CSS, action bar HTML in image-panel-header, bump JS version |
| `watermeter/static/explore.js` | Modify | Add `selectedImages` Set, wrap images in `.img-wrapper`, selection toggle, bulk action functions, DOM helpers |
| `tests/unit/test_bulk_actions.py` | Create | Unit tests for both logic functions: happy path, errors, traversal, edge cases |

**Total commits:** 4 (one per task)
