"""
Train multiple digit detection models
"""
import server_detect
server_detect.handle()

import random
import time
from pathlib import Path

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
RESOLUTION = 128
NUM_CLASSES = 11  # 0-9 + NAN
MODEL_NAMES = [
  #'densenet121', # 1.9% low-conf vs 0.5% for 169/resnext50, no advantage
  #'densenet169',
  # 'densenet201',  # Slower than 169, worse accuracy
  #'efficientnet_b2', # 91.0% acc, worse than b3/b4/lite0
  #'efficientnet_b3', # 92.0% acc, 1.5% low-conf, beaten by lite0
  #'efficientnet_b4', # 92.2% acc, 1.7% low-conf, no advantage over lite0
  # 'efficientnet_b5',  # Consistently underperforms b3/b4, higher low-confidence rate
  #'efficientnet_lite0',
  # 'efficientnetv2_rw_m',  # Slower than rw_s, worse metrics
  #'efficientnetv2_rw_s',
  # 'mobilenetv3_large_100',  # Worse than efficientnet_lite0 at similar speed
  # 'mobilenetv3_small_100',  # Catastrophic failure on some configs (45.5% digits)
  # 'resnet50', # 91.5% acc, worse than top tier
  # 'resnext101_64x4d',  # 2x slower than resnext50, worse accuracy
  'resnext50_32x4d',
  # 'convnext_tiny',  # not supported by ONNX, too new
  #'regnetx_032',  # new - efficient alternative to ResNet/EfficientNet
]
EPOCHS = 20
BATCH_SIZE = 16
LEARNING_RATE = 1e-3

# Paths
DATASET_DIR = Path('digits/ground_truth')
OV_MODEL_DIR = Path('digits/ov_model')
OV_MODEL_DIR.mkdir(exist_ok=True)


def analyze_dataset(dataset_dir: Path):
    """Analyze dataset class distribution."""
    print("\n" + "="*80)
    print("DATASET ANALYSIS")
    print("="*80)

    classes = sorted([d.name for d in dataset_dir.iterdir() if d.is_dir()])
    counts = np.array([len(list((dataset_dir/cls).glob('*'))) for cls in classes])

    print(f"Classes          : {len(counts)} ({', '.join(classes)})")
    print(f"Total samples    : {counts.sum()}")
    print(f"Min              : {counts.min()}")
    print(f"Max              : {counts.max()}")
    print(f"Mean             : {counts.mean():.1f}")
    print(f"Median           : {np.median(counts):.1f}")
    print(f"Std dev          : {counts.std():.1f}")
    print(f"CV               : {counts.std()/counts.mean():.2f}")
    print(f"Imbalance ratio  : {counts.max()/counts.min():.1f}x")

    print(f"\nPer-class counts:")
    for cls, count in zip(classes, counts):
        print(f"  {cls}: {int(count)}")


def create_data_loaders(dataset_dir: Path, resolution: int, batch_size: int):
    """Create train and validation data loaders with stratified split."""
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
    print(f"✓ Classes: {len(dataset.classes)} ({', '.join(dataset.classes)})")

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
        print(f"  Warning: {len(missing)} classes not in train set: {missing}")

    return class_weights_tensor


def train_model(model_name: str, resolution: int, epochs: int, train_loader, val_loader,
                dataset, class_weights_tensor, device, ov_model_dir: Path):
    """Train a single model and export to ONNX/OpenVINO."""
    print("\n" + "="*80)
    print(f"TRAINING: {model_name}")
    print("="*80)

    num_classes = len(dataset.classes)
    model_filename = f'model_digits_{model_name}_r{resolution}'

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
    print(f"\n[1/4] Saving training plot...")
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
    print(f"\n[2/4] Exporting to ONNX...")
    try:
        model.cpu()
        model.eval()
        dummy_input = torch.randn(1, 3, resolution, resolution)

        onnx_path = ov_model_dir / f'{model_filename}.onnx'

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
    print(f"\n[3/4] Converting to OpenVINO...")
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
        test_img_paths = list(DATASET_DIR.rglob('*.jpg'))
        if not test_img_paths:
            test_img_paths = list(DATASET_DIR.rglob('*.png'))
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
    print("DIGIT DETECTION MODEL TRAINING")
    print("="*80)
    print(f"\nModels to train: {len(MODEL_NAMES)}")
    for i, name in enumerate(MODEL_NAMES, 1):
        print(f"  {i}. {name}")
    print(f"\nExpected classes: {NUM_CLASSES} (0-9 + NAN)")

    # Setup device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Device: {device}")

    # Analyze dataset
    analyze_dataset(DATASET_DIR)

    # Create data loaders
    print("\n" + "="*80)
    print("CREATING DATA LOADERS")
    print("="*80)
    train_loader, val_loader, dataset, train_idx = create_data_loaders(
        DATASET_DIR, RESOLUTION, BATCH_SIZE
    )

    # Validate number of classes
    if len(dataset.classes) != NUM_CLASSES:
        print(f"\n⚠ Warning: Expected {NUM_CLASSES} classes, found {len(dataset.classes)}")
        print(f"  Found classes: {dataset.classes}")

    # Compute class weights
    class_weights_tensor = compute_class_weights(dataset, train_idx, device)

    # Train all models
    results = []
    for i, model_name in enumerate(MODEL_NAMES, 1):
        print(f"\n{'='*80}")
        print(f"MODEL {i}/{len(MODEL_NAMES)}")
        print(f"{'='*80}")

        try:
            result = train_model(
                model_name, RESOLUTION, EPOCHS, train_loader, val_loader,
                dataset, class_weights_tensor, device, OV_MODEL_DIR
            )
            results.append(result)
        except Exception as e:
            print(f"\n✗ Error training {model_name}: {e}")
            import traceback
            traceback.print_exc()
            continue

    # Summary
    print("\n" + "="*80)
    print("TRAINING SUMMARY")
    print("="*80)
    print(f"\n{'Model':<30} {'Params':<12} {'Val Acc':<12} {'Val Loss':<12} {'Time':<10}")
    print("-"*80)
    for r in results:
        print(f"{r['model_name']:<30} {r['num_params']:>11,} {r['best_val_acc']:>10.2f}% "
              f"{r['best_val_loss']:>11.4f} {r['training_time']:>8.1f}s")

    # Best model
    if results:
        best = max(results, key=lambda x: x['best_val_acc'])
        print("\n" + "="*80)
        print(f"✓ BEST MODEL: {best['model_name']}")
        print(f"  Validation Accuracy: {best['best_val_acc']:.2f}%")
        print(f"  Validation Loss: {best['best_val_loss']:.4f}")
        print(f"  Parameters: {best['num_params']:,}")
        print("="*80)

    print("\n✓ All models trained successfully!")


if __name__ == "__main__":
    main()
