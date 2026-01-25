"""
Train multiple arrow detection models
Converted from Arrows.ipynb
"""
import json
import os
import random
import shutil
import time
from pathlib import Path
from collections import Counter

import numpy as np
import torch
import timm
import cv2
import openvino as ov
import matplotlib.pyplot as plt
from torchvision.datasets import ImageFolder
from torchvision import transforms
from torch.utils.data import DataLoader, Subset
from sklearn.utils.class_weight import compute_class_weight


# Configuration
RESOLUTIONS = [96, 128, 144, 160, 192]
STEPS_LIST = [1.0, 0.5, 0.2, 0.1]  # 1.0 = 10 classes, 0.5 = 20 classes, 0.2 = 50 classes, 0.1 = 100 classes
MODEL_NAMES = [
  'densenet121',
  'densenet169',
  'densenet201',
  'efficientnet_b2',
  'efficientnet_b3',
  'efficientnet_b4',
  'efficientnet_b5',
  'efficientnet_lite0',
  'efficientnetv2_rw_m',
  'efficientnetv2_rw_s',
  'mobilenetv3_large_100',
  'mobilenetv3_small_100',
  'resnet50',
  'resnext101_64x4d',
  'resnext50_32x4d',
]
EPOCHS = 20
BATCH_SIZE = 16
LEARNING_RATE = 1e-3

# Paths
GROUND_TRUTH_DIR = Path('ground_truth')
DATASET_DIR = Path('dataset')
OV_MODEL_DIR = Path('ov_model')
OV_MODEL_DIR.mkdir(exist_ok=True)


def create_subsampled_dataset(ground_truth_dir: Path, dataset_dir: Path, step: float):
    """Create physical dataset folder with subsampled classes from ground_truth.

    Args:
        ground_truth_dir: Path to ground_truth folder with 100 classes (0.0-9.9 in 0.1 steps)
        dataset_dir: Path to output dataset folder
        step: Step size (1.0 = 10 classes, 0.5 = 20 classes, 0.1 = 100 classes)
    """
    print("\n" + "="*80)
    print("CREATING SUBSAMPLED DATASET")
    print("="*80)
    print(f"Source: {ground_truth_dir}")
    print(f"Target: {dataset_dir}")
    print(f"Step: {step} -> {int(10/step)} classes")

    # Remove existing dataset folder
    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    dataset_dir.mkdir(exist_ok=True)

    # Count images per class
    class_counts = {}
    total_images = 0

    # Iterate through all images in ground_truth
    for class_dir in sorted(ground_truth_dir.iterdir()):
        if not class_dir.is_dir():
            continue

        # Parse original class value (e.g., "0.0", "0.1", ..., "9.9")
        original_value = float(class_dir.name)

        # Round to nearest step
        new_value = round(original_value / step) * step

        # Handle 10.0 wrapping to 0.0
        if new_value >= 10.0:
            new_value = 0.0

        new_class_name = f"{new_value:.1f}"

        # Create target class directory if it doesn't exist
        target_class_dir = dataset_dir / new_class_name
        target_class_dir.mkdir(exist_ok=True)

        # Copy all images from this class to the target class
        for img_file in class_dir.glob('*'):
            if img_file.is_file():
                # Use symlink instead of copy for efficiency
                target_file = target_class_dir / f"{class_dir.name}_{img_file.name}"
                shutil.copy(img_file, target_file)
                total_images += 1
                class_counts[new_class_name] = class_counts.get(new_class_name, 0) + 1

    # Print summary
    num_classes = len(class_counts)
    print(f"\n✓ Created {num_classes} classes with {total_images} images")
    print(f"  Classes: {', '.join(sorted(class_counts.keys(), key=lambda x: float(x)))}")
    print(f"\n  Images per class:")
    for class_name in sorted(class_counts.keys(), key=lambda x: float(x)):
        print(f"    {class_name}: {class_counts[class_name]}")

    return dataset_dir


