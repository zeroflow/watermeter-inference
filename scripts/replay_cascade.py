"""Replay archived raw frames: legacy floor total vs carry-aware cascade total.

Run inside the DEBUG container (has OpenVINO + models), never production:
    docker exec watermeter-dashboard-debug python /app/scripts/replay_cascade.py \
        --archive-dir /data/raw_archive --config /config/config.yaml

For each frame (chronological): align + crop, infer, then compute the legacy total
(floor per arrow, NAN->0) and the cascade total (previous = last cascade total).
Reports how often they differ, NAN/unresolved counts, and backward steps
(> 0.002 m³) in each sequence — fewer backward steps means fewer rejections.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from watermeter.config_utils import load_config  # noqa: E402
from watermeter.image_pipeline import ImagePipeline  # noqa: E402
from watermeter.inference import get_inference_service  # noqa: E402
from watermeter.position_utils import calculate_total, get_position_ids  # noqa: E402


def legacy_total(config: dict, predictions: dict) -> float:
    digit_ids, arrow_ids = get_position_ids(config)
    digits = [int(predictions[i]["class"]) if predictions[i]["class"] not in ("NAN", "ERROR") else 0 for i in digit_ids]
    arrows = [float(predictions[i]["class"]) if predictions[i]["class"] != "ERROR" else 0.0 for i in arrow_ids]
    total = sum(d * 10 ** (len(digits) - 1 - i) for i, d in enumerate(digits))
    return round(total + sum(int(a) * 10 ** (-(i + 1)) for i, a in enumerate(arrows)), len(arrows))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    args = ap.parse_args()

    config = dict(load_config(args.config))
    pipeline = ImagePipeline(config)
    inference = get_inference_service()
    inference.initialize(config)

    prev_legacy = prev_cascade = None
    stats = {"frames": 0, "differ": 0, "nan_frames": 0, "unresolved": 0, "back_legacy": 0, "back_cascade": 0}
    examples = []
    for jpg in sorted(args.archive_dir.rglob("*.jpg")):
        rois, alignment = pipeline.process_whole_image(jpg.read_bytes())
        if not alignment.success or not rois:
            continue
        predictions = {}
        for image_id, (roi_bytes, model_type) in rois.items():
            result = inference.predict_from_bytes(model_type, roi_bytes)
            predictions[image_id] = {"id": image_id, "class": result["class"], "model": model_type}
        stats["frames"] += 1
        stats["nan_frames"] += int(any(p["class"] == "NAN" for p in predictions.values()))

        legacy = legacy_total(config, predictions)
        cascade, _ = calculate_total(config, predictions, previous_value=prev_cascade)
        if cascade is None:
            stats["unresolved"] += 1
            continue
        if abs(cascade - legacy) > 1e-9:
            stats["differ"] += 1
            if len(examples) < 15:
                examples.append((jpg.name, legacy, cascade))
        if prev_legacy is not None and legacy < prev_legacy - 0.002:
            stats["back_legacy"] += 1
        if prev_cascade is not None and cascade < prev_cascade - 0.002:
            stats["back_cascade"] += 1
        prev_legacy, prev_cascade = legacy, max(prev_cascade or cascade, cascade)

    print(stats)
    for name, legacy, cascade in examples:
        print(f"  {name}: legacy {legacy:.4f}  cascade {cascade:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
