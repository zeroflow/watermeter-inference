#!/bin/bash

# Setup script for watermeter-inference
# Creates a virtual environment and installs all dependencies

set -e  # Exit on error

echo "=== Watermeter Inference Setup ==="
echo ""

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed. Please install Python 3.8 or later."
    exit 1
fi

# Display Python version
PYTHON_VERSION=$(python3 --version)
echo "Found: $PYTHON_VERSION"
echo ""

# Check minimum Python version (3.8)
PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
    echo "Error: Python 3.8 or later is required."
    exit 1
fi

# Check if Docker is installed
echo "Checking for Docker..."
if ! command -v docker &> /dev/null; then
    echo "Docker is not installed. Installing Docker..."
    echo ""

    # Download and run Docker's official installation script
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    rm get-docker.sh

    # Add current user to docker group to run docker without sudo
    sudo usermod -aG docker $USER

    echo ""
    echo "Docker installed successfully!"
    echo "Note: You may need to log out and back in for group changes to take effect."
    echo ""
else
    DOCKER_VERSION=$(docker --version)
    echo "Found: $DOCKER_VERSION"
    echo ""
fi

# Create virtual environment
VENV_DIR="venv"
echo "Creating virtual environment in '$VENV_DIR'..."
python3 -m venv "$VENV_DIR"
echo "Virtual environment created successfully."
echo ""

# Activate virtual environment
echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip
echo ""

# Install dependencies
echo "Installing dependencies from requirements.txt..."
pip install -r requirements.txt
echo ""

echo "=== Setup Complete ==="
echo ""
echo "To activate the virtual environment, run:"
echo "  source $VENV_DIR/bin/activate"
echo ""
echo "To deactivate, run:"
echo "  deactivate"
