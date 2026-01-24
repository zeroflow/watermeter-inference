#!/bin/bash
set -e

echo "=== Water Meter Dashboard Startup ==="

# Initialize config directory if empty
if [ ! -f /config/config.yaml ]; then
    echo "No config found in /config, copying defaults..."
    cp /config_default/config.yaml /config/config.yaml
    echo "✓ Default config copied to /config/config.yaml"
else
    echo "✓ Using existing config from /config/config.yaml"
fi

# Create data directory for state persistence
mkdir -p /data
echo "✓ Data directory created"

# Create training directories (separate mount for Label Studio)
mkdir -p /training/digits
mkdir -p /training/arrows
echo "✓ Training directories created"

# Symlink config to /app for application to use
ln -sf /config/config.yaml /app/config.yaml
echo "✓ Config linked to /app"

echo "=== Starting application ==="
cd /app

# Execute the CMD
exec "$@"
