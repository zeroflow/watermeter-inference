"""Unit tests for BL-02: ground-truth pruning logic."""
import json
import numpy as np
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Tests for cluster_images_by_hash
# ---------------------------------------------------------------------------

class TestClusterImagesByHash:
    """Test single-linkage clustering by hamming distance."""

    def test_empty_input(self):
        from watermeter.image_hash import cluster_images_by_hash
        assert cluster_images_by_hash({}, threshold=10) == []

    def test_single_image(self):
        from watermeter.image_hash import cluster_images_by_hash
        clusters = cluster_images_by_hash({"a.jpg": 0xABCD}, threshold=10)
        assert len(clusters) == 1
        assert clusters[0] == ["a.jpg"]

    def test_two_identical_hashes(self):
        from watermeter.image_hash import cluster_images_by_hash
        hashes = {"a.jpg": 0xFF, "b.jpg": 0xFF}
        clusters = cluster_images_by_hash(hashes, threshold=0)
        # Both should be in the same cluster
        assert len(clusters) == 1
        assert sorted(clusters[0]) == ["a.jpg", "b.jpg"]

    def test_two_different_hashes(self):
        """Hashes with distance > threshold should be in separate clusters."""
        from watermeter.image_hash import cluster_images_by_hash
        # distance between 0 and 0xFFFFFFFFFFFFFFFF is 64
        hashes = {"a.jpg": 0, "b.jpg": 0xFFFFFFFFFFFFFFFF}
        clusters = cluster_images_by_hash(hashes, threshold=10)
        assert len(clusters) == 2

    def test_chain_clustering(self):
        """Single-linkage: A~B and B~C means A,B,C in one cluster even if A!~C."""
        from watermeter.image_hash import cluster_images_by_hash
        # A and B differ by 1 bit, B and C differ by 1 bit, A and C differ by 2 bits
        hashes = {
            "a.jpg": 0b0000,
            "b.jpg": 0b0001,
            "c.jpg": 0b0011,
        }
        # threshold=1: A~B and B~C, so all in one cluster
        clusters = cluster_images_by_hash(hashes, threshold=1)
        assert len(clusters) == 1
        assert sorted(clusters[0]) == ["a.jpg", "b.jpg", "c.jpg"]

    def test_separate_clusters(self):
        """Images that are far apart should form separate clusters."""
        from watermeter.image_hash import cluster_images_by_hash
        hashes = {
            "a.jpg": 0b00000000,
            "b.jpg": 0b00000001,  # 1 bit from a
            "c.jpg": 0b11111111,  # 7 bits from a, 8 bits from b... actually 8 from a
            "d.jpg": 0b11111110,  # 1 bit from c
        }
        clusters = cluster_images_by_hash(hashes, threshold=2)
        assert len(clusters) == 2
        cluster_sets = [sorted(c) for c in clusters]
        cluster_sets.sort()
        assert cluster_sets == [["a.jpg", "b.jpg"], ["c.jpg", "d.jpg"]]

    def test_all_unique(self):
        """If all images are far apart, each is its own cluster."""
        from watermeter.image_hash import cluster_images_by_hash
        # Use hashes that differ by many bits
        hashes = {
            "a.jpg": 0x0000000000000000,
            "b.jpg": 0xFFFFFFFFFFFFFFFF,
            "c.jpg": 0xAAAAAAAAAAAAAAAA,
        }
        clusters = cluster_images_by_hash(hashes, threshold=0)
        assert len(clusters) == 3


# ---------------------------------------------------------------------------
# Tests for select_prune_candidates
# ---------------------------------------------------------------------------

