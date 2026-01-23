#!/bin/bash
# RunPod Afterburner Startup Script
# This runs automatically when the pod boots via the template's dockerStartCmd

set -e

LOG_FILE="/var/log/afterburner-startup.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Afterburner Startup $(date) ==="

# Required environment variables (injected by template):
# - TS_AUTH_KEY: Tailscale auth key (from RunPod secret)
# - TS_HOSTNAME: Tailscale hostname for this pod
# - CAST2MD_SERVER_URL: cast2md server URL
# - GITHUB_REPO: GitHub repo to install from (default: meltforce/cast2md)

: "${TS_AUTH_KEY:?TS_AUTH_KEY is required}"
: "${TS_HOSTNAME:=runpod-afterburner}"
: "${CAST2MD_SERVER_URL:=https://cast2md.leo-royal.ts.net}"
: "${GITHUB_REPO:=meltforce/cast2md}"

echo "Configuration:"
echo "  TS_HOSTNAME: $TS_HOSTNAME"
echo "  CAST2MD_SERVER_URL: $CAST2MD_SERVER_URL"
echo "  GITHUB_REPO: $GITHUB_REPO"

# Install system dependencies
echo "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq ffmpeg curl > /dev/null

# Install Tailscale
echo "Installing Tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh

# Start tailscaled in userspace mode (for container environments)
echo "Starting tailscaled..."
tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state &
sleep 3

# Connect to Tailscale
echo "Connecting to Tailscale as $TS_HOSTNAME..."
tailscale up \
    --auth-key="$TS_AUTH_KEY" \
    --hostname="$TS_HOSTNAME" \
    --ssh \
    --accept-routes

echo "Tailscale connected!"
tailscale status

# Install cast2md from GitHub
echo "Installing cast2md from GitHub ($GITHUB_REPO)..."
pip install "cast2md[node] @ git+https://github.com/${GITHUB_REPO}.git"

# Verify installation
echo "Verifying cast2md installation..."
cast2md --version

# Register node with server
echo "Registering node with server..."
cast2md node register --server "$CAST2MD_SERVER_URL" --name "RunPod Afterburner"

# Start the transcription worker
echo "Starting transcription worker..."
cast2md node start

# Note: cast2md node start runs in foreground and will keep the container alive
