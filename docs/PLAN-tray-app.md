# Plan: System Tray App for Transcriber Node

## Overview

Wrap the existing Python transcriber node into a system tray application that runs in the background, showing status and allowing control without a terminal. Two separate implementations for optimal native experience:

1. **macOS**: rumps (better AppKit integration, template icons, native notifications)
2. **Linux**: pystray with GTK/AppIndicator

## Why Two Clients?

**rumps advantages on macOS:**
- Template icons (auto dark/light mode adaptation)
- Native macOS notifications via `rumps.notification()`
- Built-in timer decorators for status updates
- Cleaner API, less boilerplate
- Better integration with Apple Silicon

**pystray for Linux:**
- Works well with GTK and AppIndicator
- No macOS-specific dependencies

## Architecture

### Shared Core

Both clients embed `TranscriberNodeWorker` directly (no subprocess):
- Direct access to worker state (current job, progress)
- No IPC complexity
- Single process, cleaner shutdown

The existing FastAPI status server (port 8001) runs alongside both clients.

### Menu Structure (Both Platforms)

```
[Microphone Icon]
├── Status: Running / Waiting for jobs
├── ─────────────────────────
├── Current Job: Episode Title
│   └── Progress: 45%
├── ─────────────────────────
├── Start/Stop Worker (toggle)
├── Open Status UI... (opens http://localhost:8001)
├── ─────────────────────────
└── Quit
```

## Files to Create

```
src/cast2md/tray/
├── __init__.py
├── base.py             # Shared logic (worker management, state)
├── macos.py            # rumps implementation
├── linux.py            # pystray implementation
├── __main__.py         # Platform detection and dispatch
└── icons/
    ├── icon.png        # Standard icon
    ├── icon_busy.png   # Busy state
    └── icon.icns       # macOS app icon (for bundling)
```

## Implementation Phases

### Phase 1: macOS Client (rumps)
1. Add `rumps>=0.4.0` as macOS-only dependency
2. Create `src/cast2md/tray/base.py` with shared `TranscriberTrayBase` class
3. Create `src/cast2md/tray/macos.py` with `TranscriberMenuBar(rumps.App)`
4. Embed `TranscriberNodeWorker` with start/stop controls
5. Start FastAPI status server alongside
6. Add `@rumps.timer(2)` for status updates
7. Add CLI entry point: `cast2md tray`

### Phase 2: Linux Client (pystray)
1. Add `pystray>=0.19.0` and `Pillow` as Linux dependencies
2. Create `src/cast2md/tray/linux.py` with `TranscriberTray` class
3. Add background thread for status updates (pystray has no timer)
4. Test on Ubuntu/Debian with GNOME and KDE

### Phase 3: Polish
1. Add notifications (rumps.notification on macOS, plyer on Linux)
2. Design template icons for macOS dark/light mode
3. Dynamic menu updates with job progress

### Phase 4: Bundling (Future/Optional)
- macOS: py2app to create .app bundle
- Linux: AppImage or .deb package

## Key Implementation Details

### Shared Base Class

```python
# src/cast2md/tray/base.py
import threading
from cast2md.node.worker import TranscriberNodeWorker
from cast2md.node.config import load_config
from cast2md.node.server import run_status_server

class TranscriberTrayBase:
    """Shared logic for tray apps."""

    def __init__(self):
        self.worker = None
        self.server_thread = None

    def start_worker(self):
        if self.worker and self.worker.is_running:
            return False
        config = load_config()
        if not config:
            self.show_error("Not configured. Run 'cast2md node register' first.")
            return False
        self.worker = TranscriberNodeWorker(config)
        self.worker.start()
        # Start status server
        self.server_thread = threading.Thread(
            target=run_status_server,
            args=(self.worker, 8001),
            daemon=True
        )
        self.server_thread.start()
        return True

    def stop_worker(self):
        if self.worker:
            self.worker.stop()
            self.worker = None

    def get_status(self) -> tuple[str, str]:
        """Returns (title, tooltip) for current state."""
        if not self.worker or not self.worker.is_running:
            return (None, "Transcriber Node (stopped)")
        if self.worker.current_job:
            job = self.worker.current_job
            return ("Transcribing...", f"Transcribing: {job['episode_title'][:40]}")
        return ("Waiting", "Waiting for jobs...")

    def open_status_ui(self):
        import webbrowser
        webbrowser.open("http://localhost:8001")

    def show_error(self, message: str):
        """Override in subclass for platform-specific error dialog."""
        raise NotImplementedError
```

### macOS Implementation (rumps)

