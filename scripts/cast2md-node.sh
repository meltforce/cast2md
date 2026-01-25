#!/bin/bash
#
# cast2md Node Setup Script
# =========================
# Single script for installing, updating, and uninstalling cast2md transcriber nodes.
# Supports macOS (launchd) and Linux (systemd).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/meltforce/cast2md/main/scripts/cast2md-node.sh | bash
#
# The script prompts for action: Install/Update or Uninstall

set -e

INSTALL_DIR="$HOME/.cast2md"
REPO_DIR="$INSTALL_DIR/cast2md"
VENV_DIR="$INSTALL_DIR/venv"
LOG_FILE="$INSTALL_DIR/node.log"

# Platform-specific paths
PLIST_PATH="$HOME/Library/LaunchAgents/com.cast2md.node.plist"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
SYSTEMD_SERVICE="$SYSTEMD_USER_DIR/cast2md-node.service"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Platform detection
detect_platform() {
    case "$(uname -s)" in
        Darwin)
            PLATFORM="macos"
            ;;
        Linux)
            PLATFORM="linux"
            ;;
        *)
            PLATFORM="unknown"
            ;;
    esac
}

print_header() {
    echo ""
    echo -e "${BLUE}cast2md Node Setup${NC}"
    echo "=================="
    echo ""
}

print_step() {
    echo -e "${BLUE}[$1]${NC} $2"
}

print_success() {
    echo -e "  ${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "  ${YELLOW}!${NC} $1"
}

print_error() {
    echo -e "  ${RED}✗${NC} $1"
}

# Find Python 3.11+ (but not 3.14+ which may have compatibility issues)
find_python() {
    local candidates=()

    if [ "$PLATFORM" = "macos" ]; then
        # Try Homebrew versioned Pythons first (prefer 3.12/3.13 over 3.14+)
        candidates=(
            /opt/homebrew/bin/python3.12
            /opt/homebrew/bin/python3.13
            /opt/homebrew/bin/python3.11
            /usr/local/bin/python3.12
            /usr/local/bin/python3.13
            /usr/local/bin/python3.11
            /opt/homebrew/bin/python3
            /usr/local/bin/python3
            python3
        )
    else
        # Linux: try versioned pythons first
        candidates=(python3.12 python3.13 python3.11 python3)
    fi

    for py in "${candidates[@]}"; do
        if [ -x "$py" ] || command -v "$py" &> /dev/null; then
            version=$("$py" --version 2>&1 | cut -d' ' -f2)
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            # Accept 3.11, 3.12, 3.13 (skip 3.14+ for now due to potential compatibility issues)
            if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ] && [ "$minor" -le 13 ]; then
                PYTHON_BIN="$py"
                PYTHON_VERSION="$version"
                return 0
            fi
        fi
    done

    # Fallback: accept any 3.11+
    for py in "${candidates[@]}"; do
        if [ -x "$py" ] || command -v "$py" &> /dev/null; then
            version=$("$py" --version 2>&1 | cut -d' ' -f2)
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
                PYTHON_BIN="$py"
                PYTHON_VERSION="$version"
                return 0
            fi
        fi
    done

    return 1
}

