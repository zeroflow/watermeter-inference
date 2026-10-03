# Explore & Tune — Training Data Viewer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a dedicated "Explore & Tune" page where users can browse training images per class, run mislabel detection, and prune data — replacing the standalone mislabel/prune sections on the training page with a unified viewer.

**Architecture:** New page at `/training/explore` with its own template + JS. Two new API endpoints: one to list filenames per class (lightweight), one to serve individual images as files (browser-cached). Mislabel scan/confirm and prune preview/confirm move from the training page into the explore page as integrated tools. The Training Data stats section gets an "Explore & Tune" button; the standalone Mislabel Detection and Data Pruning sections are removed.

**Tech Stack:** Python/FastAPI (new endpoints in `routes/models.py`), Jinja2 template, vanilla JS, `FileResponse` for image serving, CSS variables from `style.css`

---

## UI Design

```
+--------------------------------------------------+
| ← Back to Training    Explore & Tune             |
| Type: [Digits ▼]                                 |
+-------------+------------------------------------+
| Classes     | Images: class "5" (50 images) 1/2  |
|             |                                    |
| 0    (42)   | +----+ +----+ +----+ +----+ +----+ |
| 1    (38)   | |    | |    | |    | |    | |    | |
| 2    (45)   | +----+ +----+ +----+ +----+ +----+ |
| 3    (41)   | +----+ +----+ +----+ +----+ +----+ |
| 4    (39)   | |    | |    | |    | |    | |    | |
| ● 5  (50)   | +----+ +----+ +----+ +----+ +----+ |
| 6    (43)   | +----+ +----+ +----+ +----+ +----+ |
| 7    (40)   | |    | |    | |    | |    | |    | |
| 8    (44)   | +----+ +----+ +----+ +----+ +----+ |
| 9    (37)   |                                    |
| NAN  (12)   | [Load More]                        |
+-------------+------------------------------------+
| Tools: [Scan for Mislabels] [Preview Pruning]    |
+--------------------------------------------------+
```

- **Left sidebar:** Class list with counts, click to select
- **Center:** Image thumbnail grid for selected class, paginated
- **Bottom toolbar:** Mislabel and prune tools (operate on the selected type)
- **Mislabel results** replace the image grid temporarily
- **Prune results** show inline below the toolbar

---

## Task 1: Backend — Image Listing + Serving APIs

**Files:**
- Modify: `watermeter/routes/models.py` (add two new endpoints)
- Modify: `tests/unit/test_api_routes.py` (add tests)

**Step 1: Write failing tests**

Add to `tests/unit/test_api_routes.py`:

```python
class TestTrainingDataImages:
    """Tests for training data image browsing endpoints."""

    def test_list_images_for_class(self, test_client, tmp_path):
        """GET /api/training-data/images returns filenames for a class."""
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)
        (gt_dir / "img001.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
        (gt_dir / "img002.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
        (gt_dir / "img003.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {"save_path": str(tmp_path)}
            }
            resp = test_client.get(
                "/api/training-data/images",
                params={"type": "digits", "class_name": "5", "offset": "0", "limit": "50"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total"] == 3
        assert len(data["filenames"]) == 3
        assert "img001.jpg" in data["filenames"]

    def test_list_images_pagination(self, test_client, tmp_path):
        """Pagination works with offset and limit."""
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)
        for i in range(10):
            (gt_dir / f"img{i:03d}.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {"save_path": str(tmp_path)}
            }
            resp = test_client.get(
                "/api/training-data/images",
                params={"type": "digits", "class_name": "5", "offset": "0", "limit": "3"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 10
        assert len(data["filenames"]) == 3

    def test_list_images_invalid_type(self, test_client):
        """Invalid type returns 400."""
        resp = test_client.get(
            "/api/training-data/images",
            params={"type": "invalid", "class_name": "5"},
        )
        assert resp.status_code == 400

    def test_serve_image(self, test_client, tmp_path):
        """GET /api/training-data/image serves a JPEG file."""
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)
        img_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 50
        (gt_dir / "img001.jpg").write_bytes(img_bytes)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {"save_path": str(tmp_path)}
            }
            resp = test_client.get("/api/training-data/image/digits/5/img001.jpg")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"

    def test_serve_image_not_found(self, test_client, tmp_path):
        """Missing image returns 404."""
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {"save_path": str(tmp_path)}
            }
            resp = test_client.get("/api/training-data/image/digits/5/nonexistent.jpg")

        assert resp.status_code == 404

    def test_serve_image_path_traversal(self, test_client, tmp_path):
        """Path traversal attempt returns 400."""
        gt_dir = tmp_path / "digits" / "ground_truth" / "5"
        gt_dir.mkdir(parents=True)

        with patch("watermeter.routes.models.watermeter_service") as mock_svc:
            mock_svc.get_service.return_value.config = {
                "low_confidence": {"save_path": str(tmp_path)}
            }
            resp = test_client.get("/api/training-data/image/digits/5/..%2F..%2Fetc%2Fpasswd")

        assert resp.status_code == 400
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_api_routes.py::TestTrainingDataImages -v`
Expected: FAIL — endpoints don't exist yet.

