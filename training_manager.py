"""
Training Manager - Orchestrates model training and benchmarking
"""
import threading
import queue
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum
import logging

from model_manager import get_model_manager

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
            'current_epoch': 0,
            'total_epochs': config.get('epochs', 20),
            'current_config': 1,
            'total_configs': 1,
            'train_loss': None,
            'val_accuracy': None,
            'message': 'Initializing...'
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
            'job_id': self.job_id,
            'status': self.status.value,
            'config': self.config,
            'progress': self.progress,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error': self.error
        }


class BenchmarkJob:
    """Represents a single benchmark job."""

    def __init__(self, job_id: str, model_type: str, model_id: str):
        self.job_id = job_id
        self.model_type = model_type
        self.model_id = model_id
        self.status = JobStatus.PENDING
        self.progress = {
            'current_class': 0,
            'total_classes': 0,
            'processed_images': 0,
            'total_images': 0,
            'message': 'Initializing...'
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
            'job_id': self.job_id,
            'model_type': self.model_type,
            'model_id': self.model_id,
            'status': self.status.value,
            'progress': self.progress,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error': self.error,
            'result': self.result
        }


class TrainingManager:
    """Manages training and benchmark jobs."""

    def __init__(self):
        self.active_training_job: Optional[TrainingJob] = None
        self.active_benchmark_job: Optional[BenchmarkJob] = None
        self.job_history: Dict[str, Any] = {}
        self.model_manager = get_model_manager()
        self._cancel_flag = threading.Event()

    def start_training(self, config: Dict[str, Any]) -> str:
        """
        Start a training job.

        Args:
            config: Training configuration with keys:
                - model_type: "digits" or "arrows"
                - architecture: Model architecture name
                - resolution: Image resolution
                - seeds: List of seeds to train
                - epochs: Number of epochs
                - batch_size: Batch size
                - step_size: For arrows only (1.0, 0.5, 0.2, 0.1)
                - notes: Optional notes

        Returns:
            Job ID

        Raises:
            RuntimeError: If training is already running
        """
        if self.active_training_job is not None:
            raise RuntimeError("Training is already running")

        # Generate job ID
        job_id = f"train_{config['model_type']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Create job
        job = TrainingJob(job_id, config)
        self.active_training_job = job
        self.job_history[job_id] = job

        # Start training in background thread
        self._cancel_flag.clear()
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
        if self.active_benchmark_job is not None:
            raise RuntimeError("Benchmark is already running")

        # Generate job ID
        job_id = f"bench_{model_type}_{model_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Create job
        job = BenchmarkJob(job_id, model_type, model_id)
        self.active_benchmark_job = job
        self.job_history[job_id] = job

        # Start benchmark in background thread
        self._cancel_flag.clear()
        job.thread = threading.Thread(target=self._run_benchmark, args=(job,), daemon=True)
        job.thread.start()

        job.add_log("Benchmark job started")
        return job_id

    def cancel_training(self, job_id: str) -> bool:
        """
        Cancel a running training job.

        Args:
            job_id: Job ID to cancel

        Returns:
            True if successful, False otherwise
        """
        if self.active_training_job is None or self.active_training_job.job_id != job_id:
            return False

        self._cancel_flag.set()
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

        self._cancel_flag.set()
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
            model_type = config['model_type']
            architecture = config['architecture']
            resolution = config['resolution']
            seeds = config.get('seeds', [42])
            epochs = config.get('epochs', 20)
            batch_size = config.get('batch_size', 16)
            notes = config.get('notes', '')

            # For arrows, we may have step_size
            step_size = config.get('step_size', 1.0) if model_type == 'arrows' else None

            # Calculate total configurations
            total_configs = len(seeds)
            job.update_progress(total_configs=total_configs)

            results = []

            # Train for each seed
            for config_idx, seed in enumerate(seeds, 1):
                if self._cancel_flag.is_set():
                    job.add_log("Training cancelled by user")
                    job.status = JobStatus.CANCELLED
                    return

                job.update_progress(
                    current_config=config_idx,
                    message=f"Training config {config_idx}/{total_configs} (seed={seed})"
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
                    step_size=step_size
                )

                if result:
                    results.append(result)
                    job.add_log(f"Completed training for seed {seed}: {result['best_val_acc']:.2f}% accuracy")
                else:
                    job.add_log(f"Training failed for seed {seed}")

            # Training completed
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now()
            job.result = results
            job.add_log(f"Training completed successfully: {len(results)}/{total_configs} models trained")

        except Exception as e:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now()
            job.error = str(e)
            job.add_log(f"Training failed with error: {str(e)}")
            logger.error(f"Training job {job.job_id} failed: {e}")
            logger.error(traceback.format_exc())

        finally:
            self.active_training_job = None

    def _execute_training(self, job: TrainingJob, model_type: str, architecture: str,
                         resolution: int, seed: int, epochs: int, batch_size: int,
                         step_size: Optional[float]) -> Optional[Dict]:
        """
        Execute the actual training process.

        This is a placeholder that will call the existing train_*.py logic.
        """
        import sys
        import importlib.util

        try:
            # Determine which training script to use
            if model_type == 'digits':
                train_script = 'train_digits.py'
            elif model_type == 'arrows':
                train_script = 'train_arrows.py'
            else:
                raise ValueError(f"Unknown model type: {model_type}")

            job.add_log(f"Loading training script: {train_script}")

            # Load training module dynamically
            spec = importlib.util.spec_from_file_location("train_module", train_script)
            train_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(train_module)

            # Prepare training parameters
            # This will require refactoring train_*.py to expose a trainable function
            # For now, this is a placeholder

            job.add_log("Training execution placeholder - actual training will be implemented")

            # Return mock result for now
            return {
                'model_name': architecture,
                'seed': seed,
                'resolution': resolution,
                'best_val_acc': 95.0,  # Placeholder
                'best_val_loss': 0.1,  # Placeholder
                'training_time': 100.0  # Placeholder
            }

        except Exception as e:
            job.add_log(f"Error in training execution: {str(e)}")
            logger.error(traceback.format_exc())
            return None

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

        except Exception as e:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now()
            job.error = str(e)
            job.add_log(f"Benchmark failed with error: {str(e)}")
            logger.error(f"Benchmark job {job.job_id} failed: {e}")
            logger.error(traceback.format_exc())

        finally:
            self.active_benchmark_job = None

    def _execute_benchmark(self, job: BenchmarkJob, model_path: Path) -> Dict:
        """
        Execute the actual benchmark process.

        This is a placeholder that will call the existing benchmark_*.py logic.
        """
        job.add_log("Benchmark execution placeholder - actual benchmark will be implemented")

        # Return mock result for now
        return {
            'accuracy': 94.5,
            'mean_confidence': 96.0,
            'low_confidence_pct': 2.5,
            'inference_time_ms': 15.0
        }


# Singleton instance
_training_manager = None


def get_training_manager() -> TrainingManager:
    """Get the global TrainingManager instance."""
    global _training_manager
    if _training_manager is None:
        _training_manager = TrainingManager()
    return _training_manager
