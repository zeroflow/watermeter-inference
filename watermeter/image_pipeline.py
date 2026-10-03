"""
Image Pipeline — fetching, rotation, marker alignment, and ROI extraction.

Extracted from WatermeterService to reduce god-object complexity.
All methods are self-contained; the only shared state is _marker_templates.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import httpx
import numpy as np

from .feature_alignment import FeatureAligner

logger = logging.getLogger(__name__)


@dataclass
class AlignmentResult:
    """Outcome of marker-based image alignment.

    On success, ``image`` holds the warped image and ``marker_confidences``
    holds the cv2.matchTemplate scores per marker.

    On failure, ``image`` is None — callers MUST NOT proceed with the original
    misaligned image (that's the whole point of fail-closed). ``error_reason``
    is one of: ``"insufficient_markers"``, ``"templates_missing"``,
    ``"search_region_too_small"``, ``"low_confidence"``, ``"transform_failed"``.
    ``failed_marker`` is 1-indexed when a single marker is at fault, else None.

    ``method`` is ``"features"``, ``"template"`` or ``"none"`` (nothing to align
    against). For ``"features"``, ``marker_confidences`` holds ``[inlier_ratio]``
    and the feature error reasons from ``feature_alignment.AlignmentOutcome`` apply.
    """

    success: bool
    image: Optional[np.ndarray] = None
    error_reason: Optional[str] = None
    failed_marker: Optional[int] = None
    marker_confidences: List[float] = field(default_factory=list)
    method: str = "template"
    inliers: int = 0
    inlier_ratio: Optional[float] = None
    homography: Optional[np.ndarray] = None


def apply_fisheye_correction(image, k1):
    """Apply radial distortion correction using a single k1 coefficient.

    Args:
        image: BGR image as numpy array
        k1: radial distortion coefficient. Positive=barrel, negative=pincushion, 0=no change.

    Returns:
        Corrected image. Uses alpha=1 to preserve all source pixels (black borders may appear).
    """
    if k1 == 0:
        return image
    h, w = image.shape[:2]
    fx = fy = float(w)
    cx, cy = w / 2.0, h / 2.0
    camera_matrix = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)
    dist_coeffs = np.array([k1, 0, 0, 0, 0], dtype=np.float64)

    # alpha=1 preserves all source pixels; no content is lost (black borders may appear at edges)
    new_camera_matrix, _ = cv2.getOptimalNewCameraMatrix(camera_matrix, dist_coeffs, (w, h), alpha=1, newImgSize=(w, h))

    return cv2.undistort(image, camera_matrix, dist_coeffs, None, new_camera_matrix)


def rotate_image_full(img, rotation_degrees):
    """Rotate image by given degrees, expanding canvas to fit full rotated image.

    Args:
        img: BGR image as numpy array
        rotation_degrees: Rotation angle in degrees (positive = clockwise, negative = counter-clockwise)

    Returns:
        Rotated image with expanded canvas (no corners clipped).
    """
    if rotation_degrees == 0:
        return img
    h, w = img.shape[:2]
    center = (w / 2, h / 2)
    # Negate angle: OpenCV treats positive as CCW, but user expects positive = CW
    angle_cv = -rotation_degrees
    matrix = cv2.getRotationMatrix2D(center, angle_cv, 1.0)

    # Compute expanded bounding box to fit the full rotated image
    cos_a = abs(np.cos(np.radians(rotation_degrees)))
    sin_a = abs(np.sin(np.radians(rotation_degrees)))
    new_w = int(np.ceil(w * cos_a + h * sin_a))
    new_h = int(np.ceil(h * cos_a + w * sin_a))

    # Adjust rotation matrix translation for the new (larger) canvas center
    matrix[0, 2] += (new_w - w) / 2
    matrix[1, 2] += (new_h - h) / 2

    return cv2.warpAffine(img, matrix, (new_w, new_h))


DEFAULT_FETCH_TIMEOUT = 30  # seconds
DEFAULT_FETCH_DELAY = 0.1  # seconds between individual image fetches


class ImagePipeline:
    """Handles image fetching, rotation, marker alignment, and ROI extraction."""

    # Alignment constants (internal tuning, not user-facing)
    SEARCH_MARGIN = 0.15  # +/-15% of image dimensions for search window
    DEFAULT_CONFIDENCE_THRESHOLD = 0.5  # cv2.TM_CCOEFF_NORMED min match score (back-compat default)
    REFERENCE_PATH = Path("/data/reference_raw.jpg")  # raw frame the ROIs were drawn on

    def __init__(self, config: dict):
        self.config = config
        alignment_cfg = config.get("alignment", {}) if isinstance(config, dict) else {}
        self.CONFIDENCE_THRESHOLD = float(
            alignment_cfg.get("marker_confidence_threshold", self.DEFAULT_CONFIDENCE_THRESHOLD)
        )
        # Cached marker templates for alignment (loaded lazily)
        self._marker_templates: Optional[List[np.ndarray]] = None
        # Cached feature aligner + the inputs it was built from (rebuilt when they change)
        self._feature_aligner: Optional[FeatureAligner] = None
        self._feature_aligner_key: Optional[tuple] = None

    async def fetch_images(self) -> Dict[str, Tuple[bytes, str]]:
        """
        Fetch all images from the camera.

        Returns:
            Dict mapping ID to (image_bytes, image_class)
        """
        images = {}
        images_cfg = self.config["images"]
        # Individual images live next to the whole image: derive base URL from images.src
        base_url = images_cfg["src"].rsplit("/", 1)[0]
        timeout = images_cfg.get("timeout", DEFAULT_FETCH_TIMEOUT)
        fetch_delay = images_cfg.get("fetch_delay", DEFAULT_FETCH_DELAY)

        # Collect all IDs with their class
        all_ids = []
        for id_name in self.config["images"]["digits"]:
            all_ids.append((id_name, "digits"))
        for id_name in self.config["images"]["arrows"]:
            all_ids.append((id_name, "arrows"))

        logger.info(f"Fetching {len(all_ids)} images from camera")

        async with httpx.AsyncClient(timeout=timeout) as client:
            for idx, (image_id, image_class) in enumerate(all_ids):
                # Rate limiting - delay between fetches
                if idx > 0:
                    await asyncio.sleep(fetch_delay)

                url = f"{base_url}/{image_id}.jpg"
                try:
                    logger.debug(f"Fetching {url}")
                    response = await client.get(url)
                    response.raise_for_status()
                    images[image_id] = (response.content, image_class)
                    logger.debug(f"✓ Fetched {image_id} ({len(response.content)} bytes)")
                except httpx.HTTPError as e:
                    logger.error(f"Failed to fetch {image_id}: {e}")
                    # Continue with other images

        logger.info(f"Successfully fetched {len(images)}/{len(all_ids)} images")
        return images

    async def fetch_whole_image(self) -> Optional[bytes]:
        """
        Fetch the whole source image from the camera.

        Returns:
            Image bytes or None on failure
        """
        src_url = self.config["images"]["src"]
        timeout = self.config["images"].get("timeout", DEFAULT_FETCH_TIMEOUT)

        logger.info(f"Fetching whole image from {src_url}")

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(src_url)
                response.raise_for_status()
                logger.info(f"Successfully fetched whole image ({len(response.content)} bytes)")
                return response.content
            except httpx.HTTPError as e:
                logger.error(f"Failed to fetch whole image: {e}")
                return None

    def process_whole_image(self, image_bytes: bytes) -> Tuple[Optional[Dict[str, Tuple[bytes, str]]], AlignmentResult]:
        """
        Process whole image: apply rotation, marker alignment, and extract ROIs.

        Args:
            image_bytes: Raw image bytes

        Returns:
            Tuple of (images_dict_or_None, AlignmentResult).
            - On alignment success: (dict mapping ID to (image_bytes, image_class),
              AlignmentResult(success=True, ...)).
            - On alignment failure: (None, AlignmentResult(success=False, ...)).
            - When markers are not configured: (images_dict, AlignmentResult(success=True, ...))
              so callers can always rely on the alignment object.
        """
        # Decode image
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        height, width = img.shape[:2]

        detection = self.config.get("detection", {})

        # 1+2. Fisheye correction and rotation (same geometry as the feature reference)
        img = self._apply_geometry(img)

        # 3. Alignment (fail-closed)
        alignment = self._align(img, detection.get("markers", []))
        if not alignment.success:
            # Fail-closed: do NOT proceed with the misaligned image.
            return None, alignment
        img = alignment.image
        height, width = img.shape[:2]  # Update dimensions after alignment

        # 4. Extract ROIs
        images = {}

        # Extract digit ROIs - use detection config count, generate IDs
        digits_config = detection.get("digits", {})
        digit_rois = digits_config.get("rois", [])
        digit_count = digits_config.get("count", len(digit_rois))

        for i, roi in enumerate(digit_rois[:digit_count]):
            roi_img = self._extract_roi(img, roi, width, height)
            # Encode to JPEG bytes
            _, encoded = cv2.imencode(".jpg", roi_img)
            digit_id = f"digit_{i + 1}"
            images[digit_id] = (encoded.tobytes(), "digits")
            logger.debug(f"Extracted digit ROI: {digit_id}")

        # Extract analog ROIs - use detection config count, generate IDs
        analogs_config = detection.get("analogs", {})
        analog_rois = analogs_config.get("rois", [])
        analog_count = analogs_config.get("count", len(analog_rois))

        for i, roi in enumerate(analog_rois[:analog_count]):
            roi_img = self._extract_roi(img, roi, width, height)
            # Encode to JPEG bytes
            _, encoded = cv2.imencode(".jpg", roi_img)
            analog_id = f"analog_{i + 1}"
            images[analog_id] = (encoded.tobytes(), "arrows")
            logger.debug(f"Extracted analog ROI: {analog_id}")

        logger.info(f"Extracted {len(images)} ROIs from whole image")
        return images, alignment

    def _apply_geometry(self, img: np.ndarray) -> np.ndarray:
        """Apply configured fisheye correction, then rotation (expanded canvas)."""
        detection = self.config.get("detection", {})
        fisheye_k1 = detection.get("fisheye_correction", 0)
        if fisheye_k1 != 0:
            img = apply_fisheye_correction(img, fisheye_k1)
            logger.debug(f"Applied fisheye correction: k1={fisheye_k1}")
        rotation = detection.get("rotation", 0)
        if rotation != 0:
            img = rotate_image_full(img, rotation)
            logger.debug(f"Applied rotation: {rotation}° (expanded canvas to {img.shape[1]}x{img.shape[0]})")
        return img

    def _align(self, img: np.ndarray, markers: List[Dict]) -> AlignmentResult:
        """Pick the alignment method from config, falling back from features to templates."""
        method = self.config.get("alignment", {}).get("method", "template")
        if method == "features":
            result = self._align_with_features(img)
            if result is not None and result.success:
                return result
            if len(markers) >= 2:
                reason = result.error_reason if result is not None else "reference_missing"
                logger.warning(f"Feature alignment unavailable ({reason}); falling back to marker templates")
                return self._align_with_markers(img, markers)
            if result is not None:
                return result
        elif len(markers) >= 2:
            return self._align_with_markers(img, markers)
        # Nothing to align against: treat as trivially aligned for caller.
        return AlignmentResult(success=True, image=img, marker_confidences=[], method="none")

    def _get_feature_aligner(self) -> Optional[FeatureAligner]:
        """Return a FeatureAligner for the current reference/config, or None if no usable reference."""
        try:
            stat = self.REFERENCE_PATH.stat()
        except OSError:
            logger.warning(f"Feature alignment: reference image not found at {self.REFERENCE_PATH}")
            return None

        detection = self.config.get("detection", {})
        alignment_cfg = self.config.get("alignment", {})
        rois = list(detection.get("digits", {}).get("rois", [])) + list(detection.get("analogs", {}).get("rois", []))
        min_inliers = int(alignment_cfg.get("min_inliers", 30))
        min_inlier_ratio = float(alignment_cfg.get("min_inlier_ratio", 0.3))
        key = (
            stat.st_mtime_ns,
            stat.st_size,
            detection.get("fisheye_correction", 0),
            detection.get("rotation", 0),
            repr(rois),
            min_inliers,
            min_inlier_ratio,
        )
        if self._feature_aligner is not None and key == self._feature_aligner_key:
            return self._feature_aligner

        reference = cv2.imread(str(self.REFERENCE_PATH), cv2.IMREAD_COLOR)
        if reference is None:
            logger.warning(f"Feature alignment: failed to read reference image {self.REFERENCE_PATH}")
            return None
        try:
            aligner = FeatureAligner(
                self._apply_geometry(reference),
                exclude_rois=rois,
                min_inliers=min_inliers,
                min_inlier_ratio=min_inlier_ratio,
            )
        except ValueError as e:
            logger.warning(f"Feature alignment: unusable reference ({e})")
            return None
        self._feature_aligner, self._feature_aligner_key = aligner, key
        logger.info(f"Feature aligner built from reference ({len(aligner.reference_keypoints)} keypoints)")
        return aligner

    def _align_with_features(self, img: np.ndarray) -> Optional[AlignmentResult]:
        """Align via AKAZE + homography. Returns None when no usable reference exists."""
        aligner = self._get_feature_aligner()
        if aligner is None:
            return None
        outcome = aligner.align(img)
        ratio = outcome.inlier_ratio
        if not outcome.success:
            logger.error(
                f"Feature alignment failed: {outcome.error_reason} (inliers={outcome.inliers}, ratio={ratio:.2f})"
            )
        else:
            logger.debug(f"Feature alignment ok (inliers={outcome.inliers}, ratio={ratio:.2f})")
        return AlignmentResult(
            success=outcome.success,
            image=outcome.image,
            error_reason=outcome.error_reason,
            marker_confidences=[ratio],
            method="features",
            inliers=outcome.inliers,
            inlier_ratio=ratio,
            homography=outcome.homography,
        )

    def _load_marker_templates(self, marker_count: int) -> Optional[List[np.ndarray]]:
        """
        Load marker template images from disk, with caching.

        Args:
            marker_count: Number of marker templates to load

        Returns:
            List of grayscale template images, or None if any are missing/unreadable
        """
        if self._marker_templates is not None:
            return self._marker_templates

        templates = []
        for i in range(1, marker_count + 1):
            path = Path(f"/data/marker_{i}.jpg")
            if not path.exists():
                logger.warning(f"Marker template not found: {path}")
                return None
            template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if template is None:
                logger.warning(f"Failed to read marker template: {path}")
                return None
            templates.append(template)

        self._marker_templates = templates
        logger.debug(f"Loaded {len(templates)} marker templates")
        return self._marker_templates

    def invalidate_marker_cache(self):
        """Clear cached marker templates and feature aligner so they are rebuilt on next alignment."""
        self._marker_templates = None
        self._feature_aligner = None
        self._feature_aligner_key = None

    def _align_with_markers(self, img: np.ndarray, markers: List[Dict]) -> AlignmentResult:
        """
        Align image using saved marker positions via template matching.

        Uses cv2.matchTemplate to locate each marker in the current image,
        then applies a similarity transform (rotation + uniform scale + translation)
        to correct for camera drift.

        Fail-closed: never returns a misaligned image. Callers receive an
        ``AlignmentResult`` and MUST check ``.success`` before using ``.image``.

        Args:
            img: Input image (BGR)
            markers: List of marker dicts with x, y, width, height (normalized 0-1)

        Returns:
            AlignmentResult with success=True and the warped image, or
            success=False with an error_reason and image=None.
        """
        height, width = img.shape[:2]

        if len(markers) < 2:
            logger.error(f"Alignment failed: need at least 2 markers, got {len(markers)}")
            return AlignmentResult(success=False, error_reason="insufficient_markers")

        # Load templates (cached after first call)
        templates = self._load_marker_templates(len(markers))
        if templates is None:
            logger.error("Alignment failed: marker templates not loadable")
            return AlignmentResult(success=False, error_reason="templates_missing")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        ref_centers: list[list[float]] = []
        found_centers: list[list[float]] = []
        confidences: list[float] = []

        for i, marker in enumerate(markers[:2]):
            template = templates[i]
            th, tw = template.shape[:2]

            # Reference center: where the marker should be. Templates are cut at the
            # integer pixel origin (routes/roi.py), so use the same grid here.
            ref_cx = int(marker["x"] * width) + int(marker["width"] * width) / 2
            ref_cy = int(marker["y"] * height) + int(marker["height"] * height) / 2
            ref_centers.append([ref_cx, ref_cy])

            # Search region: +/-SEARCH_MARGIN around expected position
            margin_x = int(self.SEARCH_MARGIN * width)
            margin_y = int(self.SEARCH_MARGIN * height)

            sx1 = max(0, int(ref_cx - margin_x))
            sy1 = max(0, int(ref_cy - margin_y))
            sx2 = min(width, int(ref_cx + margin_x))
            sy2 = min(height, int(ref_cy + margin_y))

            # Search region must be larger than template
            if (sx2 - sx1) < tw or (sy2 - sy1) < th:
                logger.error(f"Alignment failed: search region too small for marker {i+1}")
                return AlignmentResult(
                    success=False,
                    error_reason="search_region_too_small",
                    failed_marker=i + 1,
                    marker_confidences=confidences,
                )

            search_region = gray[sy1:sy2, sx1:sx2]

            result = cv2.matchTemplate(search_region, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            confidences.append(float(max_val))

            if max_val < self.CONFIDENCE_THRESHOLD:
                logger.error(
                    f"Alignment failed: marker {i+1} match confidence {max_val:.3f} "
                    f"< threshold {self.CONFIDENCE_THRESHOLD}"
                )
                return AlignmentResult(
                    success=False,
                    error_reason="low_confidence",
                    failed_marker=i + 1,
                    marker_confidences=confidences,
                )

            # Convert match position back to full-image coordinates (center of matched region)
            found_cx = sx1 + max_loc[0] + tw / 2
            found_cy = sy1 + max_loc[1] + th / 2
            found_centers.append([found_cx, found_cy])

        ref_pts = np.float32(ref_centers).reshape(-1, 1, 2)
        found_pts = np.float32(found_centers).reshape(-1, 1, 2)

        transform, _inliers = cv2.estimateAffinePartial2D(found_pts, ref_pts)
        if transform is None:
            logger.error("Alignment failed: estimateAffinePartial2D returned None")
            return AlignmentResult(
                success=False,
                error_reason="transform_failed",
                marker_confidences=confidences,
            )

        aligned = cv2.warpAffine(img, transform, (width, height), borderMode=cv2.BORDER_REPLICATE)
        logger.debug(f"Alignment ok (confidences: {[f'{c:.3f}' for c in confidences]})")
        return AlignmentResult(success=True, image=aligned, marker_confidences=confidences)

    def _extract_roi(self, img: np.ndarray, roi: Dict, width: int, height: int) -> np.ndarray:
        """
        Extract a region of interest from the image.

        Args:
            img: Source image
            roi: ROI dict with x, y, width, height (normalized 0-1)
            width: Image width
            height: Image height

        Returns:
            Cropped ROI image
        """
        # Convert normalized coordinates to pixels
        x = int(roi["x"] * width)
        y = int(roi["y"] * height)
        w = int(roi["width"] * width)
        h = int(roi["height"] * height)

        # Clamp to image bounds
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        w = min(w, width - x)
        h = min(h, height - y)

        # Extract ROI
        roi_img = img[y : y + h, x : x + w]

        return roi_img
