# Integrated Model Training

## Overview

Integration of model training directly into the watermeter application. Enables training and benchmarking of custom models via a web UI, without having to manually execute Python scripts.

---

## 1. Architecture

### 1.1 Model Storage
- **Volume Mount**: Model folder mounted as volume
  - `/app/models/digits/` for digit models
  - `/app/models/arrows/` for arrow models
- **Structure**:
  ```
  /app/models/
  ├── digits/
  │   ├── model_digits_resnext50_32x4d_r128/
  │   │   ├── model_digits_resnext50_32x4d_r128.xml
  │   │   ├── model_digits_resnext50_32x4d_r128.bin
  │   │   ├── metadata.json
  │   │   └── training_plot.png
  │   └── model_digits_efficientnetv2_rw_t_r144_s67/
  │       ├── ...
  │       └── metadata.json
  └── arrows/
      └── model_arrows_efficientnetv2_rw_s_c10_r128_s8820/
          ├── ...
          └── metadata.json
  ```

### 1.2 Training Data Storage
- **Volume Mount**: Training data mounted as volume
  - `/training/digits/ground_truth/{0,1,...,9,NAN}/*.jpg` for digit training data
  - `/training/arrows/ground_truth/{0.0,0.1,...,9.9}/*.jpg` for arrow training data
- New low-confidence images are automatically added (existing feature)

### 1.3 Model Naming Convention
```
model_{type}_{architecture}[_c{classes}][_r{resolution}][_s{seed}]
```
Examples:
- `model_digits_resnext50_32x4d_r128` (default, no seed)
- `model_arrows_efficientnetv2_rw_s_c10_r128_s8820` (with classes and seed)

---

## 2. Metadata

### 2.1 Metadata Format (metadata.json)
Each model has a `metadata.json` file with the following information:

```json
{
  "model_type": "digits|arrows",
  "model_name": "resnext50_32x4d",
  "created_at": "2025-01-31T15:30:00Z",
  "training_info": {
    "num_samples": 1500,
    "num_classes": 11,
    "resolution": 128,
    "seed": 42,
    "epochs": 20,
    "batch_size": 16,
    "learning_rate": 0.001,
    "best_val_accuracy": 95.2,
    "best_val_loss": 0.123,
    "training_time_seconds": 450
  },
  "model_info": {
    "num_parameters": 25500000,
    "file_size_mb": 97.5
  },
  "benchmark": {
    "accuracy": 94.8,
    "mean_confidence": 96.5,
    "low_confidence_pct": 2.1,
    "inference_time_ms": 12.5,
    "num_images": 350,
    "tested_at": "2025-01-31T16:00:00Z"
  },
  "status": "active|archived",
  "notes": "Initial training with all available data"
}
```

### 2.2 Model Selection
- Active model is referenced in `config.yaml`
- Dropdown in UI shows all available models with key metrics
- Models can be marked as "active" or "archived"

---

## 3. Training UI

### 3.1 Layout (from top to bottom)
**Route**: `/training`

1. **Dataset Statistics** (compact) - Class distribution for digits and arrows
2. **Training Form** - Configuration + Start/Queue button
3. **Training Queue** - List of pending jobs (only visible when queue is not empty)
4. **Training Progress** - Progress display of active job
5. **Trained Models** - Table of all models with All/Digits/Arrows tabs
6. **Benchmark Progress** - Progress display of active benchmark
7. **Benchmark Results** - Persistent results with All/Digits/Arrows tabs
8. **Log Output** - Training and benchmark logs (only visible when jobs are active)

### 3.2 Start Training
**Form Fields**:
- **Model Type**: Dropdown (digits/arrows)
- **Architecture**: Dropdown with 3 groups, sorted by parameter count:
  - **Lightweight**: MobileNetV3 Small, MobileOne S0, EfficientNet B0, MobileNetV3 Large, MobileOne S1, ResNet18
  - **Medium**: GhostNet, EdgeNeXt Small, EfficientNet B2, EfficientNetV2-T, DenseNet121, ResNet34
  - **Heavy**: DenseNet169, EfficientNetV2-S, ResNeXt50 32x4d (default), ConvNeXt Tiny
- **Resolution**: Dropdown (96, 128, 144, 160, 192)
- **Seeds**: Text (comma-separated, e.g., "42, 67, 69")
- **Epochs**: Number (default: 20)
- **Notes**: Textarea (optional)
- **Auto-benchmark after training**: Checkbox (default: checked)

**Start Button**:
- When no training is running: "Start Training" - starts immediately
- When training is running: "Add to Queue" - adds to queue

