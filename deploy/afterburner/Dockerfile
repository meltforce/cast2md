# cast2md Afterburner - Pre-configured RunPod image
# Saves ~8 minutes of setup time by pre-installing dependencies
#
# Build and push manually:
#   docker build -t meltforce/cast2md-afterburner:latest deploy/afterburner/
#   docker push meltforce/cast2md-afterburner:latest
#
# Or use GitHub Actions: .github/workflows/build-afterburner.yml

# Same base image as before - RunPod's pytorch with CUDA
FROM runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

# Install system dependencies
RUN apt-get update -qq && \
    apt-get install -y -qq ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Tailscale (just the binary - tailscale up runs at pod startup with secret token)
RUN curl -fsSL https://tailscale.com/install.sh | sh

# Install NeMo toolkit for Parakeet transcription (~5-10 min build time)
RUN pip install --no-cache-dir 'nemo_toolkit[asr]'

# Pre-download the Parakeet model (~600MB) to avoid download at runtime
RUN python -c "import nemo.collections.asr as nemo_asr; nemo_asr.models.ASRModel.from_pretrained('nvidia/parakeet-tdt-0.6b-v3')"

# Note: cast2md is installed at runtime to allow code updates without rebuilding
# Startup flow:
#   1. Pod starts with this image
#   2. Startup script runs: tailscale up --auth-key="$TS_AUTH_KEY" (from RunPod secret)
#   3. SSH setup installs cast2md and starts the worker
