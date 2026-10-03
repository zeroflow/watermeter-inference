# Training Pipeline: AdamW + Cosine Annealing LR Scheduler

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve model training quality by switching from Adam to AdamW with weight decay, lowering the base learning rate from 1e-3 to 3e-4, adding a cosine annealing LR scheduler, logging the current LR during training, and making the learning rate configurable from the UI.

**Architecture:** Changes span 5 files:
1. `watermeter/training_manager.py` -- optimizer + scheduler in `_execute_training` (L566)
2. `watermeter/routes/training.py` -- `TrainingConfig` Pydantic model (L29)
3. `watermeter/static/training.js` -- `startTraining()` form handler (L647)
4. `watermeter/templates/training.html` -- training form HTML (L1158)
5. `tests/unit/test_training_config.py` -- new test file

**Tech Stack:** Python, PyTorch, FastAPI/Pydantic, JavaScript, Jinja2 HTML

---

### Analysis: Current State

**Optimizer (L566 of `training_manager.py`):**
```python
optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
```
- Uses vanilla Adam (no weight decay)
- LR is hardcoded at 1e-3 (too high for fine-tuning pretrained models from timm)
- No learning rate schedule -- constant LR for all epochs

**Training loop (L586-L700):**
- Standard epoch loop with `optimizer.step()` per batch
- No `scheduler.step()` call
- Progress logging includes loss and accuracy but NOT the current LR

**TrainingConfig (L29 of `routes/training.py`):**
```python
class TrainingConfig(BaseModel):
    model_type: str
    architecture: str
    resolution: int
    seeds: List[int]
    epochs: int = 20
    batch_size: int = 16
    step_size: float = 1.0
    training_mode: str = "discrete"
    notes: str = ""
    auto_benchmark: bool = True
```
- No `learning_rate` field

**JS form handler (`startTraining` in training.js L647-L708):**
- Reads form values, builds config dict, sends to `/api/training/start`
- No learning rate field in the form
- `batch_size` is hardcoded to 16 in JS

**Both code paths (classification AND regression) use the same optimizer** -- confirmed at L566. The optimizer is created once and used for both. Good -- we only need to change it in one place.

---

### Task 1: Write unit tests for TrainingConfig learning_rate field

**Files:**
- Create: `tests/unit/test_training_config.py`

**Step 1: Write the failing tests**

```python
"""Tests for TrainingConfig Pydantic model — learning_rate field."""
import pytest
from pydantic import ValidationError


class TestTrainingConfigLearningRate:
    """Test the learning_rate field on TrainingConfig."""

    def test_default_learning_rate(self):
        """Default learning rate should be 3e-4."""
        from watermeter.routes.training import TrainingConfig

        config = TrainingConfig(
            model_type="digits",
            architecture="resnet18",
            resolution=128,
            seeds=[42],
        )
        assert config.learning_rate == pytest.approx(3e-4)

    def test_custom_learning_rate(self):
        """Custom learning rate should be accepted."""
        from watermeter.routes.training import TrainingConfig

        config = TrainingConfig(
            model_type="digits",
            architecture="resnet18",
            resolution=128,
            seeds=[42],
            learning_rate=1e-4,
        )
        assert config.learning_rate == pytest.approx(1e-4)

    def test_learning_rate_zero_rejected(self):
        """LR of 0 should be rejected."""
        from watermeter.routes.training import TrainingConfig

        with pytest.raises(ValidationError):
            TrainingConfig(
                model_type="digits",
                architecture="resnet18",
                resolution=128,
                seeds=[42],
                learning_rate=0.0,
            )

    def test_learning_rate_negative_rejected(self):
        """Negative LR should be rejected."""
        from watermeter.routes.training import TrainingConfig

        with pytest.raises(ValidationError):
            TrainingConfig(
                model_type="digits",
                architecture="resnet18",
                resolution=128,
                seeds=[42],
                learning_rate=-1e-3,
            )

    def test_learning_rate_too_high_rejected(self):
        """LR above 1.0 should be rejected."""
        from watermeter.routes.training import TrainingConfig

        with pytest.raises(ValidationError):
            TrainingConfig(
                model_type="digits",
                architecture="resnet18",
                resolution=128,
                seeds=[42],
                learning_rate=2.0,
            )

    def test_learning_rate_passed_through_dict(self):
        """LR should survive .dict() serialization for the training manager."""
        from watermeter.routes.training import TrainingConfig

        config = TrainingConfig(
            model_type="digits",
            architecture="resnet18",
            resolution=128,
            seeds=[42],
            learning_rate=5e-4,
        )
        d = config.dict()
        assert d["learning_rate"] == pytest.approx(5e-4)
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_training_config.py -v`
Expected: FAIL -- `TrainingConfig` has no field `learning_rate`

