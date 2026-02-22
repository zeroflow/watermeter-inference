"""Model management routes and training data stats."""

import logging
from pathlib import Path

import timm
import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import watermeter_service
from ..inference import get_inference_service
from ..mislabel_detector import scan_mislabeled, confirm_mislabeled
from ..model_manager import get_model_manager
from ..image_hash import purge_duplicates, compute_prune_preview, confirm_prune
from ..training_manager import get_training_manager

logger = logging.getLogger(__name__)

router = APIRouter()


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
    models = timm.list_models(f"*{q}*")
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
_prune_previews: dict = {}


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
_mislabel_scans: dict = {}


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
