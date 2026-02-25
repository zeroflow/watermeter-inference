"""Model management routes and training data stats."""

import logging
import shutil
import threading
from pathlib import Path

import timm
import yaml
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from .. import watermeter_service
from ..image_hash import compute_prune_preview, confirm_prune, purge_duplicates
from ..inference import get_inference_service
from ..mislabel_detector import confirm_mislabeled, scan_mislabeled
from ..model_manager import get_model_manager
from ..training_manager import get_training_manager

logger = logging.getLogger(__name__)

router = APIRouter()


class _SessionStore:
    """Thread-safe dict-like store for per-model-type session state."""

    def __init__(self):
        self._data: dict = {}
        self._lock = threading.Lock()

    def __getitem__(self, key):
        with self._lock:
            return self._data[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._data[key] = value

    def __contains__(self, key):
        with self._lock:
            return key in self._data

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def pop(self, key, default=None):
        with self._lock:
            return self._data.pop(key, default)

    def clear(self):
        with self._lock:
            self._data.clear()


@router.get(
    "/api/models/architectures",
    tags=["Models"],
    summary="Search model architectures",
    description="Search available timm model architectures by name substring",
)
async def list_architectures(q: str = ""):
    """Search available timm model architectures."""
    if len(q) < 2:
        return JSONResponse([])
    words = q.lower().split()
    pattern = "*" + "*".join(words) + "*"
    models = timm.list_models(pattern)
    return JSONResponse(models[:50])


@router.get(
    "/api/models",
    tags=["Models"],
    summary="List models",
    description="List all trained models, optionally filtered by type (digits or arrows)",
)
async def list_models(model_type: str = None):
    """List all models, optionally filtered by type."""
    try:
        model_mgr = get_model_manager()

        if model_type:
            models = model_mgr.list_models(model_type)
            return JSONResponse({"success": True, "models": models})
        else:
            # Get both types
            digits_models = model_mgr.list_models("digits")
            arrows_models = model_mgr.list_models("arrows")

            # Determine active models from config
            service = watermeter_service.get_service()
            active_digits = model_mgr.get_active_model("digits", service.config)
            active_arrows = model_mgr.get_active_model("arrows", service.config)

            return JSONResponse(
                {
                    "success": True,
                    "models": {"digits": digits_models, "arrows": arrows_models},
                    "active_models": {"digits": active_digits, "arrows": active_arrows},
                }
            )

    except Exception as e:
        logger.error(f"Error listing models: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.get(
    "/api/models/{model_type}/{model_id}",
    tags=["Models"],
    summary="Get model details",
    description="Get detailed information about a specific model including metadata and performance metrics",
)
async def get_model_details(model_type: str, model_id: str):
    """Get detailed information about a specific model."""
    try:
        model_mgr = get_model_manager()
        model = model_mgr.get_model(model_type, model_id)

        if model:
            return JSONResponse({"success": True, "model": model})
        else:
            return JSONResponse({"success": False, "message": "Model not found"}, status_code=404)

    except Exception as e:
        logger.error(f"Error getting model details: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/models/{model_type}/{model_id}/activate",
    tags=["Models"],
    summary="Activate model",
    description="Activate a model as the active inference model (updates config and reloads)",
)
async def activate_model(model_type: str, model_id: str):
    """Activate a model (set it as the active model in config and reload)."""
    try:
        model_mgr = get_model_manager()
        config_path = Path("config.yaml")

        # Activate in config
        success = model_mgr.activate_model(model_type, model_id, config_path)

        if not success:
            return JSONResponse({"success": False, "message": "Failed to activate model"}, status_code=500)

        # Reload config
        with open(config_path, "r") as f:
            new_config = yaml.safe_load(f)

        # Hot-reload inference service
        inference_svc = get_inference_service()
        inference_svc.reload_models(new_config)

        # Update watermeter service config
        service = watermeter_service.get_service()
        service.config = new_config

        logger.info(f"Model activated and reloaded: {model_type}/{model_id}")

        return JSONResponse({"success": True, "message": f"Model {model_id} activated successfully"})

    except Exception as e:
        logger.error(f"Error activating model: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/models/{model_type}/{model_id}/archive",
    tags=["Models"],
    summary="Archive model",
    description="Archive a model (mark as archived in metadata, hide from active list)",
)
async def archive_model(model_type: str, model_id: str):
    """Archive a model (mark as archived in metadata)."""
    try:
        model_mgr = get_model_manager()
        success = model_mgr.archive_model(model_type, model_id)

        if success:
            return JSONResponse({"success": True, "message": f"Model {model_id} archived successfully"})
        else:
            return JSONResponse({"success": False, "message": "Model not found"}, status_code=404)

    except Exception as e:
        logger.error(f"Error archiving model: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.delete(
    "/api/models/{model_type}/{model_id}",
    tags=["Models"],
    summary="Delete model",
    description="Permanently delete a model and all its associated files",
)
async def delete_model(model_type: str, model_id: str):
    """Delete a model and all its files."""
    try:
        model_mgr = get_model_manager()
        success = model_mgr.delete_model(model_type, model_id)

        if success:
            return JSONResponse({"success": True, "message": f"Model {model_id} deleted successfully"})
        else:
            return JSONResponse({"success": False, "message": "Model not found"}, status_code=404)

    except Exception as e:
        logger.error(f"Error deleting model: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.get(
    "/api/models/{model_type}/{model_id}/logs",
    tags=["Models"],
    summary="Get model training logs",
    description="Get persisted training logs for a model (successful or failed)",
)
async def get_model_logs(model_type: str, model_id: str):
    """Get persisted training logs for a model (successful or failed)."""
    try:
        model_mgr = get_model_manager()
        model_mgr._validate_model_id(model_id)
        model_dir = model_mgr._get_model_types_dir(model_type) / model_id
        log_file = model_dir / "training.log"

        if not log_file.exists():
            return JSONResponse({"success": False, "message": "No training log found for this model"}, status_code=404)

        logs = log_file.read_text().split("\n")
        return JSONResponse({"success": True, "logs": logs})

    except Exception as e:
        logger.error(f"Error getting model logs: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/models/benchmark-all",
    tags=["Benchmarking"],
    summary="Start benchmark all",
    description="Start benchmarking all local models sequentially using the backend queue",
)
async def start_benchmark_all():
    """Start benchmarking all local models sequentially."""
    try:
        training_mgr = get_training_manager()
        count = training_mgr.start_benchmark_all()
        return JSONResponse({"success": True, "message": f"Benchmarking {count} models", "count": count})
    except RuntimeError as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=409)
    except Exception as e:
        logger.error(f"Error starting benchmark-all: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/models/{model_type}/{model_id}/benchmark",
    tags=["Benchmarking"],
    summary="Start benchmark",
    description="Start a benchmark job to evaluate a specific model against ground truth data",
)
async def start_benchmark(model_type: str, model_id: str):
    """Start a benchmark job for a specific model."""
    try:
        training_mgr = get_training_manager()
        job_id = training_mgr.start_benchmark(model_type, model_id)

        return JSONResponse({"success": True, "job_id": job_id, "message": "Benchmark started successfully"})

    except RuntimeError as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=409)
    except Exception as e:
        logger.error(f"Error starting benchmark: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.get(
    "/api/training-data/stats",
    tags=["Training Data"],
    summary="Get training data statistics",
    description="Get statistics about available training data (ground truth counts, unlabeled images)",
)
async def get_training_data_stats():
    """Get statistics about available training data."""
    try:
        service = watermeter_service.get_service()
        training_path = Path(service.config.get("low_confidence", {}).get("save_path", "/training"))

        stats = {"digits": {}, "arrows": {}}

        # Count images per class for digits
        digits_gt_path = training_path / "digits" / "ground_truth"
        if digits_gt_path.exists():
            for class_dir in digits_gt_path.iterdir():
                if class_dir.is_dir():
                    count = len(list(class_dir.glob("*.jpg")))
                    stats["digits"][class_dir.name] = count

        # Count images per class for arrows
        arrows_gt_path = training_path / "arrows" / "ground_truth"
        if arrows_gt_path.exists():
            for class_dir in arrows_gt_path.iterdir():
                if class_dir.is_dir():
                    count = len(list(class_dir.glob("*.jpg")))
                    stats["arrows"][class_dir.name] = count

        # Count unlabeled images
        digits_input = training_path / "digits" / "input"
        arrows_input = training_path / "arrows" / "input"

        unlabeled = {
            "digits": len(list(digits_input.glob("*.jpg"))) if digits_input.exists() else 0,
            "arrows": len(list(arrows_input.glob("*.jpg"))) if arrows_input.exists() else 0,
        }

        return JSONResponse({"success": True, "ground_truth": stats, "unlabeled": unlabeled})

    except Exception as e:
        logger.error(f"Error getting training data stats: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/training-data/dedup",
    tags=["Training Data"],
    summary="Deduplicate training data",
    description="Purge near-duplicate images from input folders using perceptual hashing",
)
async def dedup_training_data():
    """Purge near-duplicate images from input folders."""
    try:
        service = watermeter_service.get_service()
        config = service.config.get("low_confidence", {})
        training_path = Path(config.get("save_path", "/training"))
        threshold = config.get("dedup_threshold", 10)
        scope = config.get("dedup_scope", "input+ground_truth")

        results = {}
        for model_type in ("digits", "arrows"):
            input_dir = training_path / model_type / "input"
            gt_dirs = None
            if scope == "input+ground_truth":
                gt_base = training_path / model_type / "ground_truth"
                if gt_base.is_dir():
                    gt_dirs = [d for d in gt_base.iterdir() if d.is_dir()]

            results[model_type] = purge_duplicates(input_dir, threshold, gt_dirs)

        return JSONResponse({"success": True, "results": results})

    except Exception as e:
        logger.error(f"Error deduplicating training data: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


# In-memory storage for prune previews (keyed by model type)
_prune_previews = _SessionStore()


@router.post(
    "/api/training-data/prune/preview",
    tags=["Training Data"],
    summary="Preview ground truth pruning",
    description="Scan ground truth for near-duplicate images and return a preview of what would be removed",
)
async def prune_preview(request: dict):
    """
    Scan ground truth for near-duplicate images and return a preview
    of what would be removed.

    Request body: {"type": "digits" | "arrows"}
    """
    try:
        model_type = request.get("type")
        if model_type not in ("digits", "arrows"):
            return JSONResponse({"success": False, "message": "type must be 'digits' or 'arrows'"}, status_code=400)

        service = watermeter_service.get_service()
        config = service.config.get("low_confidence", {})
        training_path = Path(config.get("save_path", "/training"))
        threshold = config.get("dedup_threshold", 10)

        gt_base = training_path / model_type / "ground_truth"
        preview = compute_prune_preview(gt_base, threshold)

        # Store preview for confirm step
        _prune_previews[model_type] = preview

        # Build response (strip internal candidate lists for the API response)
        response_classes = {}
        for class_name, info in preview["classes"].items():
            response_classes[class_name] = {
                "before": info["before"],
                "after": info["after"],
                "removable": info["removable"],
            }

        return JSONResponse(
            {
                "success": True,
                "type": model_type,
                "threshold": preview["threshold"],
                "median_class_size": preview["median_class_size"],
                "total_before": preview["total_before"],
                "total_after": preview["total_after"],
                "total_removable": preview["total_removable"],
                "classes": response_classes,
            }
        )

    except Exception as e:
        logger.error(f"Error computing prune preview: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/training-data/prune/confirm",
    tags=["Training Data"],
    summary="Confirm ground truth pruning",
    description="Delete the files identified in the most recent prune preview",
)
async def prune_confirm(request: dict):
    """
    Delete the files identified in the most recent prune preview.

    Request body: {"type": "digits" | "arrows"}
    """
    try:
        model_type = request.get("type")
        if model_type not in ("digits", "arrows"):
            return JSONResponse({"success": False, "message": "type must be 'digits' or 'arrows'"}, status_code=400)

        preview = _prune_previews.get(model_type)
        if not preview:
            return JSONResponse(
                {"success": False, "message": f"No prune preview found for '{model_type}'. Run preview first."},
                status_code=409,
            )

        service = watermeter_service.get_service()
        config = service.config.get("low_confidence", {})
        training_path = Path(config.get("save_path", "/training"))

        gt_base = training_path / model_type / "ground_truth"
        result = confirm_prune(gt_base, preview)

        # Clear stored preview after confirm
        _prune_previews.pop(model_type, None)

        return JSONResponse(
            {
                "success": True,
                "type": model_type,
                "total_deleted": result["total_deleted"],
                "total_errors": result["total_errors"],
                "classes": result["classes"],
            }
        )

    except Exception as e:
        logger.error(f"Error confirming prune: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


# ---------------------------------------------------------------------------
# BL-03: Mislabel detection
# ---------------------------------------------------------------------------

# In-memory storage for mislabel scan results (keyed by model type)
_mislabel_scans = _SessionStore()


@router.post(
    "/api/training-data/mislabel/scan",
    tags=["Training Data"],
    summary="Scan for mislabeled images",
    description="Scan ground truth for mislabeled images using the active model. Flags images where prediction disagrees with folder label.",
)
async def mislabel_scan(request: dict):
    """
    Scan ground truth for mislabeled images using the active model.

    Runs inference on every image in ground_truth/{class}/ and flags images
    where the model prediction disagrees with the folder label.

    Request body: {"type": "digits" | "arrows"}

    Returns:
        JSON with suspects list, total_scanned, total_suspects
    """
    try:
        model_type = request.get("type")
        if model_type not in ("digits", "arrows"):
            return JSONResponse({"success": False, "message": "type must be 'digits' or 'arrows'"}, status_code=400)

        service = watermeter_service.get_service()
        config = service.config.get("low_confidence", {})
        training_path = Path(config.get("save_path", "/training"))

        result = scan_mislabeled(model_type, training_path)

        # Store scan result for the confirm step
        _mislabel_scans[model_type] = result

        return JSONResponse(
            {
                "success": True,
                "type": model_type,
                "suspects": result["suspects"],
                "total_scanned": result["total_scanned"],
                "total_suspects": result["total_suspects"],
            }
        )

    except RuntimeError as e:
        # No active model loaded
        return JSONResponse({"success": False, "message": str(e)}, status_code=409)
    except Exception as e:
        logger.error(f"Error scanning for mislabeled images: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/training-data/mislabel/confirm",
    tags=["Training Data"],
    summary="Confirm mislabel rework",
    description="Move selected suspect images from ground truth back to input for relabeling with label hints",
)
async def mislabel_confirm(request: dict):
    """
    Move selected suspect images from ground truth back to input for relabeling.

    The filename is augmented with a label hint so the labeling UI can pre-fill
    the suggested label: {original_stem}_label={class}.jpg

    Request body: {"type": "digits" | "arrows", "selected": [list of paths]}
    """
    try:
        model_type = request.get("type")
        if model_type not in ("digits", "arrows"):
            return JSONResponse({"success": False, "message": "type must be 'digits' or 'arrows'"}, status_code=400)

        selected = request.get("selected")
        if not isinstance(selected, list):
            return JSONResponse(
                {"success": False, "message": "'selected' must be a list of file paths"}, status_code=400
            )

        if not selected:
            return JSONResponse(
                {
                    "success": True,
                    "type": model_type,
                    "moved_count": 0,
                    "error_count": 0,
                    "errors": [],
                }
            )

        # Validate that selected paths were part of the last scan
        scan = _mislabel_scans.get(model_type)
        if scan is None:
            return JSONResponse(
                {"success": False, "message": f"No mislabel scan found for '{model_type}'. Run scan first."},
                status_code=409,
            )

        scan_paths = {s["path"] for s in scan["suspects"]}
        invalid_paths = [p for p in selected if p not in scan_paths]
        if invalid_paths:
            return JSONResponse(
                {"success": False, "message": f"{len(invalid_paths)} selected path(s) were not in the scan results"},
                status_code=400,
            )

        service = watermeter_service.get_service()
        config = service.config.get("low_confidence", {})
        training_path = Path(config.get("save_path", "/training"))

        result = confirm_mislabeled(model_type, training_path, selected)

        # Clear stored scan after confirm
        _mislabel_scans.pop(model_type, None)

        return JSONResponse(
            {
                "success": True,
                "type": model_type,
                "moved_count": result["moved_count"],
                "error_count": result["error_count"],
                "errors": result["errors"],
            }
        )

    except Exception as e:
        logger.error(f"Error confirming mislabel rework: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


# ---------------------------------------------------------------------------
# Training data image browsing
# ---------------------------------------------------------------------------

_VALID_MODEL_TYPES = {"digits", "arrows"}


def _validate_path_component(component: str) -> bool:
    """Return True if component is safe (no path separators, .. or .)."""
    if ".." in component:
        return False
    if component == ".":
        return False
    if "/" in component or "\\" in component:
        return False
    return True


@router.get(
    "/api/training-data/images",
    tags=["Training Data"],
    summary="List training images for a class",
    description="List JPEG filenames in ground_truth/{type}/{class_name}/ with pagination",
)
async def list_training_images(
    type: str,
    class_name: str,
    offset: int = 0,
    limit: int = 50,
):
    """List JPEG filenames in a ground truth class directory with pagination."""
    if type not in _VALID_MODEL_TYPES:
        return JSONResponse(
            {"success": False, "message": "type must be 'digits' or 'arrows'"},
            status_code=400,
        )

    if not _validate_path_component(class_name):
        return JSONResponse(
            {"success": False, "message": "Invalid class_name"},
            status_code=400,
        )

    if offset < 0:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "offset must be >= 0"},
        )
    if limit < 1 or limit > 200:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "limit must be between 1 and 200"},
        )

    try:
        service = watermeter_service.get_service()
        training_path = Path(service.config.get("low_confidence", {}).get("save_path", "/training"))
        class_dir = training_path / type / "ground_truth" / class_name

        if not class_dir.exists():
            return JSONResponse({"success": True, "filenames": [], "total": 0})

        all_files = sorted(p.name for p in class_dir.glob("*.jpg"))
        total = len(all_files)
        page = all_files[offset : offset + limit]

        return JSONResponse({"success": True, "filenames": page, "total": total})

    except Exception as e:
        logger.error(f"Error listing training images: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.get(
    "/api/training-data/image/{model_type}/{class_name}/{filename:path}",
    tags=["Training Data"],
    summary="Serve a training image",
    description="Serve a single JPEG from ground_truth/{model_type}/{class_name}/{filename}",
)
async def serve_training_image(model_type: str, class_name: str, filename: str):
    """Serve a single training image file."""
    if model_type not in _VALID_MODEL_TYPES:
        return JSONResponse(
            {"success": False, "message": "model_type must be 'digits' or 'arrows'"},
            status_code=400,
        )

    for component in (class_name, filename):
        if not _validate_path_component(component):
            return JSONResponse(
                {"success": False, "message": "Invalid path component"},
                status_code=400,
            )

    try:
        service = watermeter_service.get_service()
        training_path = Path(service.config.get("low_confidence", {}).get("save_path", "/training"))
        base_dir = training_path / model_type / "ground_truth" / class_name
        file_path = (base_dir / filename).resolve()

        # Path traversal guard
        if not file_path.is_relative_to(base_dir.resolve()):
            return JSONResponse(
                {"success": False, "message": "Invalid path"},
                status_code=400,
            )

        if not file_path.exists():
            return JSONResponse(
                {"success": False, "message": "Image not found"},
                status_code=404,
            )

        return FileResponse(str(file_path), media_type="image/jpeg")

    except Exception as e:
        logger.error(f"Error serving training image: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


# ---------------------------------------------------------------------------
# Bulk training data operations
# ---------------------------------------------------------------------------


def bulk_move_to_input_logic(training_path: Path, model_type: str, class_name: str, filenames: list[str]) -> dict:
    """Move files from ground_truth/{model_type}/{class_name}/ to input/.

    Pure logic function (no request/response handling) for testability.
    """
    from ..app import safe_subpath  # deferred to avoid circular import

    gt_dir = training_path / model_type / "ground_truth" / class_name
    input_dir = training_path / model_type / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    errors = []

    for filename in filenames:
        try:
            src = safe_subpath(gt_dir, filename)
        except ValueError:
            errors.append(f"Invalid filename (path traversal): {filename}")
            continue

        if not src.exists():
            errors.append(f"File not found: {filename}")
            continue

        dest = input_dir / src.name
        if dest.exists():
            counter = 1
            stem, suffix = src.stem, src.suffix
            while dest.exists():
                dest = input_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        try:
            shutil.move(str(src), str(dest))
            moved += 1
        except Exception as e:
            errors.append(f"Failed to move {filename}: {e}")

    return {"moved_count": moved, "error_count": len(errors), "errors": errors}


def bulk_delete_logic(training_path: Path, model_type: str, class_name: str, filenames: list[str]) -> dict:
    """Delete files from ground_truth/{model_type}/{class_name}/.

    Pure logic function (no request/response handling) for testability.
    """
    from ..app import safe_subpath  # deferred to avoid circular import

    gt_dir = training_path / model_type / "ground_truth" / class_name
    deleted = 0
    errors = []

    for filename in filenames:
        try:
            target = safe_subpath(gt_dir, filename)
        except ValueError:
            errors.append(f"Invalid filename (path traversal): {filename}")
            continue

        if not target.exists():
            errors.append(f"File not found: {filename}")
            continue

        try:
            target.unlink()
            deleted += 1
        except Exception as e:
            errors.append(f"Failed to delete {filename}: {e}")

    return {"deleted_count": deleted, "error_count": len(errors), "errors": errors}


@router.post(
    "/api/training-data/bulk-move-to-input",
    tags=["Training Data"],
    summary="Bulk move images to input queue",
)
async def bulk_move_to_input(request: dict):
    """Move selected training images back to the input queue for re-labeling."""
    try:
        model_type = request.get("type")
        if model_type not in _VALID_MODEL_TYPES:
            return JSONResponse(
                {"success": False, "message": "type must be 'digits' or 'arrows'"},
                status_code=400,
            )

        class_name = request.get("class_name")
        if not class_name or not _validate_path_component(class_name):
            return JSONResponse(
                {"success": False, "message": "Invalid or missing class_name"},
                status_code=400,
            )

        filenames = request.get("filenames")
        if not isinstance(filenames, list):
            return JSONResponse(
                {"success": False, "message": "'filenames' must be a list"},
                status_code=400,
            )

        if not all(isinstance(f, str) and f for f in filenames):
            return JSONResponse(
                {"success": False, "message": "All filenames must be non-empty strings"},
                status_code=400,
            )

        service = watermeter_service.get_service()
        training_path = Path(service.config.get("low_confidence", {}).get("save_path", "/training"))

        result = bulk_move_to_input_logic(training_path, model_type, class_name, filenames)

        return JSONResponse(
            {
                "success": True,
                "type": model_type,
                "moved_count": result["moved_count"],
                "error_count": result["error_count"],
                "errors": result["errors"],
            }
        )

    except Exception as e:
        logger.error(f"Error in bulk move to input: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/training-data/bulk-delete",
    tags=["Training Data"],
    summary="Bulk delete training images",
)
async def bulk_delete(request: dict):
    """Permanently delete selected training images."""
    try:
        model_type = request.get("type")
        if model_type not in _VALID_MODEL_TYPES:
            return JSONResponse(
                {"success": False, "message": "type must be 'digits' or 'arrows'"},
                status_code=400,
            )

        class_name = request.get("class_name")
        if not class_name or not _validate_path_component(class_name):
            return JSONResponse(
                {"success": False, "message": "Invalid or missing class_name"},
                status_code=400,
            )

        filenames = request.get("filenames")
        if not isinstance(filenames, list):
            return JSONResponse(
                {"success": False, "message": "'filenames' must be a list"},
                status_code=400,
            )

        if not all(isinstance(f, str) and f for f in filenames):
            return JSONResponse(
                {"success": False, "message": "All filenames must be non-empty strings"},
                status_code=400,
            )

        service = watermeter_service.get_service()
        training_path = Path(service.config.get("low_confidence", {}).get("save_path", "/training"))

        result = bulk_delete_logic(training_path, model_type, class_name, filenames)

        return JSONResponse(
            {
                "success": True,
                "type": model_type,
                "deleted_count": result["deleted_count"],
                "error_count": result["error_count"],
                "errors": result["errors"],
            }
        )

    except Exception as e:
        logger.error(f"Error in bulk delete: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)
