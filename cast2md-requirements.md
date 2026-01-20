# cast2md - Requirements Document v0.3

## 1. Overview

**cast2md** is a self-hosted podcast transcription service that automatically downloads podcast episodes and generates text transcripts using Whisper. It provides a web UI for feed management and supports bulk transcription of historical episodes.

### Goals
- Reliable, automated podcast episode downloading (independent of Audiobookshelf)
- Automatic transcription of new episodes using Faster Whisper
- Backfill capability for historical episodes
- Structured storage of audio files and transcripts on NAS
- Simple web UI for feed and episode management

### Non-Goals (v1)
- Podcast playback functionality
- Full-text search across transcripts (future: MCP server)
- SRT/VTT subtitle formats (v2)

---

## 2. Functional Requirements

### 2.1 Feed Management

| Requirement | Description |
|-------------|-------------|
| Add Feed | Add RSS feed URL via web UI with immediate validation |
| Remove Feed | Remove feed and optionally associated files |
| Edit Feed | Modify feed settings (name, language, auto-download) |
| Feed List | Display all feeds with status (last polled, episode count, errors) |

**Feed Validation on Add:**
- HTTP HEAD request to verify URL accessibility
- Parse response to confirm valid RSS/XML
- Extract feed metadata (title, description) for auto-population
- Reject with clear error message if validation fails

**Feed Configuration Options:**
- `name`: Display name (auto-populated from feed, editable)
- `url`: RSS feed URL (supports private feeds with embedded tokens)
- `default_language`: Default language for transcription (`en`, `de`, or `auto`)
- `auto_download`: Enable/disable automatic downloading of new episodes
- `enabled`: Enable/disable feed entirely

### 2.2 Episode Handling

| Requirement | Description |
|-------------|-------------|
| Auto-Download | Poll feeds every 60 minutes, download new episodes |
| Manual Download | Trigger download for specific episode via UI |
| Backfill | Download/transcribe all episodes after a specified date |
| Deduplication | Track episodes by GUID to prevent duplicate processing |
| Podcast 2.0 Transcripts | Detect `<podcast:transcript>` tag and download existing transcript instead of running Whisper |

**Episode States:**
```
pending → downloading → downloaded → transcribing → completed
                ↓              ↓              ↓
              failed        failed         failed
```

### 2.3 Transcription

| Requirement | Description |
|-------------|-------------|
| Engine | Faster Whisper as Python library (in-process) |
| Hardware | Intel iGPU or CPU fallback |
| Model | `small` (default), configurable via ENV |
| Language | Configurable per feed, with per-episode override |
| Output Format | Plain text (v1), SRT/VTT (v2) |
| Queue | FIFO with priority (new episodes before backfill) |
| Concurrency | Sequential (1 at a time) to avoid GPU contention |

**Language Configuration:**
- Feed default: `en`, `de`, or `auto`
- Episode override: Set via UI before/after transcription
- Auto-detect: Let Whisper determine language (slightly slower)

### 2.4 Audio Preprocessing

Audio files may require preprocessing before transcription. The architecture supports an extensible preprocessing pipeline.

**v1 Implementation:**
- Passthrough (no preprocessing)
- Clear function stub for future extension

**Future Extensions (v1.1+):**
- Mono downmix (stereo → mono)
- Resample to 16kHz (Whisper native rate)
- Silence trimming (reduce hallucinations)
- Volume normalization

```python
async def preprocess_audio(input_path: Path) -> Path:
    """
    Prepare audio for Whisper transcription.
    
    v1: Passthrough (return input unchanged)
    v1.1+: ffmpeg processing pipeline
    
    Returns path to processed file (may be same as input).
    """
    # Future: ffmpeg -i input -ac 1 -ar 16000 -af silenceremove output.wav
    return input_path
```

### 2.5 Error Handling

