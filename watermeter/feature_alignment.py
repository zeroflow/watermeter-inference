"""
Feature-based image alignment: AKAZE keypoints + robust homography against a reference.

The reference image is the (fisheye-corrected, rotated) frame the ROIs were drawn on.
Moving parts (digit wheels, dial needles) are excluded via the ROI boxes so only the
static meter face (print, logo, bezel) drives the fit. Fails closed: an
``AlignmentOutcome`` with ``success=False`` never carries an image.
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AlignmentOutcome:
    """Result of ``FeatureAligner.align``.

    ``error_reason`` is one of ``"insufficient_matches"``, ``"transform_failed"``,
    ``"low_inliers"``, ``"implausible_transform"``, ``"high_reprojection_error"``.
    """

    success: bool
    image: Optional[np.ndarray] = None
    homography: Optional[np.ndarray] = None
    inliers: int = 0
    inlier_ratio: float = 0.0
    error_reason: Optional[str] = None


class FeatureAligner:
    """Aligns live frames onto a reference frame via AKAZE + MAGSAC homography."""

    RATIO_TEST = 0.8  # Lowe ratio for kNN matches
    RANSAC_REPROJ_THRESHOLD = 3.0  # px
    MAX_SCALE_DEVIATION = 0.10  # +/-10% uniform scale
    MAX_ROTATION_DEG = 10.0
    MAX_PERSPECTIVE = 0.05  # |h20|,|h21| normalised by image size
    MAX_MEAN_REPROJ_ERROR = 2.0  # px, mean over inliers
    BLACK_BORDER_LEVEL = 8  # gray level treated as canvas padding from rotation/undistort
    ROI_EXCLUSION_PAD = 0.01  # fraction of the larger image side added around excluded ROIs

    def __init__(
        self,
        reference_bgr: np.ndarray,
        exclude_rois: Optional[List[Dict]] = None,
        min_inliers: int = 30,
        min_inlier_ratio: float = 0.3,
    ):
        self.min_inliers = min_inliers
        self.min_inlier_ratio = min_inlier_ratio
        self._ref_shape = reference_bgr.shape[:2]
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        self._detector = cv2.AKAZE_create()
        self._matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

        gray = self._preprocess(reference_bgr)
        mask = self._content_mask(gray)
        h, w = self._ref_shape
        # Pad exclusions: descriptors near an ROI edge sample moving pixels too
        pad = int(math.ceil(self.ROI_EXCLUSION_PAD * max(w, h)))
        for roi in exclude_rois or []:
            x1 = int(roi["x"] * w) - pad
            y1 = int(roi["y"] * h) - pad
            x2 = int(math.ceil((roi["x"] + roi["width"]) * w)) + pad
            y2 = int(math.ceil((roi["y"] + roi["height"]) * h)) + pad
            mask[max(0, y1) : max(0, y2), max(0, x1) : max(0, x2)] = 0

        self.reference_keypoints, self._ref_descriptors = self._detector.detectAndCompute(gray, mask)
        if self._ref_descriptors is None or len(self.reference_keypoints) < min_inliers:
            raise ValueError(
                f"Reference has too few features for alignment ({len(self.reference_keypoints or [])} keypoints)"
            )
        logger.debug(f"Feature reference built: {len(self.reference_keypoints)} keypoints")

    def _preprocess(self, img_bgr: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        return self._clahe.apply(gray)

    def _content_mask(self, gray: np.ndarray) -> np.ndarray:
        """Mask out black canvas padding (rotation/undistort borders), eroded to drop its edge."""
        mask = np.where(gray > self.BLACK_BORDER_LEVEL, 255, 0).astype(np.uint8)
        return cv2.erode(mask, np.ones((15, 15), np.uint8))

    def align(self, img_bgr: np.ndarray) -> AlignmentOutcome:
        gray = self._preprocess(img_bgr)
        keypoints, descriptors = self._detector.detectAndCompute(gray, self._content_mask(gray))
        if descriptors is None or len(keypoints) < 2:
            return AlignmentOutcome(success=False, error_reason="insufficient_matches")

        good = []
        for pair in self._matcher.knnMatch(descriptors, self._ref_descriptors, k=2):
            if len(pair) == 2 and pair[0].distance < self.RATIO_TEST * pair[1].distance:
                good.append(pair[0])
        if len(good) < self.min_inliers:
            return AlignmentOutcome(success=False, error_reason="insufficient_matches")

        live_pts = np.array([keypoints[m.queryIdx].pt for m in good], dtype=np.float32).reshape(-1, 1, 2)
        ref_pts = np.array([self.reference_keypoints[m.trainIdx].pt for m in good], dtype=np.float32).reshape(-1, 1, 2)
        H, inlier_mask = cv2.findHomography(live_pts, ref_pts, cv2.USAC_MAGSAC, self.RANSAC_REPROJ_THRESHOLD)
        if H is None or inlier_mask is None:
            return AlignmentOutcome(success=False, error_reason="transform_failed")

        inliers = int(inlier_mask.sum())
        ratio = inliers / len(good)
        stats = {"inliers": inliers, "inlier_ratio": ratio}
        if inliers < self.min_inliers or ratio < self.min_inlier_ratio:
            return AlignmentOutcome(success=False, error_reason="low_inliers", **stats)

        if not self._is_plausible(H):
            return AlignmentOutcome(success=False, error_reason="implausible_transform", homography=H, **stats)

        sel = inlier_mask.ravel().astype(bool)
        projected = cv2.perspectiveTransform(live_pts[sel], H)
        reproj_error = float(np.mean(np.linalg.norm(projected - ref_pts[sel], axis=2)))
        if reproj_error > self.MAX_MEAN_REPROJ_ERROR:
            return AlignmentOutcome(success=False, error_reason="high_reprojection_error", homography=H, **stats)

        h, w = self._ref_shape
        aligned = cv2.warpPerspective(img_bgr, H, (w, h), borderMode=cv2.BORDER_REPLICATE)
        return AlignmentOutcome(success=True, image=aligned, homography=H, **stats)

    def _is_plausible(self, H: np.ndarray) -> bool:
        Hn = H / H[2, 2]
        A = Hn[:2, :2]
        scale = math.sqrt(abs(float(np.linalg.det(A))))
        rotation = math.degrees(math.atan2(A[1, 0], A[0, 0]))
        h, w = self._ref_shape
        perspective = max(abs(Hn[2, 0]) * w, abs(Hn[2, 1]) * h)
        plausible = (
            abs(scale - 1.0) <= self.MAX_SCALE_DEVIATION
            and abs(rotation) <= self.MAX_ROTATION_DEG
            and perspective <= self.MAX_PERSPECTIVE
        )
        if not plausible:
            logger.warning(f"Implausible homography: scale={scale:.3f} rot={rotation:.1f}° persp={perspective:.4f}")
        return plausible
