# Fix ConvNeXtV2 (and ONNX Sequence-Op Models) Export to OpenVINO

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the OpenVINO export pipeline so models that produce ONNX sequence ops (SequenceEmpty, ConcatFromSequence, SequenceInsert) can be exported successfully. Then add ConvNeXtV2 variants to the curated model list.

**Architecture:** Replace the current ONNX-then-convert pipeline in `export_to_openvino()` with a dual strategy: (1) `ov.convert_model()` for direct PyTorch-to-OpenVINO conversion (bypasses ONNX entirely), and (2) `torch.onnx.export(dynamo=False)` for a portable ONNX fallback. TDD approach throughout.

**Tech Stack:** Python, PyTorch 2.10, OpenVINO 2025.4, timm, pytest

---

### Background: The Bug

PyTorch 2.10's `torch.onnx.export()` defaults to `dynamo=True`, which uses `torch.export.ExportedProgram` internally. For models like ConvNeXtV2 and EdgeNeXt, this produces ONNX ops that OpenVINO cannot consume:
- `SequenceEmpty`
- `SequenceInsert`
- `ConcatFromSequence`

The current code in `watermeter/training_core.py:150` calls `torch.onnx.export()` without specifying `dynamo=False`, so it uses the Dynamo exporter by default. When OpenVINO's `core.read_model()` tries to load the resulting ONNX file, it fails because it doesn't support those sequence ops.

**Affected models** (confirmed from `timm.list_models()`):
- `convnextv2_atto`, `convnextv2_femto`, `convnextv2_pico`, `convnextv2_nano`, `convnextv2_tiny`, `convnextv2_small`, `convnextv2_base`
- `edgenext_small` (already in curated list!)
- Potentially other timm models with similar internal ops

**Solution:** Two changes to `export_to_openvino()`:
1. **Primary (OpenVINO):** Use `ov.convert_model(model, example_input=dummy_input)` to convert directly from PyTorch to OpenVINO IR. This bypasses ONNX entirely and works for all PyTorch models OpenVINO supports.
2. **Fallback (ONNX):** Use `torch.onnx.export(dynamo=False)` for the ONNX file, which uses the legacy TorchScript exporter that doesn't produce sequence ops.

### Current Code

**`watermeter/training_core.py` L130-166 -- `export_to_openvino()`:**
```python
def export_to_openvino(model: torch.nn.Module, resolution: int, output_dir: Path, filename: str) -> Tuple[Path, Path]:
    """Export a PyTorch model to ONNX and then OpenVINO IR format."""
    import openvino as ov

    output_dir.mkdir(parents=True, exist_ok=True)
    dummy_input = torch.randn(1, 3, resolution, resolution)

    # ONNX export (uses dynamo=True by default in PyTorch 2.10!)
    onnx_path = output_dir / f"{filename}.onnx"
    torch.onnx.export(
        model, dummy_input, onnx_path,
        export_params=True, opset_version=18,
        input_names=["input"], output_names=["output"],
    )

    # OpenVINO conversion (reads ONNX -- fails if ONNX has sequence ops)
    core = ov.Core()
    model_onnx = core.read_model(str(onnx_path))
    ov_path = output_dir / f"{filename}.xml"
    ov.save_model(model_onnx, str(ov_path))

    return onnx_path, ov_path
```

**Callers:**
| Caller | File | Line | Usage |
|--------|------|------|-------|
| `_execute_training()` | `watermeter/training_manager.py` | 720 | `onnx_path, ov_path = export_to_openvino(model, resolution, output_dir, model_filename)` |

The return tuple `(onnx_path, ov_path)` is used for logging only (L721-722). No downstream code depends on the paths beyond that.

### Curated Models List

**`watermeter/static/training.js` L59-85 -- `CURATED_MODELS`:**
Currently has 3 groups: Lightweight (6 models), Medium (11 models), Heavy (2 models). Does not include any ConvNeXtV2 variants.

---

### Task 1: Write failing test for ConvNeXtV2 export

**Files:**
- Create: `tests/unit/test_export_openvino.py`