```python
# src/cast2md/tray/macos.py
import rumps
from .base import TranscriberTrayBase

class TranscriberMenuBar(rumps.App, TranscriberTrayBase):
    def __init__(self):
        rumps.App.__init__(self, "Transcriber", icon="icon.png", template=True)
        TranscriberTrayBase.__init__(self)

        self.menu = [
            rumps.MenuItem("Status: Not Running"),
            None,
            rumps.MenuItem("Current Job: None"),
            None,
            rumps.MenuItem("Start Worker", callback=self.toggle_worker),
            rumps.MenuItem("Open Status UI", callback=lambda _: self.open_status_ui()),
            None,
        ]

    def toggle_worker(self, sender):
        if self.worker and self.worker.is_running:
            self.stop_worker()
            sender.title = "Start Worker"
            rumps.notification("Transcriber", "", "Worker stopped")
        else:
            if self.start_worker():
                sender.title = "Stop Worker"
                rumps.notification("Transcriber", "", "Worker started")

    @rumps.timer(2)
    def update_status(self, _):
        title, tooltip = self.get_status()
        self.title = title
        self.menu["Status: Not Running"].title = f"Status: {tooltip.split(':')[0] if ':' in tooltip else tooltip}"
        if self.worker and self.worker.current_job:
            self.menu["Current Job: None"].title = f"Job: {self.worker.current_job['episode_title'][:30]}"
        else:
            self.menu["Current Job: None"].title = "Current Job: None"

    def show_error(self, message: str):
        rumps.alert("Error", message)


def main():
    TranscriberMenuBar().run()
```

### Linux Implementation (pystray)

```python
# src/cast2md/tray/linux.py
import threading
import time
import pystray
from PIL import Image
from pathlib import Path
from .base import TranscriberTrayBase

class TranscriberTray(TranscriberTrayBase):
    def __init__(self):
        super().__init__()
        self.running = True
        self._build_icon()

    def _build_icon(self):
        icon_path = Path(__file__).parent / "icons" / "icon.png"
        image = Image.open(icon_path)

        self.icon = pystray.Icon(
            "transcriber",
            image,
            "Transcriber Node",
            menu=pystray.Menu(
                pystray.MenuItem("Status: Not Running", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Start Worker", self.toggle_worker),
                pystray.MenuItem("Open Status UI", lambda: self.open_status_ui()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self.quit_app),
            )
        )

    def toggle_worker(self, icon, item):
        if self.worker and self.worker.is_running:
            self.stop_worker()
        else:
            self.start_worker()

    def quit_app(self, icon, item):
        self.running = False
        self.stop_worker()
        icon.stop()

    def run(self):
        updater = threading.Thread(target=self._status_loop, daemon=True)
        updater.start()
        self.icon.run()

    def _status_loop(self):
        while self.running:
            _, tooltip = self.get_status()
            self.icon.title = tooltip
            time.sleep(2)

    def show_error(self, message: str):
        # Could use plyer or zenity for Linux dialogs
        print(f"Error: {message}")


def main():
    TranscriberTray().run()
```

### Entry Point with Platform Detection

```python
# src/cast2md/tray/__main__.py
import sys

def main():
    if sys.platform == "darwin":
        from .macos import main as run
    elif sys.platform == "linux":
        from .linux import main as run
    else:
        print(f"Unsupported platform: {sys.platform}")
        sys.exit(1)
    run()

if __name__ == "__main__":
    main()
```

## Dependencies

```toml
# pyproject.toml additions

[project.optional-dependencies]
tray-macos = ["rumps>=0.4.0"]
tray-linux = ["pystray>=0.19.0", "Pillow>=10.0.0"]
tray = [
    "rumps>=0.4.0; sys_platform == 'darwin'",
    "pystray>=0.19.0; sys_platform == 'linux'",
    "Pillow>=10.0.0; sys_platform == 'linux'",
]

[project.scripts]
cast2md = "cast2md.cli:main"

[project.gui-scripts]
cast2md-tray = "cast2md.tray:main"
```

## Critical Files to Modify/Reference

- `src/cast2md/node/worker.py` - Worker class to embed
- `src/cast2md/node/config.py` - Config loading
- `src/cast2md/node/server.py` - Status server to run alongside
- `pyproject.toml` - Add dependencies and entry points

## Verification

### macOS
1. `pip install -e ".[tray-macos]"`
2. Run `cast2md tray` - verify icon appears in menu bar
3. Click Start Worker - should connect to server
4. Queue an episode - verify "Transcribing..." appears
5. Verify notification on job completion

### Linux
1. `pip install -e ".[tray-linux]"`
2. Run `cast2md tray` - verify icon appears in system tray
3. Same functional tests as macOS
4. Test on GNOME and KDE
