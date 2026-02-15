"""Perceptual hashing for image deduplication."""
import json
import logging
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def compute_dhash(image_bytes: bytes, hash_size: int = 8) -> Optional[int]:
    """
    Compute difference hash (dHash) for an image.

    Args:
        image_bytes: Raw JPEG bytes
        hash_size: Hash size (default 8 produces 64-bit hash)

    Returns:
        64-bit integer hash, or None if image cannot be decoded
    """
    try:
        # Decode JPEG bytes to grayscale
        img_array = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)

        if img is None:
            return None

        # Resize to (hash_size + 1) x hash_size
        resized = cv2.resize(img, (hash_size + 1, hash_size))

        # Compare each pixel to its right neighbor
        diff = resized[:, 1:] > resized[:, :-1]

        # Pack bits into an integer
        hash_value = int.from_bytes(
            np.packbits(diff.flatten()).tobytes(),
            byteorder='big'
        )

        return hash_value

    except Exception as e:
        logger.warning(f"Failed to compute dhash: {e}")
        return None


def hamming_distance(h1: int, h2: int) -> int:
    """
    Calculate Hamming distance between two hashes.

    Args:
        h1: First hash
        h2: Second hash

    Returns:
        Number of differing bits
    """
    return bin(h1 ^ h2).count('1')


