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

# Install Python dependencies
COPY requirements-docker.txt /tmp/requirements-docker.txt
RUN pip install --no-cache-dir -r /tmp/requirements-docker.txt && rm /tmp/requirements-docker.txt

# Set up directory structure
WORKDIR /app
RUN mkdir -p /config /config_default /data \
    /training/arrows/input \
    /training/arrows/ground_truth \
    /training/digits/input \
    /training/digits/ground_truth

# Copy application files
COPY app.py watermeter_service.py persistence.py inference.py config_utils.py model_manager.py training_manager.py /app/
COPY templates/ /app/templates/
COPY static/ /app/static/

# Copy selected models (run ./debug.sh before docker build)
COPY digits/selected/ /app/models/digits/
COPY arrows/selected/ /app/models/arrows/

# Copy default configuration
COPY config.yaml /config_default/config.yaml

# Copy and configure entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Expose port
EXPOSE 8001

# Set entrypoint and default command
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
