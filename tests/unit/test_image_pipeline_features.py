"""
Unit tests for ImagePipeline's feature-alignment integration (alignment.method).

Uses REAL cv2; see test_feature_alignment.py for the sys.modules swap rationale.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

_saved = {name: sys.modules.get(name) for name in ("cv2", "watermeter.image_pipeline", "watermeter.feature_alignment")}
if isinstance(_saved["cv2"], MagicMock):
    del sys.modules["cv2"]

cv2 = pytest.importorskip("cv2")
if not hasattr(cv2, "AKAZE_create"):
    pytest.skip("real cv2 not available", allow_module_level=True)

sys.modules["cv2"] = cv2
sys.modules.pop("watermeter.feature_alignment", None)
sys.modules.pop("watermeter.image_pipeline", None)
import watermeter.image_pipeline as ip  # noqa: E402

for _name, _mod in _saved.items():
    if _mod is not None:
        sys.modules[_name] = _mod
    else:
        sys.modules.pop(_name, None)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "meter_snapshot.jpg"

DIGIT_ROIS = [{"x": 0.29 + i * 0.07, "y": 0.28, "width": 0.07, "height": 0.13} for i in range(5)]
ANALOG_ROIS = [
    {"x": 0.62, "y": 0.48, "width": 0.14, "height": 0.17},
    {"x": 0.56, "y": 0.66, "width": 0.14, "height": 0.17},
    {"x": 0.41, "y": 0.73, "width": 0.14, "height": 0.17},
    {"x": 0.23, "y": 0.66, "width": 0.14, "height": 0.17},
]


def make_config(method="features", markers=None, rotation=0.0, fisheye=0.0):
    return {
        "detection": {
            "digits": {"count": len(DIGIT_ROIS), "rois": DIGIT_ROIS},
            "analogs": {"count": len(ANALOG_ROIS), "rois": ANALOG_ROIS},
            "markers": markers or [],
            "rotation": rotation,
            "fisheye_correction": fisheye,
        },
        "alignment": {"method": method, "min_inliers": 30, "min_inlier_ratio": 0.3},
    }


def encode(img):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return buf.tobytes()


def shifted(img, dx=14, dy=-9, angle=1.5):
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    M[0, 2] += dx
    M[1, 2] += dy
    return cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)


@pytest.fixture
def reference_img():
    img = cv2.imread(str(FIXTURE))
    assert img is not None
    return img


@pytest.fixture
def pipeline_factory(tmp_path, reference_img):
    ref_path = tmp_path / "reference_raw.jpg"
    cv2.imwrite(str(ref_path), reference_img, [cv2.IMWRITE_JPEG_QUALITY, 95])

    def _make(config):
        p = ip.ImagePipeline(config)
        p.REFERENCE_PATH = ref_path
        return p

    return _make


def decode(b):
    return cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)


class TestFeatureMethod:
    def test_aligns_shifted_frame_and_crops_same_content(self, pipeline_factory, reference_img):
        pipeline = pipeline_factory(make_config())
        images, alignment = pipeline.process_whole_image(encode(shifted(reference_img)))

        assert alignment.success is True
        assert alignment.method == "features"
        assert alignment.inliers >= 30
        assert alignment.marker_confidences == [pytest.approx(alignment.inlier_ratio)]
        assert images is not None and set(images) >= {"digit_1", "analog_1"}

        ref_images, _ = pipeline_factory(make_config(method="template")).process_whole_image(encode(reference_img))
        diff = np.mean(np.abs(decode(images["digit_3"][0]).astype(int) - decode(ref_images["digit_3"][0]).astype(int)))
        assert diff < 12, f"aligned crop differs from reference crop (mean abs diff {diff:.1f})"

    def test_reference_gets_same_rotation_and_fisheye_as_live_frame(self, pipeline_factory, reference_img):
        # Live frame is the raw reference itself; both must go through rotation + fisheye
        pipeline = pipeline_factory(make_config(rotation=18.4, fisheye=-0.16))
        images, alignment = pipeline.process_whole_image(encode(reference_img))
        assert alignment.success is True, alignment.error_reason
        assert np.allclose(alignment.homography, np.eye(3), atol=2e-2)

    def test_failure_without_markers_is_fail_closed(self, pipeline_factory, reference_img):
        pipeline = pipeline_factory(make_config())
        images, alignment = pipeline.process_whole_image(encode(np.full_like(reference_img, 128)))
        assert images is None
        assert alignment.success is False
        assert alignment.method == "features"
        assert alignment.error_reason == "insufficient_matches"

    def test_failure_with_markers_falls_back_to_template(self, pipeline_factory, reference_img):
        markers = [{"x": 0.1, "y": 0.1, "width": 0.05, "height": 0.05}] * 2
        pipeline = pipeline_factory(make_config(markers=markers))
        blank = np.full_like(reference_img, 128)
        template_ok = ip.AlignmentResult(success=True, image=blank, marker_confidences=[0.9, 0.9])
        with patch.object(pipeline, "_align_with_markers", return_value=template_ok) as tmpl:
            images, alignment = pipeline.process_whole_image(encode(blank))
        tmpl.assert_called_once()
        assert alignment.success is True
        assert alignment.method == "template"
        assert images is not None

    def test_missing_reference_without_markers_passes_through(self, pipeline_factory, reference_img, tmp_path):
        pipeline = pipeline_factory(make_config())
        pipeline.REFERENCE_PATH = tmp_path / "does_not_exist.jpg"
        images, alignment = pipeline.process_whole_image(encode(reference_img))
        assert images is not None
        assert alignment.success is True
        assert alignment.method == "none"


class TestAlignerCache:
    def test_aligner_reused_between_frames(self, pipeline_factory, reference_img):
        pipeline = pipeline_factory(make_config())
        pipeline.process_whole_image(encode(reference_img))
        first = pipeline._feature_aligner
        pipeline.process_whole_image(encode(shifted(reference_img)))
        assert first is not None and pipeline._feature_aligner is first

    def test_roi_change_rebuilds_aligner(self, pipeline_factory, reference_img):
        pipeline = pipeline_factory(make_config())
        pipeline.process_whole_image(encode(reference_img))
        first = pipeline._feature_aligner
        new_config = make_config()
        new_config["detection"]["digits"]["rois"] = DIGIT_ROIS[:4]
        pipeline.config = new_config
        pipeline.process_whole_image(encode(reference_img))
        assert pipeline._feature_aligner is not first

    def test_invalidate_clears_aligner(self, pipeline_factory, reference_img):
        pipeline = pipeline_factory(make_config())
        pipeline.process_whole_image(encode(reference_img))
        pipeline.invalidate_marker_cache()
        assert pipeline._feature_aligner is None


class TestTemplateDefault:
    def test_template_is_default_method(self, pipeline_factory, reference_img):
        config = make_config()
        del config["alignment"]["method"]
        images, alignment = pipeline_factory(config).process_whole_image(encode(reference_img))
        # No markers configured -> trivially aligned, feature path not taken
        assert alignment.method == "none"
        assert images is not None