**Step 3: Commit**

```bash
git add tests/unit/test_training_config.py
git commit -m "claude: add failing tests for TrainingConfig learning_rate field"
```

---

### Task 2: Add learning_rate field to TrainingConfig

**Files:**
- Modify: `watermeter/routes/training.py` (L29-41)

**Step 1: Add the learning_rate field with validation**

In `watermeter/routes/training.py`, add `learning_rate` to the `TrainingConfig` class. Insert after the `auto_benchmark` field (L41):

```python
class TrainingConfig(BaseModel):
    """Request model for training configuration."""

    model_type: str  # "digits" or "arrows"
    architecture: str  # e.g., "resnext50_32x4d"
    resolution: int  # e.g., 128
    seeds: List[int]  # e.g., [42, 67, 69]
    epochs: int = 20
    batch_size: int = 16
    step_size: float = 1.0  # For arrows only
    training_mode: str = "discrete"  # "discrete" or "continuous"
    notes: str = ""
    auto_benchmark: bool = True
    learning_rate: float = 3e-4

    @field_validator("training_mode")
    @classmethod
    def validate_training_mode(cls, v):
        if v not in ("discrete", "continuous"):
            raise ValueError("training_mode must be 'discrete' or 'continuous'")
        return v

    @field_validator("learning_rate")
    @classmethod
    def validate_learning_rate(cls, v):
        if v <= 0:
            raise ValueError("learning_rate must be positive")
        if v > 1.0:
            raise ValueError("learning_rate must be <= 1.0")
        return v
```

**Step 2: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_training_config.py -v`
Expected: All 6 tests PASS

**Step 3: Run full unit tests for regressions**

Run: `.venv/bin/python -m pytest tests/unit/ -x -q`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add watermeter/routes/training.py
git commit -m "claude: add learning_rate field to TrainingConfig with validation"
```

---

### Task 3: Switch to AdamW + cosine annealing in _execute_training

**Files:**
- Modify: `watermeter/training_manager.py`

**Step 1: Replace optimizer creation (L566)**

Find line 566:
```python
            optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
```

Replace with:
```python
            # Optimizer: AdamW with weight decay (better for fine-tuning pretrained models)
            lr = config.get("learning_rate", 3e-4)
            optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
```

**Step 2: Add scheduler after optimizer (after L566, before `if model_type == "arrows"`)**

Insert right after the optimizer creation, before the `if model_type == "arrows" and training_mode == "continuous":` criterion block (L568):

```python
            # LR scheduler: cosine annealing (decays from lr to eta_min over all epochs)
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                optimizer, T_max=epochs, eta_min=1e-6
            )

            job.add_log(f"Optimizer: AdamW (lr={lr}, weight_decay=1e-4)")
            job.add_log(f"LR scheduler: CosineAnnealingLR (T_max={epochs}, eta_min=1e-6)")
```

**Step 3: Add `scheduler.step()` at the end of each epoch (after validation, before epoch timing)**

Find the section after validation metrics are computed and best model tracking is done, near L673 (just before `epoch_time = time.time() - epoch_start`). Insert `scheduler.step()`:

```python
                # Step LR scheduler
                scheduler.step()
```

Insert this line right BEFORE:
```python
                epoch_time = time.time() - epoch_start
```

**Step 4: Add current LR to progress log messages**

For the **regression** epoch log (around L685-L688), update to include LR:

Find:
```python
                    job.add_log(
                        f"Epoch {epoch+1}/{epochs} - Loss: {avg_loss:.4f} - MAE: {mae:.4f} "
                        f"- RMSE: {rmse:.4f} - Within-half: {within_half:.1f}% - Time: {epoch_time:.1f}s"
                    )
```

