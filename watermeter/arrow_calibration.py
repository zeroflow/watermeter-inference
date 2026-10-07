"""Per-dial geometry calibration for precise analog arrow reading (``arrows_mode: calibrated``).

The camera looks at the meter obliquely and the needle sits above the dial face, so in the image the
needle rotates around a pivot that is 15-20 px away from both the ROI centre and the centre of the
printed scale (parallax). Reading the needle angle around the crop centre, as ``opencv_arrows`` does,
turns that offset into an angle-dependent error of up to ~0.2 dial units.

Calibration (once, from archived raw frames, all in crop pixel coordinates):

1. Background = per-pixel median over frames with the needle masked out.
2. Scale ticks = dark radial blobs on the outer ring -> ellipse (perspective) + tick angles.
3. Pivot = least-squares intersection of the per-frame needle axes. Dials whose needle barely moved
   (ill-conditioned) get their pivot from a radial parallax model fitted on the other dials:
   ``shift = s * (V - position)``, V being the camera's nadir point in the image.
4. Offsets = per-dial residual angle offsets from cross-arrow consistency (geared dials:
   ``10 * frac(slow) == fast``), the fastest dial anchoring the chain.

Measurement: angle of the outermost needle pixels (the tip) around the pivot in ellipse-rectified
space, mapped to a value by piecewise-linear interpolation between the tick angles.
"""

from __future__ import annotations

import json
import logging
import math
import os
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from watermeter.image_pipeline import ImagePipeline
from watermeter.opencv_arrows import OpenCVArrowDetector, detect_color_mask

logger = logging.getLogger(__name__)

CALIBRATION_VERSION = 1
MAX_PIVOT_CONDITION = 20.0  # needle-axis system worse than this -> pivot from the parallax model
MIN_TICKS = 5
MAX_BACKGROUND_FRAMES = 60
DEFAULT_TIP_PERCENTILE = 95.0


class CalibrationError(Exception):
    """Calibration could not be computed (no frames, no ticks, ...)."""


def gauge_angle(dx, dy):
    """Angle in degrees, 0 at 12 o'clock, clockwise (image y axis points down)."""
    return np.degrees(np.arctan2(dx, -dy)) % 360.0


def _same_roi(a: dict, b: dict) -> bool:
    """Same normalised ROI (x, y, width, height)."""
    return all(abs(float(a.get(k, -1)) - float(b.get(k, -2))) < 1e-6 for k in ("x", "y", "width", "height"))


def _wrap(x, period=10.0):
    return (np.asarray(x) + period / 2) % period - period / 2


@dataclass
class DialCalibration:
    """Geometry of one analog dial, in pixel coordinates of its ROI crop."""

    roi: dict
    crop_size: tuple  # (w, h)
    centre: tuple  # scale (tick ellipse) centre
    ellipse_axes: tuple  # full axes (a, b) as returned by cv2.fitEllipse
    ellipse_angle: float  # degrees, cv2.fitEllipse convention
    tick_angles: list  # rectified angles around ``centre`` (degrees)
    tick_indices: list  # scale value of each tick (0-9)
    pivot: tuple  # needle pivot (parallax-shifted)
    pivot_source: str  # "needle_axes" | "parallax" | "tick_centre"
    offset: float = 0.0  # added to the value (dial units)
    n_ticks: int = 0
    n_frames: int = 0
    _interp: tuple | None = field(default=None, init=False, repr=False, compare=False)

    def rect_matrix(self) -> np.ndarray:
        """2x2 matrix that turns the tick ellipse into a circle (applied to centred coordinates)."""
        a, b = self.ellipse_axes
        t = np.deg2rad(self.ellipse_angle)
        rot = np.array([[np.cos(t), np.sin(t)], [-np.sin(t), np.cos(t)]])
        r0 = (a + b) / 4
        scale = np.diag([r0 / (a / 2), r0 / (b / 2)])
        return rot.T @ scale @ rot

    def _interp_table(self) -> tuple:
        if self._interp is None:
            idx = np.asarray(self.tick_indices, dtype=float)
            # unwrap each tick angle next to its nominal position (index * 36 deg)
            ang = idx * 36.0 + _wrap(np.asarray(self.tick_angles) - idx * 36.0, 360.0)
            order = np.argsort(idx)
            idx, ang = idx[order], ang[order]
            xs = np.concatenate([ang - 360.0, ang, ang + 360.0])
            ys = np.concatenate([idx - 10.0, idx, idx + 10.0])
            self._interp = (xs, ys)
        return self._interp

    def angle_to_value(self, angle_deg: float) -> float:
        """Rectified needle angle -> dial value (0-10), interpolated between the ticks; no offset."""
        xs, ys = self._interp_table()
        return float(np.interp(angle_deg % 360.0, xs, ys)) % 10.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_interp", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DialCalibration":
        return cls(
            roi=dict(d["roi"]),
            crop_size=tuple(d["crop_size"]),
            centre=tuple(d["centre"]),
            ellipse_axes=tuple(d["ellipse_axes"]),
            ellipse_angle=float(d["ellipse_angle"]),
            tick_angles=[float(a) for a in d["tick_angles"]],
            tick_indices=[int(i) for i in d["tick_indices"]],
            pivot=tuple(d["pivot"]),
            pivot_source=str(d["pivot_source"]),
            offset=float(d.get("offset", 0.0)),
            n_ticks=int(d.get("n_ticks", 0)),
            n_frames=int(d.get("n_frames", 0)),
        )


