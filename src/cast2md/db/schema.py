"""PostgreSQL database schema definitions."""

# Each statement is a separate element to allow individual execution
SCHEMA_STATEMENTS = [
    # Feed table
    """
    CREATE TABLE IF NOT EXISTS feed (
        id SERIAL PRIMARY KEY,
        url TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        description TEXT,
        image_url TEXT,
        author TEXT,
        link TEXT,
        categories TEXT,
        custom_title TEXT,
        last_polled TIMESTAMP,
        itunes_id TEXT,
        pocketcasts_uuid TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # Episode table
    """
    CREATE TABLE IF NOT EXISTS episode (
        id SERIAL PRIMARY KEY,
        feed_id INTEGER NOT NULL REFERENCES feed(id) ON DELETE CASCADE,
        guid TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        audio_url TEXT NOT NULL,
        duration_seconds INTEGER,
        published_at TIMESTAMP,
        status TEXT NOT NULL DEFAULT 'new',
        audio_path TEXT,
        transcript_path TEXT,
        transcript_url TEXT,
        transcript_model TEXT,
        transcript_source TEXT,
        transcript_type TEXT,
        pocketcasts_transcript_url TEXT,
        transcript_checked_at TIMESTAMP,
        next_transcript_retry_at TIMESTAMP,
        transcript_failure_reason TEXT,
        link TEXT,
        author TEXT,
        error_message TEXT,
        permanent_failure BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE(feed_id, guid)
    )
    """,
    # Episode indexes
    "CREATE INDEX IF NOT EXISTS idx_episode_feed_id ON episode(feed_id)",
    "CREATE INDEX IF NOT EXISTS idx_episode_status ON episode(status)",
    "CREATE INDEX IF NOT EXISTS idx_episode_published_at ON episode(published_at)",
    "CREATE INDEX IF NOT EXISTS idx_feed_url ON feed(url)",
    # Job queue table
    """
    CREATE TABLE IF NOT EXISTS job_queue (
        id SERIAL PRIMARY KEY,
        episode_id INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
        job_type TEXT NOT NULL,
        priority INTEGER DEFAULT 10,
        status TEXT DEFAULT 'queued',
        attempts INTEGER DEFAULT 0,
        max_attempts INTEGER DEFAULT 10,
        scheduled_at TIMESTAMP DEFAULT NOW(),
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        next_retry_at TIMESTAMP,
        error_message TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        assigned_node_id TEXT,
        claimed_at TIMESTAMP,
        progress_percent INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_job_queue_status_priority ON job_queue(status, priority)",
    "CREATE INDEX IF NOT EXISTS idx_job_queue_episode_id ON job_queue(episode_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_queue_job_type ON job_queue(job_type)",
    # Settings table
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # Whisper models table
    """
    CREATE TABLE IF NOT EXISTS whisper_models (
        id TEXT PRIMARY KEY,
        backend TEXT NOT NULL,
        hf_repo TEXT,
        description TEXT,
        size_mb INTEGER,
        is_enabled BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # RunPod transcription models table
    """
    CREATE TABLE IF NOT EXISTS runpod_models (
        id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        backend TEXT NOT NULL DEFAULT 'whisper',
        is_enabled BOOLEAN DEFAULT TRUE,
        sort_order INTEGER DEFAULT 100,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # Transcriber node table
    """
    CREATE TABLE IF NOT EXISTS transcriber_node (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        api_key TEXT NOT NULL,
        whisper_model TEXT,
        whisper_backend TEXT,
        status TEXT DEFAULT 'offline',
        last_heartbeat TIMESTAMP,
        current_job_id INTEGER,
        priority INTEGER DEFAULT 10,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transcriber_node_status ON transcriber_node(status)",
    # Schema version table
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    # Transcript segments table for full-text search
    """
    CREATE TABLE IF NOT EXISTS transcript_segments (
        id SERIAL PRIMARY KEY,
        episode_id INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
        segment_start REAL NOT NULL,
        segment_end REAL NOT NULL,
        text TEXT NOT NULL,
        text_search TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transcript_segments_episode ON transcript_segments(episode_id)",
    "CREATE INDEX IF NOT EXISTS idx_transcript_search ON transcript_segments USING GIN (text_search)",
    # Episode full-text search table
    """
    CREATE TABLE IF NOT EXISTS episode_search (
        episode_id INTEGER PRIMARY KEY REFERENCES episode(id) ON DELETE CASCADE,
        feed_id INTEGER NOT NULL REFERENCES feed(id) ON DELETE CASCADE,
        title_search TSVECTOR,
        description_search TSVECTOR
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_episode_search_title ON episode_search USING GIN (title_search)",
    "CREATE INDEX IF NOT EXISTS idx_episode_search_description ON episode_search USING GIN (description_search)",
    "CREATE INDEX IF NOT EXISTS idx_episode_search_feed ON episode_search(feed_id)",
    # Vector embeddings table for semantic search
    """
    CREATE TABLE IF NOT EXISTS segment_embeddings (
        id SERIAL PRIMARY KEY,
        episode_id INTEGER NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
        feed_id INTEGER NOT NULL REFERENCES feed(id) ON DELETE CASCADE,
        segment_start REAL NOT NULL,
        segment_end REAL NOT NULL,
        text_hash TEXT NOT NULL,
        model_name TEXT NOT NULL,
        embedding vector(384),
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE(episode_id, segment_start, segment_end)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_segment_embeddings_episode ON segment_embeddings(episode_id)",
    # HNSW index for fast vector search (cosine distance)
    "CREATE INDEX IF NOT EXISTS idx_segment_embedding_vec ON segment_embeddings USING hnsw (embedding vector_cosine_ops)",
]


def get_schema() -> list[str]:
    """Return the PostgreSQL schema statements.

    Returns:
        List of SQL statements to create the schema.
    """
    return SCHEMA_STATEMENTS
