# BL-08: Arrow Regression Mode

## Goal

Add a "continuous" training mode for arrows that treats dial position as a regression problem
(single sigmoid output, 0.0-1.0 scaled to 0.0-9.9) instead of the current N-class classification.
Both modes coexist — the user picks "discrete" or "continuous" when starting a training job. The
model filename, metadata, inference path, and benchmark logic all branch on `training_mode`.

**Motivation**: The reference project (AI-on-the-edge) uses regression for analog dials. Regression
avoids class boundary artifacts (e.g. 4.9 vs 5.0 being adjacent classes but numerically close),
produces continuous output, and uses all 100 ground truth classes directly without subsampling.

## Architecture

### Data Flow Overview

```
training_mode="discrete" (existing):
  ground_truth/{0.0..9.9}/ → _create_arrow_dataset(step) → ImageFolder → CrossEntropyLoss → softmax → class label

training_mode="continuous" (new):
  ground_truth/{0.0..9.9}/ → RegressionArrowDataset → MSELoss → sigmoid*10 → float 0.0-9.9
```

### Model Filename Conventions

```
Discrete:   model_arrows_{arch}_c{num_classes}_r{res}_s{seed}
Continuous: model_arrows_{arch}_continuous_r{res}_s{seed}
```

The `_continuous_` segment replaces the `_c{N}_` segment, making the mode unambiguously detectable
from the filename alone.

### Metadata Additions

For continuous models, `metadata.json` contains:

```json
{
  "training_mode": "continuous",
  "num_classes": 1,
  "best_val_mae": 0.023,
  "best_val_rmse": 0.031,
  "best_within_half": 94.5,
  "classes": null
}
```

For discrete models (backward compat), `training_mode` is absent or `"discrete"`, and `classes` is
a list of strings as before. Code that reads metadata must treat absent `training_mode` as `"discrete"`.

---

## Work Packages

### WP1: Training Pipeline — Regression Dataset + Model Head + Loss

**File**: `watermeter/training_manager.py` (lines 416-694, `_execute_training()`)

This is the largest WP. The current method is ~280 lines; regression mode adds a parallel code path
inside the same method, branching on `training_mode`.

#### 1a. Extract `training_mode` from config

At the top of `_execute_training()` (after line 417), add:

```python
training_mode = config.get('training_mode', 'discrete')  # "discrete" or "continuous"
```

Note: `config` is `job.config`, which is the dict from `TrainingConfig.dict()`. The `training_mode`
field is added to `TrainingConfig` in WP4.

#### 1b. New `RegressionArrowDataset` class

Add a new class **above** `_execute_training()` (or in `training_core.py` — see discussion below).
This replaces `ImageFolder` for continuous mode because ImageFolder maps folders to integer class
indices, but we need float targets.

**Location**: Add as a module-level class in `training_manager.py` (before `_execute_training`),
or preferably in `training_core.py` since it is a reusable dataset utility.

**Recommended location**: `watermeter/training_core.py` — keeps training_manager.py as orchestration
only.

```python
class RegressionArrowDataset(torch.utils.data.Dataset):
    """
    Dataset for arrow regression: maps folder name (e.g. "2.5") to normalized float target.

    Args:
        root_dir: Path to ground_truth directory containing class folders (0.0, 0.1, ..., 9.9)
        transform: torchvision transform to apply to images
    """

    def __init__(self, root_dir: Path, transform=None):
        self.transform = transform
        self.samples = []  # List of (image_path, target_float)

        for class_dir in sorted(root_dir.iterdir()):
            if not class_dir.is_dir():
                continue
            try:
                value = float(class_dir.name)
            except ValueError:
                continue

            target = value / 10.0  # Normalize to [0.0, 1.0)

            for img_path in sorted(class_dir.glob('*.jpg')):
                self.samples.append((str(img_path), target))

        if not self.samples:
            raise ValueError(f"No images found in {root_dir}")

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        img_path, target = self.samples[idx]
        from PIL import Image
        img = Image.open(img_path).convert('RGB')
        if self.transform:
            img = self.transform(img)
        return img, torch.tensor(target, dtype=torch.float32)
```

**Key design decisions**:
- Normalization: `target = value / 10.0` maps 0.0-9.9 to 0.0-0.99. The model outputs sigmoid
  (0.0-1.0), and at inference we multiply by 10 to get back to 0.0-9.9+.
- Uses `*.jpg` glob (same as `_collect_benchmark_images`).
- Returns `torch.float32` target, not a class index.
- Does NOT subsample — uses all 100 ground truth classes directly.

#### 1c. Stratified split for regression

The current `stratified_split()` in `training_core.py` takes an `ImageFolder` and uses `.targets`
(integer class indices) for stratification. For regression, we need a similar split that stratifies
by the folder (so each dial position is represented in both train and val sets).

Add to `training_core.py`:

```python
def stratified_split_regression(dataset, train_ratio: float = 0.8):
    """
    Stratified train/val split for RegressionArrowDataset.

    Groups samples by their target value (which corresponds to the folder)
    and splits each group proportionally.

    Args:
        dataset: RegressionArrowDataset instance
        train_ratio: Fraction for training

    Returns:
        (train_indices, val_indices)
    """
    from collections import defaultdict

    indices_by_target = defaultdict(list)
    for idx, (_, target) in enumerate(dataset.samples):
        # Use string key to avoid float hashing issues
        key = f"{target:.4f}"
        indices_by_target[key].append(idx)

    train_idx = []
    val_idx = []
    for key, indices in indices_by_target.items():
        random.shuffle(indices)
        split_point = max(1, int(train_ratio * len(indices)))
        train_idx.extend(indices[:split_point])
        val_idx.extend(indices[split_point:])

    return train_idx, val_idx
```

