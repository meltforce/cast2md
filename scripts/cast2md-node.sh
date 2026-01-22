#!/bin/bash
#
# cast2md Node Setup Script
# =========================
# Single script for installing and updating cast2md transcriber nodes on macOS.
#
# Usage:
#   curl -fsSL https://gist.githubusercontent.com/.../cast2md-node.sh | bash
#
# The script auto-detects install vs update:
#   - If ~/.cast2md/cast2md doesn't exist → Install mode
#   - If it exists → Update mode

set -e

INSTALL_DIR="$HOME/.cast2md"
REPO_DIR="$INSTALL_DIR/cast2md"
VENV_DIR="$INSTALL_DIR/venv"
TOKEN_FILE="$INSTALL_DIR/.github-token"
PLIST_PATH="$HOME/Library/LaunchAgents/com.cast2md.node.plist"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

check_prerequisites() {
    print_step "1/7" "Checking prerequisites..."

    # Check Python
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
        PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
        PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

        if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 11 ]; then
            print_success "Python $PYTHON_VERSION"
        else
            print_error "Python 3.11+ required (found $PYTHON_VERSION)"
            exit 1
        fi
    else
        print_error "Python 3 not found. Install with: brew install python@3.12"
        exit 1
    fi

    # Check Homebrew
    if command -v brew &> /dev/null; then
        print_success "Homebrew"
    else
        print_error "Homebrew not found. Install from https://brew.sh"
        exit 1
    fi

    # Check ffmpeg
    if command -v ffmpeg &> /dev/null; then
        print_success "ffmpeg"
    else
        print_warning "ffmpeg not found. Installing..."
        brew install ffmpeg
        print_success "ffmpeg installed"
    fi
}

check_github_auth() {
    print_step "2/7" "GitHub authentication"

    # Try to access repo without auth first (in case it's public)
    # GIT_TERMINAL_PROMPT=0 prevents git from asking for credentials
    if GIT_TERMINAL_PROMPT=0 git ls-remote https://github.com/meltforce/cast2md.git HEAD &> /dev/null; then
        print_success "Repository is public, no auth needed"
        GITHUB_TOKEN=""
        return
    fi

    # Check for GITHUB_TOKEN env variable
    if [ -n "$GITHUB_TOKEN" ]; then
        if git ls-remote "https://${GITHUB_TOKEN}@github.com/meltforce/cast2md.git" HEAD &> /dev/null; then
            # Save token for future updates
            mkdir -p "$INSTALL_DIR"
            echo "$GITHUB_TOKEN" > "$TOKEN_FILE"
            chmod 600 "$TOKEN_FILE"
            print_success "Using token from environment"
            return
        else
            print_error "GITHUB_TOKEN is invalid"
            exit 1
        fi
    fi

    # Check for saved token
    if [ -f "$TOKEN_FILE" ]; then
        GITHUB_TOKEN=$(cat "$TOKEN_FILE")
        if git ls-remote "https://${GITHUB_TOKEN}@github.com/meltforce/cast2md.git" HEAD &> /dev/null; then
            print_success "Using saved token"
            return
        else
            print_warning "Saved token is invalid"
            rm -f "$TOKEN_FILE"
        fi
    fi

    # No token available
    print_error "Repository is private. Set GITHUB_TOKEN environment variable:"
    echo "  GITHUB_TOKEN=ghp_xxx curl -fsSL ... | bash"
    exit 1
}

clone_repo() {
    print_step "3/7" "Cloning repository..."

    mkdir -p "$INSTALL_DIR"

    if [ -n "$GITHUB_TOKEN" ]; then
        git clone "https://${GITHUB_TOKEN}@github.com/meltforce/cast2md.git" "$REPO_DIR" 2>/dev/null
    else
        git clone "https://github.com/meltforce/cast2md.git" "$REPO_DIR" 2>/dev/null
    fi

    print_success "Cloned to $REPO_DIR"
}

create_venv() {
    print_step "4/7" "Creating virtual environment..."

    python3 -m venv "$VENV_DIR"

    # Detect Apple Silicon
    if [ "$(uname -m)" = "arm64" ]; then
        print_success "Detected Apple Silicon, will use MLX backend"
        USE_MLX=true
    else
        print_success "Using faster-whisper backend"
        USE_MLX=false
    fi
}

install_deps() {
    print_step "5/7" "Installing dependencies..."

    source "$VENV_DIR/bin/activate"

    # Upgrade pip
    pip install --quiet --upgrade pip

    # Install with node extras (minimal dependencies)
    cd "$REPO_DIR"
    pip install --quiet --no-deps -e .
    pip install --quiet -e ".[node]"

    if [ "$USE_MLX" = true ]; then
        pip install --quiet -e ".[node-mlx]"
    fi

    deactivate

    print_success "Dependencies installed"
}

register_node() {
    print_step "6/7" "Node registration"

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
    print_step "7/7" "Install as startup service?"

    printf "  Install as startup service? [Y/n] "
    read INSTALL_SERVICE < /dev/tty

    if [ "$INSTALL_SERVICE" != "n" ] && [ "$INSTALL_SERVICE" != "N" ]; then
        # Create launchd plist
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
    <string>$INSTALL_DIR/node.log</string>
    <key>StandardErrorPath</key>
    <string>$INSTALL_DIR/node.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
EOF

        # Load the service
        launchctl load "$PLIST_PATH" 2>/dev/null || true

        print_success "Service installed"
        echo "  Log file: $INSTALL_DIR/node.log"
    else
        print_warning "Skipped service installation"
        echo "  Start manually with: $VENV_DIR/bin/cast2md node start"
    fi
}

stop_service() {
    if [ -f "$PLIST_PATH" ]; then
        echo "Stopping service..."
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
    fi
}

start_service() {
    if [ -f "$PLIST_PATH" ]; then
        echo "Starting service..."
        launchctl load "$PLIST_PATH" 2>/dev/null || true
    fi
}

update_install() {
    print_header
    echo "Existing installation found. Updating..."
    echo ""

    stop_service

    # Load saved token if available
    if [ -f "$TOKEN_FILE" ]; then
        GITHUB_TOKEN=$(cat "$TOKEN_FILE")
    else
        GITHUB_TOKEN=""
    fi

    echo "Pulling latest changes..."
    cd "$REPO_DIR"
    if [ -n "$GITHUB_TOKEN" ]; then
        git remote set-url origin "https://${GITHUB_TOKEN}@github.com/meltforce/cast2md.git"
    fi
    git pull

    echo "Reinstalling dependencies..."
    source "$VENV_DIR/bin/activate"
    pip install --quiet --upgrade pip
    pip install --quiet --no-deps -e .
    pip install --quiet -e ".[node]"

    # Check for MLX
    if [ "$(uname -m)" = "arm64" ]; then
        pip install --quiet -e ".[node-mlx]"
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
    echo "No existing installation found. Starting install..."
    echo ""

    check_prerequisites
    check_github_auth
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

# Main
if [ -d "$REPO_DIR" ]; then
    update_install
else
    fresh_install
fi