**Step 3: Implement the endpoints**

Add to `watermeter/routes/models.py`:

```python
from fastapi.responses import FileResponse
from pathlib import Path
import re

@router.get("/api/training-data/images")
async def list_training_images(
    type: str,
    class_name: str,
    offset: int = 0,
    limit: int = 50,
):
    """List image filenames for a ground truth class."""
    if type not in ("digits", "arrows"):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "type must be 'digits' or 'arrows'"},
        )

    # Validate class_name — no path separators
    if "/" in class_name or "\\" in class_name or ".." in class_name:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Invalid class name"},
        )

    config = watermeter_service.get_service().config
    save_path = config.get("low_confidence", {}).get("save_path", "/training")
    gt_dir = Path(save_path) / type / "ground_truth" / class_name

    if not gt_dir.is_dir():
        return {"success": True, "filenames": [], "total": 0}

    all_files = sorted(f.name for f in gt_dir.iterdir() if f.suffix.lower() == ".jpg")
    total = len(all_files)
    page = all_files[offset : offset + limit]

    return {"success": True, "filenames": page, "total": total}


@router.get("/api/training-data/image/{model_type}/{class_name}/{filename}")
async def serve_training_image(model_type: str, class_name: str, filename: str):
    """Serve a single training image from ground truth."""
    if model_type not in ("digits", "arrows"):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Invalid model type"},
        )

    # Validate filename — must be a simple .jpg name
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Invalid filename"},
        )

    if "/" in class_name or "\\" in class_name or ".." in class_name:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Invalid class name"},
        )

    config = watermeter_service.get_service().config
    save_path = config.get("low_confidence", {}).get("save_path", "/training")
    image_path = Path(save_path) / model_type / "ground_truth" / class_name / filename

    # Final safety: ensure resolved path is within ground_truth
    gt_base = (Path(save_path) / model_type / "ground_truth").resolve()
    if not image_path.resolve().is_relative_to(gt_base):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Invalid path"},
        )

    if not image_path.is_file():
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "Image not found"},
        )

    return FileResponse(image_path, media_type="image/jpeg")
```

Note: `FileResponse` may need to be imported — check if it's already imported in the file.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_api_routes.py::TestTrainingDataImages -v`
Expected: All 6 PASS.

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: All pass (no regressions).

**Step 5: Commit**

```bash
git add watermeter/routes/models.py tests/unit/test_api_routes.py
git commit -m "claude: add training data image listing and serving endpoints"
```

---

## Task 2: Explore Page — Route + Template

**Files:**
- Modify: `watermeter/app.py` (add page route)
- Create: `watermeter/templates/explore.html`

**Step 1: Add page route**

In `watermeter/app.py`, find where other page routes are defined (like `/training`, `/label`). Add:

```python
@app.get("/training/explore")
async def explore_page(request: Request):
    return templates.TemplateResponse("explore.html", {"request": request, "nav_active": "training"})
```

**Step 2: Create explore.html template**

Create `watermeter/templates/explore.html` with the full page structure. Follow the same patterns as `training.html` and `label.html`:
- `{% include '_theme.html' %}` for FOUC prevention
- `{% include '_nav.html' %}` for navigation (nav_active='training')
- Inline `<style>` block (page-specific CSS)
- External `<script src="/static/explore.js?v=1">`

