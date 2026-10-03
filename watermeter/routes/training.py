"""Training and benchmark routes."""

import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from ..training_manager import get_training_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# --- Training data upload constants ---
MAX_ZIP_BYTES = 200 * 1024 * 1024  # 200 MB upload limit
DIGIT_CLASSES = set(str(i) for i in range(10)) | {"NAN", "NaN", "nan"}
ARROW_CLASSES = set(f"{i}.{j}" for i in range(10) for j in range(10))
DIGIT_PREFIX_RE = re.compile(r"^(NaN|[0-9])_")
ARROW_PREFIX_RE = re.compile(r"^([0-9]\.[0-9])_")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}


class TrainingConfig(BaseModel):
    """Request model for training configuration."""

    model_type: str  # "digits" or "arrows"
    architecture: str  # e.g., "resnext50_32x4d"
    resolution: int  # e.g., 128
    seeds: List[int]  # e.g., [42, 67, 69]
    epochs: int = 20
    batch_size: int = 16
    step_size: float = 1.0  # For arrows only
    training_mode: str = "discrete"  # "discrete" or "continuous"
    notes: str = ""
    auto_benchmark: bool = True
    learning_rate: float = 3e-4
    architecture_display: str = ""  # Human-readable name (e.g., "ResNet-18")

    @field_validator("training_mode")
    @classmethod
    def validate_training_mode(cls, v):
        if v not in ("discrete", "continuous"):
            raise ValueError("training_mode must be 'discrete' or 'continuous'")
        return v

    @field_validator("learning_rate")
    @classmethod
    def validate_learning_rate(cls, v):
        if v <= 0:
            raise ValueError("learning_rate must be positive")
        if v > 1.0:
            raise ValueError("learning_rate must be <= 1.0")
        return v


@router.get(
    "/api/training/status",
    tags=["Training"],
    summary="Get training status",
    description="Get the status of active training job, benchmark job, and training queue",
)
async def get_training_status():
    """Get the status of the active training job, benchmark, and queue."""
    training_mgr = get_training_manager()
    training_status = training_mgr.get_training_status()
    benchmark_status = training_mgr.get_benchmark_status()
    queue = training_mgr.get_queue()

    return JSONResponse(
        {
            "training": training_status,
            "benchmark": benchmark_status,
            "queue": queue,
            "auto_benchmark_pending": len(training_mgr.auto_benchmark_pending),
        }
    )