check_prerequisites() {
    print_step "1/6" "Checking prerequisites..."

    # Check Python
    if find_python; then
        print_success "Python $PYTHON_VERSION ($PYTHON_BIN)"
    else
        if [ "$PLATFORM" = "macos" ]; then
            print_error "Python 3.11+ required. Install with: brew install python@3.12"
        else
            print_error "Python 3.11+ required. Install with: sudo apt install python3.11 python3.11-venv"
        fi
        exit 1
    fi

    # Check/install ffmpeg
    if command -v ffmpeg &> /dev/null; then
        print_success "ffmpeg"
    else
        print_warning "ffmpeg not found. Installing..."
        if [ "$PLATFORM" = "macos" ]; then
            if command -v brew &> /dev/null; then
                brew install ffmpeg
            else
                print_error "Homebrew not found. Install ffmpeg manually or install Homebrew from https://brew.sh"
                exit 1
            fi
        else
            if command -v apt-get &> /dev/null; then
                sudo apt-get update && sudo apt-get install -y ffmpeg
            elif command -v dnf &> /dev/null; then
                sudo dnf install -y ffmpeg
            elif command -v pacman &> /dev/null; then
                sudo pacman -S --noconfirm ffmpeg
            else
                print_error "Could not install ffmpeg. Please install it manually."
                exit 1
            fi
        fi
        print_success "ffmpeg installed"
    fi

    # Detect architecture for whisper backend
    if [ "$PLATFORM" = "macos" ] && [ "$(uname -m)" = "arm64" ]; then
        print_success "Apple Silicon detected (will use MLX backend)"
        USE_MLX=true
        WHISPER_BACKEND="mlx"
    else
        print_success "Using faster-whisper backend"
        USE_MLX=false
        WHISPER_BACKEND="faster-whisper"
    fi
}

clone_repo() {
    print_step "2/6" "Cloning repository..."

    mkdir -p "$INSTALL_DIR"
    git clone "https://github.com/meltforce/cast2md.git" "$REPO_DIR" 2>/dev/null

    print_success "Cloned to $REPO_DIR"
}

create_venv() {
    print_step "3/6" "Creating virtual environment..."

    "$PYTHON_BIN" -m venv "$VENV_DIR"

    print_success "Created venv with $PYTHON_BIN"
}

install_deps() {
    print_step "4/6" "Installing dependencies..."

    source "$VENV_DIR/bin/activate"

    # Upgrade pip
    pip install --quiet --upgrade pip

    # Install cast2md package without dependencies (suppress resolver warnings)
    cd "$REPO_DIR"
    pip install --quiet --no-deps -e . 2>/dev/null

    # Install node dependencies directly (minimal set for nodes)
    echo "  Installing core dependencies..."
    pip install --quiet \
        httpx \
        pydantic-settings \
        python-dotenv \
        click \
        fastapi \
        'uvicorn[standard]' \
        jinja2 \
        python-multipart

    # Install transcription backend based on architecture
    if [ "$USE_MLX" = true ]; then
        echo "  Installing MLX Whisper (Apple Silicon)..."
        pip install --quiet mlx-whisper
    else
        echo "  Installing faster-whisper..."
        pip install --quiet faster-whisper
    fi

    deactivate

    print_success "Dependencies installed"
}

register_node() {
    print_step "5/6" "Node registration"

    source "$VENV_DIR/bin/activate"

    # Check if already registered
    if [ -f "$INSTALL_DIR/node.json" ]; then
        print_success "Already registered (using existing config)"
        deactivate
        return
    fi

    printf "  Server URL: "
    read SERVER_URL < /dev/tty
    printf "  Node name: "
    read NODE_NAME < /dev/tty

    cast2md node register --server "$SERVER_URL" --name "$NODE_NAME"

    deactivate

    print_success "Registered!"
}

setup_service() {
    print_step "6/6" "Service setup"

    echo ""
    echo "  How would you like to run the node?"
    echo "    [1] Auto-start service (default)"
    echo "    [2] Shell script"
    echo "    [3] Manual"
    echo ""
    printf "  Choice [1]: "
    read SERVICE_CHOICE < /dev/tty

    # Default to 1 if empty
    SERVICE_CHOICE="${SERVICE_CHOICE:-1}"

    case "$SERVICE_CHOICE" in
        1)
            setup_autostart_service
            ;;
        2)
            setup_start_script
            ;;
        3)
            print_warning "Skipped service installation"
            echo "  Start manually with: $VENV_DIR/bin/cast2md node start --no-browser"
            ;;
        *)
            print_warning "Invalid choice. Skipping service setup."
            echo "  Start manually with: $VENV_DIR/bin/cast2md node start --no-browser"
            ;;
    esac
}

setup_autostart_service() {
    if [ "$PLATFORM" = "macos" ]; then
        setup_launchd_service
    else
        setup_systemd_service
    fi
}