The template should contain:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Explore & Tune - AI Water Meter</title>
    <link rel="stylesheet" href="/static/style.css">
    {% include '_theme.html' %}
    <style>
        /* --- Layout --- */
        .explore-header {
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 20px;
        }

        .back-link {
            color: var(--primary, #2563eb);
            text-decoration: none;
            font-size: 0.9rem;
        }

        .back-link:hover { text-decoration: underline; }

        .explore-type-select {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .explore-type-select select {
            padding: 6px 12px;
            border-radius: 6px;
            border: 1px solid var(--border-input, #cbd5e1);
            background: var(--bg-input, #fff);
            color: var(--text-dark);
            font-size: 0.9rem;
        }

        .explore-layout {
            display: grid;
            grid-template-columns: 180px 1fr;
            gap: 20px;
            min-height: 500px;
        }

        /* --- Class Sidebar --- */
        .class-sidebar {
            border: 1px solid var(--border, #e2e8f0);
            border-radius: 8px;
            overflow-y: auto;
            max-height: 600px;
        }

        .class-item {
            display: flex;
            justify-content: space-between;
            padding: 8px 12px;
            cursor: pointer;
            border-bottom: 1px solid var(--border, #e2e8f0);
            font-size: 0.9rem;
            transition: background 0.1s;
        }

        .class-item:last-child { border-bottom: none; }
        .class-item:hover { background: var(--bg-alt, #f1f5f9); }

        .class-item.active {
            background: var(--info-bg, #dbeafe);
            font-weight: 600;
            border-left: 3px solid var(--primary, #2563eb);
        }

        .class-item .class-count {
            color: var(--text-light);
            font-size: 0.8rem;
        }

        /* --- Image Grid --- */
        .image-panel {
            border: 1px solid var(--border, #e2e8f0);
            border-radius: 8px;
            padding: 16px;
        }

        .image-panel-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }

        .image-panel-header h3 { margin: 0; font-size: 1rem; }

        .image-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
            gap: 8px;
            margin-bottom: 16px;
        }

        .image-grid img {
            width: 100%;
            height: 100px;
            object-fit: contain;
            border-radius: 4px;
            background: var(--bg-inset, #f1f5f9);
            border: 1px solid var(--border, #e2e8f0);
            cursor: pointer;
            transition: border-color 0.15s;
        }

        .image-grid img:hover {
            border-color: var(--primary, #2563eb);
        }

        .load-more-row {
            text-align: center;
            margin-bottom: 16px;
        }

        .empty-state {
            text-align: center;
            color: var(--text-light);
            padding: 40px;
        }

        /* --- Tools Bar --- */
        .tools-bar {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 16px;
            margin-top: 20px;
            border: 1px solid var(--border, #e2e8f0);
            border-radius: 8px;
            background: var(--bg-surface, #fff);
        }

        .tools-bar .tools-label {
            font-weight: 600;
            font-size: 0.9rem;
            color: var(--text-light);
        }

        /* --- Tool Results (mislabel/prune) --- */
        .tool-results {
            margin-top: 16px;
            border: 1px solid var(--border, #e2e8f0);
            border-radius: 8px;
            padding: 16px;
        }

        .tool-status {
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 12px;
            font-size: 0.9rem;
        }

        .tool-status.scanning { background: var(--info-bg, #dbeafe); }
        .tool-status.complete { background: var(--success-bg, #d1fae5); }
        .tool-status.error { background: var(--danger-bg, #fee2e2); }

        /* Reuse mislabel card styles */
        .mislabel-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
            gap: 10px;
            margin-bottom: 12px;
            max-height: 400px;
            overflow-y: auto;
        }

        .mislabel-card {
            border: 2px solid var(--border, #e2e8f0);
            border-radius: 8px;
            padding: 6px;
            cursor: pointer;
            text-align: center;
            transition: border-color 0.15s, background 0.15s;
        }

        .mislabel-card:hover { border-color: var(--primary, #2563eb); }
        .mislabel-card.selected { border-color: var(--primary, #2563eb); background: var(--info-bg, #dbeafe); }
        .mislabel-card img { width: 100%; height: 90px; object-fit: contain; border-radius: 4px; background: var(--bg-inset, #f1f5f9); margin-bottom: 4px; }
        .mislabel-card .mislabel-labels { font-size: 0.75rem; line-height: 1.3; }
        .mislabel-card .label-current { color: var(--danger, #dc2626); text-decoration: line-through; }
        .mislabel-card .label-predicted { color: var(--success, #059669); font-weight: 600; }

        /* Prune results */
        .prune-class-list {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
            gap: 6px;
            margin-bottom: 12px;
            max-height: 250px;
            overflow-y: auto;
        }

        .prune-class-item {
            display: flex;
            justify-content: space-between;
            padding: 4px 10px;
            border-radius: 4px;
            background: var(--bg-surface, #fff);
            border: 1px solid var(--border, #e2e8f0);
            font-size: 0.8rem;
        }

        .prune-removable { color: var(--danger, #dc2626); font-weight: 600; }

        .tool-actions {
            display: flex;
            justify-content: space-between;
            align-items: center;
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

        .btn-sm:hover { background: var(--bg-alt, #f1f5f9); }

        /* Toast message */
        .message {
            display: none;
            position: fixed;
            top: 16px;
            right: 16px;
            padding: 12px 20px;
            border-radius: 8px;
            font-size: 0.9rem;
            z-index: 1000;
            animation: slideIn 0.2s ease;
        }

        .message.success { display: block; background: var(--success-bg, #d1fae5); color: var(--success, #059669); border: 1px solid var(--success); }
        .message.error { display: block; background: var(--danger-bg, #fee2e2); color: var(--danger, #dc2626); border: 1px solid var(--danger); }

        @keyframes slideIn { from { transform: translateY(-10px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }

        @media (max-width: 768px) {
            .explore-layout { grid-template-columns: 1fr; }
            .class-sidebar { max-height: 200px; }
        }
    </style>
</head>
<body>
    {% include '_nav.html' %}
    <div class="container">
        <div id="message" class="message"></div>

        <div class="explore-header">
            <a href="/training" class="back-link">&larr; Back to Training</a>
            <h1>Explore &amp; Tune</h1>
            <div class="explore-type-select">
                <label for="explore-type">Type:</label>
                <select id="explore-type" onchange="onTypeChange()">
                    <option value="digits">Digits</option>
                    <option value="arrows">Arrows</option>
                </select>
            </div>
        </div>

        <div class="explore-layout">
            <div class="class-sidebar" id="class-sidebar">
                <div class="empty-state">Loading...</div>
            </div>

            <div class="image-panel" id="image-panel">
                <div class="image-panel-header">
                    <h3 id="image-panel-title">Select a class</h3>
                    <span id="image-panel-page" style="font-size:0.85rem;color:var(--text-light)"></span>
                </div>
                <div id="image-grid" class="image-grid"></div>
                <div id="load-more-row" class="load-more-row" style="display:none">
                    <button class="btn btn-sm" onclick="loadMoreImages()">Load More</button>
                </div>
                <div id="image-empty" class="empty-state">Click a class to browse its images.</div>
            </div>
        </div>

        <!-- Tools Bar -->
        <div class="tools-bar">
            <span class="tools-label">Tools:</span>
            <button class="btn btn-primary" id="mislabel-scan-btn" onclick="startMislabelScan()">Scan for Mislabels</button>
            <button class="btn btn-primary" id="prune-preview-btn" onclick="startPrunePreview()">Preview Pruning</button>
        </div>

        <!-- Mislabel Results -->
        <div id="mislabel-results-section" class="tool-results" style="display:none">
            <div id="mislabel-status" class="tool-status"></div>
            <div id="mislabel-results" style="display:none">
                <div class="tool-actions">
                    <span id="mislabel-count"></span>
                    <div>
                        <button class="btn btn-sm" onclick="toggleAllMislabels(true)">Select All</button>
                        <button class="btn btn-sm" onclick="toggleAllMislabels(false)">Deselect All</button>
                    </div>
                </div>
                <div id="mislabel-grid" class="mislabel-grid"></div>
                <div class="tool-actions">
                    <div></div>
                    <button class="btn btn-primary" id="mislabel-confirm-btn" onclick="confirmMislabels()">Move Selected to Input Queue</button>
                </div>
            </div>
        </div>

        <!-- Prune Results -->
        <div id="prune-results-section" class="tool-results" style="display:none">
            <div id="prune-status" class="tool-status"></div>
            <div id="prune-results" style="display:none">
                <div id="prune-summary" style="font-weight:600;margin-bottom:8px"></div>
                <div id="prune-class-list" class="prune-class-list"></div>
                <div class="tool-actions">
                    <div></div>
                    <button class="btn btn-primary" id="prune-confirm-btn" onclick="confirmPrune()">Confirm &amp; Delete</button>
                </div>
            </div>
        </div>
    </div>

    <script src="/static/explore.js?v=1"></script>
</body>
</html>
```

**Step 3: Commit**

```bash
git add watermeter/app.py watermeter/templates/explore.html
git commit -m "claude: add explore page route and template"
```

---

## Task 3: Explore Page — JavaScript (Class Browser + Image Grid)

**Files:**
- Create: `watermeter/static/explore.js`

**Step 1: Create explore.js with core browsing functionality**

```javascript
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
```

**Step 2: Commit**

```bash
git add watermeter/static/explore.js
git commit -m "claude: add explore.js with class browser and image grid"
```

---

## Task 4: Explore Page — Mislabel + Prune Tools

**Files:**
- Modify: `watermeter/static/explore.js` (append tool functions)

**Step 1: Add mislabel scan/confirm functions to explore.js**

Append to `explore.js`. These are adapted from the training.js versions, using the same API contracts but working with the explore page's DOM elements:

```javascript
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
```

**Step 2: Commit**

```bash
git add watermeter/static/explore.js
git commit -m "claude: add mislabel and prune tools to explore page"
```

---

## Task 5: Training Page — Add Button, Remove Sections

**Files:**
- Modify: `watermeter/templates/training.html`
- Modify: `watermeter/static/training.js`

**Step 1: Add "Explore & Tune" button to stats section**

In `training.html`, find the `.stats-actions` div (contains the dedup button, ~L1538). Add an "Explore & Tune" link button before the dedup button:

```html
<a href="/training/explore" class="btn btn-primary" style="text-decoration:none">Explore &amp; Tune</a>
```

**Step 2: Remove `#mislabel-section`**

Delete the entire `<section id="mislabel-section">...</section>` block (~L1547-1581).

**Step 3: Remove `#prune-section`**

Delete the entire `<section id="prune-section">...</section>` block (~L1584-1612, line numbers will shift after step 2).

**Step 4: Remove mislabel + prune CSS**

In the `<style>` block, remove all CSS rules for:
- `.mislabel-controls`, `.mislabel-type-select`, `.mislabel-status.*`, `.mislabel-results-header`, `.mislabel-select-controls`, `.mislabel-grid`, `.mislabel-card.*`, `.mislabel-labels`, `.label-current`, `.label-predicted`, `.mislabel-actions`
- `.prune-controls`, `.prune-type-select`, `.prune-status.*`, `.prune-summary`, `.prune-class-list`, `.prune-class-item`, `.prune-removable`, `.prune-actions`
- `.btn-sm` (keep if used elsewhere — check first; if only mislabel/prune use it, remove)

**Step 5: Remove mislabel + prune JS from training.js**

Remove these functions from `training.js`:
- `startMislabelScan()`
- `renderMislabelResults()`
- `toggleAllMislabels()`
- `confirmMislabels()`
- `startPrunePreview()`
- `renderPrunePreview()`
- `confirmPrune()`

Keep `runDedup()` — it stays on the training page.

**Step 6: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: All pass. (Mislabel/prune backend tests don't depend on the frontend.)

**Step 7: Commit**

```bash
git add watermeter/templates/training.html watermeter/static/training.js
git commit -m "claude: add Explore & Tune button, remove standalone mislabel/prune sections"
```

---

## Task Summary

| Task | What | Depends On |
|------|------|------------|
| 1 | Backend: image listing + serving APIs with tests | — |
| 2 | Explore page: route + template (HTML/CSS) | Task 1 |
| 3 | Explore page: JS core (class browser + image grid) | Task 2 |
| 4 | Explore page: mislabel + prune tool functions | Task 3 |
| 5 | Training page: add button, remove mislabel/prune sections | Task 4 |

**Sequential chain** — each task builds on the previous. Task 1 can start immediately.
