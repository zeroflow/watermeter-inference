"""Replay logged totals through old (strict) vs new (jitter + re-anchor) plausibility.

Usage:
    .venv/bin/python scripts/replay_plausibility.py --logs data_debug/watermeter.log* --config config_debug/config.yaml

Reads every "Calculated total: X m³" line in chronological order (log files sorted
oldest first) and feeds them through PlausibilityChecker.evaluate with a simulated
baseline. Reports acceptance rate, re-anchor count and the largest accepted downward
step of the baseline outside re-anchors. (The published high-water clamp is covered by
service-level tests; replaying it here would be 0 by construction.)
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from watermeter.config_utils import load_config  # noqa: E402
from watermeter.plausibility import PlausibilityChecker  # noqa: E402
from watermeter.rate_tracker import RateTracker  # noqa: E402

LINE = re.compile(r"^(\S+ \S+),\d+ .*Calculated total: ([\d.]+) m")


def read_totals(paths: list[Path]) -> list[tuple[datetime, float]]:
    rows = []
    for path in paths:
        for line in path.read_text(errors="replace").splitlines():
            m = LINE.match(line)
            if m:
                rows.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), float(m.group(2))))
    return sorted(rows)


def replay(rows, plausibility_cfg: dict) -> dict:
    config = {"plausibility": plausibility_cfg}
    checker = PlausibilityChecker(config=config, rate_tracker=RateTracker(max_size=25))
    previous, last_time = None, None
    accepted = reanchors = 0
    max_baseline_drop = 0.0
    for ts, value in rows:
        result = checker.evaluate(value, previous, last_time)
        if not result.is_valid:
            continue
        accepted += 1
        reanchors += int(result.reanchored)
        if previous is not None and result.baseline < previous and not result.reanchored:
            max_baseline_drop = max(max_baseline_drop, previous - result.baseline)
        previous, last_time = result.baseline, ts
    return {
        "readings": len(rows),
        "accepted": accepted,
        "accept_rate_pct": round(100 * accepted / max(1, len(rows)), 1),
        "reanchors": reanchors,
        "max_baseline_drop_without_reanchor": round(max_baseline_drop, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    args = ap.parse_args()

    base = dict(load_config(args.config).get("plausibility", {}))
    rows = read_totals(args.logs)
    old = replay(rows, {**base, "reverse_tolerance": 0.0, "reanchor_after": 0})
    new = replay(rows, {**base, "reverse_tolerance": 0.002, "reanchor_after": 6, "reanchor_max_spread": 0.01})
    print(f"old (strict):          {old}")
    print(f"new (jitter+reanchor): {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
