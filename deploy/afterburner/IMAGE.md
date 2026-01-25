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

# Build (takes 10-15 minutes)
docker build -t meltforce/cast2md-afterburner:latest deploy/afterburner/

# Push
docker push meltforce/cast2md-afterburner:latest
```

## Updating the Image

Rebuild when:
- NeMo toolkit has a major update
- Switching to a different Parakeet model
- Base RunPod pytorch image changes

No rebuild needed for:
- cast2md code changes (installed at runtime)
- Configuration changes

## Verifying the Image

After pushing, verify RunPod can pull it:

```bash
# Check image exists on Docker Hub
docker pull meltforce/cast2md-afterburner:latest

# Check size (should be ~15GB)
docker images meltforce/cast2md-afterburner
```

## Troubleshooting

### GitHub Actions build fails

1. Check the Actions tab for error logs
2. Verify Docker Hub secrets are set correctly
3. Ensure Docker Hub username matches the image name (`meltforce/...`)

### RunPod can't pull the image

1. Verify the image is public on Docker Hub
2. Check the exact image name: `meltforce/cast2md-afterburner:latest`
3. Try pulling manually: `docker pull meltforce/cast2md-afterburner:latest`

### Pod startup still slow

If pods are still slow to start, verify pre-installed packages:

```bash
ssh root@<pod-ip> "which ffmpeg && python -c 'import nemo' && echo OK"
```

If this fails, the wrong image may be in use. Check the RunPod template's image name.
