"""
Utility functions for computing position IDs from watermeter config.

Extracts the duplicated logic for determining digit and arrow (analog) IDs
that was previously spread across multiple methods in WatermeterService.
"""

import logging
from typing import Dict, List, Tuple

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


def calculate_total(config: Dict, predictions: Dict[str, Dict]) -> Tuple[float, Dict]:
    """
    Calculate total meter reading from predictions.

    Shared implementation used by WatermeterService and the one-shot CLI mode.

    Args:
        config: The watermeter configuration dict.
        predictions: Dict mapping position IDs to prediction result dicts.
            Each prediction must have at least "class" (str) and optionally "error".

    Returns:
        (total_value, {"digits": list[int], "arrows": list[float]})
    """
    digit_ids, arrow_ids = get_position_ids(config)

    digits = []
    arrows = []

    for image_id in digit_ids:
        if image_id in predictions:
            pred = predictions[image_id]
            if pred["class"] != "NAN" and pred["class"] != "ERROR":
                digits.append(int(pred["class"]))
            else:
                logger.warning(f"{image_id} has invalid class: {pred['class']}")
                digits.append(0)

    for image_id in arrow_ids:
        if image_id in predictions:
            pred = predictions[image_id]
            if pred["class"] != "ERROR":
                arrows.append(float(pred["class"]))
            else:
                logger.warning(f"{image_id} has error")
                arrows.append(0.0)

    total = 0.0

    # Digits contribution: first digit has highest place value
    for i, digit in enumerate(digits):
        multiplier = 10 ** (len(digits) - 1 - i)
        total += digit * multiplier

    # Arrows contribution: 0.1, 0.01, 0.001, ...
    for i, arrow in enumerate(arrows):
        multiplier = 10 ** (-(i + 1))
        total += int(arrow) * multiplier

    raw_values = {"digits": digits, "arrows": arrows}
    logger.info(f"Calculated total: {total:.4f} m³")
    return total, raw_values
