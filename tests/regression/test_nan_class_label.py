"""Regression test: digit classes must include 'NAN' not 'N'.

Bug: The digit model has 11 classes: 0-9 plus NAN.
Previously, code used 'N' instead of 'NAN', causing misclassification.
"""

from unittest.mock import patch

from watermeter.training_manager import TrainingManager


def test_arrow_classes_do_not_contain_nan(tmp_path):
    """Arrow classes should be numeric, never NAN."""
    with patch('watermeter.model_manager._model_manager', None):
        with patch('watermeter.model_manager.ModelManager.__init__', lambda self, **kw: setattr(self, 'models_base_path', tmp_path)):
            tm = TrainingManager()
    for num_classes in [10, 20, 50, 100]:
        classes = tm._generate_arrow_classes(num_classes)
        assert 'NAN' not in classes
        assert 'N' not in classes
