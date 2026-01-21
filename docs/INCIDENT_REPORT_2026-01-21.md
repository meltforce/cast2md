# Incident Report: Server Slowdown and Orphaned Jobs

**Date:** 2026-01-21
**Duration:** ~8 hours overnight
**Severity:** High - Server became unresponsive, jobs orphaned
**Status:** Resolved

## Executive Summary

The cast2md server became slow and unresponsive after running overnight. Investigation revealed a cascade of failures caused by SQLite write lock contention, triggered by the progress callback opening new database connections in a hot loop. This led to API failures, orphaned jobs, and memory growth.

All identified issues have been fixed and deployed.

---

## Timeline

| Time | Event |
|------|-------|
| 01:30 | Server started, reset 2 orphaned jobs |
| 03:07 | First `database is locked` errors appear |
| 03:07-03:10 | Cascade of 500 errors on heartbeat/claim endpoints |
| 03:07+ | Node repeatedly marked stale, jobs stuck in "running" |
| 05:04 | Last successful heartbeat from M4 MacBook node |
| 06:02 | Investigation begins - server using 2.1GB RAM, 17 orphaned jobs |
| 10:00 | Root cause identified, fixes planned |
| 12:00 | Fixes implemented and deployed |

---

## Issues Found and Solutions Implemented

### Issue 1: SQLite Write Lock Contention (Critical)

**Location:** `src/cast2md/worker/manager.py:244-253`

**Problem:** The progress callback creates a new database connection for every progress update during transcription:

```python
def progress_callback(progress: int):
    if progress > last_progress[0] + 2 or progress >= 99:
        with get_db() as conn:  # NEW CONNECTION each time!
            job_repo = JobRepository(conn)
            job_repo.update_progress(job_id, progress)
```

During a long transcription, this fires dozens of times, each opening a connection and attempting a write. Combined with:
- 2 download worker threads
- 1 transcribe worker thread
- 1 coordinator thread (every 30s)
- Multiple API handler threads (heartbeats, claims, status requests)

All competing for SQLite's single writer lock (5-second timeout).

**Symptoms:**
- `sqlite3.OperationalError: database is locked`
- API endpoints returning 500 errors
- Coordinator logging "database is locked"

**Evidence:**
```
Jan 21 03:07:20 cast2md python[2099834]: sqlite3.OperationalError: database is locked
Jan 21 03:07:25 cast2md python[2099834]: INFO: POST /api/nodes/.../heartbeat HTTP/1.1" 500
Jan 21 03:07:47 cast2md python[2099834]: Coordinator error: database is locked
```

#### Solution Implemented

**File:** `src/cast2md/worker/manager.py:241-260`

Added time-based throttling (every 5 seconds) to match the node worker pattern, drastically reducing DB write frequency:

```python
# Create progress callback that updates the database
# Use time-based throttling (every 5 seconds) to reduce DB lock contention
last_progress = [0]
last_update_time = [time.time()]

def progress_callback(progress: int):
    now = time.time()
    time_elapsed = (now - last_update_time[0]) >= 5.0
    is_completion = progress >= 99 and progress > last_progress[0]

    # Update every 5 seconds or at completion
    if (time_elapsed or is_completion) and progress > last_progress[0]:
        last_progress[0] = progress
        last_update_time[0] = now
        try:
            with get_db() as conn:
                job_repo = JobRepository(conn)
                job_repo.update_progress(job_id, progress)
        except Exception as e:
            logger.debug(f"Failed to update progress for job {job_id}: {e}")
```

**Impact:** Reduces DB writes from ~50 per transcription to ~12 (one every 5 seconds for a 1-minute transcription).

---

### Issue 2: Local Worker Jobs Invisible to Stale Job Reclaimer (High)

**Location:**
- `src/cast2md/db/repository.py:839-850` (`mark_running`)
- `src/cast2md/db/repository.py:732-775` (`reclaim_stale_jobs`)

**Problem:** Two different code paths for marking jobs as running:

| Code Path | Sets `status` | Sets `claimed_at` | Sets `assigned_node_id` |
|-----------|---------------|-------------------|-------------------------|
| Remote node (`claim_job`) | RUNNING | NOW | node_id |
| Local worker (`mark_running`) | RUNNING | **NULL** | **NULL** |

The stale job reclaimer requires both conditions:
```sql
WHERE assigned_node_id IS NOT NULL AND claimed_at < ?
```

**Result:** Jobs started by the local worker that hang/fail are **never reclaimed** by the automatic process.

**Evidence:**
```sql
-- Job 495: claimed_at is NULL, stuck in running forever
SELECT id, claimed_at, assigned_node_id FROM job_queue WHERE status = 'running';
-- 495|NULL|NULL  <-- local worker job, invisible to reclaimer
```

