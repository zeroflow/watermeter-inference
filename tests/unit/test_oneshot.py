"""
Failing tests for watermeter.oneshot — TDD red phase.

All tests import `run_one_shot` from `watermeter.oneshot`, which does not exist
yet. The entire module will fail with ModuleNotFoundError / ImportError until
Task 3 creates the implementation.

Real signatures discovered from source code:
  - ImagePipeline.__init__(config: dict)
  - ImagePipeline.fetch_whole_image() -> Optional[bytes]   (async)
  - ImagePipeline.process_whole_image(image_bytes: bytes) -> Dict[str, Tuple[bytes, str]]  (sync)
  - get_inference_service() -> InferenceService   (lazy singleton)
  - InferenceService.initialize(config: dict)     (sync, raises on bad config)
  - InferenceService.models_loaded -> bool        (property)
  - InferenceService.predict(model_type: str, image_path: str) -> dict  (sync)
    returns {"class": str, "confidence": float}
  - run_inference(images) takes Dict[str, Tuple[bytes, str]], returns Dict[str, Dict]
    each prediction dict: {"id", "class", "confidence", "model", "image_bytes", ...}
  - calculate_total(predictions: Dict[str, Dict]) -> Tuple[float, Dict]
    returns (total_value, {"digits": list, "arrows": list})

The oneshot module must expose:
  run_one_shot(config_path: str) -> int
  (async coroutine; returns 0 on success, 1 on any failure)
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# NOTE: This import is expected to FAIL until watermeter/oneshot.py exists.
# ---------------------------------------------------------------------------
from watermeter.oneshot import run_one_shot  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_config() -> dict:
    """Return a minimal valid config dict that the pipeline expects."""
    return {
        "images": {
            "process_separate": False,
            "src": "http://192.168.1.100/img/whole.jpg",
            "digits": ["digit_1", "digit_2"],
            "arrows": ["analog_1"],
        },
        "detection": {
            "rotation": 0,
            "digits": {"count": 2, "rois": [{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.1}]},
            "analogs": {"count": 1, "rois": [{"x": 0.5, "y": 0.5, "width": 0.1, "height": 0.1}]},
            "markers": [],
        },
        "inference": {
            "device": "CPU",
            "confidence_threshold": 0.6,
            "digits_model": "/models/digits.xml",
            "digits_classes": ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "NAN"],
            "digits_resolution": 28,
            "arrows_model": "/models/arrows.xml",
            "arrows_classes": [f"{i/10:.1f}" for i in range(100)],
            "arrows_resolution": 100,
        },
        "correction": {"enabled": False},
    }


def _write_config_yaml(tmp_path: str, config: dict) -> str:
    """Write config dict to a temporary YAML file and return its path."""
    config_path = os.path.join(tmp_path, "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    return config_path


def _make_mock_inference_service(models_loaded: bool = True) -> MagicMock:
    """Build a mock InferenceService with the correct attribute and method signatures."""
    svc = MagicMock()
    # models_loaded is a property — set it on the mock directly
    type(svc).models_loaded = property(lambda self: models_loaded)
    svc.initialize = MagicMock()
    # predict(model_type: str, image_path: str) -> {"class": str, "confidence": float}
    svc.predict = MagicMock(return_value={"class": "5", "confidence": 0.95})
    # oneshot calls predict_from_bytes; an unconfigured MagicMock result makes every ROI an inference ERROR
    svc.predict_from_bytes = MagicMock(return_value={"class": "5", "confidence": 0.95})
    # predict_detailed returns list of {"class": str, "confidence": float}
    svc.predict_detailed = MagicMock(return_value=[{"class": "5", "confidence": 0.95}])
    return svc


def _make_mock_image_pipeline(
    fetch_bytes: bytes = b"FAKEJPEG",
    fetch_fails: bool = False,
    rois: dict = None,
    alignment_success: bool = True,
) -> MagicMock:
    """Build a mock ImagePipeline with real method signatures.

    process_whole_image returns ``(rois, AlignmentResult)`` per the fail-closed
    contract. ``alignment_success=False`` simulates a failed alignment
    (rois will be None, AlignmentResult.success=False).
    """
    pipeline = MagicMock()

    if fetch_fails:
        pipeline.fetch_whole_image = AsyncMock(return_value=None)
    else:
        pipeline.fetch_whole_image = AsyncMock(return_value=fetch_bytes)

    if rois is None:
        # Default: ROIs for every configured position, matching real return format Dict[str, Tuple[bytes, str]]
        rois = {
            "digit_1": (b"DIGIT1JPG", "digits"),
            "digit_2": (b"DIGIT2JPG", "digits"),
            "analog_1": (b"ANALOG1JPG", "arrows"),
        }

    # Build AlignmentResult mock matching watermeter.image_pipeline.AlignmentResult shape.
    alignment = MagicMock()
    alignment.success = alignment_success
    alignment.error_reason = None if alignment_success else "low_confidence"
    alignment.failed_marker = None
    alignment.marker_confidences = []

    if alignment_success:
        pipeline.process_whole_image = MagicMock(return_value=(rois, alignment))
    else:
        pipeline.process_whole_image = MagicMock(return_value=(None, alignment))
    return pipeline


# ---------------------------------------------------------------------------
# Test: Successful run returns 0
# ---------------------------------------------------------------------------


class TestOneShotSuccess:
    """Happy path — all steps succeed, expect exit code 0."""

    @pytest.mark.asyncio
    async def test_returns_zero_on_success(self, tmp_path):
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        mock_svc = _make_mock_inference_service(models_loaded=True)
        mock_pipeline = _make_mock_image_pipeline()

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            result = await run_one_shot(config_path)

        assert result == 0, f"Expected 0 on success, got {result}"

    @pytest.mark.asyncio
    async def test_returns_one_when_total_unresolvable(self, tmp_path):
        """A NAN digit without previous value makes the reading unresolvable -> exit 1."""
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        mock_svc = _make_mock_inference_service(models_loaded=True)
        # Digits read NAN; arrows read a valid value so only the digit is unresolved (a NaN arrow would also be unresolved).
        mock_svc.predict_from_bytes = MagicMock(
            side_effect=lambda model_type, _bytes, **_kw: (
                {"class": "NAN", "confidence": 0.9} if model_type == "digits" else {"class": "5.0", "confidence": 0.9}
            )
        )
        mock_pipeline = _make_mock_image_pipeline()

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            result = await run_one_shot(config_path)

        assert result == 1, f"Expected 1 when total is unresolvable, got {result}"

    @pytest.mark.asyncio
    async def test_calls_fetch_whole_image(self, tmp_path):
        """Verifies the pipeline fetches the whole image (not individual ROIs)."""
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        mock_svc = _make_mock_inference_service(models_loaded=True)
        mock_pipeline = _make_mock_image_pipeline()

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            await run_one_shot(config_path)

        mock_pipeline.fetch_whole_image.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calls_process_whole_image_with_fetched_bytes(self, tmp_path):
        """Verifies the bytes from fetch_whole_image are passed to process_whole_image."""
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        fake_bytes = b"REAL_IMAGE_BYTES"
        mock_svc = _make_mock_inference_service(models_loaded=True)
        mock_pipeline = _make_mock_image_pipeline(fetch_bytes=fake_bytes)

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            await run_one_shot(config_path)

        mock_pipeline.process_whole_image.assert_called_once_with(fake_bytes)

    @pytest.mark.asyncio
    async def test_initializes_inference_service_with_config(self, tmp_path):
        """InferenceService.initialize must be called with the loaded config dict."""
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        mock_svc = _make_mock_inference_service(models_loaded=True)
        mock_pipeline = _make_mock_image_pipeline()

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            await run_one_shot(config_path)

        mock_svc.initialize.assert_called_once()
        call_args = mock_svc.initialize.call_args[0]
        assert len(call_args) == 1, "initialize should be called with exactly one positional arg (config dict)"
        assert isinstance(call_args[0], dict)
        assert "inference" in call_args[0]


# ---------------------------------------------------------------------------
# Test: Config load failure returns 1
# ---------------------------------------------------------------------------


class TestOneShotConfigFailure:
    """Config cannot be loaded — expect exit code 1."""

    @pytest.mark.asyncio
    async def test_missing_config_file_returns_one(self, tmp_path):
        nonexistent_path = str(tmp_path / "does_not_exist.yaml")

        result = await run_one_shot(nonexistent_path)

        assert result == 1, f"Expected 1 for missing config, got {result}"

    @pytest.mark.asyncio
    async def test_malformed_yaml_returns_one(self, tmp_path):
        """YAML that is syntactically invalid should cause failure."""
        config_path = str(tmp_path / "bad_config.yaml")
        with open(config_path, "w") as f:
            f.write("this: is: bad: yaml: [\n")

        result = await run_one_shot(config_path)

        assert result == 1, f"Expected 1 for bad YAML, got {result}"

    @pytest.mark.asyncio
    async def test_inference_service_not_called_on_config_failure(self, tmp_path):
        """If config load fails, inference service must not be touched."""
        nonexistent_path = str(tmp_path / "does_not_exist.yaml")
        mock_svc = _make_mock_inference_service()

        with patch("watermeter.oneshot.get_inference_service", return_value=mock_svc):
            await run_one_shot(nonexistent_path)

        mock_svc.initialize.assert_not_called()


# ---------------------------------------------------------------------------
# Test: Model load failure returns 1
# ---------------------------------------------------------------------------


class TestOneShotModelLoadFailure:
    """Models not loaded after initialize() — expect exit code 1."""

    @pytest.mark.asyncio
    async def test_models_not_loaded_returns_one(self, tmp_path):
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        # Simulate models failing to load: models_loaded = False
        mock_svc = _make_mock_inference_service(models_loaded=False)
        mock_pipeline = _make_mock_image_pipeline()

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            result = await run_one_shot(config_path)

        assert result == 1, f"Expected 1 when models_loaded=False, got {result}"

    @pytest.mark.asyncio
    async def test_image_fetch_not_attempted_when_models_not_loaded(self, tmp_path):
        """If models fail to load, we must not proceed to fetch images."""
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        mock_svc = _make_mock_inference_service(models_loaded=False)
        mock_pipeline = _make_mock_image_pipeline()

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            await run_one_shot(config_path)

        mock_pipeline.fetch_whole_image.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_initialize_exception_returns_one(self, tmp_path):
        """If initialize() raises an exception, return 1."""
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        mock_svc = MagicMock()
        mock_svc.initialize = MagicMock(side_effect=RuntimeError("Model load error"))
        type(mock_svc).models_loaded = property(lambda self: False)

        mock_pipeline = _make_mock_image_pipeline()

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            result = await run_one_shot(config_path)

        assert result == 1


# ---------------------------------------------------------------------------
# Test: Image fetch failure returns 1
# ---------------------------------------------------------------------------


class TestOneShotImageFetchFailure:
    """fetch_whole_image() returns None (HTTP error) — expect exit code 1."""

    @pytest.mark.asyncio
    async def test_image_fetch_returns_none_gives_one(self, tmp_path):
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        mock_svc = _make_mock_inference_service(models_loaded=True)
        mock_pipeline = _make_mock_image_pipeline(fetch_fails=True)

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            result = await run_one_shot(config_path)

        assert result == 1, f"Expected 1 when image fetch fails, got {result}"

    @pytest.mark.asyncio
    async def test_process_not_called_when_fetch_fails(self, tmp_path):
        """If fetch returns None, process_whole_image should not be called."""
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        mock_svc = _make_mock_inference_service(models_loaded=True)
        mock_pipeline = _make_mock_image_pipeline(fetch_fails=True)

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            await run_one_shot(config_path)

        mock_pipeline.process_whole_image.assert_not_called()


# ---------------------------------------------------------------------------
# Test: No ROIs extracted returns 1
# ---------------------------------------------------------------------------


class TestOneShotNoRois:
    """process_whole_image() returns empty dict — expect exit code 1."""

    @pytest.mark.asyncio
    async def test_empty_roi_dict_returns_one(self, tmp_path):
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        mock_svc = _make_mock_inference_service(models_loaded=True)
        # Return empty dict from process_whole_image
        mock_pipeline = _make_mock_image_pipeline(rois={})

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            result = await run_one_shot(config_path)

        assert result == 1, f"Expected 1 when ROI dict is empty, got {result}"

    @pytest.mark.asyncio
    async def test_inference_not_called_when_no_rois(self, tmp_path):
        """If no ROIs extracted, inference service predict must not be called."""
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        mock_svc = _make_mock_inference_service(models_loaded=True)
        mock_pipeline = _make_mock_image_pipeline(rois={})

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            await run_one_shot(config_path)

        mock_svc.predict.assert_not_called()
        mock_svc.predict_detailed.assert_not_called()


# ---------------------------------------------------------------------------
# Test: ImagePipeline is constructed with config dict
# ---------------------------------------------------------------------------


class TestOneShotPipelineConstruction:
    """Verify that ImagePipeline is instantiated with the loaded config."""

    @pytest.mark.asyncio
    async def test_image_pipeline_created_with_config(self, tmp_path):
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        mock_svc = _make_mock_inference_service(models_loaded=True)
        mock_pipeline = _make_mock_image_pipeline()
        mock_pipeline_class = MagicMock(return_value=mock_pipeline)

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", mock_pipeline_class),
        ):
            await run_one_shot(config_path)

        mock_pipeline_class.assert_called_once()
        call_args = mock_pipeline_class.call_args[0]
        assert len(call_args) == 1, "ImagePipeline should receive exactly one positional arg (config dict)"
        assert isinstance(call_args[0], dict)
        assert "images" in call_args[0]


# ---------------------------------------------------------------------------
# Test: Output / logging (basic sanity — run_one_shot prints meter reading)
# ---------------------------------------------------------------------------


class TestOneShotOutput:
    """Ensure run_one_shot produces a total meter reading on success."""

    @pytest.mark.asyncio
    async def test_success_with_multiple_rois(self, tmp_path):
        """Multiple digit and arrow ROIs should still return 0."""
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        rois = {
            "digit_1": (b"D1JPG", "digits"),
            "digit_2": (b"D2JPG", "digits"),
            "analog_1": (b"A1JPG", "arrows"),
        }
        mock_svc = _make_mock_inference_service(models_loaded=True)
        mock_pipeline = _make_mock_image_pipeline(rois=rois)

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            result = await run_one_shot(config_path)

        assert result == 0

    @pytest.mark.asyncio
    async def test_config_path_is_passed_as_string(self, tmp_path):
        """run_one_shot accepts config_path as a string (not Path object)."""
        config = _make_minimal_config()
        config_path = _write_config_yaml(str(tmp_path), config)

        # config_path from _write_config_yaml is already a str
        assert isinstance(config_path, str)

        mock_svc = _make_mock_inference_service(models_loaded=True)
        mock_pipeline = _make_mock_image_pipeline()

        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=mock_pipeline),
        ):
            result = await run_one_shot(config_path)

        assert result == 0


class TestOneShotBinWidth:
    """Final review C1: a discrete classifier's bin_width must reach calculate_total."""

    @pytest.mark.asyncio
    async def test_bin_width_passed_to_calculate_total(self, tmp_path):
        from watermeter import oneshot

        config_path = _write_config_yaml(str(tmp_path), _make_minimal_config())
        mock_svc = _make_mock_inference_service(models_loaded=True)
        mock_svc.predict_from_bytes = MagicMock(
            side_effect=lambda model_type, _bytes, **_kw: (
                {"class": "1", "confidence": 0.9}
                if model_type == "digits"
                else {"class": "3.0", "confidence": 0.9, "bin_width": 1.0}
            )
        )
        with (
            patch("watermeter.oneshot.get_inference_service", return_value=mock_svc),
            patch("watermeter.oneshot.ImagePipeline", return_value=_make_mock_image_pipeline()),
            patch("watermeter.oneshot._calculate_total", wraps=oneshot._calculate_total) as calc,
        ):
            assert await run_one_shot(config_path) == 0

        predictions = calc.call_args.args[1]
        assert predictions["analog_1"]["bin_width"] == 1.0
        assert "bin_width" not in predictions["digit_1"]
