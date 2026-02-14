"""
Shared training utilities used by both training_manager.py and standalone scripts.

Extracts common training/benchmark logic to avoid duplication:
- Seed management for reproducibility
- Image transforms (train/val)
- Stratified dataset splitting
- Class weight computation
- ONNX and OpenVINO model export
- Image preprocessing for inference/benchmarking
"""

import random
import logging
from pathlib import Path
from typing import Tuple, List

import numpy as np
import torch
from torchvision import transforms

logger = logging.getLogger(__name__)


# --- Reproducibility ---

def set_all_seeds(seed: int = 42):
    """Set all random seeds for reproducibility."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def worker_init_fn(worker_id: int):
    """Initialize DataLoader worker with unique but reproducible seed."""
    worker_seed = torch.initial_seed() % 2**32
    np.random.seed(worker_seed)
    random.seed(worker_seed)


# --- Data transforms ---

# ImageNet normalization constants
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]


def create_transforms(resolution: int) -> Tuple[transforms.Compose, transforms.Compose]:
    """Create train and validation transforms.

    Returns:
        (train_transform, val_transform)
    """
    train_transform = transforms.Compose([
        transforms.Resize((resolution, resolution)),
        transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.2),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD)
    ])

    val_transform = transforms.Compose([
        transforms.Resize((resolution, resolution)),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD)
    ])

    return train_transform, val_transform


# --- Dataset splitting ---

def stratified_split(dataset, train_ratio: float = 0.8) -> Tuple[List[int], List[int]]:
    """Create stratified train/val split indices.

    Args:
        dataset: A torchvision ImageFolder dataset.
        train_ratio: Fraction of data for training (default 0.8).

    Returns:
        (train_indices, val_indices)
    """
    indices_by_class = {}
    for idx, (_, label) in enumerate(dataset):
        if label not in indices_by_class:
            indices_by_class[label] = []
        indices_by_class[label].append(idx)

    train_idx = []
    val_idx = []
    for label, indices in indices_by_class.items():
        random.shuffle(indices)
        split_point = int(train_ratio * len(indices))
        train_idx.extend(indices[:split_point])
        val_idx.extend(indices[split_point:])

    return train_idx, val_idx


def compute_class_weights(dataset, train_indices: List[int], device: torch.device) -> torch.Tensor:
    """Compute balanced class weights for the training set.

    Returns:
        FloatTensor of class weights on the specified device.
    """
    from sklearn.utils.class_weight import compute_class_weight as sklearn_compute_class_weight

    num_classes = len(dataset.classes)
    train_labels = [dataset.targets[idx] for idx in train_indices]
    unique_labels = np.unique(train_labels)
    computed_weights = sklearn_compute_class_weight(
        'balanced', classes=unique_labels, y=train_labels
    )
    class_weights = np.ones(num_classes, dtype=np.float32)
    for label, weight in zip(unique_labels, computed_weights):
        class_weights[label] = weight
    return torch.FloatTensor(class_weights).to(device)


# --- Model export ---

def export_to_openvino(model: torch.nn.Module, resolution: int,
                       output_dir: Path, filename: str) -> Tuple[Path, Path]:
    """Export a PyTorch model to ONNX and then OpenVINO IR format.

    Args:
        model: Trained PyTorch model (must be on CPU and in eval mode).
        resolution: Input image resolution.
        output_dir: Directory to save the exported model files.
        filename: Base filename (without extension).

    Returns:
        (onnx_path, openvino_xml_path)
    """
    import openvino as ov

    output_dir.mkdir(parents=True, exist_ok=True)

    dummy_input = torch.randn(1, 3, resolution, resolution)

    # ONNX export
    onnx_path = output_dir / f'{filename}.onnx'
    torch.onnx.export(
        model, dummy_input, onnx_path,
        export_params=True, opset_version=18,
        input_names=['input'], output_names=['output']
    )

    # OpenVINO conversion
    core = ov.Core()
    model_onnx = core.read_model(str(onnx_path))
    ov_path = output_dir / f'{filename}.xml'
    ov.save_model(model_onnx, str(ov_path))

    return onnx_path, ov_path


# --- Inference preprocessing ---

def preprocess_image(image_path: str, resolution: int) -> np.ndarray:
    """Preprocess an image for OpenVINO inference.

    Returns:
        Preprocessed image array with shape (1, 3, H, W).
    """
    import cv2

    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"Failed to load image: {image_path}")
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, (resolution, resolution))
    img = img.astype(np.float32) / 255.0
    img = (img - IMAGENET_MEAN) / IMAGENET_STD
    return img.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)


def softmax_predict(logits: np.ndarray, classes: List[str]) -> Tuple[str, float]:
    """Apply softmax to logits and return (predicted_class, confidence).

    Args:
        logits: Raw model output array (1D).
        classes: List of class label strings.

    Returns:
        (predicted_class_label, confidence_probability)
    """
    logits = logits - logits.max()  # numerical stability
    probs = np.exp(logits) / np.exp(logits).sum()
    idx = probs.argmax()
    return classes[idx], float(probs[idx])
