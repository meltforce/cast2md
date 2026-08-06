# Stage 1: Dependencies, deliberately without the project itself.
#
# The virtualenv this produces is ~1.45 GB, almost all of it torch, and it is
# copied into the runtime image as a single layer. Anything that makes its
# contents differ between builds makes the target host re-pull that gigabyte.
# Two things did, and both are avoided here:
#
#   - Copying src/ before the installs. Any commit touching a Python file
#     invalidated the torch layer, so it was rebuilt from scratch.
#   - `uv pip install -e .` writes uv_cache.json into the venv, and that file
#     carries a fingerprint of the source tree. Even with the installs cached,
#     the copied venv therefore changed on every source change.
#
# So: dependencies here, the project in stage 2, and only its ~10 kB of
# metadata joins the venv in the runtime image.
FROM python:3.11-slim AS deps
WORKDIR /build

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Dependency manifests only — no source. `uv export --no-emit-project` reads
# pyproject.toml and uv.lock and does not need the modules.
COPY pyproject.toml uv.lock ./

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
    uv pip install -r requirements.txt

# Stage 2: The project's distribution metadata.
#
# Nothing of the project's code is needed in the venv — the modules are copied
# to /app/src and found through PYTHONPATH. What is needed is the dist-info,
# because cast2md/__init__.py resolves its version through
# importlib.metadata.version("cast2md"), which fails without it.
#
# The editable install's path hook is deliberately left behind: it points at
# this build directory, which does not exist in the runtime image.
FROM deps AS project
COPY src/ ./src/
RUN uv pip install --no-deps -e . && \
    mkdir /metadata && \
    cp -a /build/.venv/lib/python3.11/site-packages/cast2md-*.dist-info /metadata/

# Stage 3: Runtime
FROM python:3.11-slim
WORKDIR /app

# Static ffmpeg binary (~80MB vs ~460MB from apt)
COPY --from=mwader/static-ffmpeg:7.1 /ffmpeg /usr/local/bin/
COPY --from=mwader/static-ffmpeg:7.1 /ffprobe /usr/local/bin/

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 cast2md && \
    mkdir -p /app/data && \
    chown -R cast2md:cast2md /app

# The dependencies: one large layer whose contents depend only on
# pyproject.toml and uv.lock, so an ordinary commit does not move it.
COPY --from=deps /build/.venv /app/.venv

# The project's metadata: ~10 kB, changes freely.
COPY --from=project /metadata/ /app/.venv/lib/python3.11/site-packages/

# Copy application source
COPY src/ ./src/

# Set up environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

# OCI labels for version tracking. The CI workflow passes VERSION=edge-<sha>,
# so this carries the exact commit. It is also exposed as an env var, because a
# label can only be read over the Docker socket on the host -- CI verifies the
# deploy through /api/health over HTTPS and has no SSH there.
ARG VERSION=dev
ENV CAST2MD_BUILD_VERSION="${VERSION}"
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
