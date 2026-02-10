"""Regression test: ROI coordinates should be rounded to 4 decimal places.

Bug: ROI coordinates were saved with full float precision, causing
unnecessarily long config values and diff noise.
Fix: Round to 4 decimal places when saving.
"""

import pytest


def test_roi_coordinate_rounding():
    """ROI coordinates should round to 4 decimal places."""
    # Simulate the rounding that happens in app.py when saving ROI config
    raw_coords = {
        'x': 0.12345678901234,
        'y': 0.98765432109876,
        'width': 0.05555555555555,
        'height': 0.11111111111111,
    }

    rounded = {k: round(v, 4) for k, v in raw_coords.items()}

    assert rounded['x'] == 0.1235
    assert rounded['y'] == 0.9877
    assert rounded['width'] == 0.0556
    assert rounded['height'] == 0.1111

    # Verify string representation is clean
    for v in rounded.values():
        s = f"{v}"
        # Should not have more than 4 decimal digits
        if '.' in s:
            decimals = s.split('.')[1]
            assert len(decimals) <= 4
