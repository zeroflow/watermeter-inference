"""Compare template (marker) vs feature (AKAZE + homography) alignment on archived frames.

Usage:
    .venv/bin/python scripts/replay_alignment_compare.py \
        --archive-dir data_debug/raw_archive \
        --config config_debug/config.yaml \
        --data-dir data_debug \
        --output report.json

For every JPEG under archive-dir (written with alignment.archive_raw_images: true)
both methods run on the same geometry-corrected frame. The report lists success
rates and failure reasons per method, split into day/night by capture hour, the
camera drift seen by the feature homography (mean ROI-corner displacement, px),
how far the two aligned images disagree inside the ROIs, and per-frame runtime.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from watermeter.config_utils import load_config  # noqa: E402
from watermeter.image_pipeline import ImagePipeline  # noqa: E402

NIGHT_HOURS = set(range(20, 24)) | set(range(0, 7))


def roi_boxes(config: dict, width: int, height: int) -> list[tuple[int, int, int, int]]:
    detection = config.get("detection", {})
    rois = list(detection.get("digits", {}).get("rois", [])) + list(detection.get("analogs", {}).get("rois", []))
    return [
        (int(r["x"] * width), int(r["y"] * height), int(r["width"] * width), int(r["height"] * height)) for r in rois
    ]


def corner_drift(H: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> float:
    """Mean displacement (px) of ROI corners under H — how far the camera moved vs the reference."""
    pts = []
    for x, y, w, h in boxes:
        pts += [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]
    src = np.array(pts, dtype=np.float64).reshape(-1, 1, 2)
    return float(np.mean(np.linalg.norm(cv2.perspectiveTransform(src, H) - src, axis=2)))


def roi_disagreement(a: np.ndarray, b: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> float:
    """Mean absolute gray difference between two aligned images inside the ROIs."""
    ga, gb = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    diffs = [
        np.mean(np.abs(ga[y : y + h, x : x + w].astype(int) - gb[y : y + h, x : x + w].astype(int)))
        for x, y, w, h in boxes
    ]
    return float(np.mean(diffs))


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {
        "n": len(s),
        "median": round(statistics.median(s), 3),
        "p95": round(s[min(len(s) - 1, int(0.95 * len(s)))], 3),
        "max": round(s[-1], 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--data-dir", required=True, type=Path, help="dir with reference_raw.jpg and marker_N.jpg")
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    config = dict(load_config(args.config))
    markers = config.get("detection", {}).get("markers", [])
    ip = ImagePipeline(config=config)
    ip.REFERENCE_PATH = args.data_dir / "reference_raw.jpg"
    templates = [
        cv2.imread(str(args.data_dir / f"marker_{i + 1}.jpg"), cv2.IMREAD_GRAYSCALE) for i in range(len(markers))
    ]
    have_templates = len(markers) >= 2 and all(t is not None for t in templates)
    if have_templates:
        ip._marker_templates = templates

    stats: dict[str, dict] = {
        period: {"frames": 0, "template": Counter(), "features": Counter()} for period in ("day", "night")
    }
    drift, disagreement, t_template, t_features, inlier_ratios = [], [], [], [], []
    worst: list[tuple[float, str]] = []

    for jpg in sorted(args.archive_dir.rglob("*.jpg")):
        raw = cv2.imread(str(jpg))
        if raw is None:
            continue
        period = "night" if int(jpg.stem[:2]) in NIGHT_HOURS else "day"
        stats[period]["frames"] += 1
        img = ip._apply_geometry(raw)

        tmpl = None
        if have_templates:
            t0 = time.perf_counter()
            tmpl = ip._align_with_markers(img, markers)
            t_template.append((time.perf_counter() - t0) * 1000)
            stats[period]["template"]["ok" if tmpl.success else tmpl.error_reason] += 1

        t0 = time.perf_counter()
        feat = ip._align_with_features(img)
        t_features.append((time.perf_counter() - t0) * 1000)
        if feat is None:
            print("No usable reference image for feature alignment")
            return 2
        stats[period]["features"]["ok" if feat.success else feat.error_reason] += 1
        if feat.inlier_ratio is not None:
            inlier_ratios.append(feat.inlier_ratio)

        if feat.success:
            boxes = roi_boxes(config, feat.image.shape[1], feat.image.shape[0])
            drift.append(corner_drift(feat.homography, boxes))
            if tmpl is not None and tmpl.success:
                d = roi_disagreement(tmpl.image, feat.image, boxes)
                disagreement.append(d)
                worst.append((d, str(jpg.relative_to(args.archive_dir))))

    report = {
        "per_period": {
            p: {"frames": s["frames"], "template": dict(s["template"]), "features": dict(s["features"])}
            for p, s in stats.items()
        },
        "feature_inlier_ratio": summarize(inlier_ratios),
        "camera_drift_px": summarize(drift),
        "roi_disagreement_gray": summarize(disagreement),
        "worst_disagreement_frames": [f for _, f in sorted(worst, reverse=True)[:10]],
        "runtime_ms": {"template": summarize(t_template), "features": summarize(t_features)},
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
