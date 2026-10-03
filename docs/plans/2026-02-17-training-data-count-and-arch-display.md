# Training Data Count & Architecture Display Name — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Store how many training samples each model was trained with (shown absolute + relative to current dataset), and save the architecture display name in model metadata instead of only the codename.

**Architecture:** Add `training_samples`, `val_samples`, and `architecture_display` fields to model metadata at training time. Display them in the model table. Relative percentage is computed client-side by comparing stored count against current ground truth stats.

**Tech Stack:** Python (FastAPI backend), JavaScript (vanilla frontend), HTMX/Jinja2 templates

---

## Task 1: Store training sample counts in metadata

**Files:**
- Modify: `watermeter/training_manager.py` (lines ~525-545, ~744-772)

**Step 1: Write the failing test**

Create test file `tests/test_training_sample_count.py`:

```python
"""Test that training metadata includes sample counts."""
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


def test_metadata_includes_training_sample_counts(tmp_path):
    """After training, metadata.json must contain training_samples and val_samples."""
    # Create a fake metadata.json as would be written by training_manager
    metadata = {
        "model_type": "digits",
        "architecture": "resnet18",
        "resolution": 128,
        "seed": 42,
        "epochs": 20,
        "batch_size": 16,
        "learning_rate": 0.0003,
        "best_val_loss": 0.00937,
        "best_epoch": 14,
        "training_time": 123.4,
        "num_params": 11182155,
        "created_at": "2026-02-17T10:00:00",
        "training_mode": "discrete",
        "num_classes": 11,
        "best_val_acc": 100.0,
        "classes": ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "NAN"],
        "training_samples": 2840,
        "val_samples": 710,
    }
    assert "training_samples" in metadata
    assert "val_samples" in metadata
    assert isinstance(metadata["training_samples"], int)
    assert isinstance(metadata["val_samples"], int)
    assert metadata["training_samples"] > 0
    assert metadata["val_samples"] > 0
```

**Step 2: Run test to verify it passes (baseline sanity check)**

Run: `.venv/bin/python -m pytest tests/test_training_sample_count.py -v`
Expected: PASS (this is a data shape test, not integration)

**Step 3: Add sample counts to training metadata**

In `watermeter/training_manager.py`, find the section where `train_ds` and `val_ds` are created after the stratified split (around L525-545). Store the counts in local variables, then include them in the metadata dict that gets saved (around L744-772).

**Digits classification path** (~L530-543):
After the line `job.add_log(f"Train samples: {len(train_ds)}, Val samples: {len(val_ds)}")`, store:
```python
train_sample_count = len(train_ds)
val_sample_count = len(val_ds)
```

**Arrows classification path** (~L555-570):
Same pattern — after the split and log line, store:
```python
train_sample_count = len(train_ds)
val_sample_count = len(val_ds)
```

**Arrows regression path** (~L514-527):
Same pattern:
```python
train_sample_count = len(train_ds)
val_sample_count = len(val_ds)
```

Then in the metadata dict (around L744-772), add two fields:
```python
"training_samples": train_sample_count,
"val_samples": val_sample_count,
```

Initialize `train_sample_count = 0` and `val_sample_count = 0` at the top of `_execute_training()` so the variables always exist even if training fails early.

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All tests pass

**Step 5: Commit**

```bash
git add watermeter/training_manager.py tests/test_training_sample_count.py
git commit -m "claude: store training_samples and val_samples in model metadata"
```

---

## Task 2: Store architecture display name in metadata

**Files:**
- Modify: `watermeter/training_manager.py` (~L744-772 metadata dict, ~L475 model creation area)
- Modify: `watermeter/routes/training.py` (~L29-58 TrainingConfig, ~L84-108 start endpoint)
- Modify: `watermeter/static/training.js` (~L925-995 startTraining, ~L998-1073 startMatrixTraining)

**Step 1: Add `architecture_display` to TrainingConfig**

In `watermeter/routes/training.py`, add a new optional field to the `TrainingConfig` pydantic model:
```python
architecture_display: str = ""  # Human-readable architecture name (e.g., "ResNet-18")
```

**Step 2: Pass `architecture_display` through to metadata**

In `watermeter/training_manager.py`, the `start_training()` method receives the config dict. Pass `architecture_display` through to `_execute_training()` and include it in the metadata dict:

```python
"architecture_display": config.get("architecture_display", ""),
```

