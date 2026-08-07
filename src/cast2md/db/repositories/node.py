"""Repository for remote transcriber nodes."""

from datetime import datetime, timedelta

from cast2md.db.models import NodeStatus, TranscriberNode
from cast2md.db.sql import Connection, execute


class TranscriberNodeRepository:
    """Repository for transcriber node operations."""

    NODE_COLUMNS = """id, name, url, api_key, whisper_model, whisper_backend,
                      status, last_heartbeat, current_job_id, priority,
                      created_at, updated_at"""

    def __init__(self, conn: Connection):
        self.conn = conn

    def create(
        self,
        node_id: str,
        name: str,
        url: str,
        api_key: str,
        whisper_model: str | None = None,
        whisper_backend: str | None = None,
        priority: int = 10,
    ) -> TranscriberNode:
        """Create a new transcriber node."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            INSERT INTO transcriber_node (
                id, name, url, api_key, whisper_model, whisper_backend,
                status, priority, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                node_id,
                name,
                url,
                api_key,
                whisper_model,
                whisper_backend,
                NodeStatus.OFFLINE.value,
                priority,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get_by_id(node_id)

    def get_by_id(self, node_id: str) -> TranscriberNode | None:
        """Get node by ID."""
        cursor = execute(
            self.conn,
            f"SELECT {self.NODE_COLUMNS} FROM transcriber_node WHERE id = %s",
            (node_id,),
        )
        row = cursor.fetchone()
        return TranscriberNode.from_row(row) if row else None

    def get_by_api_key(self, api_key: str) -> TranscriberNode | None:
        """Get node by API key."""
        cursor = execute(
            self.conn,
            f"SELECT {self.NODE_COLUMNS} FROM transcriber_node WHERE api_key = %s",
            (api_key,),
        )
        row = cursor.fetchone()
        return TranscriberNode.from_row(row) if row else None

    def get_all(self) -> list[TranscriberNode]:
        """Get all nodes."""
        cursor = execute(
            self.conn, f"SELECT {self.NODE_COLUMNS} FROM transcriber_node ORDER BY priority, name"
        )
        return [TranscriberNode.from_row(row) for row in cursor.fetchall()]

    def get_online(self) -> list[TranscriberNode]:
        """Get all online nodes."""
        cursor = execute(
            self.conn,
            f"""
            SELECT {self.NODE_COLUMNS} FROM transcriber_node
            WHERE status IN (%s, %s)
            ORDER BY priority, name
            """,
            (NodeStatus.ONLINE.value, NodeStatus.BUSY.value),
        )
        return [TranscriberNode.from_row(row) for row in cursor.fetchall()]

    def update_status(
        self,
        node_id: str,
        status: NodeStatus,
        current_job_id: int | None = None,
    ) -> None:
        """Update node status."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE transcriber_node
            SET status = %s, current_job_id = %s, updated_at = %s
            WHERE id = %s
            """,
            (status.value, current_job_id, now, node_id),
        )
        self.conn.commit()

    def update_heartbeat(self, node_id: str, timestamp: datetime | None = None) -> None:
        """Update last heartbeat timestamp.

        Args:
            node_id: The node ID to update.
            timestamp: Optional timestamp to use (default: current time).
        """
        ts = (timestamp or datetime.now()).isoformat()
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE transcriber_node
            SET last_heartbeat = %s, updated_at = %s
            WHERE id = %s
            """,
            (ts, now, node_id),
        )
        self.conn.commit()

    def reregister(
        self,
        node_id: str,
        url: str,
        api_key: str,
        whisper_model: str | None = None,
        whisper_backend: str | None = None,
    ) -> None:
        """Reclaim an existing offline entry for a node that restarted.

        Sets the new URL and API key, puts the status back to offline and
        clears the heartbeat and job claim, so the entry looks the way create()
        leaves a fresh one. Prevents an orphaned entry per pod restart.

        Args:
            node_id: ID of the existing entry to reclaim.
            url: New URL the node is reachable at.
            api_key: Newly issued API key.
            whisper_model: Model the node reports, or None to clear it.
            whisper_backend: Backend the node reports, or None to clear it.
        """
        execute(
            self.conn,
            """
            UPDATE transcriber_node
            SET url = %s, api_key = %s, whisper_model = %s, whisper_backend = %s,
                status = %s, last_heartbeat = NULL, current_job_id = NULL,
                updated_at = %s
            WHERE id = %s
            """,
            (
                url,
                api_key,
                whisper_model,
                whisper_backend,
                NodeStatus.OFFLINE.value,
                datetime.now().isoformat(),
                node_id,
            ),
        )
        self.conn.commit()

    def update_info(
        self,
        node_id: str,
        name: str | None = None,
        whisper_model: str | None = None,
        whisper_backend: str | None = None,
    ) -> None:
        """Update node info (name, whisper model/backend)."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE transcriber_node
            SET name = %s, whisper_model = %s, whisper_backend = %s, updated_at = %s
            WHERE id = %s
            """,
            (name, whisper_model, whisper_backend, now, node_id),
        )
        self.conn.commit()

    def delete(self, node_id: str) -> bool:
        """Delete a node."""
        cursor = execute(
            self.conn,
            "DELETE FROM transcriber_node WHERE id = %s",
            (node_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_stale_nodes(self, timeout_seconds: int = 60) -> list[TranscriberNode]:
        """Get nodes that haven't sent a heartbeat within the timeout.

        Args:
            timeout_seconds: Seconds after which a node is considered stale.

        Returns:
            List of stale nodes.
        """
        threshold = (datetime.now() - timedelta(seconds=timeout_seconds)).isoformat()
        cursor = execute(
            self.conn,
            f"""
            SELECT {self.NODE_COLUMNS} FROM transcriber_node
            WHERE status != %s
            AND (last_heartbeat IS NULL OR last_heartbeat < %s)
            """,
            (NodeStatus.OFFLINE.value, threshold),
        )
        return [TranscriberNode.from_row(row) for row in cursor.fetchall()]

    def mark_offline(self, node_id: str) -> None:
        """Mark a node as offline and clear its current job."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE transcriber_node
            SET status = %s, current_job_id = NULL, updated_at = %s
            WHERE id = %s
            """,
            (NodeStatus.OFFLINE.value, now, node_id),
        )
        self.conn.commit()

    def count_by_status(self) -> dict[str, int]:
        """Count nodes by status."""
        cursor = execute(
            self.conn,
            """
            SELECT status, COUNT(*) FROM transcriber_node
            GROUP BY status
            """,
        )
        return dict(cursor.fetchall())

    def get_by_name(self, name: str) -> TranscriberNode | None:
        """Get node by name."""
        cursor = execute(
            self.conn,
            f"SELECT {self.NODE_COLUMNS} FROM transcriber_node WHERE name = %s",
            (name,),
        )
        row = cursor.fetchone()
        return TranscriberNode.from_row(row) if row else None

    def delete_by_name(self, name: str) -> bool:
        """Delete a node by exact name.

        Args:
            name: The node name to delete.

        Returns:
            True if a node was deleted.
        """
        cursor = execute(
            self.conn,
            "DELETE FROM transcriber_node WHERE name = %s",
            (name,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def cleanup_stale_nodes(self, offline_hours: int = 24) -> int:
        """Delete nodes that have been offline for longer than threshold.

        Args:
            offline_hours: Hours a node must be offline before deletion.

        Returns:
            Number of nodes deleted.
        """
        threshold = (datetime.now() - timedelta(hours=offline_hours)).isoformat()

        cursor = execute(
            self.conn,
            """
            DELETE FROM transcriber_node
            WHERE status = %s
              AND (last_heartbeat IS NULL OR last_heartbeat < %s)
              AND current_job_id IS NULL
            """,
            (NodeStatus.OFFLINE.value, threshold),
        )
        self.conn.commit()
        return cursor.rowcount

    def get_stale_offline_nodes(self, offline_hours: int = 24) -> list[TranscriberNode]:
        """Get nodes that have been offline for longer than threshold.

        Args:
            offline_hours: Hours a node must be offline to be considered stale.

        Returns:
            List of stale offline nodes.
        """
        threshold = (datetime.now() - timedelta(hours=offline_hours)).isoformat()

        cursor = execute(
            self.conn,
            f"""
            SELECT {self.NODE_COLUMNS} FROM transcriber_node
            WHERE status = %s
              AND (last_heartbeat IS NULL OR last_heartbeat < %s)
            ORDER BY last_heartbeat ASC NULLS FIRST
            """,
            (NodeStatus.OFFLINE.value, threshold),
        )
        return [TranscriberNode.from_row(row) for row in cursor.fetchall()]
