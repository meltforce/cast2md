#!/bin/bash
# Node setup script for RunPod afterburner pods.
#
# NOTE: This script is for reference only. The afterburner.py script
# runs these commands via SSH automatically. This file documents the
# setup steps for manual debugging or alternative deployment methods.
#
# Environment variables required:
#   TS_AUTH_KEY - Tailscale auth key (ephemeral, reusable)
#   TS_HOSTNAME - Hostname to use on Tailscale network

set -e

echo "[setup] Starting node setup..."

# Install system dependencies
echo "[setup] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq ffmpeg curl > /dev/null

# Install Tailscale
echo "[setup] Installing Tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh

# Start Tailscale in userspace mode (no root/tun needed in container)
echo "[setup] Starting Tailscale daemon..."
nohup tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state > /var/log/tailscaled.log 2>&1 &

# Wait for tailscaled to be ready
sleep 3

# Connect to Tailscale
echo "[setup] Connecting to Tailscale network..."
tailscale up \
    --auth-key="${TS_AUTH_KEY}" \
    --hostname="${TS_HOSTNAME:-runpod-afterburner}" \
    --ssh \
    --accept-routes

echo "[setup] Tailscale connected!"
tailscale status

echo "[setup] Setup complete!"
