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
    itunes_id TEXT,
    pocketcasts_uuid TEXT,
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
    transcript_model TEXT,
    transcript_source TEXT,
    transcript_type TEXT,
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

-- FTS5 virtual table for transcript full-text search
-- Stores transcript segments with timestamps for precise search results
CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
    text,                    -- Searchable transcript text
    episode_id UNINDEXED,    -- Reference to episode (not searchable)
    segment_start UNINDEXED, -- Start time in seconds
    segment_end UNINDEXED,   -- End time in seconds
    tokenize='porter unicode61 remove_diacritics 1'
);

-- FTS5 virtual table for episode title/description search
-- Enables word-boundary search (not substring matching)
CREATE VIRTUAL TABLE IF NOT EXISTS episode_fts USING fts5(
    title,                   -- Episode title (searchable)
    description,             -- Episode description (searchable)
    episode_id UNINDEXED,    -- Reference to episode (not searchable)
    feed_id UNINDEXED,       -- Reference to feed for filtering (not searchable)
    tokenize='porter unicode61 remove_diacritics 1'
);

-- Transcriber node table for distributed transcription
CREATE TABLE IF NOT EXISTS transcriber_node (
    id TEXT PRIMARY KEY,              -- UUID
    name TEXT NOT NULL,               -- "M4 MacBook Pro"
    url TEXT NOT NULL,                -- "http://192.168.1.100:8001"
    api_key TEXT NOT NULL,            -- Shared secret
    whisper_model TEXT,               -- Model on node
    whisper_backend TEXT,             -- "mlx" or "faster-whisper"
    status TEXT DEFAULT 'offline',    -- online/offline/busy
    last_heartbeat TEXT,
    current_job_id INTEGER,
    priority INTEGER DEFAULT 10,      -- Lower = preferred
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transcriber_node_status ON transcriber_node(status);
"""


def get_schema() -> str:
    """Return the database schema SQL."""
    return SCHEMA_SQL