@router.post(
    "/api/training/start",
    tags=["Training"],
    summary="Start training job",
    description="Start a new model training job or queue it if one is already running",
)
async def start_training(config: TrainingConfig):
    """Start a new training job or queue it if one is already running."""
    try:
        training_mgr = get_training_manager()
        result = training_mgr.start_training(config.dict())

        return JSONResponse(
            {
                "success": True,
                "job_id": result["job_id"],
                "queued": result["queued"],
                "queue_position": result["queue_position"],
                "message": "Training queued" if result["queued"] else "Training started successfully",
            }
        )

    except Exception as e:
        logger.error(f"Error starting training: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.post(
    "/api/training/cancel",
    tags=["Training"],
    summary="Cancel training job",
    description="Cancel a running training job and optionally clear the training queue",
)
async def cancel_training(job_id: str, clear_queue: bool = True):
    """Cancel a running training job and optionally clear the queue."""
    try:
        training_mgr = get_training_manager()
        success = training_mgr.cancel_training(job_id, clear_queue=clear_queue)

        if success:
            return JSONResponse(
                {
                    "success": True,
                    "message": "Training cancellation requested" + (" (queue cleared)" if clear_queue else ""),
                }
            )
        else:
            return JSONResponse(
                {"success": False, "message": "Training job not found or already completed"}, status_code=404
            )

    except Exception as e:
        logger.error(f"Error cancelling training: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.delete(
    "/api/training/queue/{index}",
    tags=["Training"],
    summary="Remove item from queue",
    description="Remove a specific item from the training queue by index",
)
async def remove_from_queue(index: int):
    """Remove a specific item from the training queue."""
    try:
        training_mgr = get_training_manager()
        success = training_mgr.remove_from_queue(index)
        if success:
            return JSONResponse({"success": True, "message": "Removed from queue"})
        else:
            return JSONResponse({"success": False, "message": "Invalid queue index"}, status_code=404)
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@router.delete(
    "/api/training/queue",
    tags=["Training"],
    summary="Clear training queue",
    description="Clear the entire training queue (removes all queued jobs)",
)
async def clear_queue():
    """Clear the entire training queue."""
    try:
        training_mgr = get_training_manager()
        count = training_mgr.clear_queue()
        return JSONResponse({"success": True, "message": f"Removed {count} items from queue"})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@router.post(
    "/api/benchmark/cancel",
    tags=["Benchmarking"],
    summary="Cancel benchmark job",
    description="Cancel a running benchmark job",
)
async def cancel_benchmark(job_id: str):
    """Cancel a running benchmark job."""
    try:
        training_mgr = get_training_manager()
        success = training_mgr.cancel_benchmark(job_id)

        if success:
            return JSONResponse({"success": True, "message": "Benchmark cancellation requested"})
        else:
            return JSONResponse(
                {"success": False, "message": "Benchmark job not found or already completed"}, status_code=404
            )

    except Exception as e:
        logger.error(f"Error cancelling benchmark: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.get(
    "/api/training/logs/{job_id}",
    tags=["Training"],
    summary="Get training logs",
    description="Get logs for a specific training job by job ID",
)
async def get_training_logs(job_id: str):
    """Get logs for a specific training job."""
    try:
        training_mgr = get_training_manager()
        logs = training_mgr.get_job_logs(job_id)

        return JSONResponse({"success": True, "logs": logs})

    except Exception as e:
        logger.error(f"Error getting training logs: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


@router.get(
    "/api/training/progress/{job_id}",
    tags=["Training"],
    summary="Get training progress",
    description="Get progress information for a specific training job by job ID",
)
async def get_training_progress(job_id: str):
    """Get progress for a specific training job."""
    try:
        training_mgr = get_training_manager()
        status = training_mgr.get_training_status()

        if status and status["job_id"] == job_id:
            return JSONResponse({"success": True, "progress": status["progress"]})
        else:
            return JSONResponse({"success": False, "message": "Training job not found"}, status_code=404)

    except Exception as e:
        logger.error(f"Error getting training progress: {e}")
        return JSONResponse({"success": False, "message": f"Error: {str(e)}"}, status_code=500)


# --- Training data ZIP upload ---


def _detect_and_sort(images: list, data_type: str) -> tuple:
    """Detect ZIP structure and return (format_name, {class_name: [paths]}).

    Tries subdirectory-based format first (standard), falls back to prefix-based
    (jomjol community format).

    Args:
        images: List of Path objects pointing to extracted image files.
        data_type: "digits" or "arrows".

    Returns:
        Tuple of (format_name, dict mapping class name to list of image Paths).

    Raises:
        HTTPException(400) if neither format is detected.
    """
    valid_classes = DIGIT_CLASSES if data_type == "digits" else ARROW_CLASSES

    # --- Try Format 1: subdirectory-based ---
    # Group images by their immediate parent directory name
    by_parent: dict[str, list[Path]] = {}
    for img in images:
        parent_name = img.parent.name
        by_parent.setdefault(parent_name, []).append(img)

    # Check if parent directory names match expected class names
    matching_parents = set(by_parent.keys()) & valid_classes
    if len(matching_parents) >= 3:
        result: dict[str, list[Path]] = {}
        for parent_name, imgs in by_parent.items():
            # Normalize NaN variants to NAN for digits
            if data_type == "digits" and parent_name.lower() == "nan":
                normalized = "NAN"
            else:
                normalized = parent_name
            if normalized in valid_classes:
                result.setdefault(normalized, []).extend(imgs)
        if result:
            return ("subdirectory", result)

    # --- Try Format 2: prefix-based ---
    prefix_re = DIGIT_PREFIX_RE if data_type == "digits" else ARROW_PREFIX_RE
    result = {}
    for img in images:
        match = prefix_re.match(img.name)
        if match:
            class_name = match.group(1)
            # Normalize NaN -> NAN for digits
            if data_type == "digits" and class_name == "NaN":
                class_name = "NAN"
            result.setdefault(class_name, []).append(img)

    if result:
        return ("prefix", result)

    raise HTTPException(
        status_code=400,
        detail=(
            "Could not detect data format in ZIP. Expected either: "
            "subdirectories per class (0/, 1/, ..., NAN/) or "
            "filename prefixes (3_img.jpg, NaN_img.jpg, 5.7_img.jpg)."
        ),
    )


@router.post(
    "/api/training-data/upload",
    tags=["Training Data"],
    summary="Upload training data ZIP",
    description=(
        "Upload a ZIP file containing training images. Supports two formats: "
        "subdirectory-based (class dirs with images inside) and prefix-based "
        "(flat files with class prefix in filename, e.g. 3_img.jpg). "
        "Images are sorted into ground_truth/{class}/ directories."
    ),
)
async def upload_training_data(
    file: UploadFile = File(..., description="ZIP file containing training images"),
    type: str = Form(..., description="Data type: 'digits' or 'arrows'"),
):
    """Upload and sort a ZIP of training images into ground_truth directories."""
    # Validate type parameter
    if type not in ("digits", "arrows"):
        raise HTTPException(status_code=400, detail="type must be 'digits' or 'arrows'")

    # Validate file extension
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP files are supported")

    ground_truth_dir = Path(f"/training/{type}/ground_truth")
    ground_truth_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        tmp_zip = tmp_path / "upload.zip"

        # Save uploaded file to temp directory
        try:
            content = await file.read(MAX_ZIP_BYTES + 1)
            if not content:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            if len(content) > MAX_ZIP_BYTES:
                raise HTTPException(status_code=413, detail=f"ZIP file too large (max {MAX_ZIP_BYTES // (1024*1024)} MB)")
            with open(tmp_zip, "wb") as f:
                f.write(content)
        finally:
            await file.close()

        # Validate it is a real ZIP file
        if not zipfile.is_zipfile(tmp_zip):
            raise HTTPException(status_code=400, detail="Invalid ZIP file")

        # Extract
        extract_dir = tmp_path / "extracted"
        try:
            with zipfile.ZipFile(tmp_zip, "r") as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Corrupt ZIP file")

        # Collect all image files recursively
        all_images = [
            p
            for p in extract_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
        ]

        if not all_images:
            raise HTTPException(status_code=400, detail="No image files found in ZIP")

        # Detect format and sort images by class
        format_type, sorted_images = _detect_and_sort(all_images, type)

        # Copy sorted images to ground_truth directory
        imported = 0
        for class_name, image_paths in sorted_images.items():
            class_dir = ground_truth_dir / class_name
            class_dir.mkdir(parents=True, exist_ok=True)
            for img_path in image_paths:
                dest = class_dir / img_path.name
                # Avoid overwriting existing files — add _imported suffix
                if dest.exists():
                    stem = img_path.stem
                    suffix = img_path.suffix
                    dest = class_dir / f"{stem}_imported{suffix}"
                    # If even that exists, add a counter
                    counter = 2
                    while dest.exists():
                        dest = class_dir / f"{stem}_imported_{counter}{suffix}"
                        counter += 1
                shutil.copy2(img_path, dest)
                imported += 1

        logger.info(
            "Uploaded %d images into %d classes (%s format) for %s",
            imported,
            len(sorted_images),
            format_type,
            type,
        )

        return {
            "message": (
                f"Imported {imported} images into {len(sorted_images)} classes "
                f"({format_type} format)"
            ),
            "format": format_type,
            "images": imported,
            "classes": len(sorted_images),
        }