### 3.3 Training Queue
- Shows all pending training jobs
- Per entry: Architecture, type, resolution, seeds
- "Remove" button per entry
- "Clear Queue" button to clear entire queue
- Automatically updated via polling (every 2 seconds)

### 3.4 Training Progress
During training:
- Progress bar: Current epoch / Total epochs
- Live updates: Current epoch, training loss, validation accuracy
- Log output (scrollable, epoch lines are filtered to avoid redundancy)
- "Cancel Training" button (also stops queue and auto-benchmark)

### 3.5 Model Management
- **Model List**: Table with All/Digits/Arrows tabs
  - Columns: Name, Architecture, Resolution, Accuracy, Date, Actions
- **Actions**:
  - "Activate": Set model as active model (updates config.yaml, hot-reload)
  - "Benchmark": Run benchmark against ground-truth data
  - "Delete": Delete model (with confirmation, not for active models)

### 3.6 Benchmark Results (Persistent)
- **Always visible** (not only while benchmark is running)
- **Tabs**: All / Digits / Arrows
- **Data source**: Loaded from `metadata.json` of all models (persists across restarts)
- **Table Columns**: Model, Type, Accuracy, Confidence, Low Conf, Inference, Images, Date, Actions
- **Actions**: "Delete" button (only for non-active models), "Active" badge for active models
- **Sorting**: By accuracy descending, best entry highlighted

---

## 4. Training Backend

### 4.1 Training Pipeline
1. Data validation (enough images per class?)
2. Create model (PyTorch + timm)
3. Training loop with progress callbacks
4. ONNX export
5. OpenVINO conversion
6. Save metadata + training plot
7. Start next job from queue (if available)
8. Execute auto-benchmark (if enabled and queue empty)

### 4.2 Training Manager

```python
class TrainingManager:
    def __init__(self):
        self.active_training_job = None
        self.active_benchmark_job = None
        self.training_queue: List[Dict] = []
        self._auto_benchmark_pending: List[tuple] = []
        self._queue_lock = threading.Lock()

    def start_training(self, config) -> Dict:
        """Start training or queue if busy.
        Returns: {'job_id': str|None, 'queued': bool, 'queue_position': int}"""

    def cancel_training(self, clear_queue=True):
        """Cancel running training. clear_queue=True also clears queue + auto-benchmark."""

    def get_queue(self) -> List[Dict]:
        """Get current training queue."""

    def remove_from_queue(self, index: int) -> bool:
        """Remove specific item from queue."""

    def clear_queue(self):
        """Clear entire training queue and auto-benchmark pending."""
```

### 4.3 Training Queue
- When training is running and new job is submitted → job is added to queue
- After training completes → next job is automatically started
- Queue can be managed via UI (remove individual items, clear completely)
- Cancel aborts current training AND clears queue

### 4.4 Auto-Benchmark
- Checkbox "Auto-benchmark after training" in form (default: on)
- Model IDs are collected during training
- When all queue jobs are processed → benchmarks are executed sequentially
- Runs in separate daemon thread
- Uses existing `_run_benchmark()` method

### 4.5 Parallelism
- **One training** at a time (additional ones are queued)
- **One benchmark** at a time
- Training and benchmark can run in parallel
- Auto-benchmark waits until queue is completely processed

---

## 5. Benchmark Integration

### 5.1 Benchmark Process
- Uses OpenVINO for inference
- Runs against all ground-truth data
- Output: Overall accuracy, per-class accuracy, mean confidence, low-confidence %, inference time

### 5.2 Benchmark Trigger
- **Manual**: "Benchmark" button in model table
- **Automatic**: After training when "Auto-benchmark" is enabled (after queue processing)

### 5.3 Persistent Results
- Benchmark results are saved in `metadata.json` of respective model
- When loading the page, all results are extracted from model metadata
- Results persist across container restarts (volume mount)

---

## 6. REST API Endpoints

### Training
- `GET /api/training/status` - Training/benchmark status + queue + auto-benchmark pending
- `POST /api/training/start` - Start training or add to queue
- `POST /api/training/cancel?clear_queue=true` - Cancel training (optionally clear queue)
- `GET /api/training/logs/{job_id}` - Training logs

### Training Queue
- `DELETE /api/training/queue/{index}` - Remove individual queue entry
- `DELETE /api/training/queue` - Clear entire queue

### Models
- `GET /api/models?type={digits|arrows}` - List all models
- `POST /api/models/{type}/{id}/activate` - Activate model
- `DELETE /api/models/{type}/{id}` - Delete model
- `POST /api/models/{type}/{id}/benchmark` - Start benchmark

### Benchmark
- `POST /api/benchmark/cancel` - Cancel benchmark

