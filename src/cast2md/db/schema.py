"""Database schema definitions."""

SCHEMA_SQL = """
-- Feed table
CREATE TABLE IF NOT EXISTS feed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
    author TEXT,
    link TEXT,
    categories TEXT,
    custom_title TEXT,
    last_polled TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Episode table
CREATE TABLE IF NOT EXISTS episode (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL REFERENCES feed(id) ON DELETE CASCADE,
    guid TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    audio_url TEXT NOT NULL,
    duration_seconds INTEGER,
    published_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    audio_path TEXT,
    transcript_path TEXT,
    transcript_url TEXT,
    link TEXT,
    author TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(feed_id, guid)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_episode_feed_id ON episode(feed_id);
CREATE INDEX IF NOT EXISTS idx_episode_status ON episode(status);
CREATE INDEX IF NOT EXISTS idx_episode_published_at ON episode(published_at);
CREATE INDEX IF NOT EXISTS idx_feed_url ON feed(url);

-- Job queue table
CREATE TABLE IF NOT EXISTS job_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,  -- 'download' or 'transcribe'
    priority INTEGER DEFAULT 10,  -- Lower = higher priority (new=1, backfill=10)
    status TEXT DEFAULT 'queued',  -- queued, running, completed, failed
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    scheduled_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    next_retry_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_job_queue_status_priority ON job_queue(status, priority);
CREATE INDEX IF NOT EXISTS idx_job_queue_episode_id ON job_queue(episode_id);
CREATE INDEX IF NOT EXISTS idx_job_queue_job_type ON job_queue(job_type);

-- Settings table for runtime configuration overrides
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Available whisper models
CREATE TABLE IF NOT EXISTS whisper_models (
    id TEXT PRIMARY KEY,  -- e.g., "base", "large-v3"
    backend TEXT NOT NULL,  -- "faster-whisper", "mlx", "both"
    hf_repo TEXT,  -- HuggingFace repo for mlx models
    description TEXT,
    size_mb INTEGER,
    is_enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_schema() -> str:
    """Return the database schema SQL."""
    return SCHEMA_SQL
