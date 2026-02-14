"""Unit tests for image_hash module."""
import json
import numpy as np
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestComputeDhash:
    """Test dHash computation."""

    def _make_grayscale_image(self, width, height, pixel_values=None):
        """Create a fake grayscale image array."""
        if pixel_values is not None:
            return np.array(pixel_values, dtype=np.uint8)
        return np.random.randint(0, 256, (height, width), dtype=np.uint8)

    def test_same_image_same_hash(self):
        """Identical bytes produce identical hash."""
        # Create a deterministic "image"
        img = self._make_grayscale_image(9, 8,
            [[i * 10 + j for j in range(9)] for i in range(8)])

        with patch('watermeter.image_hash.cv2') as mock_cv2:
            mock_cv2.IMREAD_GRAYSCALE = 0
            mock_cv2.imdecode.return_value = img
            mock_cv2.resize.return_value = img  # 9x8 is already correct size for hash_size=8

            from watermeter.image_hash import compute_dhash
            h1 = compute_dhash(b'fake_bytes')
            h2 = compute_dhash(b'fake_bytes')
            assert h1 == h2
            assert isinstance(h1, int)

    def test_different_images_different_hash(self):
        """Different images should produce different hashes."""
        img1 = np.zeros((8, 9), dtype=np.uint8)
        img2 = np.ones((8, 9), dtype=np.uint8) * 255
        # Make img2 have a gradient so diffs are different
        img2[:, ::2] = 0

        with patch('watermeter.image_hash.cv2') as mock_cv2:
            mock_cv2.IMREAD_GRAYSCALE = 0
            mock_cv2.imdecode.return_value = img1
            mock_cv2.resize.return_value = img1
            from watermeter.image_hash import compute_dhash
            h1 = compute_dhash(b'bytes1')

        with patch('watermeter.image_hash.cv2') as mock_cv2:
            mock_cv2.IMREAD_GRAYSCALE = 0
            mock_cv2.imdecode.return_value = img2
            mock_cv2.resize.return_value = img2
            from watermeter.image_hash import compute_dhash
            h2 = compute_dhash(b'bytes2')

        assert h1 != h2

    def test_invalid_image_returns_none(self):
        """Invalid image bytes should return None, not raise."""
        with patch('watermeter.image_hash.cv2') as mock_cv2:
            mock_cv2.IMREAD_GRAYSCALE = 0
            mock_cv2.imdecode.return_value = None  # cv2 returns None for invalid
            from watermeter.image_hash import compute_dhash
            result = compute_dhash(b'not_a_jpeg')
            assert result is None

    def test_hash_is_64_bits(self):
        """Default hash_size=8 should produce a hash that fits in 64 bits."""
        img = self._make_grayscale_image(9, 8)
        with patch('watermeter.image_hash.cv2') as mock_cv2:
            mock_cv2.IMREAD_GRAYSCALE = 0
            mock_cv2.imdecode.return_value = img
            mock_cv2.resize.return_value = img
            from watermeter.image_hash import compute_dhash
            h = compute_dhash(b'bytes')
            assert h is not None
            assert h >= 0
            assert h < (1 << 64)


class TestHammingDistance:
    def test_identical(self):
        from watermeter.image_hash import hamming_distance
        assert hamming_distance(0, 0) == 0
        assert hamming_distance(0xFF, 0xFF) == 0

    def test_one_bit_diff(self):
        from watermeter.image_hash import hamming_distance
        assert hamming_distance(0b0000, 0b0001) == 1

    def test_all_bits_diff(self):
        from watermeter.image_hash import hamming_distance
        assert hamming_distance(0, 0xFFFFFFFFFFFFFFFF) == 64

    def test_symmetric(self):
        from watermeter.image_hash import hamming_distance
        assert hamming_distance(0xAB, 0xCD) == hamming_distance(0xCD, 0xAB)


class TestHashCache:
    def test_empty_folder(self, tmp_path):
        from watermeter.image_hash import HashCache
        cache = HashCache(tmp_path)
        assert cache.get_all_hashes() == []
        assert cache.find_near_duplicate(123, threshold=10) is None

    def test_add_and_find(self, tmp_path):
        # Create a dummy file so prune doesn't remove it
        (tmp_path / "test.jpg").write_bytes(b'dummy')

        from watermeter.image_hash import HashCache
        cache = HashCache(tmp_path)
        cache.add("test.jpg", 0xABCD)

        # Should find exact match
        result = cache.find_near_duplicate(0xABCD, threshold=0)
        assert result == "test.jpg"

    def test_find_near_duplicate_within_threshold(self, tmp_path):
        (tmp_path / "test.jpg").write_bytes(b'dummy')

        from watermeter.image_hash import HashCache
        cache = HashCache(tmp_path)
        cache.add("test.jpg", 0b11111111)  # 8 bits set

        # 1 bit different - should be within threshold of 5
        result = cache.find_near_duplicate(0b11111110, threshold=5)
        assert result == "test.jpg"

    def test_find_no_duplicate_outside_threshold(self, tmp_path):
        (tmp_path / "test.jpg").write_bytes(b'dummy')

        from watermeter.image_hash import HashCache
        cache = HashCache(tmp_path)
        cache.add("test.jpg", 0)

        # Very different hash
        result = cache.find_near_duplicate(0xFFFFFFFFFFFFFFFF, threshold=5)
        assert result is None

    def test_persistence(self, tmp_path):
        """Cache should survive reload."""
        (tmp_path / "test.jpg").write_bytes(b'dummy')

        from watermeter.image_hash import HashCache
        cache1 = HashCache(tmp_path)
        cache1.add("test.jpg", 0xDEAD)

        # Create new cache instance - should load from file
        cache2 = HashCache(tmp_path)
        assert len(cache2.get_all_hashes()) == 1
        assert cache2.find_near_duplicate(0xDEAD, threshold=0) == "test.jpg"

    def test_prune_stale_entries(self, tmp_path):
        """Entries for deleted files should be pruned on load."""
        (tmp_path / "exists.jpg").write_bytes(b'dummy')

        from watermeter.image_hash import HashCache
        cache = HashCache(tmp_path)
        cache.add("exists.jpg", 0x1111)

        # Manually add entry for a file that doesn't exist
        cache_file = tmp_path / '.hashes.json'
        data = json.loads(cache_file.read_text())
        data["deleted.jpg"] = "2222"
        cache_file.write_text(json.dumps(data))

        # Reload - stale entry should be gone
        cache2 = HashCache(tmp_path)
        assert len(cache2.get_all_hashes()) == 1

    def test_scan_and_update(self, tmp_path):
        """scan_and_update should hash all jpg files not yet in cache."""
        # Create some "image" files
        (tmp_path / "a.jpg").write_bytes(b'image_a')
        (tmp_path / "b.jpg").write_bytes(b'image_b')
        (tmp_path / "c.txt").write_bytes(b'not_an_image')

        with patch('watermeter.image_hash.cv2') as mock_cv2:
            mock_cv2.IMREAD_GRAYSCALE = 0
            img = np.zeros((8, 9), dtype=np.uint8)
            mock_cv2.imdecode.return_value = img
            mock_cv2.resize.return_value = img

            from watermeter.image_hash import HashCache
            cache = HashCache(tmp_path)
            cache.scan_and_update()

            # Should have hashed 2 jpg files (not .txt)
            assert len(cache.get_all_hashes()) == 2