| Requirement | Description |
|-------------|-------------|
| Retry Queue | Failed jobs retry up to N times (configurable, default: 3) |
| Retry Delay | Exponential backoff (5min, 25min, 125min) |
| Notifications | Send ntfy notification after final failure |
| Error Visibility | Failed episodes visible in UI with error message |

**Notification Payload (ntfy):**
```json
{
  "topic": "cast2md",
  "title": "Transcription Failed",
  "message": "Episode '{title}' from '{podcast}' failed after 3 retries: {error}",
  "priority": 3,
  "tags": ["warning", "podcast"]
}
```

### 2.6 Storage

**Directory Structure:**
```
/mnt/pod-archiv/
├── Podcast Name/
│   ├── audio/
│   │   ├── 2024-01-15_Episode-Title.mp3
│   │   └── 2024-01-08_Another-Episode.mp3
│   └── transcripts/
│       ├── 2024-01-15_Episode-Title.txt
│       └── 2024-01-08_Another-Episode.txt
└── Another Podcast/
    ├── audio/
    └── transcripts/
```

**Filename Convention:**
- Format: `{YYYY-MM-DD}_{sanitized-title}.{ext}`
- Sanitization: Replace non-alphanumeric chars with `-`, collapse multiple `-`, trim to 200 chars
- Example: `2024-01-15_Episode-42-Interview-with-John-Doe.mp3`

**Download Strategy:**
1. Download to temporary directory (`/tmp/cast2md/downloads/`)
2. Verify file integrity (size > 0, valid audio header)
3. `shutil.move()` to final NFS destination
4. This prevents partial files from appearing in the archive

**Storage Location:**
- NFS mount from Synology NAS (`lebowski`)
- Mount point in container: `/mnt/pod-archiv`

---

## 3. Architecture

### 3.1 System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        cast2md                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  FastAPI    │  │  Scheduler  │  │  Whisper Worker     │  │
│  │  Web UI     │  │  (APSched)  │  │  (faster-whisper)   │  │
│  │  Port 8000  │  │             │  │  in-process library │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│  SQLite (WAL)  │  /tmp/downloads  │  ~/.cache/huggingface  │
├─────────────────────────────────────────────────────────────┤
│              NFS Mount: /mnt/pod-archiv                      │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Design Decisions

**Whisper as Library (not separate container):**
- Direct file access via shared NFS mount (no HTTP upload overhead)
- Model loaded once at startup, reused for all transcriptions
- Simpler deployment (single container)
- Eliminates timeout issues with large files

**SQLite with WAL Mode:**
- Enables concurrent read/write access
- Scheduler can poll while UI reads/writes
- Required initialization:
  ```sql
  PRAGMA journal_mode=WAL;
  PRAGMA busy_timeout=5000;
  ```

**Sequential Transcription:**
- One transcription job at a time
- Prevents GPU memory contention with Immich ML
- Queue ensures fairness and predictable resource usage

### 3.3 Components

**FastAPI Application:**
- Web UI (server-side rendered with Jinja2)
- REST API for all operations
- Background scheduler for feed polling (APScheduler)
- Download manager with async queue

**Whisper Worker:**
- Runs in dedicated thread/process
- Loads model once at startup
- Processes transcription queue sequentially
- Direct file access (no network transfer)

**SQLite Database:**
- Single file with WAL mode enabled
- Handles concurrent access from API and scheduler
- Sufficient for 20 feeds, thousands of episodes

### 3.4 Technology Stack

| Component | Technology | Rationale |
|-----------|------------|-----------|
| Language | Python 3.11+ | Excellent RSS/audio libraries, Whisper integration |
| Framework | FastAPI | Async support, auto-generated API docs |
| Database | SQLite (WAL) | Simple, concurrent access, no separate container |
| Transcription | faster-whisper | 4x faster than OpenAI Whisper, CTranslate2 backend |
| RSS Parsing | feedparser + podcastparser | Robust, Podcast 2.0 namespace support |
| HTTP Client | httpx | Async, streaming downloads |
| Scheduler | APScheduler | Lightweight, integrated |
| Templates | Jinja2 | Server-side rendering, no JS framework needed |
| CSS | Pico CSS | Minimal, classless CSS for quick UI |
| Audio (future) | ffmpeg-python | Preprocessing pipeline |

