"""Discrete (classifier) arrow predictions carry their bin width; regressor predictions do not.

Discrete arrow training labels are floored (training_manager: floor(value / step) * step), so class
"k.0" means the needle is somewhere in [k, k + step). calculate_total needs that step to centre the
reading before the carry-aware cascade.
"""

import importlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image


def _real_cv2():
    """The real cv2 (the unit conftest installs a mock in sys.modules)."""
    mocked = sys.modules.pop("cv2", None)
    try:
        return importlib.import_module("cv2")
    finally:
        if mocked is not None:
            sys.modules["cv2"] = mocked


def _load_real_inference():
    """Load the real watermeter.inference module (conftest mocks it).

    Only OpenVINO is faked (no model compilation on the host); image decoding uses the real cv2.
    """
    path = Path(__file__).resolve().parents[2] / "watermeter" / "inference.py"
    spec = importlib.util.spec_from_file_location("_inference_bin_width", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ov = MagicMock()
    mod.cv2 = _real_cv2()
    return mod


inference = _load_real_inference()

C10 = [f"{i}.0" for i in range(10)]
C100 = [f"{i / 10:.1f}" for i in range(100)]


class _Compiled:
    """Stands in for the compiled OpenVINO model: returns fixed raw outputs."""

    def __init__(self, raw):
        self._raw = np.array([raw], dtype=np.float32)

    def output(self, index):
        return index

    def __call__(self, inputs):
        return {0: self._raw}


def _jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(128, 128, 128)).save(buf, format="JPEG")
    return buf.getvalue()


class TestArrowBinWidth:
    def test_c10_classes(self):
        assert inference.arrow_bin_width(C10) == pytest.approx(1.0)

    def test_c100_classes(self):
        assert inference.arrow_bin_width(C100) == pytest.approx(0.1)

    def test_unsorted_classes(self):
        assert inference.arrow_bin_width(["5.0", "0.0", "2.5", "7.5"]) == pytest.approx(2.5)

    def test_non_numeric_classes_have_no_bin_width(self):
        assert inference.arrow_bin_width(["a", "b"]) is None

    def test_single_class_has_no_bin_width(self):
        assert inference.arrow_bin_width(["0.0"]) is None


class TestClassifierPredictionsCarryBinWidth:
    def _classifier(self, bin_width=None):
        clf = inference.Classifier("/fake/model.xml", C10, 16, "arrow_value", device="CPU", bin_width=bin_width)
        logits = [0.0] * 10
        logits[3] = 5.0
        clf.compiled = _Compiled(logits)
        return clf

    def test_arrow_classifier_prediction_has_bin_width(self):
        result = self._classifier(bin_width=1.0).predict_from_bytes(_jpeg_bytes())
        assert result["class"] == "3.0"
        assert result["bin_width"] == pytest.approx(1.0)

    def test_arrow_classifier_top_k_entries_have_bin_width(self):
        results = self._classifier(bin_width=1.0).predict_detailed_from_bytes(_jpeg_bytes(), top_k=3)
        assert [r["bin_width"] for r in results] == [1.0, 1.0, 1.0]

    def test_classifier_without_bin_width_omits_key(self):
        result = self._classifier().predict_from_bytes(_jpeg_bytes())
        assert "bin_width" not in result

    def test_regressor_prediction_has_no_bin_width(self):
        reg = inference.Regressor("/fake/model.xml", 16, "arrow_value", device="CPU")
        reg.compiled = _Compiled([0.3])
        assert "bin_width" not in reg.predict_from_bytes(_jpeg_bytes())
        assert "bin_width" not in reg.predict_detailed_from_bytes(_jpeg_bytes())[0]


def _config(arrows_path):
    return {
        "inference": {
            "digits_model": "/nonexistent/digits.xml",
            "digits_classes": [str(i) for i in range(10)] + ["NAN"],
            "digits_resolution": 16,
            "arrows_model": str(arrows_path),
            "arrows_classes": C10,
            "arrows_resolution": 16,
            "arrows_mode": "model",
            "device": "CPU",
        }
    }


def _model_file(tmp_path, training_mode):
    model = tmp_path / "model.xml"
    model.write_text("<xml/>")
    (tmp_path / "metadata.json").write_text(json.dumps({"training_mode": training_mode}))
    return model


class TestInferenceServiceWiring:
    @pytest.mark.parametrize("method", ["initialize", "reload_models"])
    def test_discrete_arrows_classifier_gets_bin_width(self, tmp_path, method):
        svc = inference.InferenceService()
        getattr(svc, method)(_config(_model_file(tmp_path, "discrete")))
        assert isinstance(svc.arrows_classifier, inference.Classifier)
        assert svc.arrows_classifier.bin_width == pytest.approx(1.0)

    @pytest.mark.parametrize("method", ["initialize", "reload_models"])
    def test_continuous_arrows_model_is_regressor(self, tmp_path, method):
        svc = inference.InferenceService()
        getattr(svc, method)(_config(_model_file(tmp_path, "continuous")))
        assert isinstance(svc.arrows_classifier, inference.Regressor)