### Data
- `GET /api/training-data/stats` - Training data statistics

---

## 7. Implementation Roadmap

### ✅ Phase 1: Backend Foundation (Completed)
- `training_manager.py` - Training orchestration with queue + auto-benchmark
- `model_manager.py` - Model metadata & file management
- API endpoints in `app.py`
- Updated `docker-entrypoint.sh` - Default model copy with metadata.json generation
- Updated `Dockerfile` & `docker-compose.yml` - Volume mounts

### ✅ Phase 2: Training UI (Completed)
- `templates/training.html` - Complete training UI (~1700 lines)
- Route `/training` in `app.py`
- JavaScript for polling, form handling, model management

### ✅ Phase 3: Benchmark Integration (Completed)
- `_execute_benchmark()` - Real benchmark logic with OpenVINO
- Benchmark progress section in UI
- Persistent benchmark results section with tabs

### ✅ Phase 4: Queue & Auto-Benchmark (Completed)
- Training queue backend (start → queue when busy, auto-chain)
- Training queue UI (display, remove, clear)
- Auto-benchmark after queue processing
- Benchmark results rework (persistent, tabs, delete button)
- Extended architecture selection (18 models in 3 groups)
- UI layout rework (compact stats, queue section, log section at end)

---

## 8. Technical Decisions

### Storage & Architecture
- **Docker**: Training runs in container (CPU)
- **Model Storage**: Models are persisted via volume mount (`/app/models/`)
  - Startup: if empty → copy default models from `digits/selected/` and `arrows/selected/`
- **Training Data**: Volume mount for `/training/` (digits/arrows each with `ground_truth/`)
- **Model Files**: XML/BIN + metadata.json + training_plot.png
- **Cleanup**: Manual cleanup via UI (delete button in model table and benchmark table)

### Training
- **Parallel Execution**: Training and inference simultaneously OK
  - Training on CPU (torch device='cpu')
  - Inference on GPU (OpenVINO device='AUTO')
- **Training Queue**: Multiple trainings can be submitted, are processed sequentially
- **Auto-Benchmark**: Optional after training (when all queue jobs finished)
- **Seed-based Training**: Seeds as comma-separated list (e.g., `42, 67, 69`)
- **Arrows Step-Size**: Dropdown with 1.0, 0.5, 0.2, 0.1
- **Batch Training**: Parameter ranges (cross-product of all combinations)
- **Error Handling**: Show error log, auto-cleanup of failed trainings
- **Cancellation**: Cancel stops training + clears queue + cancels auto-benchmark

### Available Architectures
| Group | Architecture | timm Name | ~Parameters |
|-------|------------|-----------|------------|
| Lightweight | MobileNetV3 Small | mobilenetv3_small_100 | 2.5M |
| Lightweight | MobileOne S0 | mobileone_s0 | 2.1M |
| Lightweight | EfficientNet B0 | efficientnet_b0 | 5.3M |
| Lightweight | MobileNetV3 Large | mobilenetv3_large_100 | 5.4M |
| Lightweight | MobileOne S1 | mobileone_s1 | 4.8M |
| Lightweight | ResNet18 | resnet18 | 11.7M |
| Medium | GhostNet | ghostnet_100 | 5.2M |
| Medium | EdgeNeXt Small | edgenext_small | 5.6M |
| Medium | EfficientNet B2 | efficientnet_b2 | 9.1M |
| Medium | EfficientNetV2-T | efficientnetv2_rw_t | 13.7M |
| Medium | DenseNet121 | densenet121 | 8.0M |
| Medium | ResNet34 | resnet34 | 21.8M |
| Heavy | DenseNet169 | densenet169 | 14.1M |
| Heavy | EfficientNetV2-S | efficientnetv2_rw_s | 24.0M |
| Heavy | ResNeXt50 32x4d | resnext50_32x4d | 25.0M |
| Heavy | ConvNeXt Tiny | convnext_tiny | 28.6M |

### Model Management
- **Hot-Reload**: Model switching without app restart (model lock during reload)
- **Benchmark Persistence**: Results stored in metadata.json, available across restarts

### UI/UX
- **Framework**: FastAPI + Jinja2 templates (no React/SPA)
- **Progress Updates**: REST API polling (every 2 seconds)
- **Log Filtering**: Epoch/loss/accuracy lines are filtered from logs (redundant with progress bars)
- **Layout**: Compact stats at top, form + queue in middle, models + benchmark below, logs at bottom

### Not Implemented
- ❌ Rollback to previous model
- ❌ A/B testing
- ❌ CPU/GPU selection in UI
- ❌ Multi-GPU support
- ❌ Auto-retry on errors
- ❌ Model comparison side-by-side
