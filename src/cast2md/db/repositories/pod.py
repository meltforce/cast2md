"""Repositories for RunPod pod runs and their setup state."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from cast2md.db.sql import execute


class PodRunRepository:
    """Repository for RunPod pod run history."""

    def __init__(self, conn: Any):
        self.conn = conn

    def create(
        self,
        instance_id: str,
        pod_id: str | None,
        pod_name: str,
        gpu_type: str,
        gpu_price_hr: float | None,
        started_at: datetime,
    ) -> int:
        """Create a new pod run record. Returns the ID."""
        cursor = execute(
            self.conn,
            """
            INSERT INTO pod_runs (instance_id, pod_id, pod_name, gpu_type, gpu_price_hr, started_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'running')
            RETURNING id
            """,
            (instance_id, pod_id, pod_name, gpu_type, gpu_price_hr, started_at.isoformat()),
        )
        self.conn.commit()
        row = cursor.fetchone()
        return row[0] if row else 0

    def end_run(self, pod_id: str, jobs_completed: int = 0) -> None:
        """Mark a pod run as ended."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            """
            UPDATE pod_runs
            SET ended_at = %s, jobs_completed = %s, status = 'completed'
            WHERE pod_id = %s AND status = 'running'
            """,
            (now, jobs_completed, pod_id),
        )
        self.conn.commit()

    def get_recent(self, limit: int = 20) -> list[dict]:
        """Get recent pod runs with computed cost."""
        cursor = execute(
            self.conn,
            """
            SELECT
                id, instance_id, pod_id, pod_name, gpu_type, gpu_price_hr,
                started_at, ended_at, jobs_completed, status,
                CASE
                    WHEN ended_at IS NOT NULL AND gpu_price_hr IS NOT NULL THEN
                        ROUND((EXTRACT(EPOCH FROM (ended_at - started_at)) / 3600 * gpu_price_hr)::numeric, 2)
                    ELSE NULL
                END as cost
            FROM pod_runs
            ORDER BY started_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_stats(self, days: int = 30) -> dict:
        """Get aggregate stats for pod runs."""
        cursor = execute(
            self.conn,
            """
            SELECT
                COUNT(*) as total_runs,
                COALESCE(SUM(jobs_completed), 0) as total_jobs,
                ROUND(COALESCE(SUM(
                    CASE WHEN ended_at IS NOT NULL AND gpu_price_hr IS NOT NULL THEN
                        EXTRACT(EPOCH FROM (ended_at - started_at)) / 3600 * gpu_price_hr
                    ELSE 0 END
                ), 0)::numeric, 2) as total_cost,
                ROUND(COALESCE(SUM(
                    CASE WHEN ended_at IS NOT NULL THEN
                        EXTRACT(EPOCH FROM (ended_at - started_at)) / 3600
                    ELSE 0 END
                ), 0)::numeric, 1) as total_hours
            FROM pod_runs
            WHERE started_at > NOW() - INTERVAL '%s days'
            """,
            (days,),
        )
        row = cursor.fetchone()
        if row:
            return {
                "total_runs": row[0],
                "total_jobs": row[1],
                "total_cost": float(row[2]) if row[2] else 0,
                "total_hours": float(row[3]) if row[3] else 0,
            }
        return {"total_runs": 0, "total_jobs": 0, "total_cost": 0, "total_hours": 0}

    def mark_orphaned_as_ended(self, active_pod_ids: set[str]) -> int:
        """Mark running pod runs as ended if their pod is no longer active.

        Returns count of runs marked as ended.
        """
        if not active_pod_ids:
            # No active pods - mark all running as ended
            cursor = execute(
                self.conn,
                """
                UPDATE pod_runs
                SET ended_at = NOW(), status = 'completed'
                WHERE status = 'running'
                """,
            )
        else:
            # Mark those not in active list
            cursor = execute(
                self.conn,
                """
                UPDATE pod_runs
                SET ended_at = NOW(), status = 'completed'
                WHERE status = 'running' AND pod_id NOT IN %s
                """,
                (tuple(active_pod_ids),),
            )
        self.conn.commit()
        return cursor.rowcount