**Step 1: Write the test**

This test creates a real (tiny) ConvNeXtV2 model via timm and attempts to export it through `export_to_openvino()`. It verifies the output .xml and .bin files exist and are valid OpenVINO models. Mark it as `@pytest.mark.slow` since it downloads model weights.

Note: This test needs real `torch`, `timm`, and `openvino` -- it cannot run in the unit test conftest that mocks openvino. Place it in `tests/unit/` but do NOT import from the unit conftest mock setup. The test imports directly from `watermeter.training_core` only.

```python
"""Tests for export_to_openvino() -- verifies models with ONNX sequence ops export correctly."""
import pytest
import torch
from pathlib import Path


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
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_export_openvino.py -v --no-header -x`

Expected: `test_convnextv2_pico_export` FAILS because the current Dynamo ONNX export produces sequence ops that OpenVINO cannot read. The other two tests may pass (resnet18 doesn't have the problematic ops).

**Step 3: Commit**

```bash
git add tests/unit/test_export_openvino.py
git commit -m "claude: add failing tests for ConvNeXtV2 OpenVINO export"
```

---

### Task 2: Fix `export_to_openvino()` -- direct PyTorch-to-OpenVINO conversion

**Files:**
- Modify: `watermeter/training_core.py` L130-166

**Step 1: Replace the export function**

Replace the entire `export_to_openvino()` function (lines 130-166) with the new implementation:

```python
def export_to_openvino(model: torch.nn.Module, resolution: int, output_dir: Path, filename: str) -> Tuple[Path, Path]:
    """Export a PyTorch model to ONNX and OpenVINO IR format.

    Uses two independent export strategies:
    1. OpenVINO: Direct PyTorch -> OpenVINO IR via ov.convert_model()
       (bypasses ONNX, works for all models including ConvNeXtV2/EdgeNeXt)
    2. ONNX: Legacy TorchScript export with dynamo=False for portability
       (avoids SequenceEmpty/ConcatFromSequence ops from Dynamo exporter)

    Args:
        model: Trained PyTorch model (must be on CPU and in eval mode).
        resolution: Input image resolution.
        output_dir: Directory to save the exported model files.
        filename: Base filename (without extension).

    Returns:
        (onnx_path, openvino_xml_path)
    """
    import openvino as ov

    output_dir.mkdir(parents=True, exist_ok=True)

    dummy_input = torch.randn(1, 3, resolution, resolution)

    # --- OpenVINO: direct conversion from PyTorch (no ONNX intermediate) ---
    ov_model = ov.convert_model(model, example_input=dummy_input)
    ov_path = output_dir / f"{filename}.xml"
    ov.save_model(ov_model, str(ov_path))

    # --- ONNX: legacy TorchScript exporter (dynamo=False) for portability ---
    onnx_path = output_dir / f"{filename}.onnx"
    torch.onnx.export(
        model,
        dummy_input,
        onnx_path,
        export_params=True,
        opset_version=18,
        dynamo=False,
        input_names=["input"],
        output_names=["output"],
    )

    return onnx_path, ov_path
```

Key changes:
1. **OpenVINO conversion is now direct** -- `ov.convert_model(model, example_input=dummy_input)` converts the PyTorch model to OpenVINO IR without going through ONNX. This works for all models because OpenVINO traces the PyTorch model directly.
2. **ONNX export uses `dynamo=False`** -- Forces the legacy TorchScript-based exporter, which doesn't produce sequence ops. This ONNX file is kept for portability (users may want to use it outside OpenVINO).
3. **The two exports are now independent** -- OpenVINO doesn't depend on the ONNX file anymore. If ONNX export fails for some reason, OpenVINO IR is still valid.
4. **Return type unchanged** -- Still returns `(onnx_path, ov_path)`, so the caller in `training_manager.py:720` doesn't need any changes.

**Step 2: Run the export tests**

Run: `.venv/bin/python -m pytest tests/unit/test_export_openvino.py -v --no-header`
Expected: All 3 tests PASS (including the ConvNeXtV2 pico test)

**Step 3: Run full test suite to verify no regressions**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add watermeter/training_core.py
git commit -m "claude: fix OpenVINO export for ConvNeXtV2 and sequence-op models

Use ov.convert_model() for direct PyTorch->OpenVINO conversion (bypasses ONNX).
Keep ONNX export with dynamo=False for portability.
Fixes export failure caused by SequenceEmpty/ConcatFromSequence ops."
```

---

### Task 3: Add ConvNeXtV2 variants to curated models list

**Files:**
- Modify: `watermeter/static/training.js` L59-85

**Step 1: Add ConvNeXtV2 models to CURATED_MODELS**

Available ConvNeXtV2 models from `timm.list_models('convnextv2*')`:
`convnextv2_atto`, `convnextv2_femto`, `convnextv2_pico`, `convnextv2_nano`, `convnextv2_tiny`, `convnextv2_small`, `convnextv2_base`

Add to the appropriate groups based on parameter count and inference speed:

**Lightweight group** (line ~60-67) -- add after existing entries:
```javascript
{ value: 'convnextv2_atto', label: 'ConvNeXtV2 Atto' },
{ value: 'convnextv2_femto', label: 'ConvNeXtV2 Femto' },
{ value: 'convnextv2_pico', label: 'ConvNeXtV2 Pico' },
```

**Medium group** (line ~68-80) -- add after existing entries:
```javascript
{ value: 'convnextv2_nano', label: 'ConvNeXtV2 Nano' },
```

**Heavy group** (line ~81-84) -- add after existing entries:
```javascript
{ value: 'convnextv2_tiny', label: 'ConvNeXtV2 Tiny' },
```

ConvNeXtV2 Tiny has ~28M params -- comparable to ResNeXt50 (~25M) already in Heavy. ConvNeXtV2 Small (~50M) and Base (~89M) are omitted as too large for edge inference on this watermeter use case.

**Step 2: Commit**

```bash
git add watermeter/static/training.js
git commit -m "claude: add ConvNeXtV2 variants (atto/femto/pico/nano/tiny) to curated models"
```

---

### Task 4: Update codebase map

**Files:**
- Modify: `docs/codebase_map.md`

**Step 1: Update the `training_core.py` entry**

Find the `export_to_openvino` entry (currently at line 146 of codebase_map.md) and update the description to reflect the new dual-export approach:

Change:
```markdown
- `export_to_openvino(model, resolution, output_dir, filename)` L130
```
to:
```markdown
- `export_to_openvino(model, resolution, output_dir, filename)` L130 -- dual export: ov.convert_model() direct + ONNX dynamo=False
```

**Step 2: Commit**

```bash
git add docs/codebase_map.md
git commit -m "claude: update codebase map for export_to_openvino changes"
```

---

### Verification Checklist

After all tasks are complete:

1. `tests/unit/test_export_openvino.py` -- all 3 tests pass (ConvNeXtV2 pico, resnet18, path naming)
2. Full test suite passes: `.venv/bin/python -m pytest tests/ -x -q`
3. Curated models list includes 5 ConvNeXtV2 variants in the correct groups
4. `export_to_openvino()` return type and caller interface unchanged
5. Both `.onnx` and `.xml`/`.bin` files produced for any model

### Risk Notes

- **`ov.convert_model()` compatibility:** OpenVINO 2025.4 (installed) supports direct PyTorch conversion. This is a stable, well-documented API. If a specific timm model fails `ov.convert_model()`, the error will be clear and model-specific (not a systematic failure like the ONNX sequence ops issue).
- **ONNX `dynamo=False` fallback:** The legacy TorchScript exporter is mature and works for all standard timm models. Setting `dynamo=False` explicitly ensures forward compatibility as PyTorch may change defaults in future versions.
- **No caller changes needed:** The return type `Tuple[Path, Path]` is preserved. The only caller (`training_manager.py:720`) uses both paths for logging only.
- **EdgeNeXt Small already in curated list:** It was already in the Medium group (L70). With this fix, it will now export correctly too -- previously it would have failed with the same sequence ops error.