#### 1d. Branch in `_execute_training()` — dataset loading

Replace the current arrows block (lines 451-462) with a branch:

```python
elif model_type == 'arrows':
    ground_truth_dir = Path("/training/arrows/ground_truth")

    if training_mode == 'continuous':
        # Regression mode: use all ground truth classes directly
        dataset_dir = ground_truth_dir  # No temp dataset needed
        model_filename = f'model_arrows_{architecture}_continuous_r{resolution}_s{seed}'
        job.add_log("Continuous (regression) mode — using all ground truth classes")
    else:
        # Discrete (classification) mode: subsample to step_size
        dataset_dir = Path("/training/arrows/dataset_temp")
        step = step_size or 1.0
        num_classes = int(10 / step)
        model_filename = f'model_arrows_{architecture}_c{num_classes}_r{resolution}_s{seed}'
        job.add_log(f"Discrete (classification) mode — step={step} ({num_classes} classes)")
        self._create_arrow_dataset(ground_truth_dir, dataset_dir, step, job)
```

#### 1e. Branch — dataset creation and splitting

After the dataset_dir block (currently lines 476-493), replace with:

```python
if model_type == 'arrows' and training_mode == 'continuous':
    # Regression dataset
    from .training_core import RegressionArrowDataset, stratified_split_regression

    train_transform, val_transform = create_transforms(resolution)

    job.add_log("Loading regression dataset...")
    train_dataset = RegressionArrowDataset(dataset_dir, transform=train_transform)
    val_dataset = RegressionArrowDataset(dataset_dir, transform=val_transform)

    train_idx, val_idx = stratified_split_regression(train_dataset)

    train_ds = Subset(train_dataset, train_idx)
    val_ds = Subset(val_dataset, val_idx)

    num_classes_actual = 1  # regression
    class_names = None

    job.add_log(f"Train samples: {len(train_ds)}, Val samples: {len(val_ds)}")
    job.add_log(f"Regression target range: 0.0 - 1.0 (dial position / 10)")
else:
    # Classification dataset (existing code for both digits and discrete arrows)
    train_transform, val_transform = create_transforms(resolution)

    job.add_log("Loading dataset and creating train/val split...")
    dataset = ImageFolder(str(dataset_dir))
    train_idx, val_idx = stratified_split(dataset)

    train_dataset = ImageFolder(str(dataset_dir), transform=train_transform)
    train_ds = Subset(train_dataset, train_idx)

    val_dataset = ImageFolder(str(dataset_dir), transform=val_transform)
    val_ds = Subset(val_dataset, val_idx)

    num_classes_actual = len(dataset.classes)
    class_names = dataset.classes

    job.add_log(f"Train samples: {len(train_ds)}, Val samples: {len(val_ds)}")
    job.add_log(f"Classes: {num_classes_actual}")

    # Compute class weights (classification only)
    class_weights_tensor = compute_class_weights(dataset, train_idx, device)
    job.add_log("Class weights computed")
```

#### 1f. Branch — model creation

Replace the `timm.create_model` call (line 503):

```python
if model_type == 'arrows' and training_mode == 'continuous':
    # Regression: replace classifier head with single output + sigmoid
    model = timm.create_model(architecture, pretrained=True, num_classes=1)
    # timm with num_classes=1 gives Linear(features, 1) as the head — that's what we want.
    # We apply sigmoid during inference, not in the model itself (BCE loss does it internally,
    # and MSE loss works on raw outputs — we apply sigmoid explicitly during validation).
    job.add_log(f"Creating regression model: {architecture} (1 output)")
else:
    model = timm.create_model(architecture, pretrained=True, num_classes=num_classes_actual)
    job.add_log(f"Creating classification model: {architecture} ({num_classes_actual} classes)")

model = model.to(device)
```

**Important note on `num_classes=1`**: `timm.create_model(arch, num_classes=1)` replaces the
classification head with `Linear(num_features, 1)`. This is exactly what we want for regression.
We do NOT need to manually modify the model architecture.

#### 1g. Branch — optimizer and loss

Replace optimizer/loss setup (lines 510-511):

```python
optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

if model_type == 'arrows' and training_mode == 'continuous':
    criterion = torch.nn.MSELoss()
    # Alternative: torch.nn.HuberLoss(delta=0.05) — more robust to outliers
    # Start with MSE; switch to Huber if training is unstable
else:
    criterion = torch.nn.CrossEntropyLoss(weight=class_weights_tensor)
```

#### 1h. Branch — training loop (forward pass)

The training inner loop (lines 534-544) needs a minor branch:

```python
for batch_idx, (imgs, labels) in enumerate(train_loader):
    if self._training_cancel_flag.is_set():
        return None

    imgs = imgs.to(device)
    optimizer.zero_grad()

    if model_type == 'arrows' and training_mode == 'continuous':
        labels = labels.to(device)  # float32 targets
        outputs = model(imgs).squeeze(-1)  # (batch,) — remove trailing dim
        loss = criterion(torch.sigmoid(outputs), labels)
    else:
        labels = labels.to(device)  # int64 class indices
        outputs = model(imgs)
        loss = criterion(outputs, labels)

    loss.backward()
    optimizer.step()
    epoch_loss += loss.item()
```