def create_dataset_from_annotations(export_file: str, dataset_dir: Path, resolution: int):
    """Create dataset directory structure from Label Studio annotations."""
    print("\n" + "="*80)
    print("CREATING DATASET")
    print("="*80)

    with open(export_file) as f:
        data = json.load(f)

    print(f"Total annotations: {len(data)}")

    # Purge existing data
    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    dataset_dir.mkdir(exist_ok=True)

    for item in data:
        # Extract label
        annotations = item.get('annotations', [])
        if not annotations:
            continue

        result = annotations[0]['result']
        if not result:
            continue

        label_float = result[0]['value']['number']
        #label = f"{label_float:.1f}" # 0.1 steps
        #label = f"{round(label_float * 5) / 5:.1f}" # 0.2 steps
        label = f"{round(label_float * 2) / 2:.1f}" # 0.5 steps
        #label = f"{label_float:.0f}" # 1 steps
        #label = str(int(label_float)) # 1 steps, round down

        if label == "10.0":
            label = "0.0"

        # Create class directory
        class_dir = dataset_dir / label
        class_dir.mkdir(exist_ok=True)

        # Extract and copy image
        img_path = item['data']['image']
        if '?d=' in img_path:
            img_path = img_path.split('?d=')[1]
            img_path = os.path.join("/import", img_path)

        # fix path outside docker
        img_path = img_path.replace("/import/", "/var/ml/label-studio-data/import/")

        src_file = Path(img_path)
        if src_file.exists():
            shutil.copy(src_file, class_dir / src_file.name)
        else:
            print(f"✗ Not found: {img_path}")

    print(f"✓ Dataset structure created in: {dataset_dir}")


def analyze_dataset(dataset_dir: Path):
    """Analyze dataset class distribution."""
    print("\n" + "="*80)
    print("DATASET ANALYSIS")
    print("="*80)

    classes = [d.name for d in dataset_dir.iterdir() if d.is_dir()]
    counts = np.array([len(list((dataset_dir/cls).glob('*'))) for cls in classes])

    print(f"Classes          : {len(counts)}")
    print(f"Total samples    : {counts.sum()}")
    print(f"Min              : {counts.min()}")
    print(f"Max              : {counts.max()}")
    print(f"Mean             : {counts.mean():.1f}")
    print(f"Median           : {np.median(counts):.1f}")
    print(f"Std dev          : {counts.std():.1f}")
    print(f"CV               : {counts.std()/counts.mean():.2f}")
    print(f"Imbalance ratio  : {counts.max()/counts.min():.1f}x")

    sorted_idx = np.argsort(counts)
    print(f"\nSmallest 5:")
    for i in sorted_idx[:5]:
        print(f"  {classes[i]}: {int(counts[i])}")

    print(f"Largest 5:")
    for i in sorted_idx[-5:]:
        print(f"  {classes[i]}: {int(counts[i])}")


def create_data_loaders(dataset_dir: Path, resolution: int, batch_size: int):
    """Create train and validation data loaders with stratified split.

    Args:
        dataset_dir: Path to dataset folder with subsampled classes
        resolution: Image resolution for training
        batch_size: Batch size for data loaders
    """
    # Transforms
    train_transform = transforms.Compose([
        transforms.Resize((resolution, resolution)),
        transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.2),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])

    val_transform = transforms.Compose([
        transforms.Resize((resolution, resolution)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])

    # Load dataset and create stratified split
    dataset = ImageFolder(str(dataset_dir))
    indices_by_class = {}
    for idx, (_, label) in enumerate(dataset):
        if label not in indices_by_class:
            indices_by_class[label] = []
        indices_by_class[label].append(idx)

    train_idx = []
    val_idx = []
    random.seed(42)
    for label, indices in indices_by_class.items():
        random.shuffle(indices)
        split_point = int(0.8 * len(indices))
        train_idx.extend(indices[:split_point])
        val_idx.extend(indices[split_point:])

    # Create datasets with appropriate transforms
    train_dataset = ImageFolder(str(dataset_dir), transform=train_transform)
    train_ds = Subset(train_dataset, train_idx)

    val_dataset = ImageFolder(str(dataset_dir), transform=val_transform)
    val_ds = Subset(val_dataset, val_idx)

    # DataLoaders
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=2)
    val_loader = DataLoader(val_ds, batch_size=batch_size, num_workers=2)

    print(f"\n✓ Train samples: {len(train_ds)}")
    print(f"✓ Val samples: {len(val_ds)}")
    print(f"✓ Classes: {len(dataset.classes)}")

    return train_loader, val_loader, dataset, train_idx