Replace with:
```python
                    current_lr = scheduler.get_last_lr()[0]
                    job.add_log(
                        f"Epoch {epoch+1}/{epochs} - Loss: {avg_loss:.4f} - MAE: {mae:.4f} "
                        f"- RMSE: {rmse:.4f} - Within-half: {within_half:.1f}% - LR: {current_lr:.2e} - Time: {epoch_time:.1f}s"
                    )
```

For the **classification** epoch log (around L698-L700), update similarly:

Find:
```python
                    job.add_log(
                        f"Epoch {epoch+1}/{epochs} - Loss: {avg_loss:.4f} - Val Acc: {val_acc:.2f}% - Time: {epoch_time:.1f}s"
                    )
```

Replace with:
```python
                    current_lr = scheduler.get_last_lr()[0]
                    job.add_log(
                        f"Epoch {epoch+1}/{epochs} - Loss: {avg_loss:.4f} - Val Acc: {val_acc:.2f}% - LR: {current_lr:.2e} - Time: {epoch_time:.1f}s"
                    )
```

**Step 5: Add LR to progress update dicts**

For the **regression** progress update (around L677-L684), add `learning_rate`:

Find the `job.update_progress(` call for regression and add `learning_rate=round(current_lr, 8)`:

```python
                    current_lr = scheduler.get_last_lr()[0]
                    job.update_progress(
                        current_epoch=epoch + 1,
                        total_epochs=epochs,
                        train_loss=round(avg_loss, 4),
                        val_accuracy=round(within_half, 2),
                        epoch_duration=round(epoch_time, 1),
                        learning_rate=round(current_lr, 8),
                        message=f"Epoch {epoch+1}/{epochs}: Loss={avg_loss:.4f}, MAE={mae:.4f}, Within-half={within_half:.1f}%",
                    )
```

For the **classification** progress update (around L690-L697), add `learning_rate`:

```python
                    current_lr = scheduler.get_last_lr()[0]
                    job.update_progress(
                        current_epoch=epoch + 1,
                        total_epochs=epochs,
                        train_loss=round(avg_loss, 4),
                        val_accuracy=round(val_acc, 2),
                        epoch_duration=round(epoch_time, 1),
                        learning_rate=round(current_lr, 8),
                        message=f"Epoch {epoch+1}/{epochs}: Loss={avg_loss:.4f}, Val Acc={val_acc:.2f}%",
                    )
```

Note: Move `current_lr = scheduler.get_last_lr()[0]` BEFORE the `job.update_progress()` call. Since `scheduler.step()` was already called, `get_last_lr()` returns the LR for the next epoch. This is fine -- it shows the LR that was just applied.

**Step 6: Add learning_rate to saved metadata**

Find the metadata dict construction (around L725-L737). Add the learning rate:

In the metadata dict, after `"batch_size": batch_size,` add:
```python
                "learning_rate": lr,
```

**Step 7: Run full unit tests**

Run: `.venv/bin/python -m pytest tests/unit/ -x -q`
Expected: All tests PASS (the training loop itself is not unit-tested, but nothing structural should break)

**Step 8: Commit**

```bash
git add watermeter/training_manager.py
git commit -m "claude: switch to AdamW optimizer with cosine annealing LR scheduler"
```

---

### Task 4: Add Learning Rate field to the training form UI

**Files:**
- Modify: `watermeter/templates/training.html` (around L1192)
- Modify: `watermeter/static/training.js` (around L647-L676)

**Step 1: Add LR field to the HTML form**

In `watermeter/templates/training.html`, find the Epochs form group (L1190-L1193):
```html
                        <div class="form-group">
                            <label for="epochs">Epochs</label>
                            <input type="number" id="epochs" value="20" min="1" max="100" required>
                        </div>
```

Insert a new form group right AFTER it (before the step-size-group div):
```html
                        <div class="form-group">
                            <label for="learning-rate">Learning Rate</label>
                            <input type="number" id="learning-rate" value="0.0003" min="0.000001" max="1.0" step="0.0001">
                            <span class="hint">Default: 3e-4 (good for fine-tuning)</span>
                        </div>
```