---

## 4. Data Model

### 4.1 Database Initialization

```python
def init_database(db_path: str):
    """Initialize database with WAL mode for concurrent access."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    # Create tables...
    conn.close()
```

### 4.2 Feed

```sql
CREATE TABLE feed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    default_language TEXT DEFAULT 'auto',  -- 'en', 'de', 'auto'
    auto_download BOOLEAN DEFAULT TRUE,
    enabled BOOLEAN DEFAULT TRUE,
    polling_interval_minutes INTEGER DEFAULT 60,
    last_polled_at TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 4.3 Episode

```sql
CREATE TABLE episode (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL REFERENCES feed(id) ON DELETE CASCADE,
    guid TEXT NOT NULL,  -- Unique identifier from RSS
    title TEXT NOT NULL,
    audio_url TEXT NOT NULL,
    transcript_url TEXT,  -- Podcast 2.0 transcript URL if available
    published_at TIMESTAMP,
    duration_seconds INTEGER,
    
    -- Processing state
    status TEXT DEFAULT 'pending',  -- pending, downloading, downloaded, transcribing, completed, failed
    transcript_source TEXT,  -- 'whisper' or 'podcast20'
    language_override TEXT,  -- Override feed default
    
    -- File paths (relative to /mnt/pod-archiv/{podcast-name}/)
    audio_path TEXT,
    transcript_path TEXT,
    
    -- Error handling
    retry_count INTEGER DEFAULT 0,
    last_error TEXT,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    downloaded_at TIMESTAMP,
    transcribed_at TIMESTAMP,
    
    UNIQUE(feed_id, guid)
);

CREATE INDEX idx_episode_status ON episode(status);
CREATE INDEX idx_episode_feed_id ON episode(feed_id);
```

### 4.4 Job Queue

```sql
CREATE TABLE job_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,  -- 'download' or 'transcribe'
    priority INTEGER DEFAULT 10,  -- Lower = higher priority (new=1, backfill=10)
    status TEXT DEFAULT 'queued',  -- queued, running, completed, failed
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    scheduled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    next_retry_at TIMESTAMP,
    error_message TEXT
);

CREATE INDEX idx_job_queue_status ON job_queue(status, priority);
```

---

## 5. API Endpoints

### 5.1 Feeds

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/feeds` | List all feeds |
| POST | `/api/feeds` | Add new feed (validates URL) |
| GET | `/api/feeds/{id}` | Get feed details |
| PATCH | `/api/feeds/{id}` | Update feed |
| DELETE | `/api/feeds/{id}` | Delete feed |
| POST | `/api/feeds/{id}/refresh` | Force poll feed now |

### 5.2 Episodes

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/feeds/{id}/episodes` | List episodes for feed |
| GET | `/api/episodes/{id}` | Get episode details |
| POST | `/api/episodes/{id}/download` | Trigger manual download |
| POST | `/api/episodes/{id}/transcribe` | Trigger manual transcription |
| PATCH | `/api/episodes/{id}` | Update episode (e.g., language override) |
| POST | `/api/feeds/{id}/backfill` | Start backfill job |

**Backfill Request:**
```json
{
  "after_date": "2024-01-01",
  "max_episodes": 50
}
```

### 5.3 System

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | System status (queue length, active jobs, model loaded) |
| GET | `/api/queue` | View job queue |
| POST | `/api/queue/{id}/retry` | Retry failed job |
| POST | `/api/queue/{id}/cancel` | Cancel queued job |

---

## 6. Configuration

### Environment Variables

```bash
# Database
DATABASE_PATH=/app/data/cast2md.db