**Why `torch.sigmoid(outputs)` in loss**: We apply sigmoid so the MSE loss operates in [0,1] space
matching our normalized targets. This is equivalent to a sigmoid + MSE formulation and ensures
stable gradients. (An alternative is BCEWithLogitsLoss, but MSE is more standard for regression.)

#### 1i. Branch — validation loop

Replace the validation block (lines 550-561):

```python
model.eval()
with torch.no_grad():
    if model_type == 'arrows' and training_mode == 'continuous':
        # Regression validation: compute MAE, RMSE, and "within-half" accuracy
        all_preds = []
        all_targets = []
        for imgs, labels in val_loader:
            imgs = imgs.to(device)
            labels = labels.to(device)
            outputs = model(imgs).squeeze(-1)
            preds = torch.sigmoid(outputs)
            all_preds.append(preds)
            all_targets.append(labels)

        all_preds = torch.cat(all_preds)
        all_targets = torch.cat(all_targets)

        mae = (all_preds - all_targets).abs().mean().item()
        rmse = ((all_preds - all_targets) ** 2).mean().sqrt().item()
        # "within-half": |pred - target| < 0.05 (= 0.5 dial position out of 10)
        within_half = ((all_preds - all_targets).abs() < 0.05).float().mean().item() * 100

        # Use within-half as the primary metric for best-model selection
        val_metric = within_half
        val_metric_name = "within-half"
    else:
        # Classification validation: compute accuracy
        correct = 0
        total = 0
        for imgs, labels in val_loader:
            imgs, labels = imgs.to(device), labels.to(device)
            outputs = model(imgs)
            _, predicted = torch.max(outputs, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()

        val_acc = 100 * correct / total
        val_metric = val_acc
        val_metric_name = "accuracy"
```

#### 1j. Branch — best model tracking

Replace the best-model tracking (lines 564-568):

```python
if model_type == 'arrows' and training_mode == 'continuous':
    val_accs.append(within_half)  # Reuse val_accs list for within-half %
    if within_half > best_val_acc:
        best_val_acc = within_half
        best_val_loss = avg_loss
        best_val_mae = mae
        best_val_rmse = rmse
        best_epoch = epoch
        best_model_state = model.state_dict().copy()
else:
    val_accs.append(val_acc)
    if val_acc > best_val_acc:
        best_val_acc = val_acc
        best_val_loss = avg_loss
        best_epoch = epoch
        best_model_state = model.state_dict().copy()
```

Initialize `best_val_mae = float('inf')` and `best_val_rmse = float('inf')` alongside `best_val_acc`
(before the epoch loop, around line 517).

#### 1k. Branch — progress update message

```python
if model_type == 'arrows' and training_mode == 'continuous':
    job.update_progress(
        current_epoch=epoch + 1,
        total_epochs=epochs,
        train_loss=round(avg_loss, 4),
        val_accuracy=round(within_half, 2),  # Repurpose val_accuracy for within-half %
        epoch_duration=round(epoch_time, 1),
        message=f"Epoch {epoch+1}/{epochs}: Loss={avg_loss:.4f}, MAE={mae:.4f}, Within-half={within_half:.1f}%"
    )
    job.add_log(
        f"Epoch {epoch+1}/{epochs} - Loss: {avg_loss:.4f} - MAE: {mae:.4f} "
        f"- RMSE: {rmse:.4f} - Within-half: {within_half:.1f}% - Time: {epoch_time:.1f}s"
    )
else:
    # existing code
```

#### 1l. Branch — metadata

Replace metadata dict (lines 603-621):

```python
metadata = {
    'model_type': model_type,
    'architecture': architecture,
    'resolution': resolution,
    'seed': seed,
    'epochs': epochs,
    'batch_size': batch_size,
    'best_val_loss': best_val_loss,
    'best_epoch': best_epoch + 1,
    'training_time': total_time,
    'num_params': num_params,
    'created_at': datetime.now().isoformat()
}

if model_type == 'arrows' and training_mode == 'continuous':
    metadata['training_mode'] = 'continuous'
    metadata['num_classes'] = 1
    metadata['best_val_mae'] = best_val_mae
    metadata['best_val_rmse'] = best_val_rmse
    metadata['best_within_half'] = best_val_acc  # best_val_acc holds within-half for regression
    metadata['classes'] = None
else:
    metadata['training_mode'] = 'discrete'
    metadata['num_classes'] = num_classes_actual
    metadata['best_val_acc'] = best_val_acc
    metadata['classes'] = class_names
    if model_type == 'arrows' and step_size:
        metadata['step_size'] = step_size
```

#### 1m. Branch — training result dict

Replace the return dict (lines 667-677):

```python
result = {
    'model_name': architecture,
    'model_id': model_filename,
    'seed': seed,
    'resolution': resolution,
    'training_time': total_time,
    'output_dir': str(output_dir)
}

if model_type == 'arrows' and training_mode == 'continuous':
    result['best_val_acc'] = best_val_acc  # within-half %
    result['best_val_mae'] = best_val_mae
    result['num_classes'] = 1
else:
    result['best_val_acc'] = best_val_acc
    result['best_val_loss'] = best_val_loss
    result['num_classes'] = num_classes_actual
```

#### 1n. Branch — _run_training success log

The success log at line 329 uses `result['best_val_acc']` with "accuracy" label. For continuous mode,
update to say "within-half" instead:

```python
if result:
    metric_name = "within-half" if config.get('training_mode') == 'continuous' else "accuracy"
    results.append(result)
    job.add_log(f"Completed training for seed {seed}: {result['best_val_acc']:.2f}% {metric_name}")
```