@dataclass
class PodSetupStateRow:
    """Database representation of a pod setup state."""

    instance_id: str
    pod_id: str | None
    pod_name: str
    ts_hostname: str
    node_name: str
    gpu_type: str
    phase: str
    message: str
    started_at: datetime
    error: str | None
    host_ip: str | None
    persistent: bool
    setup_token: str = ""

    @classmethod
    def from_row(cls, row: tuple) -> "PodSetupStateRow":
        return cls(
            instance_id=row[0],
            pod_id=row[1],
            pod_name=row[2],
            ts_hostname=row[3],
            node_name=row[4],
            gpu_type=row[5] or "",
            phase=row[6],
            message=row[7] or "",
            started_at=row[8] if isinstance(row[8], datetime) else datetime.fromisoformat(row[8]),
            error=row[9],
            host_ip=row[10],
            persistent=row[11] if row[11] is not None else False,
            setup_token=row[12] if len(row) > 12 else "",
        )


class PodSetupStateRepository:
    """Repository for persistent pod setup states."""

    COLUMNS = """instance_id, pod_id, pod_name, ts_hostname, node_name, gpu_type,
                 phase, message, started_at, error, host_ip, persistent, setup_token"""

    def __init__(self, conn: Any):
        self.conn = conn

    def upsert(self, state: PodSetupStateRow) -> None:
        """Insert or update a pod setup state."""
        now = datetime.now().isoformat()
        execute(
            self.conn,
            f"""
            INSERT INTO pod_setup_states ({self.COLUMNS}, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (instance_id) DO UPDATE SET
                pod_id = EXCLUDED.pod_id,
                pod_name = EXCLUDED.pod_name,
                ts_hostname = EXCLUDED.ts_hostname,
                node_name = EXCLUDED.node_name,
                gpu_type = EXCLUDED.gpu_type,
                phase = EXCLUDED.phase,
                message = EXCLUDED.message,
                error = EXCLUDED.error,
                host_ip = EXCLUDED.host_ip,
                persistent = EXCLUDED.persistent,
                setup_token = EXCLUDED.setup_token,
                updated_at = EXCLUDED.updated_at
            """,
            (
                state.instance_id,
                state.pod_id,
                state.pod_name,
                state.ts_hostname,
                state.node_name,
                state.gpu_type,
                state.phase,
                state.message,
                state.started_at.isoformat(),
                state.error,
                state.host_ip,
                state.persistent,
                state.setup_token,
                now,
                now,
            ),
        )
        self.conn.commit()

    def get(self, instance_id: str) -> PodSetupStateRow | None:
        """Get a pod setup state by instance ID."""
        cursor = execute(
            self.conn,
            f"SELECT {self.COLUMNS} FROM pod_setup_states WHERE instance_id = %s",
            (instance_id,),
        )
        row = cursor.fetchone()
        return PodSetupStateRow.from_row(row) if row else None

    def get_all(self) -> list[PodSetupStateRow]:
        """Get all pod setup states."""
        cursor = execute(
            self.conn,
            f"SELECT {self.COLUMNS} FROM pod_setup_states ORDER BY started_at DESC",
        )
        return [PodSetupStateRow.from_row(row) for row in cursor.fetchall()]

    def delete(self, instance_id: str) -> bool:
        """Delete a pod setup state."""
        cursor = execute(
            self.conn,
            "DELETE FROM pod_setup_states WHERE instance_id = %s",
            (instance_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def cleanup_old(self, hours: int = 24) -> int:
        """Delete setup states older than the specified hours.

        Only deletes states that are in 'ready' or 'failed' phase.
        """
        threshold = (datetime.now() - timedelta(hours=hours)).isoformat()
        cursor = execute(
            self.conn,
            """
            DELETE FROM pod_setup_states
            WHERE started_at < %s
            AND phase IN ('ready', 'failed')
            AND persistent = FALSE
            """,
            (threshold,),
        )
        self.conn.commit()
        return cursor.rowcount

    def set_persistent(self, instance_id: str, persistent: bool) -> bool:
        """Set the persistent flag for a pod setup state."""
        cursor = execute(
            self.conn,
            """
            UPDATE pod_setup_states
            SET persistent = %s, updated_at = %s
            WHERE instance_id = %s
            """,
            (persistent, datetime.now().isoformat(), instance_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0
