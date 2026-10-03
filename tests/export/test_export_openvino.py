"""Tests for export_to_openvino() -- verifies models with ONNX sequence ops export correctly."""

import pytest


@pytest.mark.slow
class TestExportToOpenvino:
    """Test OpenVINO export for models that produce problematic ONNX ops."""

    def test_convnextv2_pico_export(self, tmp_path):
        """ConvNeXtV2 pico should export to valid OpenVINO IR files.

        This model uses grn (Global Response Normalization) layers that produce
        SequenceEmpty/SequenceInsert/ConcatFromSequence ops in Dynamo ONNX export,
        which OpenVINO cannot consume. The export function must handle this.
        """
        import timm

        from watermeter.training_core import export_to_openvino

        resolution = 32  # Small for fast test
        model = timm.create_model("convnextv2_pico", pretrained=False, num_classes=11)

        onnx_path, ov_path = export_to_openvino(model, resolution, tmp_path, "test_model")

        # ONNX file should exist
        assert onnx_path.exists(), f"ONNX file not found: {onnx_path}"
        assert onnx_path.stat().st_size > 0, "ONNX file is empty"

        # OpenVINO XML+BIN should exist
        assert ov_path.exists(), f"OpenVINO XML not found: {ov_path}"
        bin_path = ov_path.with_suffix(".bin")
        assert bin_path.exists(), f"OpenVINO BIN not found: {bin_path}"

        # Verify the OpenVINO model is loadable and can run inference
        import openvino as ov
        core = ov.Core()
        ov_model = core.read_model(str(ov_path))
        compiled = core.compile_model(ov_model, "CPU")

        import numpy as np
        dummy = np.random.randn(1, 3, resolution, resolution).astype(np.float32)
        result = compiled([dummy])
        assert result[0].shape[1] == 11, f"Expected 11 classes, got {result[0].shape[1]}"

    def test_standard_model_still_works(self, tmp_path):
        """Verify a standard model (resnet18) still exports correctly after the fix."""
        import timm

        from watermeter.training_core import export_to_openvino

        resolution = 32
        model = timm.create_model("resnet18", pretrained=False, num_classes=11)

        onnx_path, ov_path = export_to_openvino(model, resolution, tmp_path, "resnet18_test")

        assert onnx_path.exists()
        assert ov_path.exists()
        assert ov_path.with_suffix(".bin").exists()

        import openvino as ov
        core = ov.Core()
        ov_model = core.read_model(str(ov_path))
        compiled = core.compile_model(ov_model, "CPU")

        import numpy as np
        dummy = np.random.randn(1, 3, resolution, resolution).astype(np.float32)
        result = compiled([dummy])
        assert result[0].shape[1] == 11

    def test_return_paths_are_correct(self, tmp_path):
        """Verify the returned paths match the expected naming convention."""
        import timm

        from watermeter.training_core import export_to_openvino

        model = timm.create_model("resnet18", pretrained=False, num_classes=5)

        onnx_path, ov_path = export_to_openvino(model, 32, tmp_path, "my_model")

        assert onnx_path == tmp_path / "my_model.onnx"
        assert ov_path == tmp_path / "my_model.xml"
