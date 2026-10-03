"""Unit tests for BL-08: Arrow regression mode.

Tests cover:
- RegressionArrowDataset (training_core.py)
- stratified_split_regression (training_core.py)
- regression_predict (training_core.py)
- Regressor class (inference.py)
- _detect_training_mode (inference.py)
- TrainingConfig.training_mode validation (routes/training.py)
- validate_model_config continuous pattern (inference.py)

See docs/tasks/2026_02_14_BL08_ArrowRegression.md, WP5 for full spec.
"""

import importlib
import importlib.util
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import yaml

# ---------------------------------------------------------------------------
# Load real inference.py symbols (bypassing the conftest mock).
#
# inference.py has module-level side effects: reads config.yaml and
# initialises OpenVINO.  We suppress these by patching builtins.open
# to serve a minimal config and letting the mocked openvino handle
# the rest.  We load the module via importlib so it does not pollute
# sys.modules or conflict with the conftest mock.
# ---------------------------------------------------------------------------

def _load_real_inference():
    """Load the real watermeter.inference module and extract symbols.

    Returns a dict mapping symbol names to objects, or None for any
    symbol that could not be loaded.
    """
    result = {
        'Regressor': None,
        '_detect_training_mode': None,
        'validate_model_config': None,
    }

    try:
        inference_path = (
            Path(__file__).resolve().parents[2] / 'watermeter' / 'inference.py'
        )
        if not inference_path.exists():
            return result

        spec = importlib.util.spec_from_file_location(
            '_inference_isolated',
            str(inference_path),
        )
        mod = importlib.util.module_from_spec(spec)

        # Provide a minimal config so yaml.safe_load succeeds
        minimal_config = yaml.dump({
            'inference': {
                'digits_model': '/fake/model.xml',
                'digits_classes': ['0'],
                'digits_resolution': 128,
                'arrows_model': '/fake/model.xml',
                'arrows_classes': ['0.0'],
                'arrows_resolution': 128,
                'device': 'CPU',
            }
        })

        import builtins
        _real_open = builtins.open

        def _patched_open(filepath, *args, **kwargs):
            if isinstance(filepath, str) and filepath == 'config.yaml':
                return io.StringIO(minimal_config)
            return _real_open(filepath, *args, **kwargs)

        with patch('builtins.open', side_effect=_patched_open):
            try:
                spec.loader.exec_module(mod)
            except Exception:
                # Module-level InferenceService.initialize() fails (no real
                # OpenVINO), but the class/function definitions above it
                # are already parsed into `mod`.
                pass

        for name in result:
            result[name] = getattr(mod, name, None)

    except Exception:
        pass

    return result


_INFERENCE = _load_real_inference()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_dummy_image(path):
    """Create a minimal valid JPEG file."""
    from PIL import Image
    img = Image.new('RGB', (32, 32), color=(128, 128, 128))
    img.save(str(path))


@pytest.fixture
def gt_dir(tmp_path):
    """Create minimal ground truth directory with a few classes."""
    gt = tmp_path / "ground_truth"
    for val in ["0.0", "2.5", "5.0", "7.5", "9.9"]:
        class_dir = gt / val
        class_dir.mkdir(parents=True)
        for i in range(5):
            _create_dummy_image(class_dir / f"img_{i}.jpg")
    return gt


# ---------------------------------------------------------------------------
# TestRegressionArrowDataset
# ---------------------------------------------------------------------------

class TestRegressionArrowDataset:
    """Tests for RegressionArrowDataset in training_core.py."""

    @pytest.fixture(autouse=True)
    def _import_deps(self):
        """Import torch-dependent classes, skip if torch unavailable."""
        pytest.importorskip('torch')
        from watermeter.training_core import RegressionArrowDataset
        self.RegressionArrowDataset = RegressionArrowDataset

    def test_loads_all_classes(self, gt_dir):
        """Dataset loads images from all ground truth folders: 5 classes x 5 images = 25."""
        ds = self.RegressionArrowDataset(gt_dir)
        assert len(ds) == 25

    def test_target_normalization(self, gt_dir):
        """Folder '5.0' produces target 0.5."""
        ds = self.RegressionArrowDataset(gt_dir)
        for path, target in ds.samples:
            if '/5.0/' in path or '\\5.0\\' in path:
                assert abs(target - 0.5) < 1e-6
                break
        else:
            pytest.fail("No sample found from '5.0' folder")

    def test_empty_dir_raises(self, tmp_path):
        """Empty directory raises ValueError."""
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ValueError, match="No images found"):
            self.RegressionArrowDataset(empty)

    def test_skips_non_numeric_dirs(self, tmp_path):
        """Non-numeric folder names are ignored."""
        root = tmp_path / "gt"
        root.mkdir()
        (root / "invalid").mkdir()
        (root / "5.0").mkdir()
        _create_dummy_image(root / "5.0" / "img.jpg")
        ds = self.RegressionArrowDataset(root)
        assert len(ds) == 1


