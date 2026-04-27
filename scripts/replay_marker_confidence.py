"""Offline confidence-histogram analysis for marker alignment.

Usage:
    .venv/bin/python scripts/replay_marker_confidence.py \
        --archive-dir /data/raw_archive \
        --config /data/config.yaml \
        --output /data/marker_confidence_report.json

Walks every JPEG under archive-dir, runs the production marker matcher,
and emits min/max/median/p5/p95 of the confidence per marker, plus how
many images would be REJECTED at thresholds 0.5/0.6/0.7/0.8.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

# Ensure the project root is on sys.path so this script works whether invoked
# from the repo root or via an absolute path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402, F401  (kept for parity with production image stack)

from watermeter.config_utils import load_config  # noqa: E402
from watermeter.image_pipeline import ImagePipeline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    config = dict(load_config(args.config))
    markers = config.get("detection", {}).get("markers", [])
    if len(markers) < 2:
        print("Need at least 2 markers in config.detection.markers")
        return 2

    ip = ImagePipeline(config=config)
    templates = ip._load_marker_templates(len(markers))
    if templates is None:
        print("Marker templates missing on disk")
        return 2

    # Per-marker confidence accumulator
    per_marker: list[list[float]] = [[] for _ in markers[:2]]

    for jpg in sorted(args.archive_dir.rglob("*.jpg")):
        img = cv2.imread(str(jpg))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = img.shape[:2]
        for i, marker in enumerate(markers[:2]):
            template = templates[i]
            th, tw = template.shape[:2]
            ref_cx = (marker["x"] + marker["width"] / 2) * w
            ref_cy = (marker["y"] + marker["height"] / 2) * h
            mx = int(ip.SEARCH_MARGIN * w)
            my = int(ip.SEARCH_MARGIN * h)
            sx1, sy1 = max(0, int(ref_cx - mx)), max(0, int(ref_cy - my))
            sx2, sy2 = min(w, int(ref_cx + mx)), min(h, int(ref_cy + my))
            if (sx2 - sx1) < tw or (sy2 - sy1) < th:
                continue
            region = gray[sy1:sy2, sx1:sx2]
            res = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            per_marker[i].append(float(max_val))

    def summarize(scores: list[float]) -> dict:
        if not scores:
            return {"count": 0}
        scores_sorted = sorted(scores)
        return {
            "count": len(scores),
            "min": scores_sorted[0],
            "max": scores_sorted[-1],
            "median": statistics.median(scores),
            "p5": scores_sorted[max(0, int(0.05 * len(scores)) - 1)],
            "p95": scores_sorted[min(len(scores) - 1, int(0.95 * len(scores)))],
            "rejected_at_0.5": sum(1 for s in scores if s < 0.5),
            "rejected_at_0.6": sum(1 for s in scores if s < 0.6),
            "rejected_at_0.7": sum(1 for s in scores if s < 0.7),
            "rejected_at_0.8": sum(1 for s in scores if s < 0.8),
        }

    report = {
        "marker_1": summarize(per_marker[0]),
        "marker_2": summarize(per_marker[1]) if len(per_marker) > 1 else {"count": 0},
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
