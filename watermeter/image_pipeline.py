"""
Image Pipeline — fetching, rotation, marker alignment, and ROI extraction.

Extracted from WatermeterService to reduce god-object complexity.
All methods are self-contained; the only shared state is _marker_templates.
"""

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import httpx
import numpy as np

logger = logging.getLogger(__name__)


class ImagePipeline:
    """Handles image fetching, rotation, marker alignment, and ROI extraction."""

    # Alignment constants (internal tuning, not user-facing)
    SEARCH_MARGIN = 0.15  # +/-15% of image dimensions for search window
    CONFIDENCE_THRESHOLD = 0.5  # Minimum template match quality

    def __init__(self, config: dict):
        self.config = config
        # Cached marker templates for alignment (loaded lazily)
        self._marker_templates: Optional[List[np.ndarray]] = None

    async def fetch_images(self) -> Dict[str, Tuple[bytes, str]]:
        """
        Fetch all images from AI-on-the-edge device.

        Returns:
            Dict mapping ID to (image_bytes, image_class)
        """
        images = {}
        aiote_config = self.config["aiote"]
        base_url = f"http://{aiote_config['host']}{aiote_config['image_path']}"

        # Collect all IDs with their class
        all_ids = []
        for id_name in self.config["images"]["digits"]:
            all_ids.append((id_name, "digits"))
        for id_name in self.config["images"]["arrows"]:
            all_ids.append((id_name, "arrows"))

        logger.info(f"Fetching {len(all_ids)} images from AI-on-the-edge")

        async with httpx.AsyncClient(timeout=aiote_config["timeout"]) as client:
            for idx, (image_id, image_class) in enumerate(all_ids):
                # Rate limiting - delay between fetches
                if idx > 0:
                    await asyncio.sleep(aiote_config["fetch_delay"])

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
        Fetch the whole source image from AI-on-the-edge device.

        Returns:
            Image bytes or None on failure
        """
        aiote_config = self.config["aiote"]
        src_url = self.config["images"]["src"]

        logger.info(f"Fetching whole image from {src_url}")

        async with httpx.AsyncClient(timeout=aiote_config["timeout"]) as client:
            try:
                response = await client.get(src_url)
                response.raise_for_status()
                logger.info(f"Successfully fetched whole image ({len(response.content)} bytes)")
                return response.content
            except httpx.HTTPError as e:
                logger.error(f"Failed to fetch whole image: {e}")
                return None

    def process_whole_image(self, image_bytes: bytes) -> Dict[str, Tuple[bytes, str]]:
        """
        Process whole image: apply rotation, marker alignment, and extract ROIs.

        Args:
            image_bytes: Raw image bytes

        Returns:
            Dict mapping ID to (image_bytes, image_class) - same format as fetch_images
        """
        # Decode image
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        height, width = img.shape[:2]

        detection = self.config.get("detection", {})

        # 1. Apply fisheye correction if configured
        fisheye_k1 = detection.get("fisheye_correction", 0)
        if fisheye_k1 != 0:
            h, w = img.shape[:2]
            fx = fy = float(w)
            cx, cy = w / 2.0, h / 2.0
            camera_matrix = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)
            dist_coeffs = np.array([fisheye_k1, 0, 0, 0, 0], dtype=np.float64)
            img = cv2.undistort(img, camera_matrix, dist_coeffs)
            logger.debug(f"Applied fisheye correction: k1={fisheye_k1}")

        # 2. Apply rotation if configured
        rotation = detection.get("rotation", 0)
        if rotation != 0:
            center = (width / 2, height / 2)
            matrix = cv2.getRotationMatrix2D(center, rotation, 1.0)
            img = cv2.warpAffine(img, matrix, (width, height))
            logger.debug(f"Applied rotation: {rotation}°")

        # 3. Marker-based alignment if markers are configured
        markers = detection.get("markers", [])
        if len(markers) >= 2:
            img = self._align_with_markers(img, markers)
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
        return images

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
        """Clear cached marker templates so they are reloaded on next alignment."""
        self._marker_templates = None

    def _align_with_markers(self, img: np.ndarray, markers: List[Dict]) -> np.ndarray:
        """
        Align image using saved marker positions via template matching.

        Uses cv2.matchTemplate to locate each marker in the current image,
        then applies a similarity transform (rotation + uniform scale + translation)
        to correct for camera drift.

        Fail-open: returns original image unchanged on any failure.

        Args:
            img: Input image (BGR)
            markers: List of marker dicts with x, y, width, height (normalized 0-1)

        Returns:
            Aligned image, or original if alignment fails
        """
        height, width = img.shape[:2]

        if len(markers) < 2:
            logger.warning("Need at least 2 markers for alignment")
            return img

        # Load templates (cached after first call)
        templates = self._load_marker_templates(len(markers))
        if templates is None:
            return img

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        ref_centers = []
        found_centers = []

        for i, marker in enumerate(markers[:2]):
            template = templates[i]
            th, tw = template.shape[:2]

            # Reference center: where the marker should be
            ref_cx = (marker["x"] + marker["width"] / 2) * width
            ref_cy = (marker["y"] + marker["height"] / 2) * height
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
                logger.warning(f"Search region too small for marker {i+1}")
                return img

            search_region = gray[sy1:sy2, sx1:sx2]

            result = cv2.matchTemplate(search_region, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val < self.CONFIDENCE_THRESHOLD:
                logger.warning(f"Marker {i+1} match confidence too low: {max_val:.3f} < {self.CONFIDENCE_THRESHOLD}")
                return img

            # Convert match position back to full-image coordinates (center of matched region)
            found_cx = sx1 + max_loc[0] + tw / 2
            found_cy = sy1 + max_loc[1] + th / 2
            found_centers.append([found_cx, found_cy])

        ref_pts = np.float32(ref_centers).reshape(-1, 1, 2)
        found_pts = np.float32(found_centers).reshape(-1, 1, 2)

        transform, inliers = cv2.estimateAffinePartial2D(found_pts, ref_pts)
        if transform is None:
            logger.warning("Failed to estimate alignment transform")
            return img

        aligned = cv2.warpAffine(img, transform, (width, height), borderMode=cv2.BORDER_REPLICATE)
        logger.debug(
            f"Applied marker alignment (confidence: "
            f"{', '.join(f'{c:.3f}' for c in [ref_centers[0][0], ref_centers[1][0]])})"
        )
        return aligned

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