def compute_class_weights(dataset, train_idx, device):
    """Compute balanced class weights for all classes in the dataset."""
    num_classes = len(dataset.classes)
    train_labels = [dataset.targets[idx] for idx in train_idx]

    # Compute weights for classes present in training set
    unique_labels = np.unique(train_labels)
    computed_weights = compute_class_weight(
        'balanced',
        classes=unique_labels,
        y=train_labels
    )

    # Create weight array for all classes (initialize with 1.0 for missing classes)
    class_weights = np.ones(num_classes, dtype=np.float32)
    for label, weight in zip(unique_labels, computed_weights):
        class_weights[label] = weight

    class_weights_tensor = torch.FloatTensor(class_weights).to(device)

    print(f"\nClass weights computed for {num_classes} classes:")
    print(f"  Classes in train set: {len(unique_labels)}/{num_classes}")
    print(f"  Min weight: {computed_weights.min():.2f}")
    print(f"  Max weight: {computed_weights.max():.2f}")
    print(f"  Weight ratio: {computed_weights.max()/computed_weights.min():.1f}x")

    if len(unique_labels) < num_classes:
        missing = set(range(num_classes)) - set(unique_labels)
        print(f"  ⚠ Warning: {len(missing)} classes not in train set: {missing}")

    return class_weights_tensor


