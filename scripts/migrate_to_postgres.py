#!/usr/bin/env python3
"""Migrate data from SQLite to PostgreSQL.

This script migrates all data from an SQLite database to a PostgreSQL database.
It handles:
- All tables (feeds, episodes, jobs, settings, etc.)
- FTS index recreation (SQLite FTS5 -> PostgreSQL tsvector)
- Vector embeddings (sqlite-vec -> pgvector)

Usage:
    python scripts/migrate_to_postgres.py --sqlite-path data/cast2md.db \
        --postgres-url postgresql://cast2md:dev@localhost:5432/cast2md

Requirements:
    - psycopg2-binary
    - pgvector
"""

import argparse
import logging
import sqlite3
import struct
import sys
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_postgres_connection(url: str):
    """Create PostgreSQL connection from URL."""
    import psycopg2
    from pgvector.psycopg2 import register_vector

    conn = psycopg2.connect(url)
    register_vector(conn)
    return conn


def migrate_feeds(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    """Migrate feeds table."""
    logger.info("Migrating feeds...")

    cursor = sqlite_conn.execute(
        """SELECT id, url, title, description, image_url, author, link,
                  categories, custom_title, last_polled, itunes_id, pocketcasts_uuid,
                  created_at, updated_at
           FROM feed"""
    )
    rows = cursor.fetchall()

    pg_cursor = pg_conn.cursor()
    count = 0

    for row in rows:
        pg_cursor.execute(
            """INSERT INTO feed (id, url, title, description, image_url, author, link,
                                 categories, custom_title, last_polled, itunes_id, pocketcasts_uuid,
                                 created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            row,
        )
        count += 1

    # Update sequence
    pg_cursor.execute("SELECT setval('feed_id_seq', (SELECT MAX(id) FROM feed))")
    pg_conn.commit()

    logger.info(f"Migrated {count} feeds")
    return count


def migrate_episodes(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    """Migrate episodes table."""
    logger.info("Migrating episodes...")

    # Get actual columns from SQLite table
    col_cursor = sqlite_conn.execute("PRAGMA table_info(episode)")
    sqlite_columns = [row[1] for row in col_cursor.fetchall()]

    # All possible columns in the PostgreSQL schema
    all_columns = [
        "id", "feed_id", "guid", "title", "description", "audio_url", "duration_seconds",
        "published_at", "status", "audio_path", "transcript_path", "transcript_url",
        "transcript_model", "transcript_source", "transcript_type",
        "pocketcasts_transcript_url", "transcript_checked_at", "next_transcript_retry_at",
        "transcript_failure_reason", "link", "author", "error_message", "created_at", "updated_at"
    ]

    # Only use columns that exist in SQLite
    columns = [c for c in all_columns if c in sqlite_columns]
    columns_str = ", ".join(columns)

    cursor = sqlite_conn.execute(f"SELECT {columns_str} FROM episode")
    rows = cursor.fetchall()

    pg_cursor = pg_conn.cursor()
    count = 0
    placeholders = ", ".join(["%s"] * len(columns))

    for row in rows:
        pg_cursor.execute(
            f"""INSERT INTO episode ({columns_str})
               VALUES ({placeholders})
               ON CONFLICT (id) DO NOTHING""",
            row,
        )
        count += 1

    # Update sequence
    pg_cursor.execute("SELECT setval('episode_id_seq', (SELECT MAX(id) FROM episode))")
    pg_conn.commit()

    logger.info(f"Migrated {count} episodes")
    return count


def migrate_jobs(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    """Migrate job_queue table."""
    logger.info("Migrating jobs...")

    cursor = sqlite_conn.execute("SELECT * FROM job_queue")
    rows = cursor.fetchall()

    # Get column names
    col_cursor = sqlite_conn.execute("PRAGMA table_info(job_queue)")
    columns = [row[1] for row in col_cursor.fetchall()]

    pg_cursor = pg_conn.cursor()
    count = 0

    for row in rows:
        placeholders = ", ".join(["%s"] * len(row))
        cols = ", ".join(columns)
        pg_cursor.execute(
            f"""INSERT INTO job_queue ({cols})
                VALUES ({placeholders})
                ON CONFLICT (id) DO NOTHING""",
            row,
        )
        count += 1

    # Update sequence
    pg_cursor.execute(
        "SELECT setval('job_queue_id_seq', COALESCE((SELECT MAX(id) FROM job_queue), 1))"
    )
    pg_conn.commit()

    logger.info(f"Migrated {count} jobs")
    return count


def migrate_settings(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    """Migrate settings table."""
    logger.info("Migrating settings...")

    cursor = sqlite_conn.execute("SELECT key, value, updated_at FROM settings")
    rows = cursor.fetchall()

    pg_cursor = pg_conn.cursor()
    count = 0

    for row in rows:
        pg_cursor.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (%s, %s, %s)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
            row,
        )
        count += 1

    pg_conn.commit()
    logger.info(f"Migrated {count} settings")
    return count


def migrate_whisper_models(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    """Migrate whisper_models table."""
    logger.info("Migrating whisper models...")

    try:
        cursor = sqlite_conn.execute("SELECT id, backend, hf_repo, description, size_mb, is_enabled, created_at FROM whisper_models")
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        logger.info("No whisper_models table found, skipping")
        return 0

    pg_cursor = pg_conn.cursor()
    count = 0

    for row in rows:
        # Convert is_enabled from INTEGER to BOOLEAN
        model_id, backend, hf_repo, description, size_mb, is_enabled, created_at = row
        pg_cursor.execute(
            """INSERT INTO whisper_models (id, backend, hf_repo, description, size_mb, is_enabled, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (model_id, backend, hf_repo, description, size_mb, bool(is_enabled), created_at),
        )
        count += 1

    pg_conn.commit()
    logger.info(f"Migrated {count} whisper models")
    return count


def migrate_transcriber_nodes(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    """Migrate transcriber_node table."""
    logger.info("Migrating transcriber nodes...")

    try:
        cursor = sqlite_conn.execute("SELECT * FROM transcriber_node")
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        logger.info("No transcriber_node table found, skipping")
        return 0

    pg_cursor = pg_conn.cursor()
    count = 0

    for row in rows:
        pg_cursor.execute(
            """INSERT INTO transcriber_node
                   (id, name, url, api_key, whisper_model, whisper_backend, status,
                    last_heartbeat, current_job_id, priority, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            row,
        )
        count += 1

    pg_conn.commit()
    logger.info(f"Migrated {count} transcriber nodes")
    return count


def rebuild_episode_fts(pg_conn) -> int:
    """Rebuild episode FTS index in PostgreSQL."""
    logger.info("Rebuilding episode FTS index...")

    pg_cursor = pg_conn.cursor()

    # Clear existing FTS data
    pg_cursor.execute("DELETE FROM episode_search")

    # Rebuild from episode table
    pg_cursor.execute(
        """INSERT INTO episode_search (episode_id, feed_id, title_search, description_search)
           SELECT id, feed_id,
                  to_tsvector('english', COALESCE(title, '')),
                  to_tsvector('english', COALESCE(description, ''))
           FROM episode"""
    )
    count = pg_cursor.rowcount
    pg_conn.commit()

    logger.info(f"Indexed {count} episodes for FTS")
    return count


def migrate_transcript_segments(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    """Migrate transcript FTS segments to PostgreSQL."""
    logger.info("Migrating transcript segments...")

    try:
        cursor = sqlite_conn.execute(
            """SELECT episode_id, segment_start, segment_end, text
               FROM transcript_fts"""
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        logger.info("No transcript_fts table found, skipping")
        return 0

    pg_cursor = pg_conn.cursor()
    count = 0

    for row in rows:
        pg_cursor.execute(
            """INSERT INTO transcript_segments (episode_id, segment_start, segment_end, text)
               VALUES (%s, %s, %s, %s)""",
            row,
        )
        count += 1
        if count % 10000 == 0:
            pg_conn.commit()
            logger.info(f"  Migrated {count} segments...")

    pg_conn.commit()
    logger.info(f"Migrated {count} transcript segments")
    return count


def convert_embedding(sqlite_blob: bytes) -> list[float]:
    """Convert sqlite-vec binary embedding to pgvector format."""
    count = len(sqlite_blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{count}f", sqlite_blob))


def migrate_embeddings(sqlite_conn: sqlite3.Connection, pg_conn) -> int:
    """Migrate vector embeddings from sqlite-vec to pgvector."""
    logger.info("Migrating embeddings...")

    try:
        cursor = sqlite_conn.execute(
            """SELECT episode_id, feed_id, segment_start, segment_end, text_hash, model_name, embedding
               FROM segment_vec"""
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        logger.info("No segment_vec table found, skipping")
        return 0

    import numpy as np

    pg_cursor = pg_conn.cursor()
    count = 0

    for row in rows:
        episode_id, feed_id, segment_start, segment_end, text_hash, model_name, embedding_blob = row

        # Convert binary embedding to list
        embedding = convert_embedding(embedding_blob)

        pg_cursor.execute(
            """INSERT INTO segment_embeddings
                   (episode_id, feed_id, segment_start, segment_end, text_hash, model_name, embedding)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (episode_id, segment_start, segment_end) DO NOTHING""",
            (episode_id, feed_id, segment_start, segment_end, text_hash, model_name, embedding),
        )
        count += 1
        if count % 5000 == 0:
            pg_conn.commit()
            logger.info(f"  Migrated {count} embeddings...")

    pg_conn.commit()
    logger.info(f"Migrated {count} embeddings")
    return count


def verify_migration(sqlite_conn: sqlite3.Connection, pg_conn) -> bool:
    """Verify that migration was successful by comparing counts."""
    logger.info("Verifying migration...")

    tables = [
        ("feed", "feed"),
        ("episode", "episode"),
        ("job_queue", "job_queue"),
        ("settings", "settings"),
    ]

    all_good = True
    pg_cursor = pg_conn.cursor()

    for sqlite_table, pg_table in tables:
        try:
            sqlite_cursor = sqlite_conn.execute(f"SELECT COUNT(*) FROM {sqlite_table}")
            sqlite_count = sqlite_cursor.fetchone()[0]
        except sqlite3.OperationalError:
            sqlite_count = 0

        try:
            pg_cursor.execute(f"SELECT COUNT(*) FROM {pg_table}")
            pg_count = pg_cursor.fetchone()[0]
        except Exception:
            pg_count = 0

        if sqlite_count != pg_count:
            logger.warning(f"Count mismatch for {sqlite_table}: SQLite={sqlite_count}, PostgreSQL={pg_count}")
            all_good = False
        else:
            logger.info(f"  {sqlite_table}: {sqlite_count} records - OK")

    return all_good


def main():
    parser = argparse.ArgumentParser(description="Migrate SQLite database to PostgreSQL")
    parser.add_argument(
        "--sqlite-path",
        type=str,
        default="data/cast2md.db",
        help="Path to SQLite database file",
    )
    parser.add_argument(
        "--postgres-url",
        type=str,
        required=True,
        help="PostgreSQL connection URL (e.g., postgresql://user:pass@host:5432/dbname)",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Skip embedding migration (faster, can be regenerated)",
    )
    parser.add_argument(
        "--skip-transcript-segments",
        action="store_true",
        help="Skip transcript segment migration (can be reindexed)",
    )

    args = parser.parse_args()

    # Check SQLite file exists
    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        logger.error(f"SQLite database not found: {sqlite_path}")
        sys.exit(1)

    logger.info(f"Migrating from SQLite: {sqlite_path}")
    logger.info(f"Migrating to PostgreSQL: {args.postgres_url.split('@')[1] if '@' in args.postgres_url else args.postgres_url}")

    # Connect to databases
    sqlite_conn = sqlite3.connect(str(sqlite_path))
    pg_conn = get_postgres_connection(args.postgres_url)

    try:
        # Initialize PostgreSQL schema
        logger.info("Initializing PostgreSQL schema...")
        from cast2md.db.schema_postgres import get_postgres_schema

        pg_cursor = pg_conn.cursor()

        # Enable pgvector
        pg_cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        pg_conn.commit()

        # Create tables
        for statement in get_postgres_schema():
            try:
                pg_cursor.execute(statement)
            except Exception as e:
                logger.debug(f"Schema statement: {e}")
        pg_conn.commit()

        # Migrate data
        migrate_feeds(sqlite_conn, pg_conn)
        migrate_episodes(sqlite_conn, pg_conn)
        migrate_jobs(sqlite_conn, pg_conn)
        migrate_settings(sqlite_conn, pg_conn)
        migrate_whisper_models(sqlite_conn, pg_conn)
        migrate_transcriber_nodes(sqlite_conn, pg_conn)

        # Rebuild FTS indexes
        rebuild_episode_fts(pg_conn)

        if not args.skip_transcript_segments:
            migrate_transcript_segments(sqlite_conn, pg_conn)

        if not args.skip_embeddings:
            migrate_embeddings(sqlite_conn, pg_conn)

        # Verify migration
        if verify_migration(sqlite_conn, pg_conn):
            logger.info("Migration completed successfully!")
        else:
            logger.warning("Migration completed with some discrepancies")

    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
