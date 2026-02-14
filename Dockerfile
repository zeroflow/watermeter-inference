FROM openvino/ubuntu22_runtime:latest
USER root

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxcb1 \
    libxcb-shm0 \
    libxcb-randr0 \
    libxcb-render0 \
    libxcb-shape0 \
    libxcb-xfixes0 \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch CPU-only FIRST (before timm/other deps that depend on torch)
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install remaining Python dependencies (timm will reuse the CPU-only torch)
COPY requirements-docker.txt /tmp/requirements-docker.txt
RUN pip install --no-cache-dir -r /tmp/requirements-docker.txt && rm /tmp/requirements-docker.txt

# Set up directory structure
WORKDIR /app
RUN mkdir -p /config /config_default /data \
    /training/arrows/input \
    /training/arrows/ground_truth \
    /training/digits/input \
    /training/digits/ground_truth

# Copy application package
COPY watermeter/ /app/watermeter/

# Copy selected models to staging area (entrypoint copies to /app/models/ on first run)
COPY digits/selected/ /app/digits/selected/
COPY arrows/selected/ /app/arrows/selected/

# Copy default configuration
COPY config.yaml /config_default/config.yaml

# Copy and configure entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Expose port
EXPOSE 8001

# Set entrypoint and default command
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python3", "-m", "uvicorn", "watermeter.app:app", "--host", "0.0.0.0", "--port", "8001"]
