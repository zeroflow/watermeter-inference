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
RESOLUTION = 144
MODEL_NAMES = [
    'mobilenetv3_small_100',
    'mobilenetv3_large_100',
    'efficientnet_lite0',
    'efficientnet_b2',
    'efficientnet_b3',
    'convnext_tiny',
    'regnetx_032 ',
    'resnet50',
    'resnext50_32x4d',
]
EPOCHS = 10
BATCH_SIZE = 16
LEARNING_RATE = 1e-3

# Paths
EXPORT_FILE = "annotations.json"
DATASET_DIR = Path('dataset')
OV_MODEL_DIR = Path('ov_model')
OV_MODEL_DIR.mkdir(exist_ok=True)


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
    print(f"✓ Classes: {len(dataset.classes)}")

    return train_loader, val_loader, dataset, train_idx


def compute_class_weights(dataset, train_idx, device):
    """Compute balanced class weights."""
    train_labels = [dataset.targets[idx] for idx in train_idx]

    class_weights = compute_class_weight(
        'balanced',
        classes=np.unique(train_labels),
        y=train_labels
    )

    class_weights_tensor = torch.FloatTensor(class_weights).to(device)

    print(f"\nClass weights computed:")
    print(f"  Min weight: {class_weights.min():.2f}")
    print(f"  Max weight: {class_weights.max():.2f}")
    print(f"  Weight ratio: {class_weights.max()/class_weights.min():.1f}x")

    return class_weights_tensor


def train_model(model_name: str, resolution: int, epochs: int, train_loader, val_loader,
                dataset, class_weights_tensor, device, ov_model_dir: Path):
    """Train a single model and export to ONNX/OpenVINO."""
    print("\n" + "="*80)
    print(f"TRAINING: {model_name}")
    print("="*80)

    num_classes = len(dataset.classes)
    model_filename = f'model_{model_name}_c{num_classes}_r{resolution}'

    # Create model
    print(f"\nCreating model: {model_name}")
    model = timm.create_model(model_name, pretrained=True, num_classes=num_classes)
    model = model.to(device)

    num_params = sum(p.numel() for p in model.parameters())
    print(f"  Model: {model.__class__.__name__}")
    print(f"  Parameters: {num_params:,}")

    # Optimizer and loss
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    criterion = torch.nn.CrossEntropyLoss(weight=class_weights_tensor)

    # Training loop
    print(f"\nTraining for {epochs} epochs...")
    train_losses = []
    val_accs = []
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

        if avg_loss < best_val_loss:
            best_val_loss = avg_loss
            best_epoch = epoch
            best_model_state = model.state_dict().copy()

        epoch_time = time.time() - epoch_start
        print(f"  Epoch {epoch+1}/{epochs} - Loss: {avg_loss:.4f} - Val Acc: {val_acc:.2f}% - Time: {epoch_time:.1f}s")

    total_time = time.time() - total_start
    print(f"\nTotal training time: {total_time:.1f}s ({total_time/60:.1f}min)")

    # Load best model
    model.load_state_dict(best_model_state)
    print(f"✓ Best model from epoch {best_epoch+1} with {best_val_loss:.3f} loss")

    # Save training plot
    print(f"\nSaving training plot...")
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

    # Export to ONNX
    print(f"\nExporting to ONNX...")
    model.cpu()
    model.eval()
    dummy_input = torch.randn(1, 3, resolution, resolution)

    onnx_path = f'{model_filename}.onnx'
    torch.onnx.export(
        model,
        dummy_input,
        onnx_path,
        export_params=True,
        opset_version=11,
        input_names=['input'],
        output_names=['output']
    )
    print(f"✓ ONNX model saved: {onnx_path}")

    # Convert to OpenVINO
    print(f"\nConverting to OpenVINO...")
    core = ov.Core()
    model_onnx = core.read_model(onnx_path)
    ov_path = ov_model_dir / f'{model_filename}.xml'
    ov.save_model(model_onnx, str(ov_path))
    print(f"✓ OpenVINO model saved: {ov_path}")

    # Test inference
    print(f"\nTesting OpenVINO inference...")
    compiled = core.compile_model(model_onnx, 'GPU')

    test_img_path = list(Path('dataset').rglob('*.jpg'))[0]
    img = cv2.imread(str(test_img_path))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (resolution, resolution))

    img_normalized = img.astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406])
    std = np.array([0.229, 0.224, 0.225])
    img_normalized = (img_normalized - mean) / std
    img_input = img_normalized.transpose(2, 0, 1)[np.newaxis, ...]

    result = compiled([img_input])[compiled.output(0)]
    result_softmax = np.exp(result[0]) / np.exp(result[0]).sum()
    predicted_idx = result_softmax.argmax()
    confidence = result_softmax[predicted_idx]

    print(f"  Test image: {test_img_path.name}")
    print(f"  Predicted class: {dataset.classes[predicted_idx]}")
    print(f"  Confidence: {confidence:.1%}")

    print(f"\n✓ Model {model_name} training complete!")

    return {
        'model_name': model_name,
        'model_filename': model_filename,
        'num_params': num_params,
        'best_val_loss': best_val_loss,
        'best_epoch': best_epoch,
        'final_val_acc': val_accs[-1],
        'training_time': total_time
    }


def main():
    """Main training pipeline."""
    print("="*80)
    print("ARROW DETECTION MODEL TRAINING")
    print("="*80)
    print(f"\nModels to train: {len(MODEL_NAMES)}")
    for i, name in enumerate(MODEL_NAMES, 1):
        print(f"  {i}. {name}")

    # Setup device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"\nDevice: {device}")

    # Create dataset (only once)
    create_dataset_from_annotations(EXPORT_FILE, DATASET_DIR, RESOLUTION)
    analyze_dataset(DATASET_DIR)

    # Create data loaders
    print("\n" + "="*80)
    print("CREATING DATA LOADERS")
    print("="*80)
    train_loader, val_loader, dataset, train_idx = create_data_loaders(
        DATASET_DIR, RESOLUTION, BATCH_SIZE
    )

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
    print(f"\n{'Model':<30} {'Params':<12} {'Val Loss':<12} {'Val Acc':<12} {'Time':<10}")
    print("-"*80)
    for r in results:
        print(f"{r['model_name']:<30} {r['num_params']:>11,} {r['best_val_loss']:>11.4f} "
              f"{r['final_val_acc']:>10.2f}% {r['training_time']:>8.1f}s")

    # Best model
    if results:
        best = max(results, key=lambda x: x['final_val_acc'])
        print("\n" + "="*80)
        print(f"✓ BEST MODEL: {best['model_name']}")
        print(f"  Validation Accuracy: {best['final_val_acc']:.2f}%")
        print(f"  Validation Loss: {best['best_val_loss']:.4f}")
        print(f"  Parameters: {best['num_params']:,}")
        print("="*80)

    print("\n✓ All models trained successfully!")


if __name__ == "__main__":
    main()