**Step 2: Add LR display to progress metrics**

In `watermeter/templates/training.html`, find the progress metrics div (L1270-L1287). Add a new metric for LR after ETA:

```html
                    <div class="metric">
                        <span class="metric-label">Learning Rate</span>
                        <span class="metric-value" id="metric-lr">-</span>
                    </div>
```

Insert this right before the closing `</div>` of the `progress-metrics` div.

**Step 3: Update JS to read LR from form and send it**

In `watermeter/static/training.js`, find the `startTraining` function (L647). After:
```javascript
    const epochs = parseInt(document.getElementById('epochs').value);
```

Add:
```javascript
    const learningRate = parseFloat(document.getElementById('learning-rate').value);
```

Then in the config object construction (around L667-L676), add `learning_rate`:
```javascript
    const config = {
        model_type: modelType,
        architecture: architecture,
        resolution: resolution,
        seeds: seeds,
        epochs: epochs,
        batch_size: 16,
        learning_rate: learningRate,
        notes: notes,
        auto_benchmark: autoBenchmark
    };
```

**Step 4: Update JS progress display to show LR**

In `watermeter/static/training.js`, find the `updateProgress` function (L555). After the ETA display logic (around L597), add LR display:

```javascript
    // Learning rate
    const lrEl = document.getElementById('metric-lr');
    const lr = progress.learning_rate;
    if (lr !== null && lr !== undefined) {
        lrEl.textContent = lr.toExponential(2);
    } else {
        lrEl.textContent = '-';
    }
```

**Step 5: Commit**

```bash
git add watermeter/templates/training.html watermeter/static/training.js
git commit -m "claude: add learning rate field to training form and progress display"
```

---

### Task 5: Update codebase map

**Files:**
- Modify: `docs/codebase_map.md`

**Step 1: Update training routes section**

Find the `routes/training.py` section in `docs/codebase_map.md`. Update the TrainingConfig line to note the new field:

Change:
```markdown
- Pydantic: `TrainingConfig` L17 (field_validator for seeds)
```

To:
```markdown
- Pydantic: `TrainingConfig` L17 (field_validator for seeds, training_mode, learning_rate)
```

**Step 2: Commit**

```bash
git add docs/codebase_map.md
git commit -m "claude: update codebase map for learning_rate field"
```

---

### Task 6: Final full test suite run

**Step 1: Run full unit test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: All tests PASS

**Step 2: Run full test suite (excluding integration)**

Run: `.venv/bin/python -m pytest tests/ -x -q --ignore=tests/integration`
Expected: All tests PASS

---

### Summary of all changes

| File | Change |
|------|--------|
| `watermeter/routes/training.py` | Add `learning_rate: float = 3e-4` field + validator to `TrainingConfig` |
| `watermeter/training_manager.py` L566 | `Adam(lr=1e-3)` -> `AdamW(lr=config.learning_rate, weight_decay=1e-4)` |
| `watermeter/training_manager.py` L568+ | Add `CosineAnnealingLR(T_max=epochs, eta_min=1e-6)` scheduler |
| `watermeter/training_manager.py` L673 | Add `scheduler.step()` at end of each epoch |
| `watermeter/training_manager.py` L685+ | Add current LR to epoch log messages and progress updates |
| `watermeter/training_manager.py` L725+ | Add `learning_rate` to saved model metadata |
| `watermeter/templates/training.html` L1193+ | Add Learning Rate input field to training form |
| `watermeter/templates/training.html` L1287+ | Add LR metric to progress display |
| `watermeter/static/training.js` L654+ | Read LR from form, send in config, display in progress |
| `tests/unit/test_training_config.py` | New file: 6 tests for LR field validation |
| `docs/codebase_map.md` | Update TrainingConfig description |

### What was NOT changed (by design)

- No warmup phase (marginal benefit for small datasets)
- No differential learning rates (backbone vs head)
- No layer freezing (separate backlog item)
- Batch size stays hardcoded at 16
- The training loop structure is unchanged -- just optimizer and scheduler swapped
- Existing training logs will look slightly different (LR column added) -- no backward compat issue