setup_launchd_service() {
    # Create launchd plist for macOS
    cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cast2md.node</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/cast2md</string>
        <string>node</string>
        <string>start</string>
        <string>--no-browser</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_FILE</string>
    <key>StandardErrorPath</key>
    <string>$LOG_FILE</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>WHISPER_BACKEND</key>
        <string>$WHISPER_BACKEND</string>
    </dict>
</dict>
</plist>
EOF

    # Load the service
    launchctl load "$PLIST_PATH" 2>/dev/null || true

    print_success "launchd service installed"
    echo ""
    echo "  Service management:"
    echo "    Stop:    launchctl unload ~/Library/LaunchAgents/com.cast2md.node.plist"
    echo "    Start:   launchctl load ~/Library/LaunchAgents/com.cast2md.node.plist"
    echo "    Restart: launchctl unload ~/Library/LaunchAgents/com.cast2md.node.plist && launchctl load ~/Library/LaunchAgents/com.cast2md.node.plist"
    echo ""
    echo "  Log file: $LOG_FILE"
    echo "    tail -f $LOG_FILE"
}

setup_systemd_service() {
    # Create systemd user service for Linux
    mkdir -p "$SYSTEMD_USER_DIR"

    cat > "$SYSTEMD_SERVICE" << EOF
[Unit]
Description=cast2md Transcriber Node
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
Environment=WHISPER_BACKEND=$WHISPER_BACKEND
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$VENV_DIR/bin/cast2md node start --no-browser
Restart=always
RestartSec=10
StandardOutput=append:$LOG_FILE
StandardError=append:$LOG_FILE

[Install]
WantedBy=default.target
EOF

    # Enable lingering so user services start at boot
    loginctl enable-linger "$USER" 2>/dev/null || true

    # Reload and enable the service
    systemctl --user daemon-reload
    systemctl --user enable cast2md-node.service
    systemctl --user start cast2md-node.service

    print_success "systemd user service installed"
    echo ""
    echo "  Service management:"
    echo "    Stop:    systemctl --user stop cast2md-node"
    echo "    Start:   systemctl --user start cast2md-node"
    echo "    Restart: systemctl --user restart cast2md-node"
    echo "    Status:  systemctl --user status cast2md-node"
    echo ""
    echo "  Log file: $LOG_FILE"
    echo "    tail -f $LOG_FILE"
}

setup_start_script() {
    local script_path="$INSTALL_DIR/start-node.sh"

    cat > "$script_path" << EOF
#!/bin/bash
# cast2md node start script
# Run: $script_path

export WHISPER_BACKEND=$WHISPER_BACKEND
cd "$REPO_DIR"
exec "$VENV_DIR/bin/cast2md" node start "\$@"
EOF

    chmod +x "$script_path"

    print_success "Start script created"
    echo "  Run: $script_path"
    echo "  (Add --no-browser to run without opening browser)"
}

stop_service() {
    if [ "$PLATFORM" = "macos" ]; then
        if [ -f "$PLIST_PATH" ]; then
            echo "Stopping launchd service..."
            launchctl unload "$PLIST_PATH" 2>/dev/null || true
        fi
    else
        if systemctl --user is-active cast2md-node.service &>/dev/null; then
            echo "Stopping systemd service..."
            systemctl --user stop cast2md-node.service 2>/dev/null || true
            systemctl --user disable cast2md-node.service 2>/dev/null || true
        fi
    fi
}

start_service() {
    if [ "$PLATFORM" = "macos" ]; then
        if [ -f "$PLIST_PATH" ]; then
            echo "Starting launchd service..."
            launchctl load "$PLIST_PATH" 2>/dev/null || true
        fi
    else
        if [ -f "$SYSTEMD_SERVICE" ]; then
            echo "Starting systemd service..."
            systemctl --user start cast2md-node.service 2>/dev/null || true
        fi
    fi
}

