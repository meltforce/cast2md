# cast2md Afterburner - Pre-configured RunPod image
# Saves ~8 minutes of setup time by pre-installing NeMo and ffmpeg
#
# Build: docker build -t meltforce/cast2md-afterburner:latest .
# Push:  docker push meltforce/cast2md-afterburner:latest

FROM runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

# Install ffmpeg
RUN apt-get update -qq && \
    apt-get install -y -qq ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install NeMo toolkit for Parakeet transcription
# This is the slowest part (~5-10 min) so we pre-install it
RUN pip install --no-cache-dir 'nemo_toolkit[asr]'

# Pre-download the Parakeet model to avoid download at runtime
RUN python -c "import nemo.collections.asr as nemo_asr; nemo_asr.models.ASRModel.from_pretrained('nvidia/parakeet-tdt-0.6b-v3')"

# Install Tailscale (needed for networking)
RUN curl -fsSL https://tailscale.com/install.sh | sh

# Note: cast2md is installed at runtime to allow code updates
# The startup script will: pip install 'cast2md[node] @ git+https://github.com/meltforce/cast2md.git'
