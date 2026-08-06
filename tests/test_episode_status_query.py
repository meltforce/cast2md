"""Tests for the watermark query behind /api/episodes/status/{status}.

The SQL that this endpoint builds is what vimmary polls, so the pieces that can
be checked without a database — the order whitelist and the clause assembly —
are checked here.
"""

import pytest

from cast2md.db.models import EpisodeStatus
from cast2md.db.repository import EpisodeRepository


class RecordingCursor:
    """Captures the SQL and parameters instead of executing them."""

    def __init__(self):
        self.sql = None
        self.params = None

    def fetchall(self):
        return []


class RecordingConnection:
    def __init__(self):
        self.cursor_obj = RecordingCursor()

    def cursor(self, *args, **kwargs):
        return self.cursor_obj


@pytest.fixture
def repo(monkeypatch):
    conn = RecordingConnection()
    repository = EpisodeRepository(conn)

    def fake_execute(_conn, sql, params=None):
        conn.cursor_obj.sql = sql
        conn.cursor_obj.params = params
        return conn.cursor_obj

    monkeypatch.setattr("cast2md.db.repository.execute", fake_execute)
    return repository, conn.cursor_obj


class TestGetByStatusOrder:
    """The order argument reaches the ORDER BY clause through a whitelist."""

    def test_default_order_is_created_asc(self, repo):
        repository, cursor = repo
        repository.get_by_status(EpisodeStatus.COMPLETED)
        assert "ORDER BY created_at ASC" in cursor.sql

    def test_updated_asc(self, repo):
        repository, cursor = repo
        repository.get_by_status(EpisodeStatus.COMPLETED, order="updated_asc")
        assert "ORDER BY updated_at ASC" in cursor.sql

    def test_updated_desc(self, repo):
        repository, cursor = repo
        repository.get_by_status(EpisodeStatus.COMPLETED, order="updated_desc")
        assert "ORDER BY updated_at DESC" in cursor.sql

    def test_unknown_order_is_rejected(self, repo):
        repository, _ = repo
        with pytest.raises(ValueError):
            repository.get_by_status(EpisodeStatus.COMPLETED, order="created_at; DROP TABLE")


class TestGetByStatusFilters:
    """since and feed_id are bound parameters, not interpolated text."""

    def test_no_filters_binds_status_and_limit_only(self, repo):
        repository, cursor = repo
        repository.get_by_status(EpisodeStatus.COMPLETED, limit=25)
        assert cursor.params == ("completed", 25)
        assert "updated_at >" not in cursor.sql
        # feed_id appears in the SELECT list; what must be absent is the clause.
        assert "feed_id = %s" not in cursor.sql

    def test_since_is_bound(self, repo):
        repository, cursor = repo
        repository.get_by_status(EpisodeStatus.COMPLETED, since="2026-08-01T10:11:12", limit=25)
        assert "updated_at > %s::timestamp" in cursor.sql
        assert cursor.params == ("completed", "2026-08-01T10:11:12", 25)

    def test_feed_id_is_bound(self, repo):
        repository, cursor = repo
        repository.get_by_status(EpisodeStatus.COMPLETED, feed_id=3, limit=10)
        assert "feed_id = %s" in cursor.sql
        assert cursor.params == ("completed", 3, 10)

    def test_all_filters_together_keep_parameter_order(self, repo):
        repository, cursor = repo
        repository.get_by_status(
            EpisodeStatus.COMPLETED,
            since="2026-08-01T10:11:12",
            feed_id=3,
            order="updated_asc",
            limit=25,
        )
        assert cursor.params == ("completed", "2026-08-01T10:11:12", 3, 25)