# --- measurement -------------------------------------------------------------------------------


def needle_mask(img_bgr: np.ndarray, hue_ranges=None, saturation_min: int = 50, value_min: int = 50) -> np.ndarray:
    """Boolean needle mask: HSV colour threshold, 3x3 opening, largest connected component."""
    mask = detect_color_mask(img_bgr, hue_ranges, saturation_min, value_min)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n < 2:
        return np.zeros(mask.shape, dtype=bool)
    return labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))


def measure(mask: np.ndarray, dial: DialCalibration, tip_percentile: float = DEFAULT_TIP_PERCENTILE):
    """Read one dial from its needle mask.

    Returns:
        (value incl. offset in [0, 10), confidence) or (None, 0.0) when the mask is (nearly) empty.
        Confidence is the resultant length of the tip pixel angles (1.0 = all point the same way).
    """
    ys, xs = np.nonzero(mask)
    if len(xs) < 20:
        return None, 0.0
    pts = np.column_stack([xs, ys]).astype(float) - np.asarray(dial.pivot, dtype=float)
    q = pts @ dial.rect_matrix().T
    r = np.hypot(q[:, 0], q[:, 1])
    tip = r >= np.percentile(r, tip_percentile)
    rad = np.deg2rad(gauge_angle(q[tip, 0], q[tip, 1]))
    w = r[tip]
    sx, sy = float((w * np.sin(rad)).sum()), float((w * np.cos(rad)).sum())
    if w.sum() <= 0:
        return None, 0.0
    angle = math.degrees(math.atan2(sx, sy)) % 360.0
    confidence = min(1.0, math.hypot(sx, sy) / float(w.sum()))
    value = (dial.angle_to_value(angle) + dial.offset) % 10.0
    return value, confidence


_SOURCE_COLOURS = {  # BGR
    "needle_axes": (60, 180, 60),
    "manual": (230, 140, 30),
    "parallax": (40, 200, 230),
    "tick_centre": (40, 40, 230),
}


