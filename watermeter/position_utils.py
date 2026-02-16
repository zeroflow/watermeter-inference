"""
Utility functions for computing position IDs from watermeter config.

Extracts the duplicated logic for determining digit and arrow (analog) IDs
that was previously spread across multiple methods in WatermeterService.
"""

from typing import Dict, List, Tuple


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
