#!/bin/bash

# Setup script for watermeter-inference
# Creates a uv-managed virtual environment (.venv) and installs all dependencies

set -e  # Exit on error

echo "=== Watermeter Inference Setup ==="
echo ""

if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed. See https://docs.astral.sh/uv/ for installation instructions."
    exit 1
fi
echo "Found: $(uv --version)"

VENV_DIR=".venv"
echo "Creating virtual environment in '$VENV_DIR' (Python >=3.10)..."
uv venv "$VENV_DIR" --python ">=3.10"

# Verify the interpreter version
if ! "$VENV_DIR/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Error: Python 3.10 or later is required."
    exit 1
fi
echo "Using: $("$VENV_DIR/bin/python" --version)"
echo ""

echo "Installing dependencies from requirements.txt and requirements-dev.txt..."
uv pip install --python "$VENV_DIR/bin/python" -r requirements.txt -r requirements-dev.txt
echo ""

echo "=== Setup Complete ==="
echo ""
echo "Run tests with:"
echo "  $VENV_DIR/bin/python -m pytest tests/unit -q"
echo ""
echo "To activate the virtual environment, run:"
echo "  source $VENV_DIR/bin/activate"