class TestSelectPruneCandidates:
    """Test selecting least-distinct images from clusters."""

    def test_no_clusters(self):
        from watermeter.image_hash import select_prune_candidates
        assert select_prune_candidates([], {}) == []

    def test_singleton_clusters(self):
        """Single-image clusters should not produce any candidates."""
        from watermeter.image_hash import select_prune_candidates
        clusters = [["a.jpg"], ["b.jpg"]]
        hashes = {"a.jpg": 0, "b.jpg": 0xFF}
        assert select_prune_candidates(clusters, hashes) == []

    def test_pair_cluster_keeps_one(self):
        """A cluster of 2 should produce 1 candidate (the less distinct one)."""
        from watermeter.image_hash import select_prune_candidates
        clusters = [["a.jpg", "b.jpg"]]
        hashes = {"a.jpg": 0, "b.jpg": 0}
        candidates = select_prune_candidates(clusters, hashes)
        assert len(candidates) == 1

    def test_keeps_most_distinct(self):
        """The most distinct image (highest avg distance) should be kept."""
        from watermeter.image_hash import select_prune_candidates
        # a and b are identical (0), c differs by many bits from both
        # In a cluster of [a, b, c], c is most distinct -> keep c
        clusters = [["a.jpg", "b.jpg", "c.jpg"]]
        hashes = {
            "a.jpg": 0b00000000,
            "b.jpg": 0b00000000,
            "c.jpg": 0b11110000,  # 4 bits from a and b
        }
        candidates = select_prune_candidates(clusters, hashes)
        assert len(candidates) == 2
        assert "c.jpg" not in candidates  # c is the keeper
        assert "a.jpg" in candidates
        assert "b.jpg" in candidates

    def test_mixed_clusters(self):
        """Mix of singleton and multi-image clusters."""
        from watermeter.image_hash import select_prune_candidates
        clusters = [
            ["a.jpg"],
            ["b.jpg", "c.jpg"],
            ["d.jpg"],
        ]
        hashes = {
            "a.jpg": 0,
            "b.jpg": 0x10,
            "c.jpg": 0x11,  # 1 bit from b
            "d.jpg": 0xFF,
        }
        candidates = select_prune_candidates(clusters, hashes)
        # Only the b/c cluster has candidates
        assert len(candidates) == 1


# ---------------------------------------------------------------------------
# Tests for compute_prune_preview
# ---------------------------------------------------------------------------

class TestComputePrunePreview:
    """Test the full prune preview scan with median protection."""

    def _make_class_images(self, class_dir: Path, count: int, hash_base: int = 0):
        """Create fake jpg files and a hash cache for a class directory."""
        class_dir.mkdir(parents=True, exist_ok=True)
        hashes = {}
        for i in range(count):
            fname = f"img_{i:03d}.jpg"
            (class_dir / fname).write_bytes(b'fake_jpeg')
            # Give each image a slightly different hash
            hashes[fname] = format(hash_base + i, 'x')

        # Write hash cache
        cache_file = class_dir / '.hashes.json'
        cache_file.write_text(json.dumps(hashes))

    def _make_class_with_duplicates(self, class_dir: Path, unique: int,
                                     duplicates: int, hash_base: int = 0):
        """Create a class with some unique and some duplicate images."""
        class_dir.mkdir(parents=True, exist_ok=True)
        hashes = {}

        # Unique images: distinct hashes (far apart)
        for i in range(unique):
            fname = f"unique_{i:03d}.jpg"
            (class_dir / fname).write_bytes(b'fake_jpeg')
            # Space hashes far apart so they don't cluster
            hashes[fname] = format(hash_base + i * 1000, 'x')

        # Duplicate images: same hash as unique_000
        for i in range(duplicates):
            fname = f"dup_{i:03d}.jpg"
            (class_dir / fname).write_bytes(b'fake_jpeg')
            hashes[fname] = format(hash_base, 'x')  # Same as unique_000

        cache_file = class_dir / '.hashes.json'
        cache_file.write_text(json.dumps(hashes))

    def test_nonexistent_directory(self):
        """Non-existent gt_base should return empty preview."""
        from watermeter.image_hash import compute_prune_preview
        preview = compute_prune_preview(Path("/nonexistent"), threshold=10)
        assert preview["total_before"] == 0
        assert preview["total_removable"] == 0
        assert preview["classes"] == {}

    def test_no_duplicates(self, tmp_path):
        """All unique images -- nothing to prune."""
        gt_base = tmp_path / "ground_truth"
        # Create classes with all unique hashes (far apart)
        self._make_class_images(gt_base / "0", 10, hash_base=0)
        self._make_class_images(gt_base / "1", 10, hash_base=10000)

        with patch('watermeter.image_hash.compute_dhash') as mock_dhash:
            # scan_and_update will be called but hashes are already in cache
            from watermeter.image_hash import compute_prune_preview
            preview = compute_prune_preview(gt_base, threshold=10)

        assert preview["total_before"] == 20
        assert preview["total_removable"] == 0
        assert preview["total_after"] == 20

    def test_with_duplicates(self, tmp_path):
        """Classes with duplicates should have candidates (when above median)."""
        gt_base = tmp_path / "ground_truth"
        # Class "3": 5 unique + 15 duplicates = 20 images
        self._make_class_with_duplicates(gt_base / "3", unique=5, duplicates=15, hash_base=0)
        # Class "7": 5 unique, no duplicates (smaller class)
        self._make_class_images(gt_base / "7", 5, hash_base=90000)

        from watermeter.image_hash import compute_prune_preview
        preview = compute_prune_preview(gt_base, threshold=10)

        # median of [20, 5] = 12 (int of 12.5)
        # Class "3" can remove up to 20 - 12 = 8, but has 15 dups -> min(15, 8) = 8
        assert preview["total_before"] == 25
        assert preview["classes"]["3"]["removable"] == 8
        assert preview["classes"]["7"]["removable"] == 0

    def test_median_protection(self, tmp_path):
        """Thin classes should not be pruned below the median."""
        gt_base = tmp_path / "ground_truth"

        # Create 3 classes with sizes 20, 10, 5
        # Median of [20, 10, 5] = 10
        # Class with 5 images should not be pruned at all (already below median)
        self._make_class_with_duplicates(gt_base / "big", unique=10, duplicates=10, hash_base=0)
        self._make_class_with_duplicates(gt_base / "medium", unique=5, duplicates=5, hash_base=100000)
        self._make_class_with_duplicates(gt_base / "small", unique=2, duplicates=3, hash_base=200000)

        from watermeter.image_hash import compute_prune_preview
        preview = compute_prune_preview(gt_base, threshold=10)

        # Median class size is 10 (middle of [5, 10, 20])
        assert preview["median_class_size"] == 10

        # "big" has 20 images, can go down to 10 -> max 10 removable
        # It has 10 duplicates -> min(10, 10) = 10
        assert preview["classes"]["big"]["removable"] == 10
        assert preview["classes"]["big"]["after"] == 10

        # "medium" has 10 images, can go down to 10 -> max 0 removable
        assert preview["classes"]["medium"]["removable"] == 0
        assert preview["classes"]["medium"]["after"] == 10

        # "small" has 5 images, can go down to 10 -> max 0 (already below)
        assert preview["classes"]["small"]["removable"] == 0
        assert preview["classes"]["small"]["after"] == 5


