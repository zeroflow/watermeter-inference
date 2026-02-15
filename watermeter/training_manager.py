"""
Training Manager - Orchestrates model training and benchmarking
"""

import threading
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum
import logging

from .model_manager import get_model_manager

logger = logging.getLogger(__name__)


class JobStatus(Enum):
    """Training job status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TrainingJob:
    """Represents a single training job."""

    def __init__(self, job_id: str, config: Dict[str, Any]):
        self.job_id = job_id
        self.config = config
        self.status = JobStatus.PENDING
        self.progress = {
            "current_epoch": 0,
            "total_epochs": config.get("epochs", 20),
            "current_config": 1,
            "total_configs": 1,
            "train_loss": None,
            "val_accuracy": None,
            "message": "Initializing...",
        }
        self.logs = []
        self.error = None
        self.result = None
        self.started_at = None
        self.completed_at = None
        self.thread = None

    def add_log(self, message: str):
        """Add a log message."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.logs.append(log_entry)
        logger.info(f"Training {self.job_id}: {message}")

    def update_progress(self, **kwargs):
        """Update progress information."""
        self.progress.update(kwargs)

    def to_dict(self) -> Dict:
        """Convert job to dictionary for API responses."""
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "config": self.config,
            "progress": self.progress,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
        }


class BenchmarkJob:
    """Represents a single benchmark job."""

    def __init__(self, job_id: str, model_type: str, model_id: str):
        self.job_id = job_id
        self.model_type = model_type
        self.model_id = model_id
        self.status = JobStatus.PENDING
        self.progress = {
            "current_class": 0,
            "total_classes": 0,
            "processed_images": 0,
            "total_images": 0,
            "message": "Initializing...",
        }
        self.logs = []
        self.error = None
        self.result = None
        self.started_at = None
        self.completed_at = None
        self.thread = None

    def add_log(self, message: str):
        """Add a log message."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.logs.append(log_entry)
        logger.info(f"Benchmark {self.job_id}: {message}")

    def update_progress(self, **kwargs):
        """Update progress information."""
        self.progress.update(kwargs)

    def to_dict(self) -> Dict:
        """Convert job to dictionary for API responses."""
        return {
            "job_id": self.job_id,
            "model_type": self.model_type,
            "model_id": self.model_id,
            "status": self.status.value,
            "progress": self.progress,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "result": self.result,
        }


class TrainingManager:
    """Manages training and benchmark jobs."""

    def __init__(self):
        self.active_training_job: Optional[TrainingJob] = None
        self.active_benchmark_job: Optional[BenchmarkJob] = None
        self.job_history: Dict[str, Any] = {}
        self.model_manager = get_model_manager()
        self._training_cancel_flag = threading.Event()
        self._benchmark_cancel_flag = threading.Event()
        self.training_queue: List[Dict[str, Any]] = []
        self._auto_benchmark_pending: List[tuple] = []  # [(model_type, model_id), ...]
        self._queue_lock = threading.Lock()

    def start_training(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Start a training job, or queue it if one is already running.

        Returns:
            Dict with job_id, queued, queue_position
        """
        if self.active_training_job is not None and self.active_training_job.status == JobStatus.RUNNING:
            with self._queue_lock:
                self.training_queue.append(config)
                position = len(self.training_queue)
            logger.info(f"Training queued at position {position}")
            return {"job_id": None, "queued": True, "queue_position": position}

        job_id = self._start_training_now(config)
        return {"job_id": job_id, "queued": False, "queue_position": 0}

    def _start_training_now(self, config: Dict[str, Any]) -> str:
        """Start a training job immediately."""
        job_id = f"train_{config['model_type']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        job = TrainingJob(job_id, config)
        self.active_training_job = job
        self.job_history[job_id] = job

        self._training_cancel_flag.clear()
        job.thread = threading.Thread(target=self._run_training, args=(job,), daemon=True)
        job.thread.start()

        job.add_log("Training job started")
        return job_id

    def start_benchmark(self, model_type: str, model_id: str) -> str:
        """
        Start a benchmark job.

        Args:
            model_type: "digits" or "arrows"
            model_id: Model directory name

        Returns:
            Job ID

        Raises:
            RuntimeError: If benchmark is already running
        """
        if self.active_benchmark_job is not None and self.active_benchmark_job.status == JobStatus.RUNNING:
            raise RuntimeError("Benchmark is already running")

        # Generate job ID
        job_id = f"bench_{model_type}_{model_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Create job
        job = BenchmarkJob(job_id, model_type, model_id)
        self.active_benchmark_job = job
        self.job_history[job_id] = job

        # Start benchmark in background thread
        self._benchmark_cancel_flag.clear()
        job.thread = threading.Thread(target=self._run_benchmark, args=(job,), daemon=True)
        job.thread.start()

        job.add_log("Benchmark job started")
        return job_id

    def cancel_training(self, job_id: str, clear_queue: bool = True) -> bool:
        """
        Cancel a running training job and optionally clear the queue.
        """
        if self.active_training_job is None or self.active_training_job.job_id != job_id:
            return False

        if clear_queue:
            with self._queue_lock:
                self.training_queue.clear()
            self._auto_benchmark_pending.clear()

        self._training_cancel_flag.set()
        self.active_training_job.add_log("Cancellation requested")
        return True

    def cancel_benchmark(self, job_id: str) -> bool:
        """
        Cancel a running benchmark job.

        Args:
            job_id: Job ID to cancel

        Returns:
            True if successful, False otherwise
        """
        if self.active_benchmark_job is None or self.active_benchmark_job.job_id != job_id:
            return False

        self._benchmark_cancel_flag.set()
        self.active_benchmark_job.add_log("Cancellation requested")
        return True

    def get_training_status(self) -> Optional[Dict]:
        """Get the status of the active training job."""
        if self.active_training_job is None:
            return None
        return self.active_training_job.to_dict()

    def get_benchmark_status(self) -> Optional[Dict]:
        """Get the status of the active benchmark job."""
        if self.active_benchmark_job is None:
            return None
        return self.active_benchmark_job.to_dict()

    def get_queue(self) -> List[Dict]:
        """Get the current training queue."""
        with self._queue_lock:
            return list(self.training_queue)

    def remove_from_queue(self, index: int) -> bool:
        """Remove an item from the queue by index."""
        with self._queue_lock:
            if 0 <= index < len(self.training_queue):
                self.training_queue.pop(index)
                return True
            return False

    def clear_queue(self) -> int:
        """Clear the entire training queue. Returns number of items removed."""
        with self._queue_lock:
            count = len(self.training_queue)
            self.training_queue.clear()
            return count

    def get_job_logs(self, job_id: str) -> List[str]:
        """Get logs for a specific job."""
        job = self.job_history.get(job_id)
        if job is None:
            return []
        return job.logs

    def _run_training(self, job: TrainingJob):
        """
        Execute training job.
        This is run in a background thread.
        """
        try:
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now()
            job.add_log("Starting training execution")

            config = job.config
            model_type = config["model_type"]
            architecture = config["architecture"]
            resolution = config["resolution"]
            seeds = config.get("seeds", [42])
            epochs = config.get("epochs", 20)
            batch_size = config.get("batch_size", 16)

            # For arrows, we may have step_size
            step_size = config.get("step_size", 1.0) if model_type == "arrows" else None

            # Training mode: "discrete" (classification) or "continuous" (regression)
            training_mode = config.get("training_mode", "discrete")

            # Calculate total configurations
            total_configs = len(seeds)
            job.update_progress(total_configs=total_configs)

            results = []

            # Train for each seed
            for config_idx, seed in enumerate(seeds, 1):
                if self._training_cancel_flag.is_set():
                    job.add_log("Training cancelled by user")
                    job.status = JobStatus.CANCELLED
                    return

                job.update_progress(
                    current_config=config_idx, message=f"Training config {config_idx}/{total_configs} (seed={seed})"
                )
                job.add_log(f"Starting training for seed {seed}")

                # Run training (import and call train function)
                result = self._execute_training(
                    job=job,
                    model_type=model_type,
                    architecture=architecture,
                    resolution=resolution,
                    seed=seed,
                    epochs=epochs,
                    batch_size=batch_size,
                    step_size=step_size,
                    training_mode=training_mode,
                )

                if result:
                    metric_name = "within-half" if config.get("training_mode") == "continuous" else "accuracy"
                    results.append(result)
                    job.add_log(f"Completed training for seed {seed}: {result['best_val_acc']:.2f}% {metric_name}")
                else:
                    job.add_log(f"Training failed for seed {seed}")

            # Check if any training succeeded
            if results:
                job.status = JobStatus.COMPLETED
                job.result = results
                job.add_log(f"Training completed successfully: {len(results)}/{total_configs} models trained")
            else:
                job.status = JobStatus.FAILED
                job.error = "All training configurations failed"
                job.add_log(f"Training failed: 0/{total_configs} models trained successfully")

            job.completed_at = datetime.now()

        except Exception as e:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now()
            job.error = str(e)
            job.add_log(f"Training failed with error: {str(e)}")
            logger.error(f"Training job {job.job_id} failed: {e}")
            logger.error(traceback.format_exc())

        finally:
            # Persist logs to disk
            self._persist_training_logs(job)

            # On failure, persist failure metadata so it appears in the model list
            if job.status == JobStatus.FAILED:
                self._persist_failure_metadata(job)

            # Collect model IDs for auto-benchmark if requested
            if job.status == JobStatus.COMPLETED and job.config.get("auto_benchmark", False):
                for result in job.result or []:
                    model_id = result.get("model_id")
                    if model_id:
                        self._auto_benchmark_pending.append((job.config["model_type"], model_id))

            # Update HA training stats (training may have consumed ground truth images)
            try:
                from . import watermeter_service
                svc = watermeter_service.get_service()
                svc.publish_training_stats()
            except Exception:
                pass  # Service might not be initialized; periodic loop will catch up

            # Start next queued job, or auto-benchmarks if queue is empty
            self._process_next_in_queue()

    def _process_next_in_queue(self):
        """Start the next queued training job, or run auto-benchmarks if queue is empty."""
        while True:
            with self._queue_lock:
                if not self.training_queue:
                    # Queue empty - start auto-benchmarks if any pending
                    if self._auto_benchmark_pending:
                        self._run_auto_benchmarks()
                    return
                next_config = self.training_queue.pop(0)

            logger.info("Starting next queued training job")
            try:
                self._start_training_now(next_config)
                return  # Successfully started
            except Exception as e:
                logger.error(f"Failed to start queued training: {e}")
                # Loop to try next item in queue

    def _run_auto_benchmarks(self):
        """Run benchmarks for all models in the auto-benchmark pending list."""
        pending = list(self._auto_benchmark_pending)
        self._auto_benchmark_pending.clear()

        if not pending:
            return

        def run_sequential():
            for model_type, model_id in pending:
                if self._benchmark_cancel_flag.is_set():
                    break
                try:
                    logger.info(f"Auto-benchmarking {model_type}/{model_id}")
                    job_id = f"bench_{model_type}_{model_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    job = BenchmarkJob(job_id, model_type, model_id)
                    self.active_benchmark_job = job
                    self.job_history[job_id] = job
                    self._benchmark_cancel_flag.clear()
                    self._run_benchmark(job)
                except Exception as e:
                    logger.error(f"Auto-benchmark failed for {model_type}/{model_id}: {e}")

        thread = threading.Thread(target=run_sequential, daemon=True)
        thread.start()

    def _execute_training(
        self,
        job: TrainingJob,
        model_type: str,
        architecture: str,
        resolution: int,
        seed: int,
        epochs: int,
        batch_size: int,
        step_size: Optional[float],
        training_mode: str = "discrete",
    ) -> Optional[Dict]:
        """
        Execute the actual training process using PyTorch and timm.

        Progress updates are sent via job.update_progress() during training.
        """
        import shutil

        import torch
        import timm
        from torchvision.datasets import ImageFolder
        from torch.utils.data import DataLoader, Subset

        from .training_core import (
            set_all_seeds,
            worker_init_fn,
            create_transforms,
            stratified_split,
            compute_class_weights,
            export_to_openvino,
        )

        try:
            # Set seeds for reproducibility
            set_all_seeds(seed)
            job.add_log(f"Random seed set to {seed}")

            # Setup device
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            job.add_log(f"Using device: {device}")

            # Determine paths and classes based on model type
            if model_type == "digits":
                dataset_dir = Path("/training/digits/ground_truth")
                num_classes = 11  # 0-9 + NAN
                model_filename = f"model_digits_{architecture}_r{resolution}_s{seed}"
            elif model_type == "arrows":
                ground_truth_dir = Path("/training/arrows/ground_truth")

                if training_mode == "continuous":
                    # Regression mode: use all ground truth classes directly
                    dataset_dir = ground_truth_dir  # No temp dataset needed
                    model_filename = f"model_arrows_{architecture}_continuous_r{resolution}_s{seed}"
                    job.add_log("Continuous (regression) mode — using all ground truth classes")
                else:
                    # Discrete (classification) mode: subsample to step_size
                    dataset_dir = Path("/training/arrows/dataset_temp")
                    step = step_size or 1.0
                    num_classes = int(10 / step)
                    model_filename = f"model_arrows_{architecture}_c{num_classes}_r{resolution}_s{seed}"
                    job.add_log(f"Discrete (classification) mode — step={step} ({num_classes} classes)")
                    self._create_arrow_dataset(ground_truth_dir, dataset_dir, step, job)
            else:
                raise ValueError(f"Unknown model type: {model_type}")

            job.add_log(f"Dataset directory: {dataset_dir}")

            # Verify dataset exists
            if not dataset_dir.exists():
                raise ValueError(f"Dataset directory not found: {dataset_dir}")

            # Create transforms
            train_transform, val_transform = create_transforms(resolution)

            # Load dataset and create stratified split — branch on regression vs classification
            if model_type == "arrows" and training_mode == "continuous":
                # Regression dataset
                from .training_core import RegressionArrowDataset, stratified_split_regression

                job.add_log("Loading regression dataset...")
                train_dataset = RegressionArrowDataset(dataset_dir, transform=train_transform)
                val_dataset = RegressionArrowDataset(dataset_dir, transform=val_transform)

                train_idx, val_idx = stratified_split_regression(train_dataset)

                train_ds = Subset(train_dataset, train_idx)
                val_ds = Subset(val_dataset, val_idx)

                num_classes_actual = 1  # regression
                class_names = None

                job.add_log(f"Train samples: {len(train_ds)}, Val samples: {len(val_ds)}")
                job.add_log("Regression target range: 0.0 - 1.0 (dial position / 10)")
            else:
                # Classification dataset (existing code for both digits and discrete arrows)
                job.add_log("Loading dataset and creating train/val split...")
                dataset = ImageFolder(str(dataset_dir))
                train_idx, val_idx = stratified_split(dataset)

                train_dataset = ImageFolder(str(dataset_dir), transform=train_transform)
                train_ds = Subset(train_dataset, train_idx)

                val_dataset = ImageFolder(str(dataset_dir), transform=val_transform)
                val_ds = Subset(val_dataset, val_idx)

                num_classes_actual = len(dataset.classes)
                class_names = dataset.classes

                job.add_log(f"Train samples: {len(train_ds)}, Val samples: {len(val_ds)}")
                job.add_log(f"Classes: {num_classes_actual}")

                # Compute class weights (classification only)
                class_weights_tensor = compute_class_weights(dataset, train_idx, device)
                job.add_log("Class weights computed")

            # DataLoaders
            train_loader = DataLoader(
                train_ds, batch_size=batch_size, shuffle=True, num_workers=2, worker_init_fn=worker_init_fn
            )
            val_loader = DataLoader(val_ds, batch_size=batch_size, num_workers=2, worker_init_fn=worker_init_fn)

            # Create model
            if model_type == "arrows" and training_mode == "continuous":
                model = timm.create_model(architecture, pretrained=True, num_classes=1)
                job.add_log(f"Creating regression model: {architecture} (1 output)")
            else:
                model = timm.create_model(architecture, pretrained=True, num_classes=num_classes_actual)
                job.add_log(f"Creating classification model: {architecture} ({num_classes_actual} classes)")

            model = model.to(device)

            num_params = sum(p.numel() for p in model.parameters())
            job.add_log(f"Model parameters: {num_params:,}")

            # Optimizer and loss
            optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

            if model_type == "arrows" and training_mode == "continuous":
                criterion = torch.nn.MSELoss()
            else:
                criterion = torch.nn.CrossEntropyLoss(weight=class_weights_tensor)

            # Training loop
            job.add_log(f"Starting training for {epochs} epochs...")
            train_losses = []
            val_accs = []
            best_val_acc = 0
            best_val_loss = float("inf")
            best_val_mae = float("inf")
            best_val_rmse = float("inf")
            best_epoch = 0
            best_model_state = None

            total_start = time.time()

            for epoch in range(epochs):
                if self._training_cancel_flag.is_set():
                    job.add_log("Training cancelled by user")
                    return None

                epoch_start = time.time()

                # Training phase
                model.train()
                epoch_loss = 0
                for batch_idx, (imgs, labels) in enumerate(train_loader):
                    if self._training_cancel_flag.is_set():
                        return None

                    imgs = imgs.to(device)
                    optimizer.zero_grad()

                    if model_type == "arrows" and training_mode == "continuous":
                        labels = labels.to(device)  # float32 targets
                        outputs = model(imgs).squeeze(-1)  # (batch,) — remove trailing dim
                        loss = criterion(torch.sigmoid(outputs), labels)
                    else:
                        labels = labels.to(device)  # int64 class indices
                        outputs = model(imgs)
                        loss = criterion(outputs, labels)

                    loss.backward()
                    optimizer.step()
                    epoch_loss += loss.item()

                avg_loss = epoch_loss / len(train_loader)
                train_losses.append(avg_loss)

                # Validation phase
                model.eval()
                with torch.no_grad():
                    if model_type == "arrows" and training_mode == "continuous":
                        # Regression validation: compute MAE, RMSE, and "within-half" accuracy
                        all_preds = []
                        all_targets = []
                        for imgs, labels in val_loader:
                            imgs = imgs.to(device)
                            labels = labels.to(device)
                            outputs = model(imgs).squeeze(-1)
                            preds = torch.sigmoid(outputs)
                            all_preds.append(preds)
                            all_targets.append(labels)

                        all_preds = torch.cat(all_preds)
                        all_targets = torch.cat(all_targets)

                        mae = (all_preds - all_targets).abs().mean().item()
                        rmse = ((all_preds - all_targets) ** 2).mean().sqrt().item()
                        # "within-half": |pred - target| < 0.05 (= 0.5 dial position out of 10)
                        within_half = ((all_preds - all_targets).abs() < 0.05).float().mean().item() * 100

                    else:
                        # Classification validation: compute accuracy
                        correct = 0
                        total = 0
                        for imgs, labels in val_loader:
                            imgs, labels = imgs.to(device), labels.to(device)
                            outputs = model(imgs)
                            _, predicted = torch.max(outputs, 1)
                            total += labels.size(0)
                            correct += (predicted == labels).sum().item()

                        val_acc = 100 * correct / total

                # Best model tracking
                if model_type == "arrows" and training_mode == "continuous":
                    val_accs.append(within_half)  # Reuse val_accs list for within-half %
                    if within_half > best_val_acc:
                        best_val_acc = within_half
                        best_val_loss = avg_loss
                        best_val_mae = mae
                        best_val_rmse = rmse
                        best_epoch = epoch
                        best_model_state = model.state_dict().copy()
                else:
                    val_accs.append(val_acc)
                    if val_acc > best_val_acc:
                        best_val_acc = val_acc
                        best_val_loss = avg_loss
                        best_epoch = epoch
                        best_model_state = model.state_dict().copy()

                epoch_time = time.time() - epoch_start

                # Update progress
                if model_type == "arrows" and training_mode == "continuous":
                    job.update_progress(
                        current_epoch=epoch + 1,
                        total_epochs=epochs,
                        train_loss=round(avg_loss, 4),
                        val_accuracy=round(within_half, 2),  # Repurpose val_accuracy for within-half %
                        epoch_duration=round(epoch_time, 1),
                        message=f"Epoch {epoch+1}/{epochs}: Loss={avg_loss:.4f}, MAE={mae:.4f}, Within-half={within_half:.1f}%",
                    )
                    job.add_log(
                        f"Epoch {epoch+1}/{epochs} - Loss: {avg_loss:.4f} - MAE: {mae:.4f} "
                        f"- RMSE: {rmse:.4f} - Within-half: {within_half:.1f}% - Time: {epoch_time:.1f}s"
                    )
                else:
                    job.update_progress(
                        current_epoch=epoch + 1,
                        total_epochs=epochs,
                        train_loss=round(avg_loss, 4),
                        val_accuracy=round(val_acc, 2),
                        epoch_duration=round(epoch_time, 1),
                        message=f"Epoch {epoch+1}/{epochs}: Loss={avg_loss:.4f}, Val Acc={val_acc:.2f}%",
                    )
                    job.add_log(
                        f"Epoch {epoch+1}/{epochs} - Loss: {avg_loss:.4f} - Val Acc: {val_acc:.2f}% - Time: {epoch_time:.1f}s"
                    )

            total_time = time.time() - total_start
            job.add_log(f"Training completed in {total_time:.1f}s ({total_time/60:.1f}min)")

            # Load best model
            model.load_state_dict(best_model_state)
            if model_type == "arrows" and training_mode == "continuous":
                job.add_log(f"Best model from epoch {best_epoch+1} with {best_val_acc:.1f}% within-half accuracy")
            else:
                job.add_log(f"Best model from epoch {best_epoch+1} with {best_val_acc:.2f}% accuracy")

            # Export to ONNX and OpenVINO
            job.update_progress(message="Exporting model...")
            job.add_log("Exporting to ONNX + OpenVINO...")

            model.cpu()
            model.eval()

            output_dir = Path(f"/app/models/{model_type}/{model_filename}")
            onnx_path, ov_path = export_to_openvino(model, resolution, output_dir, model_filename)
            job.add_log(f"ONNX model saved: {onnx_path}")
            job.add_log(f"OpenVINO model saved: {ov_path}")

            # Save metadata
            metadata = {
                "model_type": model_type,
                "architecture": architecture,
                "resolution": resolution,
                "seed": seed,
                "epochs": epochs,
                "batch_size": batch_size,
                "best_val_loss": best_val_loss,
                "best_epoch": best_epoch + 1,
                "training_time": total_time,
                "num_params": num_params,
                "created_at": datetime.now().isoformat(),
            }

            if model_type == "arrows" and training_mode == "continuous":
                metadata["training_mode"] = "continuous"
                metadata["num_classes"] = 1
                metadata["best_val_mae"] = best_val_mae
                metadata["best_val_rmse"] = best_val_rmse
                metadata["best_within_half"] = best_val_acc  # best_val_acc holds within-half for regression
                metadata["classes"] = None
            else:
                metadata["training_mode"] = "discrete"
                metadata["num_classes"] = num_classes_actual
                metadata["best_val_acc"] = best_val_acc
                metadata["classes"] = class_names
                if model_type == "arrows" and step_size:
                    metadata["step_size"] = step_size

            import json

            metadata_path = output_dir / "metadata.json"
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)
            job.add_log(f"Metadata saved: {metadata_path}")

            # Save training plot
            try:
                import matplotlib

                matplotlib.use("Agg")  # Non-interactive backend
                import matplotlib.pyplot as plt

                fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
                ax1.plot(train_losses)
                ax1.set_title("Training Loss")
                ax1.set_xlabel("Epoch")
                ax1.set_ylabel("Loss")
                ax1.grid(True)

                ax2.plot(val_accs)
                if model_type == "arrows" and training_mode == "continuous":
                    ax2.set_title("Validation Within-Half Accuracy")
                    ax2.set_ylabel("Within-Half (%)")
                else:
                    ax2.set_title("Validation Accuracy")
                    ax2.set_ylabel("Accuracy (%)")
                ax2.set_xlabel("Epoch")
                ax2.grid(True)

                plt.tight_layout()
                plot_path = output_dir / f"{model_filename}_training.png"
                plt.savefig(plot_path, dpi=150, bbox_inches="tight")
                plt.close()
                job.add_log(f"Training plot saved: {plot_path}")
            except Exception as e:
                job.add_log(f"Warning: Could not save training plot: {e}")

            # Cleanup temp dataset for arrows
            if model_type == "arrows" and dataset_dir.name == "dataset_temp":
                try:
                    shutil.rmtree(dataset_dir)
                    job.add_log("Cleaned up temporary dataset")
                except Exception as e:
                    job.add_log(f"Warning: Could not clean up temp dataset: {e}")

            # Refresh model manager to pick up new model
            self.model_manager.refresh()
            job.add_log("Model manager refreshed")

            result = {
                "model_name": architecture,
                "model_id": model_filename,
                "seed": seed,
                "resolution": resolution,
                "training_time": total_time,
                "output_dir": str(output_dir),
            }

            if model_type == "arrows" and training_mode == "continuous":
                result["best_val_acc"] = best_val_acc  # within-half %
                result["best_val_mae"] = best_val_mae
                result["num_classes"] = 1
            else:
                result["best_val_acc"] = best_val_acc
                result["best_val_loss"] = best_val_loss
                result["num_classes"] = num_classes_actual

            return result

        except Exception as e:
            job.add_log(f"Error in training execution: {str(e)}")
            logger.error(traceback.format_exc())

            # Cleanup partial model directory if it was created
            partial_dir = Path(f"/app/models/{model_type}/{model_filename}")
            if partial_dir.exists():
                try:
                    import shutil

                    shutil.rmtree(partial_dir)
                    job.add_log(f"Cleaned up partial model directory: {partial_dir}")
                except Exception as cleanup_err:
                    job.add_log(f"Warning: Could not clean up partial directory: {cleanup_err}")

            return None

    def _persist_training_logs(self, job: TrainingJob):
        """Save training logs to disk for later viewing."""

        config = job.config
        model_type = config.get("model_type", "unknown")

        # For successful jobs, save logs into each model directory
        if job.status == JobStatus.COMPLETED and job.result:
            for result in job.result:
                model_id = result.get("model_id")
                if model_id:
                    log_path = Path(f"/app/models/{model_type}/{model_id}/training.log")
                    try:
                        with open(log_path, "w") as f:
                            f.write("\n".join(job.logs))
                    except Exception as e:
                        logger.error(f"Failed to persist training logs to {log_path}: {e}")

        # For failed/cancelled jobs, save into the failure directory
        elif job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
            fail_dir = self._get_failure_dir(job)
            if fail_dir:
                fail_dir.mkdir(parents=True, exist_ok=True)
                log_path = fail_dir / "training.log"
                try:
                    with open(log_path, "w") as f:
                        f.write("\n".join(job.logs))
                except Exception as e:
                    logger.error(f"Failed to persist failure logs to {log_path}: {e}")

    def _persist_failure_metadata(self, job: TrainingJob):
        """Save metadata.json for a failed training so it appears in the model list."""
        import json

        fail_dir = self._get_failure_dir(job)
        if not fail_dir:
            return

        fail_dir.mkdir(parents=True, exist_ok=True)
        config = job.config

        metadata = {
            "model_type": config.get("model_type", "unknown"),
            "architecture": config.get("architecture", "unknown"),
            "resolution": config.get("resolution", 0),
            "status": "failed",
            "error": job.error or "Unknown error",
            "created_at": (job.started_at or datetime.now()).isoformat(),
            "training_info": {
                "seeds": config.get("seeds", []),
                "epochs": config.get("epochs", 0),
                "notes": config.get("notes", ""),
            },
        }

        metadata_path = fail_dir / "metadata.json"
        try:
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)
            logger.info(f"Saved failure metadata: {metadata_path}")
            # Refresh model manager so it picks up the failure entry
            self.model_manager.refresh()
        except Exception as e:
            logger.error(f"Failed to persist failure metadata: {e}")

    def _get_failure_dir(self, job: TrainingJob) -> Optional[Path]:
        """Get the directory path for storing failure artifacts."""
        config = job.config
        model_type = config.get("model_type")
        architecture = config.get("architecture", "unknown")
        timestamp = (job.started_at or datetime.now()).strftime("%Y%m%d_%H%M%S")
        if not model_type:
            return None
        return Path(f"/app/models/{model_type}/_failed_{architecture}_{timestamp}")

    def _create_arrow_dataset(self, ground_truth_dir: Path, dataset_dir: Path, step: float, job: TrainingJob):
        """Create subsampled arrow dataset from ground_truth."""
        import shutil
        import math

        # Remove existing dataset folder
        if dataset_dir.exists():
            shutil.rmtree(dataset_dir)
        dataset_dir.mkdir(parents=True, exist_ok=True)

        total_images = 0
        class_counts = {}

        for class_dir in sorted(ground_truth_dir.iterdir()):
            if not class_dir.is_dir():
                continue

            # Parse original class value (e.g., "0.0", "0.1", ..., "9.9")
            try:
                original_value = float(class_dir.name)
            except ValueError:
                continue

            # Round to nearest step
            new_value = math.floor(original_value / step) * step
            if new_value >= 10.0:
                new_value = 0.0

            new_class_name = f"{new_value:.1f}"

            # Create target class directory
            target_class_dir = dataset_dir / new_class_name
            target_class_dir.mkdir(exist_ok=True)

            # Copy images
            for img_file in class_dir.glob("*"):
                if img_file.is_file():
                    target_file = target_class_dir / f"{class_dir.name}_{img_file.name}"
                    shutil.copy(img_file, target_file)
                    total_images += 1
                    class_counts[new_class_name] = class_counts.get(new_class_name, 0) + 1

        job.add_log(f"Created {len(class_counts)} classes with {total_images} images")

    def _run_benchmark(self, job: BenchmarkJob):
        """
        Execute benchmark job.
        This is run in a background thread.
        """
        try:
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now()
            job.add_log("Starting benchmark execution")

            # Get model path
            model_path = self.model_manager.get_model_path(job.model_type, job.model_id)
            if not model_path:
                raise ValueError(f"Model not found: {job.model_type}/{job.model_id}")

            job.add_log(f"Benchmarking model: {model_path}")

            # Run benchmark (placeholder)
            result = self._execute_benchmark(job, model_path)

            # Benchmark completed
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now()
            job.result = result
            job.add_log("Benchmark completed successfully")

            # Persist benchmark result to model metadata
            try:
                metadata = self.model_manager.get_model(job.model_type, job.model_id)
                if metadata:
                    training_mode = metadata.get("training_mode", "discrete")
                    if training_mode == "continuous":
                        metadata["benchmark"] = {
                            "training_mode": "continuous",
                            "mae": result["mae"],
                            "rmse": result["rmse"],
                            "within_half_pct": result["within_half_pct"],
                            "within_one_pct": result["within_one_pct"],
                            "mean_confidence": result["mean_confidence"],
                            "inference_time_ms": result["inference_time_ms"],
                            "total_images": result["total_images"],
                            "date": datetime.now().isoformat(),
                        }
                    else:
                        metadata["benchmark"] = {
                            "accuracy": result["accuracy"],
                            "mean_confidence": result["mean_confidence"],
                            "low_confidence_pct": result["low_confidence_pct"],
                            "inference_time_ms": result["inference_time_ms"],
                            "total_images": result["total_images"],
                            "correct_predictions": result["correct_predictions"],
                            "date": datetime.now().isoformat(),
                        }
                    self.model_manager.save_metadata(job.model_type, job.model_id, metadata)
                    job.add_log("Benchmark result saved to model metadata")
            except Exception as e:
                logger.error(f"Failed to save benchmark to metadata: {e}")

        except RuntimeError as e:
            if "cancelled" in str(e).lower():
                job.status = JobStatus.CANCELLED
                job.completed_at = datetime.now()
                job.add_log("Benchmark cancelled")
                logger.info(f"Benchmark job {job.job_id} cancelled")
            else:
                job.status = JobStatus.FAILED
                job.completed_at = datetime.now()
                job.error = str(e)
                job.add_log(f"Benchmark failed with error: {str(e)}")
                logger.error(f"Benchmark job {job.job_id} failed: {e}")
                logger.error(traceback.format_exc())

        except Exception as e:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now()
            job.error = str(e)
            job.add_log(f"Benchmark failed with error: {str(e)}")
            logger.error(f"Benchmark job {job.job_id} failed: {e}")
            logger.error(traceback.format_exc())

        finally:
            pass  # Keep job visible so frontend can see completed/failed status

    def _execute_benchmark(self, job: BenchmarkJob, model_path: Path) -> Dict:
        """
        Execute the actual benchmark process.

        Uses logic from benchmark_digits.py and benchmark_arrows.py.
        Supports both classification (discrete) and regression (continuous) modes.
        """
        import openvino as ov
        import numpy as np
        from collections import defaultdict

        from .training_core import preprocess_image, softmax_predict, regression_predict

        model_type = job.model_type

        # Determine ground truth path and classes
        if model_type == "digits":
            gt_path = Path("/training/digits/ground_truth")
            classes = [str(i) for i in range(10)] + ["NAN"]
            training_mode = "discrete"
        else:  # arrows
            gt_path = Path("/training/arrows/ground_truth")
            metadata = self.model_manager.get_model_metadata(model_type, job.model_id)
            training_mode = metadata.get("training_mode", "discrete")

            if training_mode == "continuous":
                # No class list needed for regression benchmark
                classes = None
                job.add_log("Benchmark mode: regression (continuous)")
            else:
                num_classes = metadata.get("num_classes", 10)
                classes = self._generate_arrow_classes(num_classes)
                job.add_log(f"Benchmark mode: classification ({len(classes)} classes)")

        job.add_log(f"Ground truth path: {gt_path}")
        if classes is not None:
            job.add_log(f"Number of classes: {len(classes)}")

        # Check if ground truth exists
        if not gt_path.exists():
            raise ValueError(f"Ground truth path not found: {gt_path}")

        # Get model resolution from metadata
        metadata = self.model_manager.get_model_metadata(model_type, job.model_id)
        resolution = metadata.get("resolution", 128)
        job.add_log(f"Model resolution: {resolution}px")

        # Load model with OpenVINO
        job.add_log("Loading model with OpenVINO...")
        core = ov.Core()
        model = core.read_model(str(model_path))
        compiled = core.compile_model(model, "AUTO")
        job.add_log("Model loaded successfully")

        # Collect test images
        job.add_log("Collecting test images...")
        images_by_class = self._collect_benchmark_images(gt_path, model_type)
        total_images = sum(len(imgs) for imgs in images_by_class.values())
        total_classes = len(images_by_class)
        job.add_log(f"Found {total_images} images across {total_classes} classes")

        job.update_progress(
            total_classes=total_classes,
            total_images=total_images,
            message=f"Running inference on {total_images} images...",
        )

        # Run inference
        all_predictions = []
        all_confidences = []
        all_times = []
        correct = 0
        processed = 0

        for class_idx, (ground_truth, image_paths) in enumerate(sorted(images_by_class.items())):
            if self._benchmark_cancel_flag.is_set():
                job.add_log("Benchmark cancelled by user")
                raise RuntimeError("Benchmark cancelled")

            job.update_progress(
                current_class=class_idx + 1,
                message=f"Processing class {ground_truth} ({class_idx + 1}/{total_classes})",
            )

            for img_path in image_paths:
                try:
                    img = preprocess_image(str(img_path), resolution)
                except ValueError:
                    continue

                # Inference
                start_time = time.perf_counter()
                result = compiled([img])[compiled.output(0)][0]
                inference_time = time.perf_counter() - start_time

                if training_mode == "continuous":
                    pred_value, confidence = regression_predict(result)
                    expected_value = float(ground_truth)

                    all_predictions.append((pred_value, expected_value))
                    all_confidences.append(confidence)
                    all_times.append(inference_time)

                    # "Correct" if within half a dial position
                    if abs(pred_value - expected_value) < 0.5:
                        correct += 1
                else:
                    # Determine expected label for arrows
                    if model_type == "arrows":
                        expected_label = self._round_to_arrow_class(float(ground_truth), len(classes))
                    else:
                        expected_label = ground_truth

                    pred_label, confidence = softmax_predict(result, classes)

                    all_predictions.append((pred_label, expected_label))
                    all_confidences.append(confidence)
                    all_times.append(inference_time)

                    if pred_label == expected_label:
                        correct += 1

                processed += 1
                job.update_progress(processed_images=processed)

        # Calculate results
        if training_mode == "continuous":
            # Regression metrics
            errors = [abs(p - t) for p, t in all_predictions]

            mae = np.mean(errors) if errors else 0
            rmse = np.sqrt(np.mean([e**2 for e in errors])) if errors else 0
            within_half = sum(1 for e in errors if e < 0.5) / len(errors) * 100 if errors else 0
            within_one = sum(1 for e in errors if e < 1.0) / len(errors) * 100 if errors else 0

            mean_confidence = np.mean(all_confidences) if all_confidences else 0
            mean_inference_time = np.mean(all_times) * 1000 if all_times else 0

            # Per-class MAE (using ground truth folder as key)
            class_errors = defaultdict(list)
            for pred_val, true_val in all_predictions:
                key = f"{true_val:.1f}"
                class_errors[key].append(abs(pred_val - true_val))

            per_class = {}
            for cls, errs in class_errors.items():
                per_class[cls] = {
                    "mae": round(float(np.mean(errs)), 4),
                    "count": len(errs),
                    "within_half": round(sum(1 for e in errs if e < 0.5) / len(errs) * 100, 1),
                }

            result = {
                "training_mode": "continuous",
                "mae": round(float(mae), 4),
                "rmse": round(float(rmse), 4),
                "within_half_pct": round(within_half, 2),
                "within_one_pct": round(within_one, 2),
                "accuracy": round(within_half, 2),  # Alias for UI compatibility
                "mean_confidence": round(float(mean_confidence) * 100, 2),
                "low_confidence_pct": (
                    round(sum(1 for c in all_confidences if c < 0.8) / len(all_confidences) * 100, 2)
                    if all_confidences
                    else 0
                ),
                "inference_time_ms": round(float(mean_inference_time), 2),
                "total_images": processed,
                "correct_predictions": correct,
                "per_class_accuracy": per_class,  # Actually per-class MAE for regression
            }

            job.add_log(
                f"Benchmark complete: MAE={mae:.4f}, RMSE={rmse:.4f}, "
                f"Within-half={within_half:.1f}%, Within-one={within_one:.1f}%, "
                f"Mean confidence={mean_confidence*100:.1f}%"
            )
        else:
            # Classification metrics
            accuracy = correct / processed if processed > 0 else 0
            mean_confidence = np.mean(all_confidences) if all_confidences else 0
            low_conf_count = sum(1 for c in all_confidences if c < 0.8)
            low_conf_pct = low_conf_count / len(all_confidences) * 100 if all_confidences else 0
            mean_inference_time = np.mean(all_times) * 1000 if all_times else 0  # in ms

            # Per-class accuracy
            class_accuracy = defaultdict(lambda: {"correct": 0, "total": 0})
            for pred, expected in all_predictions:
                class_accuracy[expected]["total"] += 1
                if pred == expected:
                    class_accuracy[expected]["correct"] += 1

            per_class = {}
            for cls, data in class_accuracy.items():
                per_class[cls] = {
                    "accuracy": data["correct"] / data["total"] if data["total"] > 0 else 0,
                    "count": data["total"],
                }

            result = {
                "accuracy": round(accuracy * 100, 2),
                "mean_confidence": round(mean_confidence * 100, 2),
                "low_confidence_pct": round(low_conf_pct, 2),
                "inference_time_ms": round(mean_inference_time, 2),
                "total_images": processed,
                "correct_predictions": correct,
                "per_class_accuracy": per_class,
            }

            job.add_log(f"Benchmark complete: {accuracy*100:.1f}% accuracy, {mean_confidence*100:.1f}% mean confidence")

        return result

    def _generate_arrow_classes(self, num_classes: int) -> List[str]:
        """Generate class labels for arrows based on number of classes."""
        if num_classes == 10:
            return [str(i) for i in range(10)]
        elif num_classes == 20:
            return [f"{i/2:.1f}" for i in range(20)]
        elif num_classes == 50:
            return [f"{i/5:.1f}" for i in range(50)]
        elif num_classes == 100:
            return [f"{i/10:.1f}" for i in range(100)]
        else:
            raise ValueError(f"Unsupported num_classes for arrows: {num_classes}")

    def _round_to_arrow_class(self, value: float, num_classes: int) -> str:
        """Round ground truth value to nearest arrow class label."""
        import math

        if num_classes == 10:
            rounded = math.floor(value)
            if rounded >= 10:
                rounded = 0
            return str(int(rounded))
        elif num_classes == 20:
            rounded = math.floor(value * 2) / 2
            if rounded >= 10.0:
                rounded = 0.0
            return f"{rounded:.1f}"
        elif num_classes == 50:
            rounded = math.floor(value * 5) / 5
            if rounded >= 10.0:
                rounded = 0.0
            return f"{rounded:.1f}"
        elif num_classes == 100:
            rounded = math.floor(value * 10) / 10
            if rounded >= 10.0:
                rounded = 0.0
            return f"{rounded:.1f}"
        else:
            raise ValueError(f"Unsupported num_classes: {num_classes}")

    def _collect_benchmark_images(self, gt_path: Path, model_type: str) -> Dict[str, List[Path]]:
        """Collect benchmark images organized by ground truth label."""
        from collections import defaultdict

        images_by_class = defaultdict(list)

        for class_dir in sorted(gt_path.iterdir()):
            if not class_dir.is_dir():
                continue

            label = class_dir.name

            for img_path in class_dir.glob("*.jpg"):
                images_by_class[label].append(img_path)

        return dict(images_by_class)


# Singleton instance
_training_manager = None


def get_training_manager() -> TrainingManager:
    """Get the global TrainingManager instance."""
    global _training_manager
    if _training_manager is None:
        _training_manager = TrainingManager()
    return _training_manager
