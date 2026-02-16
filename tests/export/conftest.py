"""Export test conftest - ensures real dependencies are available.

The unit test conftest mocks openvino/cv2/paho for fast unit tests,
but export tests need the real PyTorch/timm/OpenVINO modules.

This conftest runs during collection and removes any mocks from sys.modules
so that import statements in export tests get the real modules.
"""
import sys


# Remove mocked modules if they were installed by unit conftest
REAL_MODULES = ['openvino', 'openvino.runtime']

for mod_name in REAL_MODULES:
    if mod_name in sys.modules:
        mock_module = sys.modules[mod_name]
        # Check if it's a MagicMock (has _mock_name attribute)
        if hasattr(mock_module, '_mock_name'):
            del sys.modules[mod_name]