# ---------------------------------------------------------------------------
# Tests for confirm_prune
# ---------------------------------------------------------------------------

class TestConfirmPrune:
    """Test that confirm_prune actually deletes the right files."""

    def test_deletes_candidates(self, tmp_path):
        """Candidates listed in preview should be deleted."""
        gt_base = tmp_path / "ground_truth"
        class_dir = gt_base / "3"
        class_dir.mkdir(parents=True)

        # Create files
        (class_dir / "keep.jpg").write_bytes(b'keep')
        (class_dir / "remove1.jpg").write_bytes(b'remove1')
        (class_dir / "remove2.jpg").write_bytes(b'remove2')

        # Write hash cache
        cache_data = {"keep.jpg": "1", "remove1.jpg": "2", "remove2.jpg": "3"}
        (class_dir / ".hashes.json").write_text(json.dumps(cache_data))

        preview = {
            "classes": {
                "3": {
                    "before": 3,
                    "after": 1,
                    "removable": 2,
                    "candidates": ["remove1.jpg", "remove2.jpg"],
                }
            }
        }

        from watermeter.image_hash import confirm_prune
        result = confirm_prune(gt_base, preview)

        assert result["total_deleted"] == 2
        assert result["total_errors"] == 0
        assert (class_dir / "keep.jpg").exists()
        assert not (class_dir / "remove1.jpg").exists()
        assert not (class_dir / "remove2.jpg").exists()

    def test_missing_file_counts_as_error(self, tmp_path):
        """If a candidate file is already gone, it's counted as an error."""
        gt_base = tmp_path / "ground_truth"
        class_dir = gt_base / "5"
        class_dir.mkdir(parents=True)

        # Don't create the file -- simulate already deleted
        preview = {
            "classes": {
                "5": {
                    "before": 1,
                    "after": 0,
                    "removable": 1,
                    "candidates": ["ghost.jpg"],
                }
            }
        }

        from watermeter.image_hash import confirm_prune
        result = confirm_prune(gt_base, preview)

        assert result["total_deleted"] == 0
        assert result["total_errors"] == 1

    def test_empty_preview(self, tmp_path):
        """Empty preview should produce zero deletions."""
        gt_base = tmp_path / "ground_truth"
        gt_base.mkdir(parents=True)

        preview = {"classes": {}}

        from watermeter.image_hash import confirm_prune
        result = confirm_prune(gt_base, preview)

        assert result["total_deleted"] == 0
        assert result["total_errors"] == 0

    def test_multiple_classes(self, tmp_path):
        """Confirm prune across multiple classes."""
        gt_base = tmp_path / "ground_truth"
        for cls_name in ("0", "1", "2"):
            cls_dir = gt_base / cls_name
            cls_dir.mkdir(parents=True)
            (cls_dir / "a.jpg").write_bytes(b'a')
            (cls_dir / "b.jpg").write_bytes(b'b')
            (cls_dir / ".hashes.json").write_text(
                json.dumps({"a.jpg": "1", "b.jpg": "2"})
            )

        preview = {
            "classes": {
                "0": {"before": 2, "after": 1, "removable": 1, "candidates": ["b.jpg"]},
                "1": {"before": 2, "after": 1, "removable": 1, "candidates": ["a.jpg"]},
                "2": {"before": 2, "after": 2, "removable": 0, "candidates": []},
            }
        }

        from watermeter.image_hash import confirm_prune
        result = confirm_prune(gt_base, preview)

        assert result["total_deleted"] == 2
        assert not (gt_base / "0" / "b.jpg").exists()
        assert (gt_base / "0" / "a.jpg").exists()
        assert not (gt_base / "1" / "a.jpg").exists()
        assert (gt_base / "1" / "b.jpg").exists()
        assert (gt_base / "2" / "a.jpg").exists()
        assert (gt_base / "2" / "b.jpg").exists()


