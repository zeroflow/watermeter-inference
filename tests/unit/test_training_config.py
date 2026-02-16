"""Tests for TrainingConfig Pydantic model — learning_rate field."""
import pytest
from pydantic import ValidationError


class TestTrainingConfigLearningRate:
    """Test the learning_rate field on TrainingConfig."""

    def test_default_learning_rate(self):
        """Default learning rate should be 3e-4."""
        from watermeter.routes.training import TrainingConfig

        config = TrainingConfig(
            model_type="digits",
            architecture="resnet18",
            resolution=128,
            seeds=[42],
        )
        assert config.learning_rate == pytest.approx(3e-4)

    def test_custom_learning_rate(self):
        """Custom learning rate should be accepted."""
        from watermeter.routes.training import TrainingConfig

        config = TrainingConfig(
            model_type="digits",
            architecture="resnet18",
            resolution=128,
            seeds=[42],
            learning_rate=1e-4,
        )
        assert config.learning_rate == pytest.approx(1e-4)

    def test_learning_rate_zero_rejected(self):
        """LR of 0 should be rejected."""
        from watermeter.routes.training import TrainingConfig

        with pytest.raises(ValidationError):
            TrainingConfig(
                model_type="digits",
                architecture="resnet18",
                resolution=128,
                seeds=[42],
                learning_rate=0.0,
            )

    def test_learning_rate_negative_rejected(self):
        """Negative LR should be rejected."""
        from watermeter.routes.training import TrainingConfig

        with pytest.raises(ValidationError):
            TrainingConfig(
                model_type="digits",
                architecture="resnet18",
                resolution=128,
                seeds=[42],
                learning_rate=-1e-3,
            )

    def test_learning_rate_too_high_rejected(self):
        """LR above 1.0 should be rejected."""
        from watermeter.routes.training import TrainingConfig

        with pytest.raises(ValidationError):
            TrainingConfig(
                model_type="digits",
                architecture="resnet18",
                resolution=128,
                seeds=[42],
                learning_rate=2.0,
            )

    def test_learning_rate_passed_through_dict(self):
        """LR should survive .dict() serialization for the training manager."""
        from watermeter.routes.training import TrainingConfig

        config = TrainingConfig(
            model_type="digits",
            architecture="resnet18",
            resolution=128,
            seeds=[42],
            learning_rate=5e-4,
        )
        d = config.dict()
        assert d["learning_rate"] == pytest.approx(5e-4)