def train_model(model_name: str, resolution: int, epochs: int, train_loader, val_loader,
                dataset, class_weights_tensor, device, ov_model_dir: Path):
    """Train a single model and export to ONNX/OpenVINO."""
    print("\n" + "="*80)
    print(f"TRAINING: {model_name}")
    print("="*80)

    num_classes = len(dataset.classes)
    model_filename = f'model_arrows_{model_name}_c{num_classes}_r{resolution}'

    # Validate inputs
    print(f"\nValidating training setup...")
    print(f"  Classes: {num_classes}")
    print(f"  Class weights shape: {class_weights_tensor.shape}")
    print(f"  Device: {device}")

    if class_weights_tensor.shape[0] != num_classes:
        raise ValueError(f"Class weights mismatch: got {class_weights_tensor.shape[0]}, expected {num_classes}")

    # Create model
    print(f"\nCreating model: {model_name}")
    try:
        model = timm.create_model(model_name, pretrained=True, num_classes=num_classes)
        model = model.to(device)
    except Exception as e:
        print(f"✗ Failed to create model: {e}")
        raise

    num_params = sum(p.numel() for p in model.parameters())
    print(f"  Model: {model.__class__.__name__}")
    print(f"  Parameters: {num_params:,}")
    print(f"  Output classes: {num_classes}")

    # Optimizer and loss
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    criterion = torch.nn.CrossEntropyLoss(weight=class_weights_tensor)

    # Validate one batch before training
    print(f"\nValidating data loader...")
    try:
        sample_batch = next(iter(train_loader))
        sample_imgs, sample_labels = sample_batch
        print(f"  Batch shape: {sample_imgs.shape}")
        print(f"  Labels shape: {sample_labels.shape}")
        print(f"  Label range: [{sample_labels.min()}, {sample_labels.max()}]")

        if sample_labels.max() >= num_classes or sample_labels.min() < 0:
            raise ValueError(f"Invalid labels in batch: range [{sample_labels.min()}, {sample_labels.max()}], "
                           f"expected [0, {num_classes-1}]")

        # Test forward pass
        sample_imgs = sample_imgs.to(device)
        with torch.no_grad():
            sample_output = model(sample_imgs)
            if sample_output.shape[1] != num_classes:
                raise ValueError(f"Model output mismatch: got {sample_output.shape[1]} classes, expected {num_classes}")

        print(f"  Model output shape: {sample_output.shape}")
        print(f"✓ Validation passed, starting training...")
    except Exception as e:
        print(f"✗ Data validation failed: {e}")
        raise

    # Training loop
    print(f"\nTraining for {epochs} epochs...")
    train_losses = []
    val_accs = []
    best_val_acc = 0
    best_val_loss = float('inf')
    best_epoch = 0
    best_model_state = None


    total_start = time.time()

    for epoch in range(epochs):
        epoch_start = time.time()

        # Training
        model.train()
        epoch_loss = 0
        for imgs, labels in train_loader:
            imgs, labels = imgs.to(device), labels.to(device)

            optimizer.zero_grad()
            outputs = model(imgs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()

            epoch_loss += loss.item()

        avg_loss = epoch_loss / len(train_loader)
        train_losses.append(avg_loss)

        # Validation
        model.eval()
        correct = 0
        total = 0
        with torch.no_grad():
            for imgs, labels in val_loader:
                imgs, labels = imgs.to(device), labels.to(device)
                outputs = model(imgs)
                _, predicted = torch.max(outputs, 1)
                total += labels.size(0)
                correct += (predicted == labels).sum().item()

        val_acc = 100 * correct / total
        val_accs.append(val_acc)

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_val_loss = avg_loss
            best_epoch = epoch
            best_model_state = model.state_dict().copy()

        epoch_time = time.time() - epoch_start
        print(f"  Epoch {epoch+1}/{epochs} - Loss: {avg_loss:.4f} - Val Acc: {val_acc:.2f}% - Time: {epoch_time:.1f}s")

    total_time = time.time() - total_start
    print(f"\nTotal training time: {total_time:.1f}s ({total_time/60:.1f}min)")

    # Load best model
    model.load_state_dict(best_model_state)
    print(f"✓ Best model from epoch {best_epoch+1} with {best_val_acc:.2f}% acc")

    # Save training plot
    print(f"\n[1/5] Saving training plot...")
    try:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
        ax1.plot(train_losses)
        ax1.set_title('Training Loss')
        ax1.set_xlabel('Epoch')
        ax1.set_ylabel('Loss')
        ax1.grid(True)

        ax2.plot(val_accs)
        ax2.set_title('Validation Accuracy')
        ax2.set_xlabel('Epoch')
        ax2.set_ylabel('Accuracy (%)')
        ax2.grid(True)

        plt.tight_layout()
        plot_path = ov_model_dir / f'{model_filename}_training.png'
        plt.savefig(plot_path, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"✓ Plot saved: {plot_path}")
    except Exception as e:
        print(f"✗ Failed to save plot: {e}")
        raise

    # Export to ONNX
    print(f"\n[2/5] Exporting to ONNX...")
    try:
        model.cpu()
        model.eval()
        dummy_input = torch.randn(1, 3, resolution, resolution)

        onnx_path = f'{model_filename}.onnx'

        torch.onnx.export(
            model,
            dummy_input,
            onnx_path,
            export_params=True,
            opset_version=18,
            input_names=['input'],
            output_names=['output'],
        )

        # Verify ONNX file exists and is valid
        if not Path(onnx_path).exists():
            raise FileNotFoundError(f"ONNX file not created: {onnx_path}")

        file_size = Path(onnx_path).stat().st_size / (1024 * 1024)  # MB
        print(f"✓ ONNX model saved: {onnx_path} ({file_size:.1f} MB)")
    except Exception as e:
        print(f"✗ ONNX export failed: {e}")
        raise

    # Convert to OpenVINO
    print(f"\n[3/5] Converting to OpenVINO...")
    try:
        core = ov.Core()
        model_onnx = core.read_model(onnx_path)

        # Validate model was loaded
        if model_onnx is None:
            raise ValueError("Failed to read ONNX model")

        ov_path = ov_model_dir / f'{model_filename}.xml'
        ov.save_model(model_onnx, str(ov_path))

        # Verify OpenVINO files exist
        if not ov_path.exists():
            raise FileNotFoundError(f"OpenVINO XML file not created: {ov_path}")

        bin_path = ov_path.with_suffix('.bin')
        if not bin_path.exists():
            raise FileNotFoundError(f"OpenVINO BIN file not created: {bin_path}")

        xml_size = ov_path.stat().st_size / 1024  # KB
        bin_size = bin_path.stat().st_size / (1024 * 1024)  # MB
        print(f"✓ OpenVINO model saved: {ov_path}")
        print(f"  XML: {xml_size:.1f} KB, BIN: {bin_size:.1f} MB")
    except Exception as e:
        print(f"✗ OpenVINO conversion failed: {e}")
        raise

    # Test OpenVINO inference
    print(f"\n[4/4] Testing OpenVINO inference...")
    try:
        test_img_paths = list(Path('dataset').rglob('*.jpg'))
        if not test_img_paths:
            test_img_paths = list(Path('dataset').rglob('*.png'))
        if not test_img_paths:
            raise FileNotFoundError("No test images found in dataset folder")

        test_img_path = test_img_paths[0]
        img = cv2.imread(str(test_img_path))
        if img is None:
            raise ValueError(f"Failed to load test image: {test_img_path}")

        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (resolution, resolution))

        img_normalized = img.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406])
        std = np.array([0.229, 0.224, 0.225])
        img_normalized = (img_normalized - mean) / std
        img_input = img_normalized.transpose(2, 0, 1)[np.newaxis, ...]

        compiled = core.compile_model(model_onnx, 'AUTO')
        result = compiled([img_input])[compiled.output(0)]
        result_softmax = np.exp(result[0]) / np.exp(result[0]).sum()
        ov_predicted_idx = result_softmax.argmax()
        ov_confidence = result_softmax[ov_predicted_idx]

        print(f"✓ OpenVINO inference:")
        print(f"  Test image: {test_img_path.name}")
        print(f"  Predicted class: {dataset.classes[ov_predicted_idx]}")
        print(f"  Confidence: {ov_confidence:.1%}")
    except Exception as e:
        print(f"✗ OpenVINO inference failed: {e}")
        raise

    print(f"\n✓ All validation steps passed for {model_name}!")

    return {
        'model_name': model_name,
        'model_filename': model_filename,
        'num_params': num_params,
        'best_val_acc': best_val_acc,
        'best_val_loss': best_val_loss,
        'best_epoch': best_epoch,
        'training_time': total_time
    }


