"""Tests for the per-feed status breakdown behind GET /api/feeds.

vimmary reads these counts to label its "Summarize all" and "Transcribe all"
buttons, so the grouping has to be one query over all feeds rather than one per
feed and status.
"""

from cast2md.db.repository import EpisodeRepository


class RecordingCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def fetchall(self):
        return self.rows


class RecordingConnection:
    def __init__(self, rows):
        self.cursor_obj = RecordingCursor(rows)

    def cursor(self, *args, **kwargs):
        return self.cursor_obj


def make_repo(monkeypatch, rows):
    conn = RecordingConnection(rows)
    repo = EpisodeRepository(conn)

    def fake_execute(_conn, sql, params=None):
        conn.cursor_obj.sql = sql
        conn.cursor_obj.params = params
        return conn.cursor_obj

    monkeypatch.setattr("cast2md.db.repository.execute", fake_execute)
    return repo, conn.cursor_obj


class TestCountByStatusPerFeed:
    def test_groups_rows_by_feed(self, monkeypatch):
        repo, _ = make_repo(
            monkeypatch,
            [(1, "completed", 68), (1, "new", 780), (2, "completed", 367), (2, "failed", 3)],
        )
        counts = repo.count_by_status_per_feed()
        assert counts == {
            1: {"completed": 68, "new": 780},
            2: {"completed": 367, "failed": 3},
        }

    def test_single_query(self, monkeypatch):
        repo, cursor = make_repo(monkeypatch, [])
        repo.count_by_status_per_feed()
        assert "GROUP BY feed_id, status" in cursor.sql
        # No parameters: the whole table is grouped in one pass.
        assert cursor.params is None

    def test_empty_database_yields_empty_mapping(self, monkeypatch):
        repo, _ = make_repo(monkeypatch, [])
        assert repo.count_by_status_per_feed() == {}

    def test_absent_status_is_not_reported_as_zero(self, monkeypatch):
        repo, _ = make_repo(monkeypatch, [(1, "completed", 5)])
        counts = repo.count_by_status_per_feed()
        assert counts[1] == {"completed": 5}
        assert "new" not in counts[1]