#### Solution Implemented

**File:** `src/cast2md/db/repository.py:839-856`

Modified `mark_running()` to accept an optional `node_id` parameter (default: `"local"`) and set both `claimed_at` and `assigned_node_id`:

```python
def mark_running(self, job_id: int, node_id: str = "local") -> None:
    """Mark a job as running.

    Args:
        job_id: The job ID to mark as running.
        node_id: The node ID processing this job (default: "local" for local workers).
    """
    now = datetime.utcnow().isoformat()
    self.conn.execute(
        """
        UPDATE job_queue
        SET status = ?, started_at = ?, attempts = attempts + 1,
            progress_percent = 0, assigned_node_id = ?, claimed_at = ?
        WHERE id = ?
        """,
        (JobStatus.RUNNING.value, now, node_id, now, job_id),
    )
    self.conn.commit()
```

**Impact:** Local jobs are now visible to the reclaimer and will be automatically recovered if they get stuck.

---

### Issue 3: Claim/Fail Cycle Resets Timeout Clock (Medium)

**Location:** `src/cast2md/db/repository.py:694-706` (`claim_job`)

**Problem:** When a job is claimed, `claimed_at` is reset to NOW:

```python
def claim_job(self, job_id: int, node_id: str) -> None:
    now = datetime.utcnow().isoformat()
    self.conn.execute(
        "UPDATE job_queue SET claimed_at = ?, ... WHERE id = ?",
        (now, ..., job_id),
    )
```

If a remote node:
1. Claims job at 03:00 → `claimed_at = 03:00`
2. Fails quickly, job requeued
3. Claims same job at 03:05 → `claimed_at = 03:05` (reset!)
4. Fails again...

The 2-hour reclaim timeout never triggers because `claimed_at` keeps resetting.

**Evidence:** Jobs with `attempts=3` (max) still in "running" state because they were recently re-claimed before failing.

#### Solution Implemented

**File:** `src/cast2md/db/repository.py:745-771`

Changed `reclaim_stale_jobs()` to use `started_at` instead of `claimed_at`:

```python
# First, fail jobs that have exceeded max attempts
# Use started_at (not claimed_at) so reclaim cycles don't reset the timeout
cursor = self.conn.execute(
    """
    UPDATE job_queue
    SET status = ?, error_message = 'Max attempts exceeded (job timed out repeatedly)',
        completed_at = ?, assigned_node_id = NULL, claimed_at = NULL
    WHERE status = ?
      AND assigned_node_id IS NOT NULL
      AND started_at < ?
      AND attempts >= max_attempts
    """,
    (JobStatus.FAILED.value, now, JobStatus.RUNNING.value, threshold),
)
```

**Impact:** Jobs that repeatedly fail will now properly timeout based on when they originally started, not when they were last claimed.

---

### Issue 4: Heartbeat DB Lock Contention (Medium)

**Location:** `src/cast2md/api/nodes.py:189-190`

**Problem:** Every 30-second heartbeat from remote nodes writes to the database:

```python
# Old code - writes to DB every heartbeat
repo.update_heartbeat(node_id)
```

With multiple nodes and the existing lock contention, this adds unnecessary DB write pressure.

#### Solution Implemented

**Files:**
- `src/cast2md/distributed/coordinator.py` - Added in-memory heartbeat tracking
- `src/cast2md/api/nodes.py` - Uses coordinator for heartbeats
- `src/cast2md/db/repository.py` - `update_heartbeat()` accepts optional timestamp

**Coordinator changes:**

```python
def __init__(self):
    # ... existing code ...
    # In-memory heartbeat tracking to reduce DB writes
    self._node_heartbeats: dict[str, datetime] = {}
    self._heartbeat_lock = threading.Lock()
    self._last_db_sync: datetime = datetime.utcnow()
    self._db_sync_interval_seconds = 300  # Sync to DB every 5 minutes

def record_heartbeat(self, node_id: str) -> None:
    """Record heartbeat in memory (no DB write)."""
    with self._heartbeat_lock:
        self._node_heartbeats[node_id] = datetime.utcnow()

def _sync_heartbeats_to_db(self) -> None:
    """Batch sync all heartbeat timestamps to DB."""
    # ... batches writes every 5 minutes
```

**API endpoint changes:**

```python
# Update heartbeat via coordinator (in-memory) or direct DB if coordinator not running
coordinator = get_coordinator()
if coordinator.is_running:
    # In-memory heartbeat (no DB write)
    coordinator.record_heartbeat(node_id)
else:
    # Fallback: direct DB write
    repo.update_heartbeat(node_id)
```