def main():
    """Main training pipeline."""
    print("="*80)
    print("ARROW DETECTION MODEL TRAINING")
    print("="*80)

    total_configs = len(STEPS_LIST) * len(RESOLUTIONS) * len(MODEL_NAMES)
    print(f"\nConfigurations to train: {total_configs}")
    print(f"  Steps: {STEPS_LIST}")
    print(f"  Resolutions: {RESOLUTIONS}")
    print(f"  Models: {len(MODEL_NAMES)}")
    for i, name in enumerate(MODEL_NAMES, 1):
        print(f"    {i}. {name}")

    # Setup device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Device: {device}")

    # Track all results
    all_results = []
    config_idx = 0

    # Outer loop: steps (dataset needs to be recreated for each step)
    for step_idx, step in enumerate(STEPS_LIST, 1):
        print("\n" + "#"*80)
        print(f"# STEP {step_idx}/{len(STEPS_LIST)}: {step} ({int(10/step)} classes)")
        print("#"*80)

        # Create subsampled dataset from ground_truth
        create_subsampled_dataset(GROUND_TRUTH_DIR, DATASET_DIR, step)

        # Analyze dataset
        analyze_dataset(DATASET_DIR)

        # Middle loop: resolutions
        for res_idx, resolution in enumerate(RESOLUTIONS, 1):
            print("\n" + "+"*80)
            print(f"+ RESOLUTION {res_idx}/{len(RESOLUTIONS)}: {resolution}px (step={step})")
            print("+"*80)

            # Create data loaders for this resolution
            print("\n" + "="*80)
            print("CREATING DATA LOADERS")
            print("="*80)
            train_loader, val_loader, dataset, train_idx = create_data_loaders(
                DATASET_DIR, resolution, BATCH_SIZE
            )

            # Compute class weights
            class_weights_tensor = compute_class_weights(dataset, train_idx, device)

            # Inner loop: models
            for model_idx, model_name in enumerate(MODEL_NAMES, 1):
                config_idx += 1
                print(f"\n{'='*80}")
                print(f"CONFIG {config_idx}/{total_configs}: {model_name} (step={step}, res={resolution})")
                print(f"{'='*80}")

                try:
                    result = train_model(
                        model_name, resolution, EPOCHS, train_loader, val_loader,
                        dataset, class_weights_tensor, device, OV_MODEL_DIR
                    )
                    result['step'] = step
                    result['resolution'] = resolution
                    all_results.append(result)
                except Exception as e:
                    print(f"\n✗ Error training {model_name}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue

    # Summary
    print("\n" + "="*80)
    print("TRAINING SUMMARY")
    print("="*80)
    print(f"\n{'Model':<30} {'Step':<6} {'Res':<6} {'Params':<12} {'Val Acc':<12} {'Val Loss':<12} {'Time':<10}")
    print("-"*90)
    for r in all_results:
        print(f"{r['model_name']:<30} {r['step']:<6} {r['resolution']:<6} {r['num_params']:>11,} {r['best_val_acc']:>10.2f}% "
              f"{r['best_val_loss']:>11.4f} {r['training_time']:>8.1f}s")

    # Best model
    if all_results:
        best = max(all_results, key=lambda x: x['best_val_acc'])
        print("\n" + "="*80)
        print(f"✓ BEST MODEL: {best['model_name']}")
        print(f"  Step: {best['step']} ({int(10/best['step'])} classes)")
        print(f"  Resolution: {best['resolution']}px")
        print(f"  Validation Accuracy: {best['best_val_acc']:.2f}%")
        print(f"  Validation Loss: {best['best_val_loss']:.4f}")
        print(f"  Parameters: {best['num_params']:,}")
        print("="*80)

    print(f"\n✓ All {len(all_results)} models trained successfully!")


if __name__ == "__main__":
    main()
