#!/usr/bin/env python3
"""Tune synthetic data parameters by training mini-models and benchmarking
against real reference photos.

Usage:
    .venv/bin/python scripts/tune_synthetic.py [--type digits|arrows|both] [--epochs 5]
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Subset
from torchvision import models, transforms
from torchvision.datasets import ImageFolder

from watermeter.synthetic_generator import (
    SyntheticGenerator,
    DIGIT_CLASSES,
    ARROW_CLASSES,
)
from watermeter.training_core import create_transforms, set_all_seeds, IMAGENET_MEAN, IMAGENET_STD


def generate_mini_dataset(tmpdir: Path, data_type: str, count_per_class: int = 50):
    """Generate a small synthetic dataset for quick training."""
    gen = SyntheticGenerator(base_dir=str(tmpdir))
    stats = gen.generate(type=data_type, count_per_class=count_per_class, seed=42)
    return stats


def train_mini_model(dataset_dir: Path, num_classes: int, resolution: int = 64, epochs: int = 5):
    """Train a tiny model on the synthetic dataset."""
    set_all_seeds(42)

    train_tf, val_tf = create_transforms(resolution)
    dataset = ImageFolder(str(dataset_dir), transform=train_tf)

    # Use 80% for train, 20% for val
    n = len(dataset)
    indices = list(range(n))
    np.random.shuffle(indices)
    split = int(0.8 * n)
    train_ds = Subset(dataset, indices[:split])
    val_ds = Subset(ImageFolder(str(dataset_dir), transform=val_tf), indices[split:])

    train_loader = DataLoader(train_ds, batch_size=32, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=32, shuffle=False, num_workers=0)

    # Tiny model
    model = models.mobilenet_v3_small(weights=None, num_classes=num_classes)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()

    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for imgs, labels in train_loader:
            imgs, labels = imgs.to(device), labels.to(device)
            optimizer.zero_grad()
            out = model(imgs)
            loss = criterion(out, labels)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        # Validation
        model.eval()
        correct = total = 0
        with torch.no_grad():
            for imgs, labels in val_loader:
                imgs, labels = imgs.to(device), labels.to(device)
                out = model(imgs)
                _, pred = torch.max(out, 1)
                total += labels.size(0)
                correct += (pred == labels).sum().item()

        val_acc = 100 * correct / total if total > 0 else 0
        avg_loss = total_loss / len(train_loader)
        print(f"  Epoch {epoch+1}/{epochs}: Loss={avg_loss:.4f}, Val Acc={val_acc:.1f}%")

    return model, dataset.classes


def evaluate_against_real(model, classes: list, input_dir: Path, resolution: int = 64):
    """Evaluate the trained model against real reference photos."""
    transform = transforms.Compose([
        transforms.Resize((resolution, resolution)),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])

    device = next(model.parameters()).device
    model.eval()
    results = []

    for img_path in sorted(input_dir.glob("*.jpg")):
        if img_path.name.startswith("."):
            continue
        img = Image.open(img_path).convert("RGB")
        tensor = transform(img).unsqueeze(0).to(device)

        with torch.no_grad():
            output = model(tensor)
            probs = torch.softmax(output, dim=1)
            top_prob, top_idx = torch.max(probs, 1)
            pred_class = classes[top_idx.item()]
            confidence = top_prob.item() * 100

        results.append({
            "file": img_path.name,
            "predicted": pred_class,
            "confidence": confidence,
        })
        print(f"  {img_path.name}: predicted={pred_class} ({confidence:.1f}%)")

    return results


def main():
    parser = argparse.ArgumentParser(description="Tune synthetic data parameters")
    parser.add_argument("--type", default="both", choices=["digits", "arrows", "both"])
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--count", type=int, default=50, help="Images per class")
    parser.add_argument("--resolution", type=int, default=64)
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent

    print("=" * 60)
    print("Synthetic Data Parameter Tuning")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Step 1: Generate
        print(f"\n1. Generating synthetic data ({args.count} per class)...")
        t0 = time.time()
        stats = generate_mini_dataset(tmpdir, args.type, args.count)
        print(f"   Generated: {stats} in {time.time()-t0:.1f}s")

        types_to_eval = []
        if args.type in ("digits", "both"):
            types_to_eval.append(("digits", DIGIT_CLASSES))
        if args.type in ("arrows", "both"):
            types_to_eval.append(("arrows", ARROW_CLASSES))

        for data_type, classes_list in types_to_eval:
            print(f"\n{'='*40}")
            print(f"Evaluating: {data_type}")
            print(f"{'='*40}")

            dataset_dir = tmpdir / data_type / "ground_truth"
            if not dataset_dir.exists():
                print(f"  No data for {data_type}, skipping")
                continue

            # Step 2: Train
            print(f"\n2. Training mini model ({args.epochs} epochs)...")
            num_classes = len(classes_list)
            model, model_classes = train_mini_model(
                dataset_dir, num_classes, args.resolution, args.epochs
            )

            # Step 3: Evaluate against real photos
            input_dir = project_root / data_type / "input"
            if input_dir.exists() and any(input_dir.glob("*.jpg")):
                print(f"\n3. Evaluating against real photos in {data_type}/input/...")
                evaluate_against_real(model, model_classes, input_dir, args.resolution)
            else:
                print(f"\n3. No real photos in {data_type}/input/ to evaluate against")

    print("\n" + "=" * 60)
    print("Done. Adjust parameters in synthetic_generator.py and re-run.")
    print("=" * 60)


if __name__ == "__main__":
    main()
