# Node Installation Status

## Current State (2026-01-22)

Successfully installed node on Mac Mini M4 via manual process.

### What Works
- Node registers with server
- Heartbeats working
- Job polling working
- MLX Whisper backend available
- Launchd service auto-starts on boot

### Manual Installation Steps Required

```bash
# 1. Copy repo from dev machine (excluding data/caches)
rsync -av --exclude='.git' --exclude='__pycache__' --exclude='.venv' \
  --exclude='*.egg-info' --exclude='data' --exclude='.pytest_cache' \
  --exclude='.ruff_cache' --exclude='.claude' --exclude='.env' \
  --exclude='*.db' /path/to/cast2md/ user@node:~/.cast2md/cast2md/

# 2. Create venv with Homebrew Python (system Python 3.9 is too old)
/opt/homebrew/bin/python3 -m venv ~/.cast2md/venv

# 3. Install cast2md without deps
~/.cast2md/venv/bin/pip install --no-deps -e ~/.cast2md/cast2md

# 4. Install node dependencies manually (skip faster-whisper on Apple Silicon)
~/.cast2md/venv/bin/pip install \
  httpx pydantic-settings python-dotenv click fastapi \
  'uvicorn[standard]' jinja2 mlx-whisper feedparser python-multipart

# 5. Register node
~/.cast2md/venv/bin/cast2md node register \
  --server https://cast2md.leo-royal.ts.net \
  --name "Mac Mini M4"

# 6. Create launchd plist (see below)

# 7. Load service
launchctl load ~/Library/LaunchAgents/com.cast2md.node.plist
```

### Launchd Plist

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cast2md.node</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/linus/.cast2md/venv/bin/cast2md</string>
        <string>node</string>
        <string>start</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/linus/.cast2md/cast2md</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/linus/.cast2md/node.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/linus/.cast2md/node.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>WHISPER_BACKEND</key>
        <string>mlx</string>
    </dict>
</dict>
</plist>
```

---

## Install Script Issues

The `scripts/cast2md-node.sh` script has several problems that prevent smooth installation:

### 1. GitHub Token Authentication
- Fine-grained PATs (`github_pat_...`) need `oauth2:TOKEN@` URL format
- Environment variables don't pass through `curl | bash` pipe
- Interactive prompts fail when stdin is piped

### 2. Optional Dependencies Don't Work as Intended
- `[node]` extras are **additive**, not replacements
- `pip install -e ".[node]"` still pulls all main dependencies
- Must use `--no-deps` then install deps manually

### 3. faster-whisper Build Failure
- Requires PyAV which needs `pkg-config`
- Python 3.14 wheels not available
- Not needed on Apple Silicon (use mlx-whisper instead)

### 4. CLI Imports Server Dependencies
- `cast2md node register` imports `feedparser` via `cli.py`
- Even node-only commands require server deps installed

### 5. Non-existent CLI Flag
- Script uses `--no-browser` which doesn't exist
- Node start only has `--port` option

---

## Recommendations

### Short-term: Direct rsync from dev machine
For private repo, rsync from dev machine is simplest. No auth issues.

### Medium-term: Make repo public
Eliminates all GitHub auth complexity.

### Long-term: Fix the install script

1. **Separate node CLI entry point**
   - Create `cast2md-node` command that doesn't import server modules
   - Or lazy-load imports in `cli.py`

2. **Fix dependency installation**
   - Don't use optional extras for node deps
   - Script should install deps directly (current manual approach)

3. **Skip faster-whisper on Apple Silicon**
   - Detect arch and only install mlx-whisper

4. **Remove `--no-browser` from script**
   - Flag doesn't exist

5. **For private repo: use SSH clone**
   - `git clone git@github.com:meltforce/cast2md.git`
   - Relies on SSH key, no token needed

---

## Installed Packages on Node

```
httpx, pydantic-settings, python-dotenv, click, fastapi,
uvicorn[standard], jinja2, mlx-whisper, feedparser, python-multipart
```

Plus transitive dependencies (~280 MB total).

**Not installed** (server-only):
- sentence-transformers (~500 MB)
- psycopg2-binary, pgvector
- apscheduler, bleach, mcp
- faster-whisper (build issues)