# ---------------------------------------------------------------------------
# TestStratifiedSplitRegression
# ---------------------------------------------------------------------------

class TestStratifiedSplitRegression:
    """Tests for stratified_split_regression in training_core.py."""

    @pytest.fixture(autouse=True)
    def _import_deps(self):
        pytest.importorskip('torch')
        from watermeter.training_core import (
            RegressionArrowDataset,
            stratified_split_regression,
        )
        self.RegressionArrowDataset = RegressionArrowDataset
        self.stratified_split_regression = stratified_split_regression

    def test_all_targets_in_both_splits(self, gt_dir):
        """Every target value appears in both train and val (5 images per class >= 2)."""
        ds = self.RegressionArrowDataset(gt_dir)
        train_idx, val_idx = self.stratified_split_regression(ds)
        train_targets = {ds.samples[i][1] for i in train_idx}
        val_targets = {ds.samples[i][1] for i in val_idx}
        all_targets = {t for _, t in ds.samples}
        for t in all_targets:
            count = sum(1 for _, tt in ds.samples if tt == t)
            if count >= 2:
                assert t in train_targets, f"Target {t} missing from train split"
                assert t in val_targets, f"Target {t} missing from val split"

    def test_no_overlap(self, gt_dir):
        """Train and val indices don't overlap."""
        ds = self.RegressionArrowDataset(gt_dir)
        train_idx, val_idx = self.stratified_split_regression(ds)
        assert len(set(train_idx) & set(val_idx)) == 0

    def test_covers_all_indices(self, gt_dir):
        """All dataset indices are assigned to either train or val."""
        ds = self.RegressionArrowDataset(gt_dir)
        train_idx, val_idx = self.stratified_split_regression(ds)
        assert sorted(train_idx + val_idx) == list(range(len(ds)))


# ---------------------------------------------------------------------------
# TestRegressionPredict
# ---------------------------------------------------------------------------

class TestRegressionPredict:
    """Tests for regression_predict() in training_core.py."""

    @pytest.fixture(autouse=True)
    def _import_deps(self):
        pytest.importorskip('torch')
        from watermeter.training_core import regression_predict
        self.regression_predict = regression_predict

    def test_sigmoid_midpoint(self):
        """Raw output 0.0 -> sigmoid 0.5 -> dial position ~5.0, low confidence."""
        pos, conf = self.regression_predict(np.array([0.0]))
        assert abs(pos - 5.0) < 0.1
        assert conf < 0.1  # Low confidence at sigmoid midpoint

    def test_high_positive(self):
        """Large positive output -> near 9.9, high confidence."""
        pos, conf = self.regression_predict(np.array([10.0]))
        assert pos > 9.5
        assert conf > 0.9

    def test_high_negative(self):
        """Large negative output -> near 0.0, high confidence."""
        pos, conf = self.regression_predict(np.array([-10.0]))
        assert pos < 0.5
        assert conf > 0.9

    def test_clamped_to_valid_range(self):
        """Output is clamped to [0.0, 9.9]."""
        pos, _ = self.regression_predict(np.array([100.0]))
        assert pos <= 9.9
        pos, _ = self.regression_predict(np.array([-100.0]))
        assert pos >= 0.0


# ---------------------------------------------------------------------------
# TestRegressor
# ---------------------------------------------------------------------------

class TestRegressor:
    """Tests for the Regressor class in inference.py.

    Uses object.__new__() to avoid OpenVINO initialization, and mocks
    the compiled model to return known outputs.
    """

    @pytest.fixture(autouse=True)
    def _check_available(self):
        if _INFERENCE['Regressor'] is None:
            pytest.skip("Regressor not available from inference.py")
        self.Regressor = _INFERENCE['Regressor']

    def _make_regressor(self, raw_output):
        """Create a Regressor instance bypassing __init__, with mocked compiled model."""
        reg = object.__new__(self.Regressor)
        reg.resolution = 128
        reg.label_config_tag = 'arrow_value'
        reg.model_path = '/fake/model.xml'

        # Mock the compiled model
        mock_compiled = MagicMock()
        mock_output_key = MagicMock()
        mock_compiled.output.return_value = mock_output_key
        # compiled([img])[compiled.output(0)][0] should return raw_output
        mock_compiled.__call__ = MagicMock(
            return_value={mock_output_key: np.array([raw_output])}
        )
        reg.compiled = mock_compiled

        # Mock preprocess to return a dummy tensor (cv2 is mocked in test env)
        reg.preprocess = MagicMock(
            return_value=np.zeros((1, 3, 128, 128), dtype=np.float32)
        )

        return reg

    def test_predict_returns_class_and_confidence(self, tmp_path):
        """predict() returns dict with 'class' (string) and 'confidence' (float)."""
        reg = self._make_regressor(np.array([2.0]))
        img_path = tmp_path / "test.jpg"
        _create_dummy_image(img_path)
        result = reg.predict(str(img_path))
        assert 'class' in result
        assert 'confidence' in result
        assert isinstance(result['class'], str)
        assert '.' in result['class']  # Should be like "8.8"

    def test_predict_detailed_returns_list(self, tmp_path):
        """predict_detailed() returns a single-element list."""
        reg = self._make_regressor(np.array([0.0]))
        img_path = tmp_path / "test.jpg"
        _create_dummy_image(img_path)
        result = reg.predict_detailed(str(img_path))
        assert isinstance(result, list)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# TestDetectTrainingMode
