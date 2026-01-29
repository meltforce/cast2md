# cast2md Afterburner - Minimal CUDA 12.4 image for Parakeet transcription
#
# This image is optimized for Parakeet/NeMo transcription with pinned versions
# to avoid CUDA compatibility issues with RunPod's newer CUDA 12.8 drivers.
#
# Target size: ~8GB (vs ~15GB with runpod/pytorch base)
#
# Build and push manually:
#   docker build -t meltforce/cast2md-afterburner:cuda124 deploy/afterburner/
#   docker push meltforce/cast2md-afterburner:cuda124
#
# Or use GitHub Actions: .github/workflows/build-afterburner.yml

# Use CUDA 12.4.1 runtime with cuDNN (not devel - we only do inference)
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# CUDA graphs compatibility is handled at runtime by NeMo 2.6+ (auto-detects
# driver/toolkit mismatch) and by TranscriptionService._disable_cuda_graphs()

# Install Python 3.11 and minimal system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        software-properties-common && \
    add-apt-repository -y ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3.11-dev \
        python3-pip \
        ffmpeg \
        curl \
        git \
        ca-certificates && \
    # Set Python 3.11 as default
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 && \
    # Clean up
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Tailscale (for secure pod-to-server communication)
RUN curl -fsSL https://tailscale.com/install.sh | sh

# Upgrade pip
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

# Install PyTorch with CUDA 12.4 support (pinned versions)
RUN pip install --no-cache-dir \
    torch==2.4.0 \
    torchaudio==2.4.0 \
    --index-url https://download.pytorch.org/whl/cu124

# Install NeMo toolkit for Parakeet transcription
RUN pip install --no-cache-dir 'nemo_toolkit[asr]'

# Pre-download Parakeet model (~600MB) to avoid download at runtime
RUN python -c "import nemo.collections.asr as nemo_asr; \
    model = nemo_asr.models.ASRModel.from_pretrained('nvidia/parakeet-tdt-0.6b-v3'); \
    print('Parakeet model loaded successfully')"

# Install faster-whisper for fallback (much smaller than NeMo)
RUN pip install --no-cache-dir faster-whisper==1.0.3

# Clean up pip cache to reduce image size
RUN rm -rf /root/.cache/pip

# Create directory for Tailscale state
RUN mkdir -p /var/lib/tailscale

# Note: cast2md is installed at runtime to allow code updates without rebuilding
# Startup flow:
#   1. Pod starts with this image
#   2. Startup script runs tailscale with auth key from RunPod secret
#   3. SSH setup installs cast2md from git and starts the worker
