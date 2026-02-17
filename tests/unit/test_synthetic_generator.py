"""
Unit tests for SyntheticGenerator orchestrator.

These tests need the REAL cv2 (not the mocked one from conftest.py).
We follow the same unmocking strategy as test_synthetic_transforms.py.
"""

import sys
from unittest.mock import MagicMock

import pytest
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# The unit conftest.py mocks cv2 in sys.modules BEFORE this file loads.
# We must replace the mock with the real cv2 so TransformPipeline works.
# ---------------------------------------------------------------------------

_saved_cv2_mock = sys.modules.get('cv2')

# Remove mock cv2 so we can import the real one
if 'cv2' in sys.modules and isinstance(sys.modules['cv2'], MagicMock):
    del sys.modules['cv2']

# Import the real cv2 -- skip if not installed
cv2 = pytest.importorskip('cv2')

if not hasattr(cv2, 'warpAffine'):
    pytest.skip("cv2 module is mocked, cannot run generator tests", allow_module_level=True)

sys.modules['cv2'] = cv2

# Remove any cached watermeter.synthetic_generator that was imported with the mock cv2
for _mod_name in list(sys.modules.keys()):
    if _mod_name == 'watermeter.synthetic_generator':
        del sys.modules[_mod_name]

# Now import SyntheticGenerator -- it will see the real cv2
from watermeter.synthetic_generator import SyntheticGenerator

# Restore the conftest mock so other test files are not affected
if _saved_cv2_mock is not None:
    sys.modules['cv2'] = _saved_cv2_mock


class TestSyntheticGenerator:
    def test_generate_digits(self, tmp_path):
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="digits", count_per_class=3, seed=42)

        gt_dir = tmp_path / "digits" / "ground_truth"
        for digit in range(10):
            class_dir = gt_dir / str(digit)
            assert class_dir.exists()
            images = list(class_dir.glob("synth_*.jpg"))
            assert len(images) == 3

    def test_generate_arrows(self, tmp_path):
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="arrows", count_per_class=2, seed=42)

        gt_dir = tmp_path / "arrows" / "ground_truth"
        for i in range(10):
            for j in range(10):
                class_dir = gt_dir / f"{i}.{j}"
                assert class_dir.exists()
                images = list(class_dir.glob("synth_*.jpg"))
                assert len(images) == 2

    def test_generate_both(self, tmp_path):
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="both", count_per_class=1, seed=42)
        assert (tmp_path / "digits" / "ground_truth" / "0").exists()
        assert (tmp_path / "arrows" / "ground_truth" / "0.0").exists()

    def test_images_are_valid_jpeg(self, tmp_path):
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="digits", count_per_class=1, seed=42)
        img_path = next((tmp_path / "digits" / "ground_truth" / "5").glob("synth_*.jpg"))
        img = Image.open(img_path)
        assert img.mode == "RGB"

    def test_reproducible_with_seed(self, tmp_path):
        dir1 = tmp_path / "run1"
        dir2 = tmp_path / "run2"
        gen1 = SyntheticGenerator(base_dir=str(dir1))
        gen1.generate(type="digits", count_per_class=1, seed=42)
        gen2 = SyntheticGenerator(base_dir=str(dir2))
        gen2.generate(type="digits", count_per_class=1, seed=42)
        img1 = Image.open(next((dir1 / "digits" / "ground_truth" / "3").glob("synth_*.jpg")))
        img2 = Image.open(next((dir2 / "digits" / "ground_truth" / "3").glob("synth_*.jpg")))
        assert np.array_equal(np.array(img1), np.array(img2))

    def test_progress_callback(self, tmp_path):
        progress_calls = []
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(
            type="digits",
            count_per_class=2,
            seed=42,
            progress_callback=lambda cur, total, msg: progress_calls.append((cur, total, msg)),
        )
        assert len(progress_calls) > 0
        last = progress_calls[-1]
        assert last[0] == last[1]  # Final progress = 100%

    def test_synth_prefix(self, tmp_path):
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="digits", count_per_class=1, seed=42)
        for img_path in (tmp_path / "digits" / "ground_truth" / "0").glob("*.jpg"):
            assert img_path.name.startswith("synth_")

    def test_does_not_overwrite_existing(self, tmp_path):
        gt_dir = tmp_path / "digits" / "ground_truth" / "0"
        gt_dir.mkdir(parents=True)
        existing = gt_dir / "real_image.jpg"
        existing.write_bytes(b"original")
        gen = SyntheticGenerator(base_dir=str(tmp_path))
        gen.generate(type="digits", count_per_class=1, seed=42)
        assert existing.read_bytes() == b"original"