class HashCache:
    """Manages a .hashes.json sidecar file per directory to avoid re-hashing images."""

    def __init__(self, folder: Path):
        """Load or create hash cache for the given folder."""
        self.folder = Path(folder)
        self.cache_file = self.folder / '.hashes.json'
        self._hashes: Dict[str, str] = {}  # filename -> hex hash string
        self._load()

    def _load(self):
        """Load cache from disk, prune entries whose files no longer exist."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file) as f:
                    raw = json.load(f)
                # Prune stale entries
                self._hashes = {
                    k: v for k, v in raw.items()
                    if (self.folder / k).exists()
                }
            except Exception as e:
                logger.warning(f"Failed to load hash cache from {self.cache_file}: {e}")
                self._hashes = {}
        else:
            self._hashes = {}

    def _save(self):
        """Persist cache to disk."""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self._hashes, f, indent=1)
        except Exception as e:
            logger.error(f"Failed to save hash cache to {self.cache_file}: {e}")

    def add(self, filename: str, hash_value: int) -> None:
        """Add a hash entry and save."""
        self._hashes[filename] = format(hash_value, 'x')
        self._save()

    def get_all_hashes(self) -> List[int]:
        """Return all cached hash values as integers."""
        return [int(v, 16) for v in self._hashes.values()]

    def get_hashes_dict(self) -> Dict[str, int]:
        """Return dict mapping filename -> integer hash for all cached entries."""
        return {k: int(v, 16) for k, v in self._hashes.items()}

    def remove(self, filename: str) -> None:
        """Remove a hash entry and save."""
        self._hashes.pop(filename, None)
        self._save()

    def find_near_duplicate(self, new_hash: int, threshold: int) -> Optional[str]:
        """
        Check if any cached hash is within threshold hamming distance.

        Args:
            new_hash: Hash to check
            threshold: Maximum hamming distance to consider a match

        Returns:
            Filename of the first match, or None
        """
        for filename, hex_hash in self._hashes.items():
            cached_hash = int(hex_hash, 16)
            if hamming_distance(new_hash, cached_hash) <= threshold:
                return filename
        return None

    def scan_and_update(self) -> None:
        """Scan folder for .jpg files not in cache, compute and add their hashes."""
        for img_path in self.folder.glob('*.jpg'):
            if img_path.name == '.hashes.json':
                continue
            if img_path.name not in self._hashes:
                try:
                    image_bytes = img_path.read_bytes()
                    h = compute_dhash(image_bytes)
                    if h is not None:
                        self._hashes[img_path.name] = format(h, 'x')
                except Exception as e:
                    logger.warning(f"Failed to hash {img_path.name}: {e}")
        self._save()


def purge_duplicates(input_dir: Path, threshold: int = 10,
                     gt_dirs: Optional[List[Path]] = None) -> Dict:
    """
    Remove near-duplicate images from an input folder.

    Iterates images sorted by name (oldest first due to timestamp naming).
    Keeps the first occurrence of each visually unique image, deletes the rest.
    Also deletes companion _next.jpg files.

    Args:
        input_dir: Path to the input folder (e.g. /training/arrows/input/)
        threshold: Maximum hamming distance to consider a duplicate
        gt_dirs: Optional list of ground_truth class dirs to cross-check against

    Returns:
        Dict with 'kept', 'removed', 'errors' counts
    """
    if not input_dir.is_dir():
        return {"kept": 0, "removed": 0, "errors": 0}

    images = sorted([
        p for p in input_dir.glob('*.jpg')
        if not p.name.endswith('_next.jpg')
    ])

    if not images:
        return {"kept": 0, "removed": 0, "errors": 0}

    # Build ground truth hash index if provided
    gt_hashes: List[int] = []
    if gt_dirs:
        for gt_dir in gt_dirs:
            if gt_dir.is_dir():
                cache = HashCache(gt_dir)
                cache.scan_and_update()
                gt_hashes.extend(cache.get_all_hashes())

    kept_hashes: List[int] = []
    kept = 0
    removed = 0
    errors = 0

    for img_path in images:
        try:
            h = compute_dhash(img_path.read_bytes())
            if h is None:
                errors += 1
                continue

            # Check against ground truth
            is_dup = False
            for gt_h in gt_hashes:
                if hamming_distance(h, gt_h) <= threshold:
                    is_dup = True
                    break

            # Check against already-kept input images
            if not is_dup:
                for kept_h in kept_hashes:
                    if hamming_distance(h, kept_h) <= threshold:
                        is_dup = True
                        break

            if is_dup:
                img_path.unlink()
                # Delete companion _next.jpg
                next_path = img_path.with_name(
                    img_path.stem + '_next.jpg'
                )
                if next_path.exists():
                    next_path.unlink()
                removed += 1
            else:
                kept_hashes.append(h)
                kept += 1

        except Exception as e:
            logger.warning(f"Error processing {img_path.name}: {e}")
            errors += 1

    # Rebuild hash cache for remaining files
    cache = HashCache(input_dir)
    cache.scan_and_update()

    logger.info(
        f"Purge {input_dir}: kept={kept}, removed={removed}, errors={errors}"
    )
    return {"kept": kept, "removed": removed, "errors": errors}


# ---------------------------------------------------------------------------
# BL-02: Ground-truth pruning
# ---------------------------------------------------------------------------

def cluster_images_by_hash(
    hashes: Dict[str, int],
    threshold: int = 10,
) -> List[List[str]]:
    """
    Cluster filenames by perceptual hash similarity (single-linkage).

    Two images end up in the same cluster if any path of pairwise distances
    <= threshold connects them.

    Args:
        hashes: Mapping of filename -> integer dHash
        threshold: Maximum hamming distance to consider two images similar

    Returns:
        List of clusters, each cluster is a list of filenames.
        Singletons (clusters of size 1) are included.
    """
    filenames = list(hashes.keys())
    if not filenames:
        return []

    # Union-Find for single-linkage clustering
    parent: Dict[str, str] = {f: f for f in filenames}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Compare all pairs — O(n^2) but n is small per class (< 200 images)
    for i in range(len(filenames)):
        for j in range(i + 1, len(filenames)):
            if hamming_distance(hashes[filenames[i]], hashes[filenames[j]]) <= threshold:
                union(filenames[i], filenames[j])

    # Group by root
    clusters_map: Dict[str, List[str]] = {}
    for f in filenames:
        root = find(f)
        clusters_map.setdefault(root, []).append(f)

    return list(clusters_map.values())


def select_prune_candidates(
    clusters: List[List[str]],
    hashes: Dict[str, int],
) -> List[str]:
    """
    From each cluster with 2+ images, keep the most distinct image and
    return the rest as prune candidates.

    "Most distinct" = highest average hamming distance to the other members
    of its cluster (i.e. the image that is least similar to its neighbours).

    Args:
        clusters: List of clusters (each a list of filenames)
        hashes: Mapping of filename -> integer dHash

    Returns:
        List of filenames to remove
    """
    candidates: List[str] = []

    for cluster in clusters:
        if len(cluster) <= 1:
            continue

        # Find the most distinct member
        best_file = None
        best_avg_dist = -1.0

        for fname in cluster:
            distances = [
                hamming_distance(hashes[fname], hashes[other])
                for other in cluster
                if other != fname
            ]
            avg_dist = sum(distances) / len(distances) if distances else 0
            if avg_dist > best_avg_dist:
                best_avg_dist = avg_dist
                best_file = fname

        # Everything except the keeper is a candidate
        for fname in cluster:
            if fname != best_file:
                candidates.append(fname)

    return candidates


def compute_prune_preview(
    gt_base: Path,
    threshold: int = 10,
) -> Dict:
    """
    Scan all class folders under gt_base, find near-duplicate images,
    and return a preview of what would be pruned.

    Applies median protection: no class is pruned below the median class size.

    Args:
        gt_base: Path to ground_truth directory (e.g. /training/digits/ground_truth)
        threshold: Hamming distance threshold for clustering

    Returns:
        Dict with structure:
        {
            "threshold": int,
            "median_class_size": int,
            "total_before": int,
            "total_after": int,
            "total_removable": int,
            "classes": {
                "<class_name>": {
                    "before": int,
                    "after": int,
                    "removable": int,
                    "candidates": ["file1.jpg", ...]
                },
                ...
            }
        }
    """
    if not gt_base.is_dir():
        return {
            "threshold": threshold,
            "median_class_size": 0,
            "total_before": 0,
            "total_after": 0,
            "total_removable": 0,
            "classes": {},
        }

    # Phase 1: scan all classes and compute raw candidates
    class_data: Dict[str, Dict] = {}
    class_sizes: List[int] = []

    for class_dir in sorted(gt_base.iterdir()):
        if not class_dir.is_dir():
            continue

        cache = HashCache(class_dir)
        cache.scan_and_update()
        hashes = cache.get_hashes_dict()

        class_size = len(hashes)
        class_sizes.append(class_size)

        clusters = cluster_images_by_hash(hashes, threshold)
        raw_candidates = select_prune_candidates(clusters, hashes)

        class_data[class_dir.name] = {
            "dir": class_dir,
            "before": class_size,
            "raw_candidates": raw_candidates,
        }

    # Phase 2: compute median and apply protection
    median_size = int(statistics.median(class_sizes)) if class_sizes else 0

    result_classes: Dict[str, Dict] = {}
    total_before = 0
    total_removable = 0

    for class_name, data in class_data.items():
        before = data["before"]
        raw_candidates = data["raw_candidates"]

        # How many can we actually remove without going below median?
        max_removable = max(0, before - median_size)
        actual_candidates = raw_candidates[:max_removable]

        after = before - len(actual_candidates)

        result_classes[class_name] = {
            "before": before,
            "after": after,
            "removable": len(actual_candidates),
            "candidates": actual_candidates,
        }

        total_before += before
        total_removable += len(actual_candidates)

    return {
        "threshold": threshold,
        "median_class_size": median_size,
        "total_before": total_before,
        "total_after": total_before - total_removable,
        "total_removable": total_removable,
        "classes": result_classes,
    }


def confirm_prune(gt_base: Path, preview: Dict) -> Dict:
    """
    Delete the files listed in a prune preview.

    Args:
        gt_base: Path to ground_truth directory
        preview: The preview dict returned by compute_prune_preview()

    Returns:
        Dict with per-class and total deletion counts:
        {
            "total_deleted": int,
            "total_errors": int,
            "classes": {
                "<class_name>": {"deleted": int, "errors": int},
                ...
            }
        }
    """
    total_deleted = 0
    total_errors = 0
    result_classes: Dict[str, Dict] = {}

    for class_name, class_info in preview.get("classes", {}).items():
        # Validate class name to prevent path traversal
        if '..' in class_name or '/' in class_name or '\\' in class_name:
            logger.warning(f"Invalid class name blocked: {class_name}")
            continue

        candidates = class_info.get("candidates", [])
        class_dir = gt_base / class_name
        deleted = 0
        errors = 0

        for filename in candidates:
            file_path = class_dir / filename
            # Validate path stays within ground truth directory
            try:
                resolved = file_path.resolve()
                if not resolved.is_relative_to(gt_base.resolve()):
                    logger.warning(f"Path traversal blocked: {file_path}")
                    errors += 1
                    continue
            except (ValueError, OSError):
                logger.warning(f"Invalid path: {file_path}")
                errors += 1
                continue
            try:
                if file_path.exists():
                    file_path.unlink()
                    deleted += 1
                else:
                    # File already gone (e.g. manually deleted between preview and confirm)
                    errors += 1
            except Exception as e:
                logger.warning(f"Failed to delete {file_path}: {e}")
                errors += 1

        # Rebuild hash cache for remaining files
        if deleted > 0 and class_dir.is_dir():
            cache = HashCache(class_dir)
            cache.scan_and_update()

        result_classes[class_name] = {"deleted": deleted, "errors": errors}
        total_deleted += deleted
        total_errors += errors

    logger.info(
        f"Ground-truth prune {gt_base}: deleted={total_deleted}, errors={total_errors}"
    )

    return {
        "total_deleted": total_deleted,
        "total_errors": total_errors,
        "classes": result_classes,
    }