**Step 3: Send display name from frontend**

In `watermeter/static/training.js`:

In `startTraining()` (~L925-995), after getting the architecture value, also resolve the display name:
```javascript
const archDisplay = prettifyModelName(architecture);
```
Add `architecture_display: archDisplay` to the request body.

In `startMatrixTraining()` (~L998-1073), same pattern for each architecture in the matrix:
```javascript
const archDisplay = prettifyModelName(arch);
```
Add `architecture_display: archDisplay` to each job config.

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All tests pass

**Step 5: Commit**

```bash
git add watermeter/routes/training.py watermeter/training_manager.py watermeter/static/training.js
git commit -m "claude: store architecture_display in model metadata"
```

---

## Task 3: Display sample counts and architecture display name in model table

**Files:**
- Modify: `watermeter/static/training.js` (~L1224-1366 renderModels, ~L1210-1277 sorting)

**Step 1: Add architecture display name to model table**

In `renderModels()`, change the Architecture column from:
```javascript
<td>${model.architecture || '-'}</td>
```
to:
```javascript
<td title="${model.architecture}">${model.architecture_display || prettifyModelName(model.architecture) || '-'}</td>
```

This uses `architecture_display` from metadata if available, falls back to `prettifyModelName()` for older models, and shows the raw codename as tooltip.

**Step 2: Add training samples column to the model table**

Add a new column "Samples" between "Resolution" and "Accuracy".

In the table header (inside `renderModels()`), add:
```javascript
<th data-sort="samples">Samples</th>
```

For the cell content, show absolute count. If training stats are loaded, also show relative percentage:
```javascript
const samples = model.training_samples;
let samplesHtml = '-';
if (samples) {
    samplesHtml = `${samples.toLocaleString()}`;
    // Calculate relative percentage against current ground truth
    const currentTotal = getCurrentDatasetTotal(model.model_type);
    if (currentTotal > 0) {
        const pct = Math.round((samples / currentTotal) * 100);
        const color = pct >= 100 ? 'var(--success)' : pct >= 80 ? 'var(--warning)' : 'var(--error, #e74c3c)';
        samplesHtml += ` <span style="color:${color};font-size:0.85em">(${pct}%)</span>`;
    }
}
```

**Step 3: Add helper function to get current dataset total**

Add a helper function that reads the cached training stats to compute the total sample count for a model type. The training stats are already fetched by `loadTrainingStats()` — cache them in a module-level variable:

```javascript
let cachedTrainingStats = null;

// Inside loadTrainingStats(), after successful fetch:
cachedTrainingStats = data;

function getCurrentDatasetTotal(modelType) {
    if (!cachedTrainingStats?.ground_truth?.[modelType]) return 0;
    const classCounts = cachedTrainingStats.ground_truth[modelType];
    return Object.values(classCounts).reduce((sum, count) => sum + count, 0);
}
```

The percentage shows how the model's training data compares to the *current* ground truth total. This lets the user see at a glance if a model was trained on an older, smaller dataset.

**Step 4: Add sorting support for the new column**

In the sort logic (~L1210-1277), add a case for the `samples` sort key:
```javascript
case 'samples':
    aVal = a.training_samples || 0;
    bVal = b.training_samples || 0;
    break;
```

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest -x -q`
Expected: All tests pass

**Step 6: Manual UI test**

Start debug container: `./debug.sh --detach`
Open `http://localhost:8002/training` in browser.
Verify:
- Architecture column shows pretty names (with codename as tooltip)
- Samples column shows "-" for older models without data
- Newly trained models (after code deploy) would show sample counts

**Step 7: Commit**

```bash
git add watermeter/static/training.js
git commit -m "claude: display training sample count and architecture display name in model table"
```

---

## Task 4: Backfill sample counts for existing models from training logs

**Files:**
- Create: `scripts/backfill_training_samples.py`

**Step 1: Write backfill script**

This script reads each model's `training.log` file, extracts the "Train samples: X, Val samples: Y" line via regex, and patches the metadata.json.

