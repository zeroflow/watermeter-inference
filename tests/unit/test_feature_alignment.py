"""
Unit tests for feature-based (AKAZE + homography) image alignment.

Uses REAL cv2 on the meter snapshot fixture. The unit conftest mocks cv2 in
sys.modules, so we temporarily swap in the real module while importing
watermeter.feature_alignment, then restore the mock for other test files.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

_saved_cv2_mock = sys.modules.get("cv2")
if isinstance(_saved_cv2_mock, MagicMock):
    del sys.modules["cv2"]

cv2 = pytest.importorskip("cv2")
if not hasattr(cv2, "AKAZE_create"):
    pytest.skip("real cv2 not available", allow_module_level=True)

sys.modules["cv2"] = cv2
sys.modules.pop("watermeter.feature_alignment", None)
import watermeter.feature_alignment as fa  # noqa: E402

if _saved_cv2_mock is not None:
    sys.modules["cv2"] = _saved_cv2_mock

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "meter_snapshot.jpg"

# Digit window and the four dials of the fixture (normalised), used as exclusion ROIs
FIXTURE_ROIS = [
    {"x": 0.29, "y": 0.28, "width": 0.36, "height": 0.13},
    {"x": 0.62, "y": 0.48, "width": 0.14, "height": 0.17},
    {"x": 0.23, "y": 0.66, "width": 0.14, "height": 0.17},
    {"x": 0.41, "y": 0.73, "width": 0.14, "height": 0.17},
    {"x": 0.56, "y": 0.66, "width": 0.14, "height": 0.17},
]


@pytest.fixture(scope="module")
def reference():
    img = cv2.imread(str(FIXTURE))
    assert img is not None
    return img


@pytest.fixture(scope="module")
def aligner(reference):
    return fa.FeatureAligner(reference, exclude_rois=FIXTURE_ROIS)


def _known_warp(img, angle_deg=0.0, scale=1.0, shift=(0.0, 0.0), persp=(0.0, 0.0)):
    """Warp ``img`` with a known homography; return (warped, H_ref_to_warped)."""
    h, w = img.shape[:2]
    rot = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, scale)
    H = np.vstack([rot, [0, 0, 1]]).astype(np.float64)
    H[0, 2] += shift[0]
    H[1, 2] += shift[1]
    H[2, 0] = persp[0] / w
    H[2, 1] = persp[1] / h
    warped = cv2.warpPerspective(img, H, (w, h), borderMode=cv2.BORDER_REPLICATE)
    return warped, H


def _roi_corner_error(H_est, H_true, shape):
    """Mean px error at ROI corners after mapping ref -> warped (H_true) -> ref (H_est)."""
    h, w = shape[:2]
    pts = []
    for r in FIXTURE_ROIS:
        x1, y1 = r["x"] * w, r["y"] * h
        x2, y2 = x1 + r["width"] * w, y1 + r["height"] * h
        pts += [[x1, y1], [x2, y1], [x1, y2], [x2, y2]]
    pts = np.float64(pts).reshape(-1, 1, 2)
    roundtrip = cv2.perspectiveTransform(cv2.perspectiveTransform(pts, H_true), H_est)
    return float(np.mean(np.linalg.norm(roundtrip - pts, axis=2)))


class TestSuccessfulAlignment:
    def test_identity_on_reference_itself(self, aligner, reference):
        result = aligner.align(reference)
        assert result.success is True
        assert result.error_reason is None
        assert np.allclose(result.homography, np.eye(3), atol=1e-2)
        assert result.image is not None and result.image.shape == reference.shape

    @pytest.mark.parametrize(
        "warp",
        [
            {"shift": (25, -18)},
            {"angle_deg": 3.0, "scale": 1.03, "shift": (10, 5)},
            {"angle_deg": -2.5, "scale": 0.97, "persp": (0.012, -0.01)},
        ],
    )
    def test_recovers_known_transform(self, aligner, reference, warp):
        warped, H_true = _known_warp(reference, **warp)
        result = aligner.align(warped)
        assert result.success is True, result.error_reason
        assert _roi_corner_error(result.homography, H_true, reference.shape) < 1.0

    def test_robust_to_brightness_change_and_glare(self, aligner, reference):
        warped, H_true = _known_warp(reference, angle_deg=2.0, shift=(12, 8))
        dim = cv2.convertScaleAbs(warped, alpha=0.6, beta=-20)
        cv2.circle(dim, (420, 120), 40, (255, 255, 255), -1)  # specular glare blob
        result = aligner.align(dim)
        assert result.success is True, result.error_reason
        assert _roi_corner_error(result.homography, H_true, reference.shape) < 1.5

    def test_reports_inlier_statistics(self, aligner, reference):
        warped, _ = _known_warp(reference, shift=(5, 5))
        result = aligner.align(warped)
        assert result.inliers >= aligner.min_inliers
        assert 0.0 < result.inlier_ratio <= 1.0


class TestFailClosed:
    def test_blank_image_fails_without_image(self, aligner, reference):
        blank = np.full_like(reference, 128)
        result = aligner.align(blank)
        assert result.success is False
        assert result.image is None
        assert result.error_reason == "insufficient_matches"

    def test_unrelated_image_fails(self, aligner, reference):
        noise = np.random.RandomState(7).randint(0, 256, reference.shape, dtype=np.uint8)
        result = aligner.align(noise)
        assert result.success is False
        assert result.image is None
        assert result.error_reason in ("insufficient_matches", "low_inliers")

    def test_implausible_scale_rejected(self, aligner, reference):
        warped, _ = _known_warp(reference, scale=1.4)
        result = aligner.align(warped)
        assert result.success is False
        assert result.image is None
        assert result.error_reason == "implausible_transform"

    def test_implausible_rotation_rejected(self, aligner, reference):
        warped, _ = _known_warp(reference, angle_deg=25.0)
        result = aligner.align(warped)
        assert result.success is False
        assert result.error_reason == "implausible_transform"

    def test_featureless_reference_rejected_at_construction(self):
        with pytest.raises(ValueError):
            fa.FeatureAligner(np.full((480, 640, 3), 90, dtype=np.uint8))


class TestExclusionMask:
    def test_no_reference_keypoints_inside_excluded_rois(self, aligner, reference):
        h, w = reference.shape[:2]
        for kp in aligner.reference_keypoints:
            x, y = kp.pt
            for r in FIXTURE_ROIS:
                inside = r["x"] * w <= x <= (r["x"] + r["width"]) * w and r["y"] * h <= y <= (r["y"] + r["height"]) * h
                assert not inside, f"keypoint {kp.pt} inside excluded ROI {r}"

    def test_changed_content_inside_rois_does_not_break_alignment(self, aligner, reference):
        warped, H_true = _known_warp(reference, angle_deg=1.5, shift=(8, -6))
        h, w = warped.shape[:2]
        altered = warped.copy()
        for r in FIXTURE_ROIS:
            x1, y1 = int(r["x"] * w), int(r["y"] * h)
            x2, y2 = int((r["x"] + r["width"]) * w), int((r["y"] + r["height"]) * h)
            altered[y1:y2, x1:x2] = np.random.RandomState(3).randint(0, 256, (y2 - y1, x2 - x1, 3), dtype=np.uint8)
        result = aligner.align(altered)
        assert result.success is True, result.error_reason
        assert _roi_corner_error(result.homography, H_true, reference.shape) < 1.0