def render_overlay(
    crop_bgr: np.ndarray,
    dial: "DialCalibration | None",
    hue_ranges=None,
    saturation_min: int = 50,
    value_min: int = 50,
    tip_percentile: float = DEFAULT_TIP_PERCENTILE,
) -> np.ndarray:
    """Draw the calibration onto a crop: tick ellipse + ticks, pivot (coloured by source), tip pixels, value.

    Without a calibration the crop is returned unchanged.
    """
    if dial is None:
        return crop_bgr
    out = crop_bgr.copy()
    (cx, cy), (a, b) = dial.centre, dial.ellipse_axes
    cv2.ellipse(out, ((cx, cy), (a, b), dial.ellipse_angle), (255, 255, 255), 1, cv2.LINE_AA)
    inv = np.linalg.inv(dial.rect_matrix())
    r0 = (a + b) / 4
    for ang in dial.tick_angles:  # rectified tick angles -> image points on the ellipse
        t = np.deg2rad(ang)
        q = inv @ np.array([r0 * np.sin(t), -r0 * np.cos(t)]) + np.array([cx, cy])
        cv2.circle(out, (int(round(q[0])), int(round(q[1]))), 4, (255, 255, 255), -1, cv2.LINE_AA)
    mask = needle_mask(crop_bgr, hue_ranges, saturation_min, value_min)
    value, _conf = measure(mask, dial, tip_percentile)
    px, py = dial.pivot
    colour = _SOURCE_COLOURS.get(dial.pivot_source, (255, 255, 255))
    if value is not None:
        ys, xs = np.nonzero(mask)
        q = (np.column_stack([xs, ys]).astype(float) - [px, py]) @ dial.rect_matrix().T
        r = np.hypot(q[:, 0], q[:, 1])
        tip = r >= np.percentile(r, tip_percentile)
        out[ys[tip], xs[tip]] = (255, 255, 0)
        tx, ty = xs[tip].mean(), ys[tip].mean()
        cv2.line(out, (int(px), int(py)), (int(tx), int(ty)), colour, 2, cv2.LINE_AA)
        cv2.putText(out, f"{value:.2f}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(out, f"{value:.2f}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.drawMarker(out, (int(round(px)), int(round(py))), colour, cv2.MARKER_CROSS, 18, 3, cv2.LINE_AA)
    return out


# --- calibration building blocks ----------------------------------------------------------------


def background(crops: list, hue_ranges=None, saturation_min: int = 50, value_min: int = 50) -> np.ndarray:
    """Grayscale dial background without the needle: per-pixel median over unmasked frames."""
    if not crops:
        raise CalibrationError("no crops for background")
    step = max(1, len(crops) // MAX_BACKGROUND_FRAMES)
    sample = crops[::step][:MAX_BACKGROUND_FRAMES]
    stack = np.empty((len(sample),) + sample[0].shape[:2], dtype=np.float32)
    kernel = np.ones((7, 7), np.uint8)
    for i, img in enumerate(sample):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        needle = cv2.dilate(detect_color_mask(img, hue_ranges, saturation_min, value_min), kernel) > 0
        gray[needle] = np.nan
        stack[i] = gray
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels (needle never moved away)
        bg = np.nanmedian(stack, axis=0)
    fill = float(np.nanmedian(bg)) if np.isfinite(bg).any() else 0.0
    return np.nan_to_num(bg, nan=fill).astype(np.uint8)


def _line_intersection(lines) -> tuple[np.ndarray, float]:
    """Least-squares point closest to all lines (centre point, unit direction)."""
    a = np.zeros((2, 2))
    b = np.zeros(2)
    for c, d in lines:
        p = np.eye(2) - np.outer(d, d)
        a += p
        b += p @ c
    cond = float(np.linalg.cond(a))
    point = np.linalg.lstsq(a, b, rcond=None)[0]
    return point, cond


def detect_ticks(bg: np.ndarray) -> tuple[np.ndarray, tuple]:
    """Find the scale ticks: dark, elongated blobs on a ring whose long axes point at a common centre.

    Returns:
        (ticks as Nx2 array of blob centroids, cv2 ellipse fitted through them)
    Raises:
        CalibrationError: fewer than MIN_TICKS ticks found.
    """
    gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY) if bg.ndim == 3 else bg
    h, w = gray.shape
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    th[gray == 0] = 0  # black padding outside the rotated source image
    n, labels, stats, _ = cv2.connectedComponentsWithStats(th)
    cands = []
    for i in range(1, n):
        if not 30 <= stats[i, cv2.CC_STAT_AREA] <= 600:
            continue
        ys, xs = np.nonzero(labels == i)
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(np.column_stack([xs, ys]).astype(np.float32))
        elong = max(rw, rh) / max(1.0, min(rw, rh))
        if not 1.4 <= elong <= 8:
            continue
        a = np.deg2rad(ang if rw >= rh else ang + 90)
        cands.append((cx, cy, np.cos(a), np.sin(a)))
    if len(cands) < MIN_TICKS:
        raise CalibrationError(f"only {len(cands)} tick candidates found")
    cand = np.array(cands)
    lo, hi = 0.33 * min(h, w), 0.56 * min(h, w)

    def radial_fit(centre):
        v = cand[:, :2] - centre
        r = np.hypot(v[:, 0], v[:, 1])
        sin_err = np.abs(v[:, 0] * cand[:, 3] - v[:, 1] * cand[:, 2]) / np.maximum(r, 1.0)
        return sin_err, r

    centre = np.array([w / 2, h / 2])
    sin_err, r = radial_fit(centre)
    keep = (sin_err < 0.3) & (r > lo) & (r < hi)
    for _ in range(5):
        if keep.sum() < 2:
            break
        centre, _cond = _line_intersection([(c[:2], c[2:]) for c in cand[keep]])
        sin_err, r = radial_fit(centre)
        keep = (sin_err < 0.2) & (r > lo) & (r < hi)
    ticks = cand[keep][:, :2]
    # drop blobs off the ring (e.g. neighbouring dial labels), worst first
    while len(ticks) > MIN_TICKS:
        (ex, ey), (a, b), ang = cv2.fitEllipse(ticks.astype(np.float32))
        t = np.deg2rad(ang)
        rot = np.array([[np.cos(t), np.sin(t)], [-np.sin(t), np.cos(t)]])
        q = (ticks - [ex, ey]) @ rot.T
        rr = np.hypot(q[:, 0] / (a / 2), q[:, 1] / (b / 2))
        worst = int(np.argmax(np.abs(rr - 1)))
        if abs(rr[worst] - 1) < 0.04:
            break
        ticks = np.delete(ticks, worst, axis=0)
    if len(ticks) < MIN_TICKS:
        raise CalibrationError(f"only {len(ticks)} ticks on the scale ring")
    return ticks, cv2.fitEllipse(ticks.astype(np.float32))


def needle_axes_pivot(masks: list) -> tuple[np.ndarray, float]:
    """Pivot = least-squares intersection of the needle's principal axes over many frames.

    Returns (pivot, condition number); a large condition number means the needle did not rotate
    enough for the axes to pin down the pivot.
    """
    lines = []
    for m in masks:
        ys, xs = np.nonzero(m)
        if len(xs) < 50:
            continue
        pts = np.column_stack([xs, ys]).astype(float)
        c = pts.mean(axis=0)
        evals, evecs = np.linalg.eigh(np.cov((pts - c).T))
        if evals[1] < 1.5 * max(evals[0], 1e-9):  # nearly round blob: no usable direction
            continue
        lines.append((c, evecs[:, 1]))
    if len(lines) < 2:
        return np.array([np.nan, np.nan]), math.inf
    return _line_intersection(lines)


def fit_parallax(samples: list, image_centre: np.ndarray) -> Callable[[np.ndarray], np.ndarray]:
    """Fit the radial parallax field ``shift = s * (V - position)``.

    Args:
        samples: (position in full-image px, measured pivot shift in px) per well-calibrated dial.
        image_centre: used as V when only one sample is available.
    Returns:
        position -> predicted shift.
    """
    if not samples:
        raise CalibrationError("no dial with a measured pivot for the parallax model")
    if len(samples) == 1:
        pos, shift = (np.asarray(x, dtype=float) for x in samples[0])
        d = np.asarray(image_centre, dtype=float) - pos
        s = float(shift @ d / max(d @ d, 1e-9))
        vp = np.asarray(image_centre, dtype=float)
        return lambda p: s * (vp - np.asarray(p, dtype=float))
    rows, rhs = [], []
    for pos, shift in samples:
        rows += [[1.0, 0.0, -pos[0]], [0.0, 1.0, -pos[1]]]
        rhs += [shift[0], shift[1]]
    (svx, svy, s), *_ = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)
    return lambda p: np.array([svx - s * p[0], svy - s * p[1]])