#### 1o. Skip temp dataset cleanup for continuous mode

The cleanup block at lines 656-661 checks `dataset_dir.name == 'dataset_temp'`. Since continuous
mode uses ground_truth_dir directly, this check already prevents accidental deletion. No change
needed — just verify this is safe.

#### 1p. Training plot

For continuous mode, change the validation plot label (line 643):

```python
if model_type == 'arrows' and training_mode == 'continuous':
    ax2.set_title('Validation Within-Half Accuracy')
    ax2.set_ylabel('Within-Half (%)')
else:
    ax2.set_title('Validation Accuracy')
    ax2.set_ylabel('Accuracy (%)')
```

---

### WP2: Regressor Class + InferenceService Mode Detection

**File**: `watermeter/inference.py` (lines 1-64, Classifier; lines 66-210, InferenceService)

#### 2a. New `Regressor` class

Add after the `Classifier` class (after line 64):

```python
class Regressor:
    """
    Regression-based inference for continuous arrow models.

    Outputs a dial position (0.0-9.9) instead of a class label.
    """

    def __init__(self, model_path, resolution, label_config_tag, device='GPU'):
        core = ov.Core()
        model = core.read_model(model_path)
        self.compiled = core.compile_model(model, device)
        self.resolution = resolution
        self.label_config_tag = label_config_tag
        self.model_path = model_path

    def preprocess(self, image_path):
        """Same preprocessing as Classifier."""
        img = cv2.imread(str(image_path))
        if img is None:
            raise ValueError(f"Failed to read image: {image_path}")
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (self.resolution, self.resolution))
        img = img.astype(np.float32) / 255.0
        img = (img - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        return img.transpose(2, 0, 1)[np.newaxis, ...]

    def predict(self, image_path):
        """
        Run regression inference.

        Returns:
            dict with 'class' (string like "3.7") and 'confidence' (float 0.0-1.0)
        """
        img = self.preprocess(image_path)
        raw_output = self.compiled([img])[self.compiled.output(0)][0]

        # Apply sigmoid to get [0, 1], then scale to [0, 10)
        sigmoid_val = 1.0 / (1.0 + np.exp(-float(raw_output[0])))
        dial_position = sigmoid_val * 10.0

        # Clamp to valid range
        dial_position = max(0.0, min(9.9, dial_position))

        # Format as class string (same format as ground truth folder names)
        class_str = f"{dial_position:.1f}"

        # Confidence heuristic: how certain the model is.
        # Use distance from 0.5 sigmoid midpoint as confidence proxy.
        # Values near sigmoid midpoint (0.5) are uncertain; values near 0 or 1 are confident.
        # Map: |sigmoid - 0.5| * 2 gives 0.0 (uncertain) to 1.0 (confident).
        confidence = abs(sigmoid_val - 0.5) * 2.0

        return {
            'class': class_str,
            'confidence': float(confidence)
        }

    def predict_detailed(self, image_path, top_k=3):
        """
        For regression, return a single prediction (no top-K concept).

        Returns list with one entry for API compatibility with Classifier.predict_detailed().
        """
        result = self.predict(image_path)
        return [result]
```

**Confidence heuristic discussion**:

The `|sigmoid - 0.5| * 2` approach is simple but may not be optimal. Alternatives:
1. **Temperature-calibrated**: Train a temperature parameter on validation set.
2. **MC Dropout**: Run multiple forward passes with dropout enabled, use variance.
3. **Gradient-based**: Compute gradient magnitude as uncertainty measure.

For v1, the simple sigmoid distance is adequate. The correction engine (BL-04) uses confidence to
decide whether to override — a regression model that saturates toward 0 or 1 is indeed more
confident about its prediction, so this heuristic has the right shape. If needed, we can replace
it with something more sophisticated later.

#### 2b. Update `InferenceService` to detect mode from metadata

The `InferenceService.initialize()` method (line 75) and `reload_models()` (line 113) currently
always create `Classifier` instances. They need to detect the training mode from metadata and
create a `Regressor` for continuous models.

**Helper function** (add near `validate_model_config`):

```python
def _detect_training_mode(model_path: str) -> str:
    """
    Detect whether a model is discrete (classification) or continuous (regression).

    Checks metadata.json in the model directory first, falls back to filename pattern.

    Returns:
        "discrete" or "continuous"
    """
    model_dir = Path(model_path).parent
    metadata_file = model_dir / "metadata.json"

    if metadata_file.exists():
        try:
            import json
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            return metadata.get('training_mode', 'discrete')
        except Exception:
            pass

    # Fallback: check filename pattern
    filename = Path(model_path).stem
    if '_continuous_' in filename:
        return 'continuous'

    return 'discrete'
```

**Updated `initialize()`** (line 75):

```python
def initialize(self, config: dict):
    with self._lock:
        inference_config = config['inference']

        # --- Digits (always classification) ---
        validate_model_config(
            inference_config['digits_model'], 'digits',
            inference_config['digits_classes'], inference_config['digits_resolution']
        )
        self._digits_classifier = Classifier(
            inference_config['digits_model'],
            inference_config['digits_classes'],
            inference_config['digits_resolution'],
            'digit',
            device=inference_config.get('device', 'GPU')
        )

        # --- Arrows (classification or regression) ---
        arrows_mode = _detect_training_mode(inference_config['arrows_model'])

        if arrows_mode == 'continuous':
            # Regression model — no class list validation needed
            self._arrows_classifier = Regressor(
                inference_config['arrows_model'],
                inference_config['arrows_resolution'],
                'arrow_value',
                device=inference_config.get('device', 'GPU')
            )
            logger.info("Arrows model initialized in REGRESSION mode")
        else:
            validate_model_config(
                inference_config['arrows_model'], 'arrows',
                inference_config['arrows_classes'], inference_config['arrows_resolution']
            )
            self._arrows_classifier = Classifier(
                inference_config['arrows_model'],
                inference_config['arrows_classes'],
                inference_config['arrows_resolution'],
                'arrow_value',
                device=inference_config.get('device', 'GPU')
            )
            logger.info("Arrows model initialized in CLASSIFICATION mode")

        logger.info("Inference service initialized")
```

