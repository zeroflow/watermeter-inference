"""Calibrated arrow detector (``inference.arrows_mode: calibrated``).

Reads each dial around its calibrated needle pivot and interpolates between the detected scale ticks
(see ``arrow_calibration``). The calibration is per ROI, so callers pass the ROI id (``image_id``).
Whenever a dial has no usable calibration -- no file, unknown/missing id, ROI edited or crop size
changed since calibrating -- it falls back to ``OpenCVArrowDetector`` with the same colour settings.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from watermeter.arrow_calibration import (
    DEFAULT_TIP_PERCENTILE,
    DialCalibration,
    load_calibration,
    measure,
    needle_mask,
)
from watermeter.opencv_arrows import OpenCVArrowDetector

logger = logging.getLogger(__name__)

DEFAULT_CALIBRATION_FILE = "/data/arrow_calibration.json"
_CROP_SIZE_TOLERANCE = 2  # px
_RECALIBRATE_HINT = "re-run the calibration (POST /api/arrows/calibrate)"
_NAN_RESULT = {"class": "NaN", "confidence": 0.0}


def _same_roi(a: dict, b: dict) -> bool:
    return all(abs(float(a.get(k, -1)) - float(b.get(k, -2))) < 1e-6 for k in ("x", "y", "width", "height"))


class CalibratedArrowDetector:
    """Same predict interface as ``OpenCVArrowDetector``, plus an optional ``image_id``."""

    def __init__(
        self,
        calibrations: dict[str, DialCalibration],
        rois: dict[str, dict] | None = None,
        hue_ranges: list[list[int]] | None = None,
        saturation_min: int = 50,
        value_min: int = 50,
        tip_percentile: float = DEFAULT_TIP_PERCENTILE,
    ):
        self.hue_ranges = hue_ranges
        self.saturation_min = saturation_min
        self.value_min = value_min
        self.tip_percentile = tip_percentile
        self._fallback = OpenCVArrowDetector(hue_ranges, saturation_min, value_min)
        self._warned: set = set()
        self._dials: dict[str, DialCalibration] = {}
        for rid, dial in calibrations.items():
            if rois is not None and rid in rois and not _same_roi(dial.roi, rois[rid]):
                logger.warning(f"Arrow calibration for {rid} is stale (ROI changed) -- {_RECALIBRATE_HINT}")
                continue
            self._dials[rid] = dial

    @classmethod
    def from_config(cls, config: dict) -> "CalibratedArrowDetector":
        inference_cfg = config.get("inference", {})
        color_cfg = inference_cfg.get("opencv_arrows", {}) or {}
        cal_cfg = inference_cfg.get("calibrated_arrows", {}) or {}
        path = cal_cfg.get("calibration_file", DEFAULT_CALIBRATION_FILE)
        try:
            calibrations = load_calibration(path)
        except FileNotFoundError:
            logger.warning(f"No arrow calibration at {path} -- using opencv arrows until you {_RECALIBRATE_HINT}")
            calibrations = {}
        except Exception as e:
            logger.warning(f"Arrow calibration {path} unreadable ({e}) -- using opencv arrows")
            calibrations = {}
        rois = None
        if not config.get("images", {}).get("process_separate", False):
            analog_rois = config.get("detection", {}).get("analogs", {}).get("rois", [])
            rois = {f"analog_{i + 1}": dict(r) for i, r in enumerate(analog_rois)}
        detector = cls(
            calibrations,
            rois=rois,
            hue_ranges=color_cfg.get("hue_ranges"),
            saturation_min=color_cfg.get("saturation_min", 50),
            value_min=color_cfg.get("value_min", 50),
            tip_percentile=cal_cfg.get("tip_percentile", DEFAULT_TIP_PERCENTILE),
        )
        logger.info(f"Calibrated arrows: {len(detector._dials)} dial(s) calibrated ({sorted(detector._dials)})")
        return detector

    @property
    def calibrated_ids(self) -> list[str]:
        return sorted(self._dials)

    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(msg)

    def _fallback_result(self, image: np.ndarray) -> dict:
        value, confidence = self._fallback._detect(image)
        if value is None:
            return dict(_NAN_RESULT)
        return {"class": str(value), "confidence": confidence}

    def _predict_image(self, image: np.ndarray, image_id: str | None) -> dict:
        dial = self._dials.get(image_id) if image_id else None
        if dial is None:
            self._warn_once(f"nocal:{image_id}", f"No arrow calibration for {image_id!r} -- using opencv arrows")
            return self._fallback_result(image)
        h, w = image.shape[:2]
        cw, ch = dial.crop_size
        if abs(w - cw) > _CROP_SIZE_TOLERANCE or abs(h - ch) > _CROP_SIZE_TOLERANCE:
            self._warn_once(
                f"size:{image_id}",
                f"Arrow calibration for {image_id} is stale (crop {w}x{h}, calibrated {cw}x{ch}) -- "
                f"using opencv arrows; {_RECALIBRATE_HINT}",
            )
            return self._fallback_result(image)
        mask = needle_mask(image, self.hue_ranges, self.saturation_min, self.value_min)
        value, confidence = measure(mask, dial, self.tip_percentile)
        if value is None:
            logger.warning(f"Calibrated arrows: no needle pixels found for {image_id}")
            return dict(_NAN_RESULT)
        value = round(value, 4) % 10.0
        return {"class": f"{round(value, 2) % 10.0:.2f}", "confidence": confidence, "value": value}

    def predict(self, image_path, image_id: str | None = None) -> dict:
        image = cv2.imread(str(image_path))
        if image is None:
            logger.warning("Calibrated arrows: failed to read %s", image_path)
            return dict(_NAN_RESULT)
        return self._predict_image(image, image_id)

    def predict_detailed(self, image_path, top_k: int = 3, image_id: str | None = None) -> list[dict]:
        return [self.predict(image_path, image_id=image_id)]

    def predict_from_bytes(self, image_bytes: bytes, image_id: str | None = None) -> dict:
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            logger.warning("Calibrated arrows: failed to decode image")
            return dict(_NAN_RESULT)
        return self._predict_image(image, image_id)

    def predict_detailed_from_bytes(self, image_bytes: bytes, top_k: int = 3, image_id: str | None = None) -> list:
        """Single-element list (no top-K for geometric detection)."""
        return [self.predict_from_bytes(image_bytes, image_id=image_id)]