def _pair_residuals(values: np.ndarray, p: int) -> np.ndarray:
    """Cross-arrow residual of dial p vs p+1 in units of dial p (NaN rows dropped)."""
    r = _wrap(10 * (values[:, p] % 1) - values[:, p + 1]) / 10
    return r[np.isfinite(r)]


def cross_arrow_mae(values: np.ndarray) -> float | None:
    """Mean absolute cross-arrow residual over all adjacent dial pairs (columns slow -> fast)."""
    res = [_pair_residuals(values, p) for p in range(values.shape[1] - 1)]
    res = np.concatenate(res) if res else np.array([])
    return float(np.mean(np.abs(res))) if len(res) else None


def fit_offsets(values: np.ndarray, max_abs: float = 0.2, min_frames: int = 30) -> tuple[list, list]:
    """Per-dial offsets that make geared dials consistent; the fastest dial (last column) stays fixed.

    Returns:
        (applied offsets, raw offsets); a raw offset beyond ``max_abs`` (or without enough frames,
        NaN) is not applied (0.0).
    """
    k = values.shape[1]
    applied, raw = [0.0] * k, [0.0] * k
    for p in range(k - 2, -1, -1):
        corrected = (values + np.asarray(applied)) % 10
        r = _pair_residuals(corrected, p)
        if len(r) < min_frames:
            raw[p] = math.nan
            continue
        raw[p] = -float(np.median(r))
        applied[p] = raw[p] if abs(raw[p]) <= max_abs else 0.0
    return applied, raw


