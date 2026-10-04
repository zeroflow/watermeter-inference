"""Synthetic analog dial renderer for calibrated-arrow tests.

Draws 10 dark radial scale ticks on a slightly elliptical ring (perspective) around ``centre`` and a red
lancet needle that rotates around ``centre + shift`` (parallax: the needle plane sits above the dial).
"""

import numpy as np

SIZE = (290, 282)  # crop (w, h), like a real analog ROI
CENTRE = (145.0, 141.0)
SQUASH = 0.96  # y axis scale of the dial plane -> elliptical ring


def _project(pts: np.ndarray, origin) -> np.ndarray:
    """Dial-plane coordinates (x right, y down) -> image pixels (squash y, then translate)."""
    out = np.asarray(pts, dtype=float).copy()
    out[:, 1] *= SQUASH
    return out + np.asarray(origin, dtype=float)


def _polar(radius: float, value: float) -> np.ndarray:
    a = np.deg2rad(value * 36.0)  # 0 at 12 o'clock, clockwise
    return np.array([radius * np.sin(a), -radius * np.cos(a)])


def render_dial(cv2, value: float | None, shift=(0.0, 0.0), centre=CENTRE, ticks: bool = True, seed: int = 0):
    """Render one BGR crop. ``value=None`` draws no needle (background only)."""
    rng = np.random.default_rng(seed)
    w, h = SIZE
    img = np.full((h, w, 3), 205, dtype=np.uint8)
    if ticks:
        for i in range(10):
            p0, p1 = _polar(108, i), _polar(126, i)
            q = _project(np.array([p0, p1]), centre)
            cv2.line(img, tuple(int(round(v)) for v in q[0]), tuple(int(round(v)) for v in q[1]), (45, 45, 45), 7)
    if value is not None:
        pivot = np.asarray(centre, dtype=float) + np.asarray(shift, dtype=float)
        tip = _polar(88, value)
        side = _polar(20, value + 2.5)  # perpendicular (90 deg = 2.5 units)
        tail = _polar(-22, value)
        poly = np.array([tip, tail + side, tail - side])
        q = _project(poly, pivot)
        cv2.fillPoly(img, [np.round(q).astype(np.int32)], (40, 40, 200))
        hub = _project(np.array([[0.0, 0.0]]), pivot)[0]
        cv2.circle(img, (int(round(hub[0])), int(round(hub[1]))), 24, (40, 40, 200), -1)
    noise = rng.normal(0, 3, img.shape)
    img = np.clip(img.astype(float) + noise, 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (3, 3), 0)