# ---------------------------------------------------------------------------

class TestDetectTrainingMode:
    """Tests for _detect_training_mode() in inference.py."""

    @pytest.fixture(autouse=True)
    def _check_available(self):
        if _INFERENCE['_detect_training_mode'] is None:
            pytest.skip("_detect_training_mode not available from inference.py")
        self._detect_training_mode = _INFERENCE['_detect_training_mode']

    def test_from_metadata_continuous(self, tmp_path):
        """Detects continuous from metadata.json."""
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "metadata.json").write_text('{"training_mode": "continuous"}')
        assert self._detect_training_mode(str(model_dir / "model.xml")) == "continuous"

    def test_from_metadata_discrete(self, tmp_path):
        """Detects discrete from metadata.json."""
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "metadata.json").write_text('{"training_mode": "discrete"}')
        assert self._detect_training_mode(str(model_dir / "model.xml")) == "discrete"

    def test_from_metadata_absent(self, tmp_path):
        """Missing training_mode in metadata defaults to discrete."""
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "metadata.json").write_text('{"architecture": "resnet18"}')
        assert self._detect_training_mode(str(model_dir / "model.xml")) == "discrete"

    def test_fallback_filename_continuous(self, tmp_path):
        """No metadata -> detect from filename pattern '_continuous_'."""
        model_dir = tmp_path / "model_arrows_resnet18_continuous_r128_s42"
        model_dir.mkdir()
        xml = model_dir / "model_arrows_resnet18_continuous_r128_s42.xml"
        xml.write_text("")
        assert self._detect_training_mode(str(xml)) == "continuous"

    def test_fallback_filename_discrete(self, tmp_path):
        """No metadata -> filename without '_continuous_' defaults to discrete."""
        model_dir = tmp_path / "model_arrows_resnet18_c100_r128_s42"
        model_dir.mkdir()
        xml = model_dir / "model_arrows_resnet18_c100_r128_s42.xml"
        xml.write_text("")
        assert self._detect_training_mode(str(xml)) == "discrete"


# ---------------------------------------------------------------------------
# TestTrainingConfigValidation
# ---------------------------------------------------------------------------

class TestTrainingConfigValidation:
    """Tests for TrainingConfig.training_mode field and validation."""

    def test_default_training_mode(self):
        """Default training_mode is 'discrete'."""
        from watermeter.routes.training import TrainingConfig
        config = TrainingConfig(
            model_type="arrows", architecture="resnet18",
            resolution=128, seeds=[42],
        )
        assert config.training_mode == "discrete"

    def test_continuous_training_mode(self):
        """training_mode='continuous' is accepted."""
        from watermeter.routes.training import TrainingConfig
        config = TrainingConfig(
            model_type="arrows", architecture="resnet18",
            resolution=128, seeds=[42], training_mode="continuous",
        )
        assert config.training_mode == "continuous"

    def test_invalid_training_mode_rejected(self):
        """Invalid training_mode raises ValueError."""
        from watermeter.routes.training import TrainingConfig
        with pytest.raises(ValueError):
            TrainingConfig(
                model_type="arrows", architecture="resnet18",
                resolution=128, seeds=[42], training_mode="bogus",
            )


# ---------------------------------------------------------------------------
# TestValidateModelConfigContinuous
# ---------------------------------------------------------------------------

class TestValidateModelConfigContinuous:
    """Tests for validate_model_config with continuous arrow filename pattern."""

    @pytest.fixture(autouse=True)
    def _check_available(self):
        if _INFERENCE['validate_model_config'] is None:
            pytest.skip("validate_model_config not available from inference.py")
        self.validate_model_config = _INFERENCE['validate_model_config']

    def test_continuous_pattern_valid(self):
        """Continuous filename with matching resolution passes validation."""
        # Should not raise
        self.validate_model_config(
            '/app/models/arrows/model_arrows_resnet18_continuous_r128_s42/'
            'model_arrows_resnet18_continuous_r128_s42.xml',
            'arrows', ['dummy'], 128,
        )

    def test_continuous_pattern_wrong_resolution(self):
        """Continuous filename with mismatched resolution raises ValueError."""
        with pytest.raises(ValueError, match="resolution mismatch"):
            self.validate_model_config(
                '/app/models/arrows/m/model_arrows_resnet18_continuous_r64_s42.xml',
                'arrows', ['dummy'], 128,
            )