Apply the same pattern to `reload_models()` (line 113).

#### 2c. Update `validate_model_config` for continuous filenames

The `validate_model_config` function (line 222) currently only matches the discrete arrow pattern
`model_arrows_<model>_c<num_classes>_r<resolution>`. Add a new pattern for continuous:

```python
if model_type == 'arrows':
    # Check continuous pattern first
    continuous_pattern = r'^model_arrows_(.+)_continuous_r(\d+)$'
    continuous_match = re.match(continuous_pattern, filename)
    if continuous_match:
        file_resolution = int(continuous_match.group(2))
        if file_resolution != resolution:
            msg = f"arrows resolution mismatch: filename has r{file_resolution}, config has {resolution}"
            logger.error(msg)
            raise ValueError(msg)
        logger.info(f"Arrows continuous model validated: {filename} (resolution={resolution})")
        return

    # Check discrete pattern
    pattern = r'^model_arrows_(.+)_c(\d+)_r(\d+)$'
    match = re.match(pattern, filename)
    # ... existing code ...
```

Note: For continuous models, class count validation is skipped (there is no `_c{N}_` in the
filename, and the model has a single output neuron).

#### 2d. Compatibility with `calculate_total()`

`watermeter_service.py:calculate_total()` (line 456) already does `float(pred['class'])` for arrows
and then `int(arrow)` for the contribution. This works for both modes:
- Discrete: `pred['class']` is e.g. `"7"` or `"7.5"` -> `float("7.5")` = 7.5 -> `int(7.5)` = 7
- Continuous: `pred['class']` is e.g. `"7.3"` -> `float("7.3")` = 7.3 -> `int(7.3)` = 7

**No changes needed in `calculate_total()` or `check_consistency()`.**

#### 2e. Compatibility with `correct_predictions()`

The correction engine (BL-04) uses `predict_detailed()` to get top-K alternatives. For regression,
`predict_detailed()` returns a single-element list, meaning there are no alternatives. This means
the correction engine will naturally skip regression predictions (no alternatives = no correction
candidates). This is acceptable for v1 — regression confidence is handled differently.

**No changes needed in correction engine.**

#### 2f. Update model activation for continuous models

In `model_manager.py`, the `activate_model()` method (line 192) writes classes and resolution from
metadata to config. For continuous models, `metadata['classes']` is `None`. The activation code
(line 233) checks `if 'classes' in metadata:` — but `None` is truthy for `in` checks.

Add a guard:

```python
if metadata.get('classes'):
    config['inference'][f'{model_type}_classes'] = metadata['classes']
```

This uses `.get()` which returns `None` for missing keys, and `None` is falsy. For continuous
models with `"classes": null` in JSON (which deserializes to `None`), the config classes list
remains unchanged — which is correct because the Regressor does not use a class list.

---

### WP3: Benchmark for Regression Models

**File**: `watermeter/training_manager.py` (lines 885-1020, `_execute_benchmark()`)

#### 3a. Detect mode from metadata

At the start of `_execute_benchmark()`, after loading metadata (line 918):

```python
training_mode = metadata.get('training_mode', 'discrete')
```

#### 3b. Branch — arrows benchmark setup

Replace the arrows branch in the class/ground-truth setup (lines 904-908):

```python
else:  # arrows
    gt_path = Path("/training/arrows/ground_truth")
    metadata = self.model_manager.get_model_metadata(model_type, job.model_id)
    training_mode = metadata.get('training_mode', 'discrete')

    if training_mode == 'continuous':
        # No class list needed for regression benchmark
        classes = None
        job.add_log("Benchmark mode: regression (continuous)")
    else:
        num_classes = metadata.get('num_classes', 10)
        classes = self._generate_arrow_classes(num_classes)
        job.add_log(f"Benchmark mode: classification ({len(classes)} classes)")
```

#### 3c. Branch — inference and scoring

The current inference loop (lines 949-986) uses `softmax_predict()` which returns a class label.
For regression, we need a different scoring approach.

Add a new helper to `training_core.py`:

```python
def regression_predict(raw_output: np.ndarray) -> Tuple[float, float]:
    """
    Apply sigmoid and scale for regression model output.

    Args:
        raw_output: Raw model output (1D array with single element).

    Returns:
        (dial_position, confidence) where dial_position is 0.0-9.9
    """
    sigmoid_val = 1.0 / (1.0 + np.exp(-float(raw_output[0])))
    dial_position = max(0.0, min(9.9, sigmoid_val * 10.0))
    confidence = abs(sigmoid_val - 0.5) * 2.0
    return dial_position, confidence
```

Then in the benchmark loop, branch on `training_mode`:

