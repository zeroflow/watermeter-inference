# Plan: Training Failure Tracking

## Problem

When model training fails, there is no persistent record. The user sees a brief toast message, then everything disappears. Failures are not discoverable after the fact.

### Current Bugs
1. **Silent failure**: If `_execute_training()` returns `None` (e.g. ONNX export fails), `_run_training()` still marks the job as `COMPLETED` instead of `FAILED`
2. **No persistent logs**: Training logs exist only in memory (`job.logs`). Lost on page refresh or next training.
3. **Partial model directories**: On failure after directory creation, incomplete model files are left on disk without cleanup
4. **No failure history**: Failed trainings don't appear anywhere in the UI after the toast disappears

---

## Implementation Plan

### 1. Fix Status Bug (`training_manager.py`)

In `_run_training()`, after the seed loop: if `_execute_training()` returned `None` for all seeds (no results), set `job.status = JobStatus.FAILED` instead of `COMPLETED`.

```python
# After seed loop
if results:
    job.status = JobStatus.COMPLETED
else:
    job.status = JobStatus.FAILED
    job.error = "All training configurations failed"
```

### 2. Persist Logs to Disk (`training_manager.py`)

Save training logs to a file when training completes (success or failure).

- **Success**: Save to `/app/models/{type}/{model_id}/training.log`
- **Failure**: Save to `/app/models/{type}/_failed_{model_id}/training.log`

In `_run_training()` finally block, write `job.logs` to disk:
```python
log_path = output_dir / "training.log"
with open(log_path, 'w') as f:
    f.write('\n'.join(job.logs))
```

### 3. Persist Failure Metadata (`training_manager.py`)

On failure, create a `metadata.json` with `"status": "failed"` and error details:

```json
{
  "model_type": "digits",
  "architecture": "resnext50_32x4d",
  "resolution": 128,
  "status": "failed",
  "error": "ONNX export failed: ...",
  "created_at": "2025-02-01T12:00:00Z",
  "training_info": {
    "seeds": [42],
    "epochs": 20,
    "notes": "..."
  }
}
```

Directory: `/app/models/{type}/_failed_{timestamp}_{architecture}/`

### 4. Cleanup Partial Files (`training_manager.py`)

In `_execute_training()` except block: if the output directory was created but training failed, remove incomplete `.xml`/`.bin`/`.onnx` files but keep the directory for the failure metadata and log.

### 5. Model Manager: Discover Failed Models (`model_manager.py`)

Update `get_models()` to also return models with `"status": "failed"`. The API already passes through whatever ModelManager returns.

### 6. API: Serve Log Files (`app.py`)

Add endpoint to serve persisted log files:
```
GET /api/models/{type}/{id}/logs
```
Returns the contents of `training.log` from the model directory.

### 7. UI: Show Failed Models (`training.html`)

In the model table (`renderModels()`):
- Show failed models with a red **"Failed"** badge
- Replace Activate/Benchmark buttons with a **"View Log"** button
- Keep the **Delete** button to clean up failed entries
- Failed models shown at the bottom of the list (after active/archived)

CSS additions:
```css
.status-badge.failed {
    background: #fee2e2;
    color: var(--danger);
}

.model-actions .btn-viewlog {
    background: var(--bg-light);
    color: var(--text-dark);
}
```

Log viewer: clicking "View Log" opens a modal or expands an inline log viewer showing the persisted training log.

---

## Files to Modify

| File | Changes |
|------|---------|
| `training_manager.py` | Fix status bug, persist logs, persist failure metadata, cleanup partial files |
| `model_manager.py` | Include failed models in `get_models()` |
| `app.py` | Add `GET /api/models/{type}/{id}/logs` endpoint |
| `templates/training.html` | Failed badge, View Log button, log viewer modal, CSS |

## Implementation Order

1. Fix status bug in `_run_training()` (quick, critical)
2. Persist logs + failure metadata in `_run_training()`
3. Cleanup partial model files in `_execute_training()`
4. Update ModelManager to discover failed models
5. Add log file API endpoint
6. UI: failed badge + View Log button + log viewer