# ---------------------------------------------------------------------------
# Tests for HashCache additions
# ---------------------------------------------------------------------------

class TestHashCacheExtensions:
    """Test get_hashes_dict and remove methods added for BL-02."""

    def test_get_hashes_dict(self, tmp_path):
        """get_hashes_dict should return filename->int mapping."""
        (tmp_path / "a.jpg").write_bytes(b'a')
        (tmp_path / "b.jpg").write_bytes(b'b')

        from watermeter.image_hash import HashCache
        cache = HashCache(tmp_path)
        cache.add("a.jpg", 0xABCD)
        cache.add("b.jpg", 0x1234)

        d = cache.get_hashes_dict()
        assert d == {"a.jpg": 0xABCD, "b.jpg": 0x1234}

    def test_remove(self, tmp_path):
        """remove() should delete entry and persist."""
        (tmp_path / "a.jpg").write_bytes(b'a')

        from watermeter.image_hash import HashCache
        cache = HashCache(tmp_path)
        cache.add("a.jpg", 0x1111)
        assert len(cache.get_all_hashes()) == 1

        cache.remove("a.jpg")
        assert len(cache.get_all_hashes()) == 0

        # Verify persistence
        cache2 = HashCache(tmp_path)
        assert len(cache2.get_all_hashes()) == 0

    def test_remove_nonexistent_is_noop(self, tmp_path):
        """Removing a key that doesn't exist should not raise."""
        from watermeter.image_hash import HashCache
        cache = HashCache(tmp_path)
        cache.remove("nonexistent.jpg")  # Should not raise


# ---------------------------------------------------------------------------
# Tests for API endpoints
# ---------------------------------------------------------------------------