```python
for img_path in image_paths:
    try:
        img = preprocess_image(str(img_path), resolution)
    except ValueError:
        continue

    start_time = time.perf_counter()
    result = compiled([img])[compiled.output(0)][0]
    inference_time = time.perf_counter() - start_time

    if training_mode == 'continuous':
        pred_value, confidence = regression_predict(result)
        expected_value = float(ground_truth)

        all_predictions.append((pred_value, expected_value))
        all_confidences.append(confidence)
        all_times.append(inference_time)

        # "Correct" if within half a dial position
        if abs(pred_value - expected_value) < 0.5:
            correct += 1
    else:
        pred_label, confidence = softmax_predict(result, classes)
        expected_label = self._round_to_arrow_class(float(ground_truth), len(classes)) \
            if model_type == 'arrows' else ground_truth

        all_predictions.append((pred_label, expected_label))
        all_confidences.append(confidence)
        all_times.append(inference_time)

        if pred_label == expected_label:
            correct += 1

    processed += 1
    job.update_progress(processed_images=processed)
```

#### 3d. Branch — result computation

After the inference loop, replace the result computation (lines 988-1020):

```python
if training_mode == 'continuous':
    # Regression metrics
    pred_values = [p for p, _ in all_predictions]
    true_values = [t for _, t in all_predictions]
    errors = [abs(p - t) for p, t in all_predictions]

    mae = np.mean(errors) if errors else 0
    rmse = np.sqrt(np.mean([e**2 for e in errors])) if errors else 0
    within_half = sum(1 for e in errors if e < 0.5) / len(errors) * 100 if errors else 0
    within_one = sum(1 for e in errors if e < 1.0) / len(errors) * 100 if errors else 0

    mean_confidence = np.mean(all_confidences) if all_confidences else 0
    mean_inference_time = np.mean(all_times) * 1000 if all_times else 0

    # Per-class MAE (using ground truth folder as key)
    from collections import defaultdict
    class_errors = defaultdict(list)
    for (pred_val, true_val) in all_predictions:
        key = f"{true_val:.1f}"
        class_errors[key].append(abs(pred_val - true_val))

    per_class = {}
    for cls, errs in class_errors.items():
        per_class[cls] = {
            'mae': round(float(np.mean(errs)), 4),
            'count': len(errs),
            'within_half': round(sum(1 for e in errs if e < 0.5) / len(errs) * 100, 1)
        }

    result = {
        'training_mode': 'continuous',
        'mae': round(float(mae), 4),
        'rmse': round(float(rmse), 4),
        'within_half_pct': round(within_half, 2),
        'within_one_pct': round(within_one, 2),
        'accuracy': round(within_half, 2),  # Alias for UI compatibility
        'mean_confidence': round(float(mean_confidence) * 100, 2),
        'low_confidence_pct': round(sum(1 for c in all_confidences if c < 0.8) / len(all_confidences) * 100, 2) if all_confidences else 0,
        'inference_time_ms': round(float(mean_inference_time), 2),
        'total_images': processed,
        'correct_predictions': correct,
        'per_class_accuracy': per_class  # Actually per-class MAE for regression
    }
else:
    # Existing classification result computation (lines 988-1017 unchanged)
    ...
```

#### 3e. Persist regression benchmark results

In `_run_benchmark()` (lines 843-856), the benchmark result is saved to metadata. For regression,
include the regression-specific fields:

```python
if training_mode == 'continuous':
    metadata['benchmark'] = {
        'training_mode': 'continuous',
        'mae': result['mae'],
        'rmse': result['rmse'],
        'within_half_pct': result['within_half_pct'],
        'within_one_pct': result['within_one_pct'],
        'mean_confidence': result['mean_confidence'],
        'inference_time_ms': result['inference_time_ms'],
        'total_images': result['total_images'],
        'date': datetime.now().isoformat(),
    }
else:
    metadata['benchmark'] = {
        'accuracy': result['accuracy'],
        'mean_confidence': result['mean_confidence'],
        'low_confidence_pct': result['low_confidence_pct'],
        'inference_time_ms': result['inference_time_ms'],
        'total_images': result['total_images'],
        'correct_predictions': result['correct_predictions'],
        'date': datetime.now().isoformat(),
    }
```

But this requires knowing `training_mode` inside `_run_benchmark()`. Since we already load metadata
there, pass it through or re-detect:

```python
training_mode = metadata.get('training_mode', 'discrete')
```

---

### WP4: API + Config Changes

**File**: `watermeter/routes/training.py` (lines 17-27, `TrainingConfig`)

#### 4a. Add `training_mode` to TrainingConfig

```python
class TrainingConfig(BaseModel):
    """Request model for training configuration."""
    model_type: str
    architecture: str
    resolution: int
    seeds: List[int]
    epochs: int = 20
    batch_size: int = 16
    step_size: float = 1.0
    training_mode: str = "discrete"  # "discrete" or "continuous"
    notes: str = ""
    auto_benchmark: bool = True
```

The `training_mode` field defaults to `"discrete"` for backward compatibility. The UI sends
`"continuous"` when the user selects regression mode. The field is passed through to
`training_manager.start_training()` via `config.dict()`.

**Validation**: Consider adding a validator to reject invalid values:

```python
from pydantic import validator

@validator('training_mode')
def validate_training_mode(cls, v):
    if v not in ('discrete', 'continuous'):
        raise ValueError("training_mode must be 'discrete' or 'continuous'")
    return v
```

#### 4b. No config_utils.py changes needed

Training mode is a per-job parameter, not a persistent config setting. It is passed in the
training request JSON and stored in model metadata. There is no need to add it to `config.yaml`
or the config schema.

#### 4c. Update model activation for continuous models

In `model_manager.py`, line 233, change:

