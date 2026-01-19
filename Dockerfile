# Stage 1: Build
FROM python:3.11-slim AS builder
WORKDIR /build

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Create virtual environment and install dependencies only (not the project itself)
RUN uv sync --frozen --no-dev --no-install-project

# Stage 2: Runtime
FROM python:3.11-slim
WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /build/.venv /app/.venv

# Copy application source
COPY src/ ./src/

# Set up environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

# Expose port
EXPOSE 8000

# Create data volume mount point
VOLUME ["/app/data"]

# Run the server
CMD ["python", "-m", "cast2md", "serve", "--host", "0.0.0.0"]
