"""Label management routes: next image, submit label, delete image."""

import base64
import logging
from pathlib import Path
import random
import shutil

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import watermeter_service
from ..app import safe_subpath

logger = logging.getLogger(__name__)

router = APIRouter()


class LabelSubmission(BaseModel):
    filename: str
    model_type: str
    label: str


class DeleteSubmission(BaseModel):
    filename: str
    model_type: str


@router.get(
    "/api/label/next-image",
    tags=["Labeling"],
    summary="Get next unlabeled image",
    description="Get the next unlabeled image from input folders, prioritizing digits over arrows"
)
async def get_next_unlabeled_image():
    """Get the next unlabeled image, prioritizing digits over arrows."""
    service = watermeter_service.get_service()
    training_path = Path(service.config.get('low_confidence', {}).get('save_path', '/training'))

    # Collect all unlabeled images, excluding _next.jpg helper images
    digits_images = [p for p in (training_path / 'digits' / 'input').glob('*.jpg')
                     if not p.name.endswith('_next.jpg')]
    arrows_images = [p for p in (training_path / 'arrows' / 'input').glob('*.jpg')
                     if not p.name.endswith('_next.jpg')]

    # Prioritize digits, then arrows; randomize within each category
    if digits_images:
        random.shuffle(digits_images)
        image_path = digits_images[0]
        model_type = 'digits'
    elif arrows_images:
        random.shuffle(arrows_images)
        image_path = arrows_images[0]
        model_type = 'arrows'
    else:
        return JSONResponse({
            "has_images": False,
            "message": "No unlabeled images available"
        })

    # Read and encode image
    image_data = image_path.read_bytes()
    image_base64 = base64.b64encode(image_data).decode('utf-8')

    # Check for corresponding _next.jpg helper image
    next_image_base64 = None
    next_image_path = image_path.with_name(image_path.stem + '_next.jpg')
    if next_image_path.exists():
        next_image_data = next_image_path.read_bytes()
        next_image_base64 = base64.b64encode(next_image_data).decode('utf-8')

    # Count remaining images
    remaining_digits = len(digits_images)
    remaining_arrows = len(arrows_images)

    response_data = {
        "has_images": True,
        "filename": image_path.name,
        "model_type": model_type,
        "image_base64": image_base64,
        "remaining": {
            "digits": remaining_digits,
            "arrows": remaining_arrows,
            "total": remaining_digits + remaining_arrows
        }
    }

    if next_image_base64:
        response_data["next_image_base64"] = next_image_base64

    return JSONResponse(response_data)


@router.post(
    "/api/label/submit",
    tags=["Labeling"],
    summary="Submit label",
    description="Submit a label for an image and move it to the appropriate ground truth folder"
)
async def submit_label(submission: LabelSubmission):
    """Submit a label and move the image to the ground truth folder."""
    try:
        service = watermeter_service.get_service()
        training_path = Path(service.config.get('low_confidence', {}).get('save_path', '/training'))

        # Source path (validated against traversal)
        source_path = safe_subpath(training_path, submission.model_type, 'input', submission.filename)

        if not source_path.exists():
            return JSONResponse({
                "success": False,
                "message": f"Image not found: {submission.filename}"
            }, status_code=404)

        # Validate label
        if submission.model_type == 'digits':
            # Valid labels: 0-9, NAN
            valid_labels = [str(i) for i in range(10)] + ['NAN']
            if submission.label not in valid_labels:
                return JSONResponse({
                    "success": False,
                    "message": f"Invalid label for digits: {submission.label}. Must be 0-9 or NAN"
                }, status_code=400)
            label_folder = submission.label

        elif submission.model_type == 'arrows':
            # Valid labels: decimal 0.0 to 9.9 (e.g., "1.2", "6.9", "3.3")
            try:
                # Try parsing as float (decimal format)
                label_value = float(submission.label)
                if label_value < 0.0 or label_value > 9.9:
                    raise ValueError("Out of range")
                # Format to one decimal place
                label_folder = f"{label_value:.1f}"
            except ValueError:
                # Fallback: try old integer format (0-99) for backward compatibility
                try:
                    value = int(submission.label)
                    if value < 0 or value > 99:
                        raise ValueError("Out of range")
                    # Convert to decimal format: 12 -> 1.2
                    label_value = value / 10.0
                    label_folder = f"{label_value:.1f}"
                except ValueError:
                    return JSONResponse({
                        "success": False,
                        "message": f"Invalid label for arrows: {submission.label}. Must be 0.0-9.9 (e.g., 1.2, 6.9)"
                    }, status_code=400)
        else:
            return JSONResponse({
                "success": False,
                "message": f"Invalid model type: {submission.model_type}"
            }, status_code=400)

        # Destination path
        dest_dir = training_path / submission.model_type / 'ground_truth' / label_folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / submission.filename

        # Move file
        shutil.move(str(source_path), str(dest_path))

        logger.info(f"Labeled image moved: {source_path} -> {dest_path}")

        # Delete corresponding _next.jpg helper image if it exists
        next_image_path = source_path.with_name(source_path.stem + '_next.jpg')
        if next_image_path.exists():
            next_image_path.unlink()
            logger.info(f"Deleted helper image: {next_image_path}")

        return JSONResponse({
            "success": True,
            "message": f"Image labeled as {label_folder}",
            "label": label_folder
        })

    except Exception as e:
        logger.error(f"Error submitting label: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)


@router.post(
    "/api/label/delete",
    tags=["Labeling"],
    summary="Delete unusable image",
    description="Delete an image that is unusable or garbage (not suitable for training)"
)
async def delete_image(submission: DeleteSubmission):
    """Delete an image (garbage/unusable)."""
    try:
        service = watermeter_service.get_service()
        training_path = Path(service.config.get('low_confidence', {}).get('save_path', '/training'))

        # Source path (validated against traversal)
        source_path = safe_subpath(training_path, submission.model_type, 'input', submission.filename)

        if not source_path.exists():
            return JSONResponse({
                "success": False,
                "message": f"Image not found: {submission.filename}"
            }, status_code=404)

        # Delete file
        source_path.unlink()

        logger.info(f"Deleted garbage image: {source_path}")

        # Delete corresponding _next.jpg helper image if it exists
        next_image_path = source_path.with_name(source_path.stem + '_next.jpg')
        if next_image_path.exists():
            next_image_path.unlink()
            logger.info(f"Deleted helper image: {next_image_path}")

        return JSONResponse({
            "success": True,
            "message": f"Image deleted: {submission.filename}"
        })

    except Exception as e:
        logger.error(f"Error deleting image: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "message": f"Error: {str(e)}"
        }, status_code=500)