```python
# Before:
if 'classes' in metadata:
    config['inference'][f'{model_type}_classes'] = metadata['classes']

# After:
if metadata.get('classes'):
    config['inference'][f'{model_type}_classes'] = metadata['classes']
```

This prevents writing `null` to the config when activating a continuous model.

---

### WP5: Unit Tests

**File**: `tests/unit/test_arrow_regression.py` (new file)

#### Test Categories

**5a. RegressionArrowDataset tests** (if in `training_core.py`)

```python
class TestRegressionArrowDataset:
    def test_loads_all_classes(self, gt_dir_with_100_classes):
        """Dataset loads images from all 100 ground truth folders."""
        ds = RegressionArrowDataset(gt_dir_with_100_classes)
        # Each class has at least 1 image
        assert len(ds) >= 100

    def test_target_normalization(self, gt_dir_with_100_classes):
        """Folder '5.0' produces target 0.5, folder '9.9' produces target 0.99."""
        ds = RegressionArrowDataset(gt_dir_with_100_classes)
        # Find a sample from the "5.0" folder
        for path, target in ds.samples:
            if '/5.0/' in path:
                assert abs(target - 0.5) < 1e-6
                break

    def test_empty_dir_raises(self, tmp_path):
        """Empty directory raises ValueError."""
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ValueError, match="No images found"):
            RegressionArrowDataset(empty)

    def test_skips_non_numeric_dirs(self, tmp_path):
        """Non-numeric folder names are ignored."""
        root = tmp_path / "gt"
        root.mkdir()
        (root / "invalid").mkdir()
        (root / "5.0").mkdir()
        _create_dummy_image(root / "5.0" / "img.jpg")
        ds = RegressionArrowDataset(root)
        assert len(ds) == 1
```

**5b. stratified_split_regression tests**

```python
class TestStratifiedSplitRegression:
    def test_all_targets_in_both_splits(self, gt_dir):
        """Every target value appears in both train and val."""
        ds = RegressionArrowDataset(gt_dir)
        train_idx, val_idx = stratified_split_regression(ds)
        train_targets = {ds.samples[i][1] for i in train_idx}
        val_targets = {ds.samples[i][1] for i in val_idx}
        all_targets = {t for _, t in ds.samples}
        # Every target with >= 2 samples should appear in both
        for t in all_targets:
            count = sum(1 for _, tt in ds.samples if tt == t)
            if count >= 2:
                assert t in train_targets
                assert t in val_targets

    def test_no_overlap(self, gt_dir):
        """Train and val indices don't overlap."""
        ds = RegressionArrowDataset(gt_dir)
        train_idx, val_idx = stratified_split_regression(ds)
        assert len(set(train_idx) & set(val_idx)) == 0

    def test_covers_all_indices(self, gt_dir):
        """All dataset indices are assigned."""
        ds = RegressionArrowDataset(gt_dir)
        train_idx, val_idx = stratified_split_regression(ds)
        assert sorted(train_idx + val_idx) == list(range(len(ds)))
```

**5c. regression_predict tests** (training_core.py)

```python
class TestRegressionPredict:
    def test_sigmoid_midpoint(self):
        """Raw output 0.0 -> sigmoid 0.5 -> dial position 5.0."""
        pos, conf = regression_predict(np.array([0.0]))
        assert abs(pos - 5.0) < 0.1
        assert conf < 0.1  # Low confidence at midpoint

    def test_high_positive(self):
        """Large positive output -> near 9.9."""
        pos, conf = regression_predict(np.array([10.0]))
        assert pos > 9.5
        assert conf > 0.9

    def test_high_negative(self):
        """Large negative output -> near 0.0."""
        pos, conf = regression_predict(np.array([-10.0]))
        assert pos < 0.5
        assert conf > 0.9

    def test_clamped_to_valid_range(self):
        """Output is clamped to [0.0, 9.9]."""
        pos, _ = regression_predict(np.array([100.0]))
        assert pos <= 9.9
        pos, _ = regression_predict(np.array([-100.0]))
        assert pos >= 0.0
```

**5d. Regressor class tests** (inference.py)

These require mocking OpenVINO. Use the same mock pattern as `conftest.py`.

```python
class TestRegressor:
    def test_predict_returns_class_and_confidence(self, mock_ov_model):
        """predict() returns dict with 'class' (string) and 'confidence' (float)."""
        regressor = Regressor('/fake/model.xml', 128, 'arrow_value')
        # Mock compiled model to return a known raw output
        regressor.compiled = mock_ov_model(raw_output=np.array([2.0]))
        result = regressor.predict('/fake/image.jpg')
        assert 'class' in result
        assert 'confidence' in result
        assert isinstance(result['class'], str)
        assert '.' in result['class']  # Should be like "8.8"

    def test_predict_detailed_returns_list(self, mock_ov_model):
        """predict_detailed() returns a single-element list."""
        regressor = Regressor('/fake/model.xml', 128, 'arrow_value')
        regressor.compiled = mock_ov_model(raw_output=np.array([0.0]))
        result = regressor.predict_detailed('/fake/image.jpg')
        assert isinstance(result, list)
        assert len(result) == 1
```

**5e. _detect_training_mode tests**

