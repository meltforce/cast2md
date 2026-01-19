"""Database schema definitions."""

SCHEMA_SQL = """
-- Feed table
CREATE TABLE IF NOT EXISTS feed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
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
"""


def get_schema() -> str:
    """Return the database schema SQL."""
    return SCHEMA_SQL