```python
#!/usr/bin/env python3
"""Backfill training_samples and val_samples from training logs into metadata.json."""
import json
import re
import sys
from pathlib import Path

MODELS_DIR = Path("/app/models")
SAMPLE_RE = re.compile(r"Train samples:\s*(\d+),\s*Val samples:\s*(\d+)")


def backfill():
    updated = 0
    skipped = 0
    for model_type in ["digits", "arrows"]:
        type_dir = MODELS_DIR / model_type
        if not type_dir.exists():
            continue
        for model_dir in sorted(type_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            meta_path = model_dir / "metadata.json"
            log_path = model_dir / "training.log"
            if not meta_path.exists():
                continue

            with open(meta_path) as f:
                metadata = json.load(f)

            # Skip if already has sample counts
            if metadata.get("training_samples"):
                skipped += 1
                continue

            # Skip failed models
            if metadata.get("status") == "failed":
                skipped += 1
                continue

            # Try to extract from training log
            if not log_path.exists():
                print(f"  SKIP {model_dir.name}: no training.log")
                skipped += 1
                continue

            log_text = log_path.read_text()
            match = SAMPLE_RE.search(log_text)
            if not match:
                print(f"  SKIP {model_dir.name}: no sample count in log")
                skipped += 1
                continue

            train_samples = int(match.group(1))
            val_samples = int(match.group(2))
            metadata["training_samples"] = train_samples
            metadata["val_samples"] = val_samples

            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2)

            print(f"  OK   {model_dir.name}: {train_samples} train, {val_samples} val")
            updated += 1

    print(f"\nDone: {updated} updated, {skipped} skipped")


if __name__ == "__main__":
    backfill()
```

**Step 2: Test locally against debug container**

Run inside debug container:
```bash
docker exec watermeter-dashboard-debug python /app/scripts/backfill_training_samples.py
```
Expected: Existing models get sample counts patched into their metadata.

**Step 3: Commit**

```bash
git add scripts/backfill_training_samples.py
git commit -m "claude: add backfill script for training sample counts from logs"
```

---

## Task 5: Backfill architecture_display for existing models

**Files:**
- Modify: `scripts/backfill_training_samples.py` (extend to also backfill architecture_display)

**Step 1: Add architecture_display backfill**

Extend the backfill script to also add `architecture_display` for models that don't have it. Use a Python dict mapping codenames to display names (matching the JS `CURATED_LABELS` + `prettifyModelName` logic):

```python
# Architecture display name mapping
ARCH_DISPLAY = {
    "resnet18": "ResNet-18",
    "resnet34": "ResNet-34",
    "resnet50": "ResNet-50",
    "efficientnet_lite0": "EfficientNet-Lite0",
    "mobilenetv3_small_100": "MobileNetV3 Small",
    "efficientnetv2_rw_t": "EfficientNetV2-RW Tiny",
    "efficientnetv2_rw_s": "EfficientNetV2-RW Small",
    "efficientnetv2_rw_m": "EfficientNetV2-RW Medium",
    "convnext_nano": "ConvNeXt Nano",
    "convnextv2_atto": "ConvNeXt-V2 Atto",
    "convnextv2_tiny": "ConvNeXt-V2 Tiny",
    "resnext50_32x4d": "ResNeXt-50 32x4d",
}

def prettify_arch(codename):
    """Python equivalent of JS prettifyModelName()."""
    if codename in ARCH_DISPLAY:
        return ARCH_DISPLAY[codename]
    # Strip training recipe suffix
    base = codename.split(".")[0]
    # Simple fallback: capitalize and clean up
    return base.replace("_", " ").title()
```

Then in the backfill loop, after the sample count section:
```python
if not metadata.get("architecture_display") and metadata.get("architecture"):
    metadata["architecture_display"] = prettify_arch(metadata["architecture"])
    arch_updated = True
```

**Step 2: Run against debug container and verify**

```bash
docker exec watermeter-dashboard-debug python /app/scripts/backfill_training_samples.py
```

**Step 3: Commit**

```bash
git add scripts/backfill_training_samples.py
git commit -m "claude: extend backfill script to add architecture_display names"
```

---

## Summary of Changes

| File | Change |
|------|--------|
| `watermeter/training_manager.py` | Store `training_samples`, `val_samples`, `architecture_display` in metadata |
| `watermeter/routes/training.py` | Add `architecture_display` to `TrainingConfig` |
| `watermeter/static/training.js` | Send display name, show samples column + pretty arch names in table |
| `scripts/backfill_training_samples.py` | Backfill script for existing models |
| `tests/test_training_sample_count.py` | Test for metadata shape |

**No template changes needed** — the model table is rendered entirely in JavaScript.
