"""
Utility functions for computing position IDs from watermeter config.

Extracts the duplicated logic for determining digit and arrow (analog) IDs
that was previously spread across multiple methods in WatermeterService.
"""

import logging
import math
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def get_position_ids(config: Dict) -> Tuple[List[str], List[str]]:
    """
    Compute digit and arrow position IDs from watermeter config.

    Supports two modes:
    - process_separate=True: IDs come from config["images"]["digits"] and
      config["images"]["arrows"] arrays directly.
    - process_separate=False (default): IDs are generated from
      detection.digits.count and detection.analogs.count as
      "digit_1", "digit_2", ... and "analog_1", "analog_2", ...

    Args:
        config: The watermeter configuration dict.

    Returns:
        (digit_ids, arrow_ids) — two lists of position ID strings.
    """
    process_separate = config["images"].get("process_separate", False)

    if process_separate:
        digit_ids = config["images"]["digits"]
        arrow_ids = config["images"]["arrows"]
    else:
        detection = config.get("detection", {})
        digit_count = detection.get("digits", {}).get("count", 0)
        analog_count = detection.get("analogs", {}).get("count", 0)
        digit_ids = [f"digit_{i + 1}" for i in range(digit_count)]
        arrow_ids = [f"analog_{i + 1}" for i in range(analog_count)]

    return digit_ids, arrow_ids


# A wheel showing the next/previous integer is only an early/late roll when the
# arrow fraction is this close to the wrap (0.1 dial at >= 8 or < 2).
ROLL_WINDOW = 0.2


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def resolve_arrows(arrows: List[float]) -> List[int]:
    """Carry-aware integer per dial (0.1 dial first), resolved finest to coarsest.

    Each dial should read ``int + resolved_finer / 10``; using the *resolved* finer
    value (not its raw reading) keeps a just-rolled-over dial from dragging the
    next coarser one down by a unit.
    """
    if not arrows:
        return []
    ints = [0] * len(arrows)
    resolved = arrows[-1]
    ints[-1] = int(math.floor(arrows[-1])) % 10
    for i in range(len(arrows) - 2, -1, -1):
        ints[i] = _round_half_up(arrows[i] - resolved / 10) % 10
        resolved = ints[i] + resolved / 10
    return ints


# Diagnostic only: a dial deviating this much from its cascade expectation contradicts its finer neighbour.
PAIR_INCONSISTENCY = 0.35


def arrow_pair_deviations(arrows: List[float], ints: List[int]) -> List[float]:
    """Circular deviation of each dial from ``int[i] + resolved_finer / 10``, one per adjacent pair."""
    deviations: List[float] = []
    resolved = arrows[-1] if arrows else 0.0
    for i in range(len(arrows) - 2, -1, -1):
        expected = ints[i] + resolved / 10
        diff = abs(arrows[i] - expected) % 10
        deviations.append(min(diff, 10 - diff))
        resolved = expected
    return list(reversed(deviations))


def _to_digits(value: int, count: int) -> List[int]:
    value %= 10**count
    return [int(c) for c in str(value).zfill(count)]


def _matches(raw_digits: List[Optional[int]], candidate: List[int]) -> bool:
    return all(r is None or r == c for r, c in zip(raw_digits, candidate))


def resolve_digits(
    raw_digits: List[Optional[int]],
    frac: float,
    previous_value: Optional[float],
    has_arrows: bool,
    notes: List[str],
) -> Optional[List[int]]:
    """Resolve the integer part. ``None`` entries are NAN digits. Returns None if unresolvable."""
    count = len(raw_digits)
    if count == 0:
        return []
    has_nan = any(d is None for d in raw_digits)

    if previous_value is None or not has_arrows:
        if has_nan:
            notes.append("unresolved NAN digit without carry context")
            return None
        return [int(d) for d in raw_digits if d is not None]

    p_int = int(math.floor(previous_value))
    p_frac = previous_value - p_int
    expected = p_int + 1 if frac < p_frac - 0.5 else p_int

    chosen: Optional[int] = None
    if _matches(raw_digits, _to_digits(expected, count)):
        chosen = expected
    elif frac >= 1 - ROLL_WINDOW and _matches(raw_digits, _to_digits(expected + 1, count)):
        chosen = expected
    elif frac < ROLL_WINDOW and expected > 0 and _matches(raw_digits, _to_digits(expected - 1, count)):
        chosen = expected

    if chosen is not None:
        resolved = _to_digits(chosen, count)
        if resolved != raw_digits:
            shown = "".join("?" if d is None else str(d) for d in raw_digits)
            notes.append(f"integer part from carry context: {shown} → {''.join(map(str, resolved))}")
        return resolved
    if has_nan:
        notes.append("NAN digit inconsistent with previous value")
        return None
    return [int(d) for d in raw_digits if d is not None]


