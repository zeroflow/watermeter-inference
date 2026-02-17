#!/usr/bin/env python3
"""Backfill training_samples, val_samples, and architecture_display into metadata.json."""
import json
import re
from pathlib import Path

MODELS_DIR = Path("/app/models")
SAMPLE_RE = re.compile(r"Train samples:\s*(\d+),\s*Val samples:\s*(\d+)")

# Architecture display name mapping (matches JS CURATED_LABELS + prettifyModelName)
ARCH_DISPLAY = {
    "resnet18": "ResNet-18",
    "resnet34": "ResNet-34",
    "resnet50": "ResNet-50",
    "efficientnet_lite0": "EfficientNet-Lite0",
    "mobilenetv3_small_100": "MobileNetV3 Small",
    "efficientnetv2_rw_t": "EfficientNetV2-RW Tiny",
    "efficientnetv2_rw_s": "EfficientNetV2-RW Small",
    "efficientnetv2_rw_m": "EfficientNetV2-RW Medium",
    "convnext_nano": "ConvNeXt Nano",
    "convnextv2_atto": "ConvNeXt-V2 Atto",
    "convnextv2_tiny": "ConvNeXt-V2 Tiny",
    "resnext50_32x4d": "ResNeXt-50 32x4d",
}


def prettify_arch(codename):
    """Python equivalent of JS prettifyModelName()."""
    if codename in ARCH_DISPLAY:
        return ARCH_DISPLAY[codename]
    # Strip training recipe suffix (everything after first dot)
    base = codename.split(".")[0]
    # Simple fallback: capitalize and clean up
    return base.replace("_", " ").title()


def backfill():
    updated = 0
    skipped = 0
    for model_type in ["digits", "arrows"]:
        type_dir = MODELS_DIR / model_type
        if not type_dir.exists():
            continue
        for model_dir in sorted(type_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            meta_path = model_dir / "metadata.json"
            log_path = model_dir / "training.log"
            if not meta_path.exists():
                continue

            with open(meta_path) as f:
                metadata = json.load(f)

            changed = False

            # Skip failed models for sample count backfill
            if metadata.get("status") != "failed":
                # Backfill training_samples from log
                if not metadata.get("training_samples") and log_path.exists():
                    log_text = log_path.read_text()
                    match = SAMPLE_RE.search(log_text)
                    if match:
                        train_count = int(match.group(1))
                        val_count = int(match.group(2))
                        metadata["training_samples"] = train_count + val_count
                        metadata["val_samples"] = val_count
                        changed = True
                        print(f"  SAMPLES {model_dir.name}: {metadata['training_samples']} total ({train_count} train + {val_count} val)")

            # Backfill architecture_display (for all models including failed)
            if not metadata.get("architecture_display") and metadata.get("architecture"):
                metadata["architecture_display"] = prettify_arch(metadata["architecture"])
                changed = True
                print(f"  ARCH    {model_dir.name}: {metadata['architecture_display']}")

            if changed:
                with open(meta_path, "w") as f:
                    json.dump(metadata, f, indent=2)
                updated += 1
            else:
                skipped += 1

    print(f"\nDone: {updated} updated, {skipped} skipped")


if __name__ == "__main__":
    backfill()