```python
class TestDetectTrainingMode:
    def test_from_metadata_continuous(self, tmp_path):
        """Detects continuous from metadata.json."""
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "metadata.json").write_text('{"training_mode": "continuous"}')
        assert _detect_training_mode(str(model_dir / "model.xml")) == "continuous"

    def test_from_metadata_discrete(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "metadata.json").write_text('{"training_mode": "discrete"}')
        assert _detect_training_mode(str(model_dir / "model.xml")) == "discrete"

    def test_from_metadata_absent(self, tmp_path):
        """Missing training_mode defaults to discrete."""
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "metadata.json").write_text('{"architecture": "resnet18"}')
        assert _detect_training_mode(str(model_dir / "model.xml")) == "discrete"

    def test_fallback_filename_continuous(self, tmp_path):
        """No metadata -> detect from filename."""
        model_dir = tmp_path / "model_arrows_resnet18_continuous_r128_s42"
        model_dir.mkdir()
        xml = model_dir / "model_arrows_resnet18_continuous_r128_s42.xml"
        xml.write_text("")
        assert _detect_training_mode(str(xml)) == "continuous"

    def test_fallback_filename_discrete(self, tmp_path):
        model_dir = tmp_path / "model_arrows_resnet18_c100_r128_s42"
        model_dir.mkdir()
        xml = model_dir / "model_arrows_resnet18_c100_r128_s42.xml"
        xml.write_text("")
        assert _detect_training_mode(str(xml)) == "discrete"
```

**5f. TrainingConfig validation test**

```python
class TestTrainingConfigValidation:
    def test_default_training_mode(self):
        config = TrainingConfig(
            model_type="arrows", architecture="resnet18",
            resolution=128, seeds=[42]
        )
        assert config.training_mode == "discrete"

    def test_continuous_training_mode(self):
        config = TrainingConfig(
            model_type="arrows", architecture="resnet18",
            resolution=128, seeds=[42], training_mode="continuous"
        )
        assert config.training_mode == "continuous"

    def test_invalid_training_mode_rejected(self):
        with pytest.raises(ValueError):
            TrainingConfig(
                model_type="arrows", architecture="resnet18",
                resolution=128, seeds=[42], training_mode="bogus"
            )
```

**5g. validate_model_config tests for continuous pattern**

```python
class TestValidateModelConfigContinuous:
    def test_continuous_pattern_valid(self):
        """Continuous filename with matching resolution passes."""
        validate_model_config(
            '/app/models/arrows/model_arrows_resnet18_continuous_r128_s42/'
            'model_arrows_resnet18_continuous_r128_s42.xml',
            'arrows', ['dummy'], 128
        )

    def test_continuous_pattern_wrong_resolution(self):
        """Continuous filename with wrong resolution raises."""
        with pytest.raises(ValueError, match="resolution mismatch"):
            validate_model_config(
                '/app/models/arrows/m/model_arrows_resnet18_continuous_r64_s42.xml',
                'arrows', ['dummy'], 128
            )
```

**5h. Fixture: create a minimal ground truth directory**

```python
@pytest.fixture
def gt_dir(tmp_path):
    """Create a minimal ground truth directory with a few classes."""
    gt = tmp_path / "ground_truth"
    for val in ["0.0", "2.5", "5.0", "7.5", "9.9"]:
        class_dir = gt / val
        class_dir.mkdir(parents=True)
        for i in range(5):
            _create_dummy_image(class_dir / f"img_{i}.jpg")
    return gt


def _create_dummy_image(path):
    """Create a minimal valid JPEG file."""
    from PIL import Image
    img = Image.new('RGB', (32, 32), color=(128, 128, 128))
    img.save(str(path))
```

---

## Open Items

1. **Confidence heuristic for regression**: The `|sigmoid - 0.5| * 2` approach is a placeholder.
   After initial training, evaluate whether it meaningfully correlates with prediction quality.
   If not, consider MC Dropout or learned calibration. (Post-v1 improvement, not blocking.)

2. **Huber vs MSE loss**: Start with MSE. If arrow ground truth has labeling noise (e.g. images
   labeled 5.0 that are actually 4.8), Huber loss may train better. Evaluate after first training
   run and switch if needed. (Trivial change: one line.)

3. **BL-04 correction engine with regression**: Currently skipped for regression models because
   `predict_detailed()` returns only one entry (no alternatives). If we want corrections, we could
   generate synthetic alternatives by perturbing the output (e.g. +/- 1 dial position). Defer to
   a future BL item.

## Progress Log

- 2026-02-14: Task document created. Architecture designed. Work packages defined.

## Done Criteria

- [ ] WP1: `_execute_training()` supports `training_mode="continuous"` with regression dataset, model head, loss, validation metrics, and metadata
- [ ] WP1: `RegressionArrowDataset` and `stratified_split_regression` added to `training_core.py`
- [ ] WP1: Continuous model filename follows pattern `model_arrows_{arch}_continuous_r{res}_s{seed}`
- [ ] WP2: `Regressor` class in `inference.py` with `predict()` and `predict_detailed()` methods
- [ ] WP2: `InferenceService` auto-detects mode from metadata and instantiates Classifier or Regressor
- [ ] WP2: `validate_model_config` handles continuous arrow filename pattern
- [ ] WP3: `_execute_benchmark()` computes MAE, RMSE, within-half for regression models
- [ ] WP3: Regression benchmark results persisted to metadata
- [ ] WP4: `TrainingConfig` has `training_mode` field with validation
- [ ] WP4: `model_manager.activate_model()` handles `classes: null` gracefully
- [ ] WP5: All unit tests pass (`python -m pytest tests/unit/test_arrow_regression.py -v`)
- [ ] Full test suite passes (`python -m pytest tests/unit/ tests/regression/ --tb=short -q`)
- [ ] `calculate_total()` and `check_consistency()` work unchanged with regression predictions
- [ ] `backlog.md` BL-08 status updated to `done`
