"""Export test conftest - ensures real dependencies are available.

The unit test conftest mocks openvino/cv2/paho for fast unit tests,
but export tests need the real PyTorch/timm/OpenVINO modules.

This conftest uses an autouse fixture to remove mocks before each test runs.
"""
import sys

import pytest


@pytest.fixture(autouse=True)
def unmock_openvino():
    """Remove openvino mocks and force training_core to re-import with real modules.

    This runs before each test in the export directory, ensuring that even if
    unit tests have polluted sys.modules with mocks, we get the real openvino.
    """
    # Remove mocked modules if they were installed by unit conftest
    REAL_MODULES = ['openvino', 'openvino.runtime']

    for mod_name in REAL_MODULES:
        if mod_name in sys.modules:
            mock_module = sys.modules[mod_name]
            # Check if it's a MagicMock (has _mock_name attribute)
            if hasattr(mock_module, '_mock_name'):
                del sys.modules[mod_name]

    # Also remove watermeter.training_core if it was imported with mocked openvino
    # This ensures it re-imports with the real openvino module
    if 'watermeter.training_core' in sys.modules:
        del sys.modules['watermeter.training_core']

    # Yield to run the test
    yield