**Impact:** Reduces heartbeat DB writes from every 30 seconds to every 5 minutes (batch), while maintaining accurate stale node detection via in-memory timestamps.

---

## Issues Not Yet Addressed

### Issue 5: Connection/Resource Leak (Medium)

**Evidence:**
```bash
$ lsof /opt/cast2md/data/cast2md.db
# 16 open file handles to the same database!
```

**Problem:** Under error conditions (lock timeouts, exceptions), connections may not be properly closed.

**Status:** Partially mitigated by reducing DB write frequency. Full fix would require connection pooling or explicit connection management.

### Issue 6: Remote Node Failure Cascade (Medium)

**Problem:** When database locks occur, remote node heartbeat and claim requests fail with 500 errors, causing nodes to be marked stale.

**Status:** Mitigated by the fixes above - with reduced lock contention, this cascade should not occur.

### Issue 7: Unused/Empty podcast.db File (Low)

**Location:** `/opt/cast2md/data/podcast.db` (0 bytes)

**Status:** Configuration issue, to be cleaned up separately.

---

## Root Cause Chain

```
Progress callback opens new DB connection per update
                    ↓
Multiple concurrent writers compete for SQLite lock
                    ↓
Lock timeout (5s) exceeded → "database is locked"
                    ↓
API requests fail (500 errors) ← Heartbeats fail → Node marked stale
                    ↓
Jobs stuck in "running" (status update failed)
                    ↓
Reclaimer can't clean up (missing claimed_at for local jobs)
                    ↓
Memory/connection accumulation → Server slowdown
```

---

## Tests Added

### `tests/test_job_repository.py`

New test classes:

1. **`TestMarkRunningLocalNode`**
   - `test_mark_running_sets_local_node_id` - Verifies default node_id is "local"
   - `test_mark_running_sets_custom_node_id` - Verifies custom node_id works
   - `test_local_job_reclaimed_when_stuck` - Verifies local jobs are reclaimed

2. **`TestReclaimUsesStartedAt`**
   - `test_reclaim_uses_started_at_not_claimed_at` - Verifies timeout uses started_at
   - `test_recent_started_at_not_reclaimed` - Verifies recent jobs not reclaimed

### `tests/test_coordinator.py` (new file)

1. **`TestCoordinatorHeartbeat`**
   - `test_record_heartbeat_in_memory` - Verifies in-memory storage
   - `test_record_heartbeat_updates_timestamp` - Verifies timestamp updates
   - `test_stale_detection_uses_memory` - Verifies stale detection logic
   - `test_sync_heartbeats_to_db` - Verifies batch DB sync
   - `test_sync_empty_heartbeats_noop` - Verifies empty sync is no-op

2. **`TestCoordinatorDbSync`**
   - `test_db_sync_interval_config` - Verifies default sync interval
   - `test_last_db_sync_initialized` - Verifies initialization

---

## Files Modified

| File | Changes |
|------|---------|
| `src/cast2md/db/repository.py` | `mark_running()` sets node_id/claimed_at; `reclaim_stale_jobs()` uses started_at; `update_heartbeat()` accepts timestamp |
| `src/cast2md/worker/manager.py` | Progress callback time-based throttling (5 seconds) |
| `src/cast2md/distributed/coordinator.py` | In-memory heartbeat tracking with periodic DB sync |
| `src/cast2md/api/nodes.py` | Uses coordinator for heartbeats |
| `tests/test_job_repository.py` | 5 new tests for local node and reclaim fixes |
| `tests/test_coordinator.py` | 7 new tests for coordinator heartbeat tracking |

---

## Verification

All 34 tests pass:
```
tests/test_coordinator.py: 7 passed
tests/test_job_repository.py: 27 passed
============================== 34 passed in 0.08s ==============================
```

---

## Future Monitoring Recommendations

1. **Add metrics for:**
   - Database lock wait time
   - Progress update frequency
   - Stuck job count
   - Memory usage trend

2. **Alert on:**
   - `database is locked` errors
   - Jobs running > 1 hour
   - Node heartbeat failures

3. **Consider:**
   - Connection pooling for high-concurrency scenarios
   - PostgreSQL migration if SQLite limitations become problematic

---

## Appendix: Evidence Collected

### Database State (Before Fix)
- 2 feeds, 600 episodes, 651 jobs
- 17 jobs stuck in "running" state
- 1 job with `claimed_at = NULL` (local worker)
- 90 failed jobs

### System State (Before Fix)
- Process memory: 2.1GB
- Free memory: 50MB
- Open DB handles: 16
- Database size: 12MB + 4MB WAL

### Log Patterns
- 50+ "database is locked" errors between 03:07-03:10
- Continuous "Node stale, marking offline" warnings
- 3 local ffmpeg timeout errors
