# Stage 1: Build
FROM python:3.11-slim AS builder
WORKDIR /build

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy dependency files and source
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Create virtual environment
RUN uv venv

# Install CPU-only PyTorch first (before other deps to avoid CUDA)
RUN uv pip install torch --index-url https://download.pytorch.org/whl/cpu

# Export requirements (excluding torch and NVIDIA packages) and install rest
RUN uv export --frozen --no-dev --no-hashes --no-emit-project \
    --prune torch --prune triton \
    --prune nvidia-cublas-cu12 --prune nvidia-cuda-cupti-cu12 \
    --prune nvidia-cuda-nvrtc-cu12 --prune nvidia-cuda-runtime-cu12 \
    --prune nvidia-cudnn-cu12 --prune nvidia-cufft-cu12 \
    --prune nvidia-cufile-cu12 --prune nvidia-curand-cu12 \
    --prune nvidia-cusolver-cu12 --prune nvidia-cusparse-cu12 \
    --prune nvidia-cusparselt-cu12 --prune nvidia-nccl-cu12 \
    --prune nvidia-nvjitlink-cu12 --prune nvidia-nvshmem-cu12 \
    --prune nvidia-nvtx-cu12 --prune cuda-bindings --prune cuda-pathfinder \
    > requirements.txt && \
    uv pip install -r requirements.txt && \
    uv pip install --no-deps -e .

# Stage 2: Runtime
FROM python:3.11-slim
WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 cast2md && \
    mkdir -p /app/data && \
    chown -R cast2md:cast2md /app

# Copy virtual environment from builder
COPY --from=builder /build/.venv /app/.venv

# Copy application source
COPY src/ ./src/

# Set up environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

# OCI labels for version tracking
ARG VERSION=dev
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.source="https://github.com/meltforce/cast2md"

# Expose port
EXPOSE 8000

# Create data volume mount point
VOLUME ["/app/data"]

# Switch to non-root user
USER cast2md

# Run the server
CMD ["python", "-m", "cast2md", "serve", "--host", "0.0.0.0"]
