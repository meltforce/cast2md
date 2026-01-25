# Building the Afterburner Docker Image

The pre-configured Docker image speeds up RunPod pod creation from ~8 minutes to ~2 minutes by pre-installing dependencies.

## What's Pre-installed

| Component | Size | Build Time Saved |
|-----------|------|------------------|
| Tailscale | ~50MB | 20s |
| ffmpeg | ~100MB | 20s |
| NeMo toolkit | ~5GB | 5-8 min |
| Parakeet model | ~600MB | 1-2 min |

Total image size: ~15GB (base pytorch image is ~10GB)

## Prerequisites

- Docker installed
- GitHub account with access to meltforce/cast2md
- GitHub Personal Access Token with `write:packages` scope

## Build Steps

### 1. Login to GitHub Container Registry

```bash
# Create a token at https://github.com/settings/tokens
# Required scope: write:packages

echo $GITHUB_TOKEN | docker login ghcr.io -u YOUR_USERNAME --password-stdin
```

### 2. Build the Image

```bash
cd /path/to/cast2md
docker build -t ghcr.io/meltforce/cast2md-afterburner:latest deploy/afterburner/
```

Build takes 10-15 minutes (downloading NeMo dependencies and Parakeet model).

### 3. Push to Registry

```bash
docker push ghcr.io/meltforce/cast2md-afterburner:latest
```

### 4. Make the Package Public

By default, GitHub packages are private. RunPod can't pull private images without credentials.

1. Go to https://github.com/orgs/meltforce/packages
2. Click on `cast2md-afterburner`
3. Click "Package settings"
4. Scroll to "Danger Zone" → "Change visibility" → Public

## Updating the Image

Rebuild when:
- NeMo toolkit has a major update
- Switching to a different Parakeet model
- Base RunPod image changes

No rebuild needed for:
- cast2md code changes (installed at runtime)
- Configuration changes

## Troubleshooting

### Build fails with CUDA errors

The Parakeet model download requires CUDA. If building on a non-GPU machine, you may see warnings but the model should still download. If it fails, build on a GPU-enabled machine or RunPod itself.

### RunPod can't pull the image

1. Verify the image is public (see step 4)
2. Check the image name matches exactly: `ghcr.io/meltforce/cast2md-afterburner:latest`
3. Try pulling manually: `docker pull ghcr.io/meltforce/cast2md-afterburner:latest`

### Pod startup still slow

If pods are still slow to start, check the logs - the pre-installed packages might not be detected:

```bash
ssh root@<pod-ip> "which ffmpeg && python -c 'import nemo'"
```

Both should succeed without errors if the image is correct.