class TestPruneAPIEndpoints:
    """Test /api/training-data/prune/preview and /confirm endpoints."""

    def test_preview_invalid_type(self, test_client):
        """Preview with invalid type should return 400."""
        resp = test_client.post(
            "/api/training-data/prune/preview",
            json={"type": "invalid"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False

    def test_confirm_invalid_type(self, test_client):
        """Confirm with invalid type should return 400."""
        resp = test_client.post(
            "/api/training-data/prune/confirm",
            json={"type": "invalid"},
        )
        assert resp.status_code == 400

    def test_confirm_without_preview(self, test_client):
        """Confirm without prior preview should return 409."""
        # Clear any stored previews
        import watermeter.routes.models as models_mod
        models_mod._prune_previews.clear()

        resp = test_client.post(
            "/api/training-data/prune/confirm",
            json={"type": "digits"},
        )
        assert resp.status_code == 409
        data = resp.json()
        assert "preview" in data["message"].lower()

    def test_preview_returns_structure(self, test_client, tmp_path):
        """Preview should return expected JSON structure."""
        # Set up a fake ground_truth dir
        gt_base = tmp_path / "training" / "digits" / "ground_truth"
        cls_dir = gt_base / "0"
        cls_dir.mkdir(parents=True)
        (cls_dir / "a.jpg").write_bytes(b'a')
        (cls_dir / ".hashes.json").write_text(json.dumps({"a.jpg": "abcd"}))

        # Patch training path
        import watermeter.routes.models as models_mod
        svc_mock = models_mod.watermeter_service.get_service()
        svc_mock.config = {
            'low_confidence': {
                'save_path': str(tmp_path / "training"),
                'dedup_threshold': 10,
            }
        }

        resp = test_client.post(
            "/api/training-data/prune/preview",
            json={"type": "digits"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "total_before" in data
        assert "total_after" in data
        assert "total_removable" in data
        assert "median_class_size" in data
        assert "classes" in data

    def test_preview_then_confirm(self, test_client, tmp_path):
        """Full workflow: preview then confirm."""
        gt_base = tmp_path / "training" / "arrows" / "ground_truth"

        # Class "0.0": 10 images, 5 with identical hash -> 5 duplicates
        cls_dir = gt_base / "0.0"
        cls_dir.mkdir(parents=True)
        hashes = {}
        for i in range(5):
            fname = f"unique_{i}.jpg"
            (cls_dir / fname).write_bytes(b'unique')
            hashes[fname] = format(i * 1000, 'x')  # far apart
        for i in range(5):
            fname = f"dup_{i}.jpg"
            (cls_dir / fname).write_bytes(b'dup')
            hashes[fname] = "0"  # same hash as unique_0
        (cls_dir / ".hashes.json").write_text(json.dumps(hashes))

        # Class "1.0": 1 image (small class, pulls median down)
        cls_dir2 = gt_base / "1.0"
        cls_dir2.mkdir(parents=True)
        (cls_dir2 / "solo.jpg").write_bytes(b'solo')
        (cls_dir2 / ".hashes.json").write_text(json.dumps({"solo.jpg": "ffff"}))

        import watermeter.routes.models as models_mod
        svc_mock = models_mod.watermeter_service.get_service()
        svc_mock.config = {
            'low_confidence': {
                'save_path': str(tmp_path / "training"),
                'dedup_threshold': 10,
            }
        }

        # Step 1: Preview
        # median of [10, 1] = 5 (int of 5.5)
        # class "0.0" has 10, can go to 5 -> max 5 removable, has 5 dups -> actual 5
        resp = test_client.post(
            "/api/training-data/prune/preview",
            json={"type": "arrows"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_removable"] == 5

        # Step 2: Confirm
        resp = test_client.post(
            "/api/training-data/prune/confirm",
            json={"type": "arrows"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_deleted"] == 5

        # Verify correct number of files remain
        remaining = list(cls_dir.glob("*.jpg"))
        assert len(remaining) == 5

    def test_confirm_clears_preview(self, test_client, tmp_path):
        """After confirm, stored preview should be cleared."""
        gt_base = tmp_path / "training" / "digits" / "ground_truth"
        cls_dir = gt_base / "1"
        cls_dir.mkdir(parents=True)
        (cls_dir / "a.jpg").write_bytes(b'a')
        (cls_dir / ".hashes.json").write_text(json.dumps({"a.jpg": "1234"}))

        import watermeter.routes.models as models_mod
        svc_mock = models_mod.watermeter_service.get_service()
        svc_mock.config = {
            'low_confidence': {
                'save_path': str(tmp_path / "training"),
                'dedup_threshold': 10,
            }
        }

        # Preview
        test_client.post("/api/training-data/prune/preview", json={"type": "digits"})

        # Confirm
        test_client.post("/api/training-data/prune/confirm", json={"type": "digits"})

        # Second confirm should fail -- preview was cleared
        resp = test_client.post(
            "/api/training-data/prune/confirm",
            json={"type": "digits"},
        )
        assert resp.status_code == 409
