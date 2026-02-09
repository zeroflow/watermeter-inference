"""
Model Manager - Handles model metadata, storage, and lifecycle
"""
import json
import shutil
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ModelManager:
    """Manages model files and metadata."""

    def __init__(self, models_base_path: str = "/app/models"):
        """
        Initialize ModelManager.

        Args:
            models_base_path: Base directory for model storage
        """
        self.models_base_path = Path(models_base_path)
        self.models_base_path.mkdir(parents=True, exist_ok=True)

    def _get_model_types_dir(self, model_type: str) -> Path:
        """Get the directory for a specific model type (digits/arrows)."""
        if model_type not in ["digits", "arrows"]:
            raise ValueError(f"Invalid model type: {model_type}. Must be 'digits' or 'arrows'")
        return self.models_base_path / model_type

    def list_models(self, model_type: str) -> List[Dict]:
        """
        List all models of a given type.

        Args:
            model_type: "digits" or "arrows"

        Returns:
            List of model metadata dictionaries
        """
        models_dir = self._get_model_types_dir(model_type)
        if not models_dir.exists():
            return []

        models = []
        for model_dir in sorted(models_dir.iterdir()):
            if not model_dir.is_dir():
                continue

            metadata_file = model_dir / "metadata.json"
            if not metadata_file.exists():
                logger.warning(f"Model directory without metadata: {model_dir}")
                continue

            try:
                metadata = self.get_model(model_type, model_dir.name)
                if metadata:
                    models.append(metadata)
            except Exception as e:
                logger.error(f"Error reading model metadata from {model_dir}: {e}")
                continue

        # Sort by created_at descending (newest first)
        models.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        return models

    def get_model(self, model_type: str, model_id: str) -> Optional[Dict]:
        """
        Get metadata for a specific model.

        Args:
            model_type: "digits" or "arrows"
            model_id: Model directory name

        Returns:
            Model metadata dictionary or None if not found
        """
        model_dir = self._get_model_types_dir(model_type) / model_id
        metadata_file = model_dir / "metadata.json"

        if not metadata_file.exists():
            return None

        try:
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)

            # Add computed fields
            metadata['id'] = model_id
            metadata['model_type'] = model_type

            # Check if model files exist
            xml_file = model_dir / f"{model_id}.xml"
            bin_file = model_dir / f"{model_id}.bin"
            metadata['files_exist'] = xml_file.exists() and bin_file.exists()

            return metadata

        except Exception as e:
            logger.error(f"Error reading metadata from {metadata_file}: {e}")
            return None

    def save_metadata(self, model_type: str, model_id: str, metadata: Dict) -> bool:
        """
        Save model metadata.

        Args:
            model_type: "digits" or "arrows"
            model_id: Model directory name
            metadata: Metadata dictionary to save

        Returns:
            True if successful, False otherwise
        """
        model_dir = self._get_model_types_dir(model_type) / model_id
        model_dir.mkdir(parents=True, exist_ok=True)

        metadata_file = model_dir / "metadata.json"

        try:
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            logger.info(f"Saved metadata for {model_type}/{model_id}")
            return True
        except Exception as e:
            logger.error(f"Error saving metadata to {metadata_file}: {e}")
            return False

    def delete_model(self, model_type: str, model_id: str) -> bool:
        """
        Delete a model and all its files.

        Args:
            model_type: "digits" or "arrows"
            model_id: Model directory name

        Returns:
            True if successful, False otherwise
        """
        model_dir = self._get_model_types_dir(model_type) / model_id

        if not model_dir.exists():
            logger.warning(f"Model directory does not exist: {model_dir}")
            return False

        try:
            shutil.rmtree(model_dir)
            logger.info(f"Deleted model {model_type}/{model_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting model {model_dir}: {e}")
            return False

    def get_active_model(self, model_type: str, config: Dict) -> Optional[str]:
        """
        Get the currently active model ID from config.

        Args:
            model_type: "digits" or "arrows"
            config: Application config dictionary

        Returns:
            Model ID (filename without extension) or None
        """
        if model_type == "digits":
            model_path = config.get('inference', {}).get('digits_model', '')
        elif model_type == "arrows":
            model_path = config.get('inference', {}).get('arrows_model', '')
        else:
            return None

        if not model_path:
            return None

        # Extract model ID from path: /app/models/digits/model_xyz/model_xyz.xml -> model_xyz
        path = Path(model_path)
        if path.suffix == '.xml':
            return path.stem
        return None

    def activate_model(self, model_type: str, model_id: str, config_path: Path) -> bool:
        """
        Activate a model by updating the config file.

        Args:
            model_type: "digits" or "arrows"
            model_id: Model directory name
            config_path: Path to config.yaml

        Returns:
            True if successful, False otherwise
        """
        import yaml

        model_dir = self._get_model_types_dir(model_type) / model_id
        xml_file = model_dir / f"{model_id}.xml"

        if not xml_file.exists():
            logger.error(f"Model file not found: {xml_file}")
            return False

        try:
            # Read config
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)

            # Update model path
            if 'inference' not in config:
                config['inference'] = {}

            if model_type == "digits":
                config['inference']['digits_model'] = str(xml_file)
            elif model_type == "arrows":
                config['inference']['arrows_model'] = str(xml_file)
            else:
                return False

            # Write config back
            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

            logger.info(f"Activated model {model_type}/{model_id}")
            return True

        except Exception as e:
            logger.error(f"Error activating model: {e}")
            return False

    def get_model_path(self, model_type: str, model_id: str) -> Optional[Path]:
        """
        Get the XML file path for a model.

        Args:
            model_type: "digits" or "arrows"
            model_id: Model directory name

        Returns:
            Path to .xml file or None if not found
        """
        model_dir = self._get_model_types_dir(model_type) / model_id
        xml_file = model_dir / f"{model_id}.xml"

        if xml_file.exists():
            return xml_file
        return None

    def create_model_id(self, model_name: str, resolution: int, num_classes: int,
                       seed: int, timestamp: Optional[str] = None) -> str:
        """
        Create a standardized model ID.

        Args:
            model_name: Model architecture name (e.g., "resnext50_32x4d")
            resolution: Image resolution
            num_classes: Number of classes
            seed: Training seed
            timestamp: Optional timestamp (generated if not provided)

        Returns:
            Model ID string
        """
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        return f"model_{model_name}_c{num_classes}_r{resolution}_s{seed}_{timestamp}"

    def archive_model(self, model_type: str, model_id: str) -> bool:
        """
        Mark a model as archived in its metadata.

        Args:
            model_type: "digits" or "arrows"
            model_id: Model directory name

        Returns:
            True if successful, False otherwise
        """
        metadata = self.get_model(model_type, model_id)
        if not metadata:
            return False

        metadata['status'] = 'archived'
        return self.save_metadata(model_type, model_id, metadata)


# Singleton instance
_model_manager = None


def get_model_manager() -> ModelManager:
    """Get the global ModelManager instance."""
    global _model_manager
    if _model_manager is None:
        _model_manager = ModelManager()
    return _model_manager