def calculate_total(
    config: Dict, predictions: Dict[str, Dict], previous_value: Optional[float] = None
) -> Tuple[Optional[float], Dict]:
    """
    Calculate the meter total, carry-aware (see docs/plans/2026-10-04-reading-plausibility-cascade-design.md).

    Shared implementation used by WatermeterService, CorrectionEngine and the one-shot CLI.

    Args:
        config: The watermeter configuration dict.
        predictions: Position ID -> prediction dict with at least "class". Discrete classifier arrow
            predictions also carry "bin_width" (their class is the floor of the needle position) and are
            centred by half a bin; continuous (regressor/OpenCV) predictions are used as-is.
        previous_value: Last accepted reading, used to resolve NAN digits and rolling wheels.

    Returns:
        (total or None if unresolvable,
         {"digits": resolved digits, "arrows": arrow floats (centred if discrete), "notes": [...], "raw_total": float or None})
    """
    digit_ids, arrow_ids = get_position_ids(config)
    notes: List[str] = []
    invalid = False

    raw_digits: List[Optional[int]] = []
    for image_id in digit_ids:
        if image_id not in predictions:
            notes.append(f"{image_id}: missing prediction")
            invalid = True
            continue
        cls = predictions[image_id]["class"]
        if cls == "ERROR":
            notes.append(f"{image_id}: inference error")
            invalid = True
        elif cls == "NAN":
            logger.warning(f"{image_id} is NAN (wheel between digits)")
            raw_digits.append(None)
        elif isinstance(cls, str) and cls.isascii() and cls.isdigit() and len(cls) == 1:
            raw_digits.append(int(cls))
        else:
            notes.append(f"{image_id}: no reading ({cls})")
            invalid = True

    arrows: List[float] = []
    used_arrow_ids: List[str] = []
    for image_id in arrow_ids:
        if image_id not in predictions:
            notes.append(f"{image_id}: missing prediction")
            invalid = True
            continue
        cls = predictions[image_id]["class"]
        if cls == "ERROR":
            notes.append(f"{image_id}: inference error")
            invalid = True
            continue
        try:
            value = float(cls)
        except (TypeError, ValueError):
            value = math.nan
        if not math.isfinite(value):
            notes.append(f"{image_id}: no reading ({cls})")
            invalid = True
        else:
            # Discrete classifier labels are floored (class k = needle in [k, k + bin_width)): centre them.
            bin_width = predictions[image_id].get("bin_width") or 0.0
            if bin_width:
                value = (value + bin_width / 2) % 10
            arrows.append(value)
            used_arrow_ids.append(image_id)

    def unresolved() -> Tuple[None, Dict]:
        logger.warning(f"Reading unresolved: {'; '.join(notes)}")
        return None, {"digits": [], "arrows": arrows, "notes": notes, "raw_total": None}

    if invalid:
        return unresolved()

    ints = resolve_arrows(arrows)
    for i, dev in enumerate(arrow_pair_deviations(arrows, ints)):
        if dev > PAIR_INCONSISTENCY:
            notes.append(f"{used_arrow_ids[i]}/{used_arrow_ids[i + 1]} inconsistent ({dev:.2f})")
    frac = sum(v * 10 ** (-(i + 1)) for i, v in enumerate(ints))
    digits = resolve_digits(raw_digits, frac, previous_value, bool(arrows), notes)
    if digits is None:
        return unresolved()

    integer = sum(d * 10 ** (len(digits) - 1 - i) for i, d in enumerate(digits))
    total = round(integer + frac, len(arrows))
    if arrows:
        coarse = sum(v * 10 ** (-(i + 1)) for i, v in enumerate(ints[:-1]))
        raw_total = integer + coarse + arrows[-1] * 10 ** (-len(arrows))
    else:
        raw_total = float(integer)

    logger.info(f"Calculated total: {total:.4f} m³")
    return total, {"digits": digits, "arrows": arrows, "notes": notes, "raw_total": raw_total}
