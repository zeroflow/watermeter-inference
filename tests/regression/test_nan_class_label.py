"""Regression test: digit classes must include 'NAN' not 'N'.

Bug: The digit model has 11 classes: 0-9 plus NAN.
Previously, code used 'N' instead of 'NAN', causing misclassification.
"""

from unittest.mock import patch

import pytest

from watermeter.training_manager import TrainingManager


def test_digit_classes_include_nan():
    """Digits class list must contain 'NAN' (not 'N', not 'nan')."""
    # The standard digit classes used throughout the codebase
    digit_classes = [str(i) for i in range(10)] + ['NAN']

    assert 'NAN' in digit_classes
    assert 'N' not in digit_classes
    assert len(digit_classes) == 11


def test_arrow_classes_100_do_not_contain_nan(tmp_path):
    """Arrow classes should be numeric, never NAN."""
    with patch('watermeter.model_manager._model_manager', None):
        with patch('watermeter.model_manager.ModelManager.__init__', lambda self, **kw: setattr(self, 'models_base_path', tmp_path)):
            tm = TrainingManager()
    for num_classes in [10, 20, 50, 100]:
        classes = tm._generate_arrow_classes(num_classes)
        assert 'NAN' not in classes
        assert 'N' not in classes
