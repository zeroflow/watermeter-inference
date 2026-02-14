"""Perceptual hashing for image deduplication."""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

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