def _dial_geometry(bg: np.ndarray) -> dict:
    ticks, ellipse = detect_ticks(bg)
    (ex, ey), axes, ang = ellipse
    proto = DialCalibration(
        roi={}, crop_size=(0, 0), centre=(ex, ey), ellipse_axes=tuple(axes), ellipse_angle=float(ang),
        tick_angles=[], tick_indices=[], pivot=(ex, ey), pivot_source="tick_centre",
    )  # fmt: skip
    q = (ticks - [ex, ey]) @ proto.rect_matrix().T
    angles = gauge_angle(q[:, 0], q[:, 1])
    idx = np.round(angles / 36.0).astype(int) % 10  # ROI rotation is corrected upstream: |residual| < 18 deg
    tick_angles, tick_indices = [], []
    for i in sorted(set(idx.tolist())):
        sel = angles[idx == i]
        tick_indices.append(int(i))
        tick_angles.append(float(i * 36.0 + np.mean(_wrap(sel - i * 36.0, 360.0))) % 360.0)
    return {
        "centre": (float(ex), float(ey)),
        "ellipse_axes": (float(axes[0]), float(axes[1])),
        "ellipse_angle": float(ang),
        "tick_angles": tick_angles,
        "tick_indices": tick_indices,
        "n_ticks": len(ticks),
    }


