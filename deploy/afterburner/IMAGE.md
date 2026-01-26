# Building the Afterburner Docker Image

The pre-configured Docker image speeds up RunPod pod creation from ~8 minutes to ~2 minutes by pre-installing dependencies.

## Architecture

This image uses a minimal CUDA 12.4 base with pinned versions to ensure compatibility with RunPod's infrastructure. The key design decisions:

| Decision | Rationale |
|----------|-----------|
| CUDA 12.4.1 runtime | NeMo CUDA graph compatibility with RunPod's CUDA 12.8 drivers |
| NeMo 2.0.0 pinned | Avoids CUDA graph issues that cause error 35 on newer NeMo versions |
| cudnn-runtime base | Smaller than devel (no compilers needed for inference) |
| Minimal packages | Only what's needed for transcription |

## What's Pre-installed

| Component | Version | Size | Purpose |
|-----------|---------|------|---------|
| CUDA | 12.4.1 | ~2GB | GPU runtime |
| Python | 3.11 | ~100MB | Runtime |
| PyTorch | 2.4.0+cu124 | ~2GB | ML framework |
| NeMo toolkit | 2.0.0 | ~3GB | Parakeet ASR |
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
NeMo: 2.0.0
```

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

This error indicates CUDA graph compatibility issues:
1. Verify NeMo version is 2.0.0: `python -c "import nemo; print(nemo.__version__)"`
2. Check PyTorch CUDA version matches: `python -c "import torch; print(torch.version.cuda)"`
3. If still failing, try setting `NEMO_CUDA_GRAPHS=0` environment variable

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
| CUDA base | 12.4.1-cudnn-runtime | Driver compatibility with NeMo CUDA graphs |
| PyTorch | 2.4.0+cu124 | Match CUDA base version |
| NeMo | 2.0.0 | Last stable version before CUDA graph changes |
| faster-whisper | 1.0.3 | Stable Whisper fallback |

**Do not upgrade NeMo without testing on RunPod first** - newer versions use CUDA graphs that fail on certain GPU/driver combinations.
