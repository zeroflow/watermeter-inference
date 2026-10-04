"""Compute the arrow calibration (arrows_mode: calibrated) from archived raw frames.

Same computation as POST /api/arrows/calibrate. Inside the DEBUG container (never production):
    docker exec watermeter-dashboard-debug python /app/scripts/calibrate_arrows.py \\
        --config /config/config.yaml --archive-dir /data/raw_archive --out /data/arrow_calibration.json

On the host (paths relative to the repo):
    .venv/bin/python scripts/calibrate_arrows.py --config config_debug/config.yaml \\
        --archive-dir data_debug/raw_archive --reference data_debug/reference_raw.jpg --out /tmp/cal.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from watermeter.arrow_calibration import calibrate_from_archive, save_calibration  # noqa: E402
from watermeter.config_utils import load_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--archive-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, help="write the calibration JSON here (omit for a dry run)")
    ap.add_argument("--reference", type=Path, help="reference_raw.jpg for feature alignment (default /data/...)")
    ap.add_argument("--max-frames", type=int, default=300)
    args = ap.parse_args()

    config = dict(load_config(args.config))
    dials, report = calibrate_from_archive(config, args.archive_dir, args.max_frames, args.reference)
    print(json.dumps(report, indent=2))
    if args.out:
        save_calibration(args.out, dials, report)
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
