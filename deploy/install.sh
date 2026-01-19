#!/bin/bash
set -e

APP_DIR="/opt/cast2md"
DATA_DIR="$APP_DIR/data"
NAS_MOUNT="/mnt/nas/cast2md"

echo "=== cast2md Installation Script ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root"
    exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
apt update
apt install -y python3.11 python3.11-venv python3-pip git curl

# Install uv
echo "Installing uv..."
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Clone or update repository
echo "Setting up application..."
if [ -d "$APP_DIR" ]; then
    echo "Updating existing installation..."
    cd $APP_DIR && git pull
else
    echo "Cloning repository..."
    git clone https://github.com/meltforce/cast2md.git $APP_DIR
fi

cd $APP_DIR

# Create virtual environment and install dependencies
echo "Installing Python dependencies..."
uv sync --frozen

# Create data directory (local, for SQLite)
echo "Creating data directories..."
mkdir -p $DATA_DIR
mkdir -p $DATA_DIR/temp

# Create .env if not exists
if [ ! -f "$APP_DIR/.env" ]; then
    echo "Creating default .env configuration..."
    cat > "$APP_DIR/.env" << EOF
DATABASE_PATH=$DATA_DIR/cast2md.db
STORAGE_PATH=$NAS_MOUNT
TEMP_DOWNLOAD_PATH=$DATA_DIR/temp
WHISPER_MODEL=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
EOF
    echo "Created .env file - please review and adjust settings as needed"
else
    echo ".env file already exists, keeping current configuration"
fi

# Initialize database if needed
if [ ! -f "$DATA_DIR/cast2md.db" ]; then
    echo "Initializing database..."
    .venv/bin/python -m cast2md init-db
fi

# Install systemd service
echo "Installing systemd service..."
cp deploy/cast2md.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable cast2md
systemctl start cast2md

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Service status:"
systemctl status cast2md --no-pager || true
echo ""
echo "Access the web interface at: http://$(hostname -I | awk '{print $1}'):8000"
echo ""
echo "Useful commands:"
echo "  systemctl status cast2md    - Check service status"
echo "  systemctl restart cast2md   - Restart service"
echo "  journalctl -u cast2md -f    - View logs"
echo ""
echo "Configuration file: $APP_DIR/.env"
echo "Data directory: $DATA_DIR"
echo "NAS mount point: $NAS_MOUNT (ensure NFS is configured)"