def calibrate_dials(
    crops_by_id: dict,
    rois: dict,
    arrow_ids: list,
    hue_ranges=None,
    saturation_min: int = 50,
    value_min: int = 50,
    tip_percentile: float = DEFAULT_TIP_PERCENTILE,
    min_offset_frames: int = 30,
    pivot_overrides: dict | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> tuple[dict, dict]:
    """Calibrate every dial from frame-aligned crop lists (``crops_by_id[id][i]`` = frame i).

    ``pivot_overrides`` ({roi_id: {"pivot": [x, y], "roi": {...}}}) replace the estimated pivot of a dial
    (``pivot_source: "manual"``) as long as the dial's ROI is unchanged; they also feed the parallax model.
    ``progress(stage, done, total)`` is called once per measured dial (stage ``"measure"``).

    Returns:
        (calibrations by ROI id, report dict)
    Raises:
        CalibrationError: no dial could be calibrated.
    """
    color = {"hue_ranges": hue_ranges, "saturation_min": saturation_min, "value_min": value_min}
    ids = [i for i in arrow_ids if crops_by_id.get(i)]
    n_frames = min(len(crops_by_id[i]) for i in ids) if ids else 0
    report: dict = {"frames": n_frames, "dials": {}, "failed": {}}

    geo, masks, pivots = {}, {}, {}
    for rid in ids:
        crops = crops_by_id[rid][:n_frames]
        try:
            geo[rid] = _dial_geometry(background(crops, **color))
        except CalibrationError as e:
            report["failed"][rid] = str(e)
            logger.warning(f"Arrow calibration: {rid} failed: {e}")
            continue
        masks[rid] = [needle_mask(c, **color) for c in crops]
        pivots[rid] = needle_axes_pivot(masks[rid])
    if not geo:
        raise CalibrationError(f"no dial could be calibrated: {report['failed']}")

    def full_position(rid):
        roi, (h, w) = rois[rid], crops_by_id[rid][0].shape[:2]
        sx, sy = w / roi["width"], h / roi["height"]  # crop px per normalised unit = aligned image size
        return np.array([(roi["x"] + roi["width"] / 2) * sx, (roi["y"] + roi["height"] / 2) * sy]), (sx, sy)

    manual, ignored = {}, set()
    for rid, ov in (pivot_overrides or {}).items():
        if rid not in geo:
            continue
        if rid in rois and _same_roi(ov.get("roi", {}), rois[rid]):
            manual[rid] = tuple(float(v) for v in ov["pivot"])
        else:
            ignored.add(rid)
            logger.warning(f"Arrow calibration: manual pivot for {rid} ignored (ROI changed since it was set)")
    for rid, p in manual.items():
        pivots[rid] = (np.asarray(p), 0.0)  # a manual pivot is as good as a measured one

    good = [r for r in geo if r in rois and pivots[r][1] <= MAX_PIVOT_CONDITION]
    samples = [(full_position(r)[0], np.asarray(pivots[r][0]) - np.asarray(geo[r]["centre"])) for r in good]
    parallax = None
    if samples:
        sizes = np.array([full_position(r)[1] for r in geo if r in rois])
        parallax = fit_parallax(samples, image_centre=np.median(sizes, axis=0) / 2)

    dials = {}
    for rid, g in geo.items():
        pivot_px, cond = pivots[rid]
        if rid in manual:
            pivot, source = manual[rid], "manual"
        elif cond <= MAX_PIVOT_CONDITION:
            pivot, source = tuple(float(v) for v in pivot_px), "needle_axes"
        elif parallax is not None and rid in rois:
            pivot = tuple(float(v) for v in np.asarray(g["centre"]) + parallax(full_position(rid)[0]))
            source = "parallax"
        else:
            pivot, source = g["centre"], "tick_centre"
            logger.warning(f"Arrow calibration: {rid} pivot unknown, using the scale centre (degraded)")
        h, w = crops_by_id[rid][0].shape[:2]
        dials[rid] = DialCalibration(
            roi=dict(rois.get(rid, {})), crop_size=(int(w), int(h)), pivot=pivot, pivot_source=source,
            n_frames=n_frames, **g,
        )  # fmt: skip

    # measure every frame with the fresh geometry, then fit the residual offsets
    cols = [r for r in ids]
    values = np.full((n_frames, len(cols)), np.nan)
    baseline = np.full((n_frames, len(cols)), np.nan)
    opencv = OpenCVArrowDetector(**color)
    for j, rid in enumerate(cols):
        for i in range(n_frames):
            if rid in dials:
                v, _ = measure(masks[rid][i], dials[rid], tip_percentile)
                values[i, j] = np.nan if v is None else v
            v, _ = opencv._detect(crops_by_id[rid][i])
            baseline[i, j] = np.nan if v is None else v
        if progress:
            progress("measure", j + 1, len(cols))
    applied, raw = fit_offsets(values, min_frames=min_offset_frames)
    for j, rid in enumerate(cols):
        if rid in dials:
            dials[rid].offset = float(applied[j])
    corrected = (values + np.asarray(applied)) % 10

    report["opencv"] = {"mae": cross_arrow_mae(baseline)}
    report["calibrated"] = {"mae_before": cross_arrow_mae(values), "mae_after": cross_arrow_mae(corrected)}
    for j, rid in enumerate(cols):
        if rid in dials:
            d = dials[rid]
            report["dials"][rid] = {
                "ticks": d.n_ticks,
                "pivot": [round(v, 1) for v in d.pivot],
                "pivot_source": d.pivot_source,
                "pivot_condition": None if not math.isfinite(pivots[rid][1]) else round(pivots[rid][1], 1),
                "centre": [round(v, 1) for v in d.centre],
                "offset": round(d.offset, 4),
                "offset_raw": None if not math.isfinite(raw[j]) else round(raw[j], 4),
                "override_ignored": rid in ignored,
            }
    return dials, report


# --- persistence + archive runner ---------------------------------------------------------------


def save_calibration(path, dials: dict, report: dict, pivot_overrides: dict | None = None) -> None:
    """Write the calibration JSON atomically (manual pivot overrides travel with it)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "version": CALIBRATION_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dials": {rid: d.to_dict() for rid, d in dials.items()},
        "report": report,
        "pivot_overrides": pivot_overrides or {},
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2))
    os.replace(tmp, path)


def load_calibration(path) -> dict:
    """Read a calibration JSON -> {roi_id: DialCalibration}. Raises on missing/invalid files."""
    doc = json.loads(Path(path).read_text())
    if doc.get("version") != CALIBRATION_VERSION:
        raise CalibrationError(f"unsupported calibration version {doc.get('version')}")
    return {rid: DialCalibration.from_dict(d) for rid, d in doc["dials"].items()}


def load_pivot_overrides(path) -> dict:
    """Manual pivot overrides stored with a calibration ({} without file or key)."""
    try:
        return dict(json.loads(Path(path).read_text()).get("pivot_overrides") or {})
    except (OSError, ValueError):
        return {}


def calibrate_from_archive(
    config: dict,
    archive_dir,
    max_frames: int = 300,
    reference_path=None,
    pivot_overrides: dict | None = None,
    progress: Callable[[str, int, int], None] | None = None,
):
    """Calibrate from archived whole images, cropped exactly like production does.

    Frames are spread evenly over the whole archive (more needle rotation -> better pivot fit).
    """
    if config.get("images", {}).get("process_separate", False):
        raise CalibrationError("calibration needs whole-image mode (images.process_separate: false)")
    files = sorted(Path(archive_dir).rglob("*.jpg"))
    if len(files) > max_frames:
        files = [files[int(round(i))] for i in np.linspace(0, len(files) - 1, max_frames)]
    pipeline = ImagePipeline(config)
    if reference_path is not None:
        pipeline.REFERENCE_PATH = Path(reference_path)

    crops_by_id: dict = {}
    for n, f in enumerate(files, start=1):
        if progress:
            progress("align", n, len(files))
        try:
            rois, alignment = pipeline.process_whole_image(f.read_bytes())
        except Exception as e:  # corrupt archive file: skip, don't abort the calibration
            logger.debug(f"Arrow calibration: skipping {f}: {e}")
            continue
        if not alignment.success or not rois:
            continue
        frame = {}
        for rid, (data, cls) in rois.items():
            if cls == "arrows":
                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    frame[rid] = img
        for rid, img in frame.items():
            crops_by_id.setdefault(rid, []).append(img)
    if not crops_by_id:
        raise CalibrationError(f"no aligned frames in {archive_dir} ({len(files)} files tried)")

    inf = config.get("inference", {})
    color_cfg = inf.get("opencv_arrows", {}) or {}
    cal_cfg = inf.get("calibrated_arrows", {}) or {}
    analogs = config.get("detection", {}).get("analogs", {})
    analog_rois = analogs.get("rois", [])[: analogs.get("count", len(analogs.get("rois", [])))]
    rois = {f"analog_{i + 1}": dict(r) for i, r in enumerate(analog_rois)}
    arrow_ids = list(rois)  # same IDs and order (slow -> fast) as ImagePipeline / get_position_ids
    return calibrate_dials(
        crops_by_id,
        rois,
        arrow_ids,
        hue_ranges=color_cfg.get("hue_ranges"),
        saturation_min=color_cfg.get("saturation_min", 50),
        value_min=color_cfg.get("value_min", 50),
        tip_percentile=cal_cfg.get("tip_percentile", DEFAULT_TIP_PERCENTILE),
        pivot_overrides=pivot_overrides,
        progress=progress,
    )
