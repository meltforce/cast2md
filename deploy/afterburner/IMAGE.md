# Building the Afterburner Docker Image

The pre-configured Docker image speeds up RunPod pod creation from ~8 minutes to ~2 minutes by pre-installing dependencies.

## Architecture

This image uses a minimal CUDA 12.4 base with pinned versions to ensure compatibility with RunPod's infrastructure. The key design decisions:

| Decision | Rationale |
|----------|-----------|
| CUDA 12.4.1 runtime | Stable CUDA version for inference |
| NEMO_CUDA_GRAPHS=0 | Disables CUDA graphs to avoid error 35 on RunPod's CUDA 12.8 drivers |
| cudnn-runtime base | Smaller than devel (no compilers needed for inference) |
| Minimal packages | Only what's needed for transcription |

## What's Pre-installed

| Component | Version | Size | Purpose |
|-----------|---------|------|---------|
| CUDA | 12.4.1 | ~2GB | GPU runtime |
| Python | 3.11 | ~100MB | Runtime |
| PyTorch | 2.4.0+cu124 | ~2GB | ML framework |
| NeMo toolkit | latest | ~3GB | Parakeet ASR |
| Parakeet model | 0.6B v3 | ~600MB | Transcription model |
| faster-whisper | 1.0.3 | ~100MB | Fallback transcription |
| Tailscale | latest | ~50MB | Secure networking |
| ffmpeg | system | ~100MB | Audio processing |

**Total image size: ~8GB** (vs ~15GB with previous runpod/pytorch base)

## Image Tags

| Tag | Description |
|-----|-------------|
| `cuda124` | Recommended - pinned CUDA 12.4 for stability |
| `latest` | Always points to newest build |
| `<commit-sha>` | Specific git commit for rollback |

## Option 1: GitHub Actions (Recommended)

The image is built automatically via GitHub Actions when the Dockerfile changes.

### Setup (one-time)

1. Create a Docker Hub account at [hub.docker.com](https://hub.docker.com)

2. Create an access token:
   - Go to [Account Settings → Security](https://hub.docker.com/settings/security)
   - Click "New Access Token"
   - Name: `github-actions`
   - Permissions: Read & Write

3. Add secrets to your GitHub repo:
   - Go to Settings → Secrets and variables → Actions
   - Add `DOCKERHUB_USERNAME`: your Docker Hub username
   - Add `DOCKERHUB_TOKEN`: the access token from step 2

### Trigger a Build

- **Automatic**: Push changes to `deploy/afterburner/Dockerfile`
- **Manual**: Go to Actions → "Build Afterburner Image" → "Run workflow"

Build takes ~20-30 minutes on GitHub's free runners.

## Option 2: Build Locally

```bash
# Login to Docker Hub
docker login

# Build (takes 15-20 minutes)
docker build -t meltforce/cast2md-afterburner:cuda124 deploy/afterburner/

# Push
docker push meltforce/cast2md-afterburner:cuda124

# Also tag as latest
docker tag meltforce/cast2md-afterburner:cuda124 meltforce/cast2md-afterburner:latest
docker push meltforce/cast2md-afterburner:latest
```

## Testing the Image

### Local GPU Test

```bash
# Test CUDA availability
docker run --gpus all -it meltforce/cast2md-afterburner:cuda124 \
    python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}')"

# Test NeMo/Parakeet loads
docker run --gpus all -it meltforce/cast2md-afterburner:cuda124 \
    python -c "import nemo.collections.asr as nemo_asr; \
               model = nemo_asr.models.ASRModel.from_pretrained('nvidia/parakeet-tdt-0.6b-v3'); \
               print('Parakeet loaded successfully')"

# Test Whisper fallback
docker run --gpus all -it meltforce/cast2md-afterburner:cuda124 \
    python -c "from faster_whisper import WhisperModel; print('faster-whisper OK')"
```

### Version Verification

```bash
docker run --rm meltforce/cast2md-afterburner:cuda124 \
    python -c "import torch; import nemo; \
               print(f'PyTorch: {torch.__version__}'); \
               print(f'NeMo: {nemo.__version__}')"
```

Expected output:
```
PyTorch: 2.4.0+cu124
NeMo: 2.x.x (latest)
```

### RunPod Runtime Behavior

On RunPod, NeMo detects the driver/toolkit version mismatch and automatically disables CUDA graphs:

```
[NeMo W] No conditional node support for Cuda.
    Cuda graphs with while loops are disabled, decoding speed will be slower
    Reason: Driver supports cuda toolkit version 12.4, but the driver needs to support at least 12,6.
```

This is expected behavior. The `NEMO_CUDA_GRAPHS=0` environment variable provides an explicit safeguard, and NeMo's auto-detection handles it gracefully. Transcription proceeds normally at ~60-70x realtime (vs ~87x with CUDA graphs enabled).

To verify a running pod:
```bash
ssh -o StrictHostKeyChecking=no root@<pod-ip> "tail -50 /tmp/cast2md-node.log"
```

Look for:
- "Cuda graphs with while loops are disabled" (expected warning)
- "Job XXXX completed successfully" (transcription working)

## Updating the Image

Rebuild when:
- NeMo toolkit has a major update (test CUDA compatibility first!)
- Switching to a different Parakeet model
- CUDA compatibility issues arise

No rebuild needed for:
- cast2md code changes (installed at runtime)
- Configuration changes

## Rollback

If the new image has issues:

```bash
# Tag current as legacy before pushing new
docker tag meltforce/cast2md-afterburner:latest meltforce/cast2md-afterburner:legacy
docker push meltforce/cast2md-afterburner:legacy

# Rollback by using commit sha tag
# In settings, set: runpod_image_name = "meltforce/cast2md-afterburner:<commit-sha>"
```

## Troubleshooting

### GitHub Actions build fails

1. Check the Actions tab for error logs
2. Verify Docker Hub secrets are set correctly
3. Ensure Docker Hub username matches the image name (`meltforce/...`)
4. Check disk space - the build needs ~30GB free

### CUDA error 35 on RunPod

This error indicates CUDA graph compatibility issues. The image already sets `NEMO_CUDA_GRAPHS=0` to prevent this, but if you still see it:
1. Verify the env var is set: `echo $NEMO_CUDA_GRAPHS` (should be `0`)
2. Check PyTorch CUDA version: `python -c "import torch; print(torch.version.cuda)"`
3. Try a different GPU type (RTX 4090/4080 and L4 are known to have issues)

### RunPod can't pull the image

1. Verify the image is public on Docker Hub
2. Check the exact image name: `meltforce/cast2md-afterburner:cuda124`
3. Try pulling manually: `docker pull meltforce/cast2md-afterburner:cuda124`

### Pod startup still slow

If pods are still slow to start, verify pre-installed packages:

```bash
ssh root@<pod-ip> "which ffmpeg && python -c 'import nemo' && echo OK"
```

If this fails, the wrong image may be in use. Check the RunPod settings page for the image name.

## Key Version Pins

These versions are specifically chosen for compatibility:

| Package | Version | Reason |
|---------|---------|--------|
| CUDA base | 12.4.1-cudnn-runtime | Stable CUDA version for inference |
| PyTorch | 2.4.0+cu124 | Match CUDA base version |
| NeMo | latest | Uses NEMO_CUDA_GRAPHS=0 env var to disable CUDA graphs |
| faster-whisper | 1.0.3 | Stable Whisper fallback |

**Note:** CUDA graphs are disabled via `NEMO_CUDA_GRAPHS=0` environment variable to avoid CUDA error 35 on RunPod's infrastructure. This may reduce performance slightly (~60-70x vs 87x realtime) but ensures stability.