# Storage
STORAGE_PATH=/mnt/pod-archiv
TEMP_DOWNLOAD_PATH=/tmp/cast2md/downloads

# Whisper
WHISPER_MODEL=small                    # tiny, base, small, medium, large-v3, distil-medium.en
WHISPER_DEVICE=auto                    # auto, cpu, cuda
WHISPER_COMPUTE_TYPE=auto              # auto, int8, float16, float32

# Downloads
MAX_CONCURRENT_DOWNLOADS=3

# Notifications
NTFY_URL=https://ntfy.example.com
NTFY_TOPIC=cast2md

# Retry
MAX_RETRY_ATTEMPTS=3
RETRY_BACKOFF_BASE=300                 # seconds (5min, 25min, 125min with exponential)

# Polling
DEFAULT_POLLING_INTERVAL=60            # minutes
```

---

## 7. Deployment

### 7.1 Docker Compose

```yaml
version: "3.8"

services:
  cast2md:
    build: .
    container_name: cast2md
    ports:
      - "8000:8000"
    volumes:
      # Application data (database, logs)
      - ./data:/app/data
      # Podcast archive (NFS mount on host)
      - /mnt/nfs/pod-archiv:/mnt/pod-archiv
      # Whisper model cache (persist across restarts)
      - whisper-models:/root/.cache/huggingface
      # Temp downloads (can use tmpfs for performance)
      - /tmp/cast2md:/tmp/cast2md
    environment:
      - DATABASE_PATH=/app/data/cast2md.db
      - STORAGE_PATH=/mnt/pod-archiv
      - TEMP_DOWNLOAD_PATH=/tmp/cast2md/downloads
      - WHISPER_MODEL=small
      - WHISPER_DEVICE=auto
      - MAX_CONCURRENT_DOWNLOADS=3
      - NTFY_URL=https://ntfy.leo-royal.ts.net
      - NTFY_TOPIC=cast2md
    # For Intel iGPU access (OpenVINO)
    devices:
      - /dev/dri:/dev/dri
    restart: unless-stopped

volumes:
  whisper-models:
```

### 7.2 Dockerfile

```dockerfile
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create directories
RUN mkdir -p /app/data /tmp/cast2md/downloads

EXPOSE 8000