update_install() {
    print_header
    echo "Existing installation found. Updating..."
    echo ""

    stop_service

    echo "Pulling latest changes..."
    cd "$REPO_DIR"
    git pull

    echo "Reinstalling dependencies..."
    source "$VENV_DIR/bin/activate"
    pip install --quiet --upgrade pip
    pip install --quiet --no-deps -e .

    # Reinstall node deps (minimal set)
    pip install --quiet \
        httpx \
        pydantic-settings \
        python-dotenv \
        click \
        fastapi \
        'uvicorn[standard]' \
        jinja2 \
        python-multipart

    # Check for MLX
    if [ "$PLATFORM" = "macos" ] && [ "$(uname -m)" = "arm64" ]; then
        pip install --quiet mlx-whisper
    else
        pip install --quiet faster-whisper
    fi

    deactivate

    start_service

    # Get version
    VERSION=$("$VENV_DIR/bin/cast2md" --version 2>/dev/null || echo "unknown")

    echo ""
    print_success "Updated to $VERSION"
    echo ""
    echo "Status UI: http://localhost:8001"
}

fresh_install() {
    print_header
    echo "Installing cast2md transcriber node..."
    echo ""

    check_prerequisites
    clone_repo
    create_venv
    install_deps
    register_node
    setup_service

    echo ""
    print_success "Installation complete!"
    echo ""
    echo "Status UI: http://localhost:8001"
}

uninstall() {
    print_header
    echo -e "${YELLOW}Uninstalling cast2md node...${NC}"
    echo ""

    if [ ! -d "$INSTALL_DIR" ]; then
        print_warning "No installation found at $INSTALL_DIR"
        exit 0
    fi

    # Confirm uninstall
    echo "This will:"
    echo "  - Stop and remove the service"
    echo "  - Delete $INSTALL_DIR (repo, venv, logs, config)"
    echo ""
    printf "Continue? [y/N] "
    read CONFIRM < /dev/tty

    if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
        echo "Cancelled."
        exit 0
    fi

    # Try to unregister from server first
    if [ -f "$INSTALL_DIR/node.json" ] && [ -f "$VENV_DIR/bin/cast2md" ]; then
        echo ""
        echo "Unregistering from server..."
        source "$VENV_DIR/bin/activate"
        "$VENV_DIR/bin/cast2md" node unregister --force 2>/dev/null || true
        deactivate
    fi

    # Stop service
    stop_service

    # Remove service files
    if [ "$PLATFORM" = "macos" ]; then
        if [ -f "$PLIST_PATH" ]; then
            rm -f "$PLIST_PATH"
            print_success "Removed launchd plist"
        fi
    else
        if [ -f "$SYSTEMD_SERVICE" ]; then
            rm -f "$SYSTEMD_SERVICE"
            systemctl --user daemon-reload 2>/dev/null || true
            print_success "Removed systemd service"
        fi
    fi

    # Remove installation directory
    rm -rf "$INSTALL_DIR"
    print_success "Removed $INSTALL_DIR"

    echo ""
    print_success "Uninstall complete!"
}

show_menu() {
    print_header

    # Check if already installed
    if [ -d "$REPO_DIR" ]; then
        echo "Existing installation found."
        echo ""
        echo "What would you like to do?"
        echo "  [1] Update"
        echo "  [2] Uninstall"
        echo ""
        printf "Choice [1]: "
    else
        echo "No existing installation found."
        echo ""
        echo "What would you like to do?"
        echo "  [1] Install"
        echo "  [2] Uninstall (nothing to uninstall)"
        echo ""
        printf "Choice [1]: "
    fi

    read MENU_CHOICE < /dev/tty
    MENU_CHOICE="${MENU_CHOICE:-1}"

    case "$MENU_CHOICE" in
        1)
            if [ -d "$REPO_DIR" ]; then
                detect_platform
                update_install
            else
                detect_platform
                fresh_install
            fi
            ;;
        2)
            detect_platform
            uninstall
            ;;
        *)
            echo "Invalid choice."
            exit 1
            ;;
    esac
}

# Main
detect_platform
show_menu
