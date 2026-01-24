FROM openvino/ubuntu22_runtime:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxcb1 \
    libxcb-shm0 \
    libxcb-randr0 \
    libxcb-render0 \
    libxcb-shape0 \
    libxcb-xfixes0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn[standard] opencv-python-headless python-multipart httpx

WORKDIR /app
COPY inference.py .
COPY digits/ov_model/ ov_model_digits/
COPY arrows/ov_model/ ov_model_arrows/

EXPOSE 8000

CMD ["python3", "-m", "uvicorn", "inference:app", "--host", "0.0.0.0", "--port", "8000"]

