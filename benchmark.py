"""
Benchmark script to compare all arrow models in arrows/ov_model directory
"""
import openvino as ov
import cv2
import numpy as np
from pathlib import Path
import time
from collections import defaultdict
from typing import Dict, List, Tuple
import re


class ArrowClassifier:
    """Lightweight classifier for benchmarking."""

    def __init__(self, model_path: str, classes: List[str], device: str = 'GPU'):
        core = ov.Core()
        model = core.read_model(model_path)
        self.compiled = core.compile_model(model, device)
        self.classes = classes
        self.model_path = model_path
        self.num_classes = len(classes)

    def preprocess(self, image_path: str) -> np.ndarray:
        """Preprocess image for inference."""
        img = cv2.imread(str(image_path))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (144, 144))
        img = img.astype(np.float32) / 255.0
        img = (img - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        return img.transpose(2, 0, 1)[np.newaxis, ...]

    def predict(self, image_path: str) -> Tuple[str, float, float]:
        """
        Run inference on image.

        Returns:
            (predicted_class, confidence, inference_time)
        """
        start_time = time.perf_counter()
        img = self.preprocess(image_path)
        result = self.compiled([img])[self.compiled.output(0)][0]
        probs = np.exp(result) / np.exp(result).sum()
        idx = probs.argmax()
        inference_time = time.perf_counter() - start_time

        return self.classes[idx], float(probs[idx]), inference_time


def parse_model_filename(filepath: Path) -> Dict[str, any]:
    """
    Parse model filename to extract metadata.

    Expected format: model_{model_name}_c{num_classes}_r{resolution}.xml

    Returns:
        Dict with keys: model_name, num_classes, resolution, filepath
    """
    pattern = r'model_(.+?)_c(\d+)_r(\d+)\.xml'
    match = re.match(pattern, filepath.name)

    if not match:
        return None

    return {
        'model_name': match.group(1),
        'num_classes': int(match.group(2)),
        'resolution': int(match.group(3)),
        'filepath': str(filepath)
    }


def generate_classes(num_classes: int) -> List[str]:
    """
    Generate class labels based on number of classes.

    Args:
        num_classes: 10, 20, 50, or 100

    Returns:
        List of class labels
    """
    if num_classes == 10:
        return [str(i) for i in range(10)]
    elif num_classes == 20:
        return [f"{i/2:.1f}" for i in range(20)]
    elif num_classes == 50:
        return [f"{i/5:.1f}" for i in range(50)]
    elif num_classes == 100:
        return [f"{i/10:.1f}" for i in range(100)]
    else:
        raise ValueError(f"Unsupported num_classes: {num_classes}")


def round_to_class(value: float, num_classes: int) -> str:
    """
    Round ground truth value to nearest class label.

    Args:
        value: Ground truth value (0.0 - 9.99...)
        num_classes: Number of classes (10, 20, 50, 100)

    Returns:
        Rounded class label as string
    """
    if num_classes == 10:
        rounded = round(value)
        if rounded >= 10:
            rounded = 0
        return str(int(rounded))
    elif num_classes == 20:
        rounded = round(value * 2) / 2
        if rounded >= 10.0:
            rounded = 0.0
        return f"{rounded:.1f}"
    elif num_classes == 50:
        rounded = round(value * 5) / 5
        if rounded >= 10.0:
            rounded = 0.0
        return f"{rounded:.1f}"
    elif num_classes == 100:
        rounded = round(value * 10) / 10
        if rounded >= 10.0:
            rounded = 0.0
        return f"{rounded:.1f}"
    else:
        raise ValueError(f"Unsupported num_classes: {num_classes}")


def collect_test_images(dataset_path: str = "arrows/dataset_100") -> Dict[float, List[str]]:
    """
    Collect test images organized by ground truth value.

    Returns:
        Dict mapping ground truth value to list of image paths
    """
    dataset = Path(dataset_path)
    images_by_value = defaultdict(list)

    for class_dir in sorted(dataset.iterdir()):
        if not class_dir.is_dir():
            continue

        try:
            ground_truth = float(class_dir.name)
        except ValueError:
            continue

        for img_path in class_dir.glob("*.jpg"):
            images_by_value[ground_truth].append(str(img_path))

    return dict(images_by_value)


def calculate_accuracy(predictions: List[Tuple[str, float]], expected_label: str) -> float:
    """
    Calculate accuracy by exact label match.

    Args:
        predictions: List of (predicted_value, confidence) tuples
        expected_label: The expected label string (e.g., "5" or "5.5")
    """
    correct = 0
    for pred_str, _ in predictions:
        if pred_str == expected_label:
            correct += 1

    return correct / len(predictions) if predictions else 0.0


def discover_models(ov_model_dir: str = "arrows/ov_model") -> List[Dict]:
    """
    Discover all models in the OpenVINO model directory.

    Returns:
        List of model metadata dicts
    """
    model_dir = Path(ov_model_dir)
    models = []

    for xml_file in sorted(model_dir.glob("model_*.xml")):
        metadata = parse_model_filename(xml_file)
        if metadata:
            models.append(metadata)

    return models


def main():
    print("=" * 100)
    print("Arrow Model Benchmark - Auto-Discovery")
    print("=" * 100)

    # Discover all models
    print("\n[1/5] Discovering models...")
    model_metadata = discover_models()

    if not model_metadata:
        print("  ✗ No models found in arrows/ov_model/")
        return

    print(f"  ✓ Found {len(model_metadata)} model(s):")
    for meta in model_metadata:
        print(f"    - {meta['model_name']} ({meta['num_classes']} classes, {meta['resolution']}px)")

    # Load models
    print("\n[2/5] Loading models...")
    models = []
    for meta in model_metadata:
        try:
            classes = generate_classes(meta['num_classes'])
            model = ArrowClassifier(meta['filepath'], classes)
            models.append({
                'classifier': model,
                'metadata': meta,
                'label': f"{meta['model_name']}_c{meta['num_classes']}"
            })
            print(f"  ✓ {meta['model_name']} ({meta['num_classes']} classes)")
        except Exception as e:
            print(f"  ✗ Failed to load {meta['model_name']}: {e}")

    if not models:
        print("  ✗ No models loaded successfully")
        return

    # Collect test images
    print("\n[3/5] Collecting test images...")
    images_by_value = collect_test_images()
    total_images = sum(len(imgs) for imgs in images_by_value.values())
    print(f"  ✓ Found {total_images} images across {len(images_by_value)} classes")

    # Run benchmark
    print("\n[4/5] Running inference on all images...")
    all_results = {model['label']: defaultdict(list) for model in models}
    all_times = {model['label']: [] for model in models}

    for ground_truth, image_paths in sorted(images_by_value.items()):
        print(f"  Processing GT {ground_truth}: {len(image_paths)} images...", end=" ")

        for img_path in image_paths:
            for model_info in models:
                label = model_info['label']
                classifier = model_info['classifier']

                pred, conf, inference_time = classifier.predict(img_path)
                all_results[label][ground_truth].append((pred, conf))
                all_times[label].append(inference_time)

        print("✓")

    # Analyze results
    print("\n[5/5] Analyzing results...")
    print("\n" + "=" * 100)
    print("PERFORMANCE METRICS")
    print("=" * 100)

    # Inference time
    print(f"\nInference Time:")
    for model_info in models:
        label = model_info['label']
        times = all_times[label]
        print(f"  {label:<40}: {np.mean(times)*1000:6.2f}ms ± {np.std(times)*1000:5.2f}ms")

    # Overall accuracy comparison
    print(f"\nOverall Accuracy (exact label match):")
    print(f"  {'Model':<40} {'Accuracy':<12} {'Mean Conf':<12} {'Low Conf':<12}")
    print(f"  {'-'*39} {'-'*11} {'-'*11} {'-'*11}")

    summary = []
    for model_info in models:
        label = model_info['label']
        num_classes = model_info['metadata']['num_classes']

        # Calculate overall accuracy
        total_correct = 0
        total_samples = 0
        all_confidences = []

        for ground_truth, predictions in all_results[label].items():
            expected_label = round_to_class(ground_truth, num_classes)

            for pred_str, conf in predictions:
                all_confidences.append(conf)
                if pred_str == expected_label:
                    total_correct += 1
                total_samples += 1

        accuracy = total_correct / total_samples if total_samples > 0 else 0
        mean_conf = np.mean(all_confidences)
        low_conf_count = sum(1 for c in all_confidences if c < 0.8)
        low_conf_pct = low_conf_count / len(all_confidences) * 100

        summary.append({
            'label': label,
            'accuracy': accuracy,
            'mean_conf': mean_conf,
            'low_conf_pct': low_conf_pct
        })

        print(f"  {label:<40} {accuracy*100:6.1f}%      {mean_conf*100:6.1f}%      {low_conf_pct:6.1f}%")

    # Per-class accuracy breakdown (only show if 3 or fewer models)
    if len(models) <= 3:
        print(f"\n" + "=" * 100)
        print("PER-CLASS ACCURACY BREAKDOWN")
        print("=" * 100)

        # Build header
        header_cols = ['GT Value']
        for model_info in models:
            label = model_info['label']
            header_cols.append(f"→{label[:8]}")
            header_cols.append(f"{label[:8]}")
        header_cols.append('Imgs')

        # Print header
        print(f"\n  ", end="")
        for col in header_cols:
            if col == 'GT Value':
                print(f"{col:<10}", end=" ")
            elif col == 'Imgs':
                print(f"{col:<6}", end="")
            elif col.startswith('→'):
                print(f"{col:<10}", end=" ")
            else:
                print(f"{col:<10}", end=" ")
        print()

        # Print separator
        print(f"  {'-'*9}", end=" ")
        for _ in range(len(models)):
            print(f"{'-'*9} {'-'*9}", end=" ")
        print(f"{'-'*5}")

        # Aggregate stats
        total_acc_by_model = {model['label']: [] for model in models}

        # Print data rows
        for ground_truth in sorted(images_by_value.keys()):
            num_images = len(images_by_value[ground_truth])
            print(f"  {ground_truth:<10.1f}", end=" ")

            for model_info in models:
                label = model_info['label']
                num_classes = model_info['metadata']['num_classes']
                expected = round_to_class(ground_truth, num_classes)
                acc = calculate_accuracy(all_results[label][ground_truth], expected)

                total_acc_by_model[label].append(acc)

                print(f"{expected:<10} {acc*100:<9.1f}%", end=" ")

            print(f"{num_images:<6}")

        # Print average row
        print(f"  {'-'*9}", end=" ")
        for _ in range(len(models)):
            print(f"{'-'*9} {'-'*9}", end=" ")
        print(f"{'-'*5}")

        print(f"  {'AVERAGE':<10}", end=" ")
        for model_info in models:
            label = model_info['label']
            avg_acc = np.mean(total_acc_by_model[label])
            print(f"{'':<10} {avg_acc*100:<9.1f}%", end=" ")
        print(f"{total_images:<6}")

    # Recommendation
    print("\n" + "=" * 100)
    print("RECOMMENDATION")
    print("=" * 100)

    # Find best model by accuracy
    best = max(summary, key=lambda x: x['accuracy'])

    print(f"\n✓ BEST MODEL: {best['label']}")
    print(f"  - Accuracy: {best['accuracy']*100:.1f}%")
    print(f"  - Mean Confidence: {best['mean_conf']*100:.1f}%")
    print(f"  - Low Confidence: {best['low_conf_pct']:.1f}%")

    # Compare to others
    if len(summary) > 1:
        print(f"\nComparison to other models:")
        for model in sorted(summary, key=lambda x: x['accuracy'], reverse=True)[1:]:
            acc_diff = best['accuracy'] - model['accuracy']
            print(f"  - {model['label']}: {acc_diff*100:+.1f}% accuracy difference")

    print("\n" + "=" * 100)


if __name__ == "__main__":
    main()
