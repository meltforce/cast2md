#!/bin/bash
# RunPod Afterburner Startup Script
#
# NOTE: This file is for reference only. The actual startup script is embedded
# inline in afterburner.py (STARTUP_SCRIPT constant). If you modify this file,
# also update the inline version in afterburner.py.
#
# This script runs automatically when the pod boots via the template's dockerStartCmd.
# It only sets up Tailscale - all other setup (ffmpeg, cast2md) is done via SSH
# from afterburner.py for better visibility.

set -e

echo "=== Afterburner Startup $(date) ==="

# Required env vars from template
: "${TS_AUTH_KEY:?TS_AUTH_KEY is required}"
: "${TS_HOSTNAME:=runpod-afterburner}"

echo "Config: TS_HOSTNAME=$TS_HOSTNAME"

# === TAILSCALE SETUP ===
echo "Installing Tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh

echo "Starting tailscaled (userspace networking with HTTP proxy)..."
tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state --outbound-http-proxy-listen=localhost:1055 &

# Wait for tailscaled to be ready
echo "Waiting for tailscaled socket..."
for i in {1..30}; do
    if [ -S /var/run/tailscale/tailscaled.sock ]; then
        echo "tailscaled ready"
        break
    fi
    sleep 1
done

echo "Connecting to Tailscale as $TS_HOSTNAME..."
tailscale up --auth-key="$TS_AUTH_KEY" --hostname="$TS_HOSTNAME" --ssh --accept-dns

echo "Tailscale connected!"
tailscale status

# Export proxy for applications
export http_proxy=http://localhost:1055
export https_proxy=http://localhost:1055
echo "HTTP proxy available at localhost:1055"

# Keep container alive - setup continues via SSH from afterburner.py
echo "Container ready - waiting for SSH setup..."
tail -f /dev/null