CMD ["uvicorn", "cast2md.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 7.3 Requirements.txt

```
# Web framework
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
jinja2>=3.1.0
python-multipart>=0.0.6

# Database
aiosqlite>=0.19.0

# RSS/Podcast
feedparser>=6.0.0
podcastparser>=0.6.0

# HTTP client
httpx>=0.26.0

# Transcription
faster-whisper>=1.0.0

# Scheduling
apscheduler>=3.10.0

# Utilities
python-dotenv>=1.0.0
```

### 7.4 Target Environment

- **Host:** Proxmox LXC on `dude` (i5-1340P, 62GB RAM, Intel iGPU)
- **GPU:** Intel iGPU (shared with Immich ML) - sequential transcription prevents contention
- **Storage:** NFS mount to Synology `lebowski`
- **Network:** Tailscale for remote access

---

## 8. Development Phases

### Phase 1: Core CLI (v0.1)
- [ ] Project structure and configuration
- [ ] SQLite database with WAL mode
- [ ] RSS feed parsing with feedparser
- [ ] Episode download with streaming to temp + move
- [ ] Whisper transcription via faster-whisper library
- [ ] File storage with naming convention
- [ ] Basic CLI for testing

### Phase 2: Web Service (v0.2)
- [ ] FastAPI application structure
- [ ] Feed CRUD endpoints with validation
- [ ] Episode list/detail endpoints
- [ ] Basic web UI (list feeds, view episodes)
- [ ] Background scheduler for polling

### Phase 3: Queue System (v0.3)
- [ ] Job queue implementation
- [ ] Download queue with concurrency limit (3)
- [ ] Transcription queue (sequential)
- [ ] Status tracking in UI
- [ ] Progress indication

### Phase 4: Advanced Features (v0.4)
- [ ] Podcast 2.0 transcript detection and download
- [ ] Backfill functionality with date filter
- [ ] Language override per episode
- [ ] Retry logic with exponential backoff
- [ ] ntfy notifications on failure

### Phase 5: Production Ready (v1.0)
- [ ] Docker build and compose
- [ ] Health checks endpoint
- [ ] Structured logging
- [ ] Graceful shutdown (finish current job)
- [ ] Documentation
- [ ] Error recovery on restart (resume interrupted jobs)

### Future (v2.0+)
- [ ] Audio preprocessing pipeline (ffmpeg)
- [ ] MCP server for transcript access
- [ ] SRT/VTT output formats
- [ ] OPML import
- [ ] Feed authentication (HTTP Basic, custom headers)
- [ ] Full-text search across transcripts
- [ ] Speaker diarization

---

## 9. Implementation Notes

### 9.1 Whisper Model Loading

```python
from faster_whisper import WhisperModel
import threading

class TranscriptionService:
    _instance = None
    _lock = threading.Lock()
    
    def __init__(self, model_size: str = "small", device: str = "auto"):
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type="auto"
        )
    
    @classmethod
    def get_instance(cls) -> "TranscriptionService":
        """Singleton to ensure model is loaded only once."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(
                        model_size=settings.WHISPER_MODEL,
                        device=settings.WHISPER_DEVICE
                    )
        return cls._instance
    
    def transcribe(self, audio_path: Path, language: str = None) -> str:
        """Transcribe audio file to text."""
        segments, info = self.model.transcribe(
            str(audio_path),
            language=language if language != "auto" else None,
            beam_size=5,
            vad_filter=True,  # Helps with silence/music
        )
        return " ".join(segment.text for segment in segments)
```

### 9.2 Download with Temp + Move

```python
import shutil
import httpx
from pathlib import Path

async def download_episode(url: str, final_path: Path, temp_dir: Path) -> Path:
    """Download to temp directory, then move to final location."""
    temp_path = temp_dir / f"{uuid.uuid4()}.tmp"
    
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(temp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
        
        # Verify file is not empty
        if temp_path.stat().st_size == 0:
            raise ValueError("Downloaded file is empty")
        
        # Ensure parent directory exists
        final_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Atomic move to final destination
        shutil.move(str(temp_path), str(final_path))
        return final_path
        
    finally:
        # Cleanup temp file if still exists
        temp_path.unlink(missing_ok=True)
```

### 9.3 Filename Sanitization

```python
import re
from datetime import datetime

def sanitize_filename(title: str, max_length: int = 200) -> str:
    """Convert episode title to safe filename."""
    # Replace non-alphanumeric with hyphen
    safe = re.sub(r'[^a-zA-Z0-9\-]', '-', title)
    # Collapse multiple hyphens
    safe = re.sub(r'-+', '-', safe)
    # Strip leading/trailing hyphens
    safe = safe.strip('-')
    # Truncate
    return safe[:max_length]

def episode_filename(published_at: datetime, title: str, extension: str) -> str:
    """Generate standardized episode filename."""
    date_str = published_at.strftime("%Y-%m-%d")
    safe_title = sanitize_filename(title)
    return f"{date_str}_{safe_title}.{extension}"
```

---

## 10. References

- [feedparser Documentation](https://feedparser.readthedocs.io/)
- [Podcast Namespace (Podcast 2.0)](https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md#transcript)
- [faster-whisper GitHub](https://github.com/SYSTRAN/faster-whisper)
- [Whisper Model Comparison](https://github.com/openai/whisper#available-models-and-languages)
- [SQLite WAL Mode](https://www.sqlite.org/wal.html)
