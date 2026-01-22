# Database Lock Contention Investigation

## Problem Summary

The cast2md application experiences SQLite "database is locked" errors when running multiple transcript download workers concurrently. With 4 workers, jobs frequently fail and get stuck in "running" state, showing counts like "6/4 running jobs" (more running than workers).

## Architecture

- **Database**: SQLite with WAL mode
- **Workers**: Thread-based, running in a single Python process
- **Job types**: DOWNLOAD (audio), TRANSCRIPT_DOWNLOAD (external providers), TRANSCRIBE (Whisper), EMBED (vectors)
- **Concurrency**: Multiple worker threads share the database

## Current Configuration

```python
# db/connection.py
conn = sqlite3.connect(str(db_path), timeout=30.0)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA foreign_keys=ON")
conn.execute("PRAGMA busy_timeout=30000")  # 30 seconds
```

## Symptoms

1. Jobs fail with "database is locked" error
2. Failed jobs can't be marked as failed (also locked), leaving them in "running" state
3. Running job count exceeds worker count (orphaned jobs)
4. Errors occur rapidly (multiple per second), suggesting busy_timeout isn't working as expected

Example log output:
```
07:50:36 ERROR - Transcript download job 4331 failed: database is locked
07:50:36 ERROR - Transcript download job 4332 failed: database is locked
07:51:06 ERROR - Failed to mark job 4331 as failed: database is locked
07:51:06 ERROR - Failed to mark job 4332 as failed: database is locked
```

## Worker Implementation

### Worker Thread Loop

```python
# worker/manager.py
def _transcript_download_worker(self):
    """Worker thread for processing transcript download jobs."""
    while not self._stop_event.is_set():
        try:
            # Wait if paused (with 60s timeout as safety net)
            if not self._tdl_pause_event.wait(timeout=60.0):
                continue

            job = self._get_next_job(JobType.TRANSCRIPT_DOWNLOAD)
            if job is None:
                self._stop_event.wait(timeout=5.0)
                continue

            self._process_transcript_download_job(job.id, job.episode_id)

        except Exception as e:
            logger.error(f"Transcript download worker error: {e}")
            time.sleep(5.0)
```

### Job Processing

```python
# worker/manager.py
def _process_transcript_download_job(self, job_id: int, episode_id: int):
    # Transaction 1: Mark job as running, get episode/feed data
    with get_db_write() as conn:
        job_repo = JobRepository(conn)
        episode_repo = EpisodeRepository(conn)
        feed_repo = FeedRepository(conn)

        job_repo.mark_running(job_id)  # UPDATE job_queue SET status='running'

        episode = episode_repo.get_by_id(episode_id)
        feed = feed_repo.get_by_id(episode.feed_id)

    # HTTP calls happen here (outside transaction)
    try:
        result = try_fetch_transcript(episode, feed)  # External API calls
        now = datetime.now()

        if isinstance(result, TranscriptResult):
            # Transaction 2: Save transcript, update status
            with get_db_write() as conn:
                episode_repo = EpisodeRepository(conn)
                job_repo = JobRepository(conn)

                transcript_path.write_text(result.content)
                episode_repo.update_transcript_from_download(...)
                episode_repo.update_status(episode.id, EpisodeStatus.COMPLETED)
                job_repo.mark_completed(job_id)

    except Exception as e:
        # Transaction 3: Mark job as failed
        with get_db_write() as conn:
            job_repo = JobRepository(conn)
            job_repo.mark_failed(job_id, str(e))
```

### Getting Next Job (Contention Point)

```python
# worker/manager.py
def _get_next_job(self, job_type: JobType):
    with get_db() as conn:
        repo = JobRepository(conn)
        return repo.get_next_job(job_type, local_only=local_only)

# db/repository.py
def get_next_job(self, job_type: JobType, local_only: bool = False) -> Job | None:
    # SELECT with ORDER BY priority, created_at
    # Then UPDATE to mark as 'running'
    # This is a read-then-write pattern that can cause contention
```

## Database Connection Management

```python
# db/connection.py
@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# Alias - both are identical now
get_db_write = get_db
```

## Attempted Solutions

### 1. BEGIN IMMEDIATE (Failed)

Tried acquiring write lock upfront to prevent deadlocks from lock upgrades:

```python
@contextmanager
def get_db_write():
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")  # Acquire write lock immediately
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

**Result**: `BEGIN IMMEDIATE` fails immediately when another writer holds the lock - it doesn't respect `busy_timeout`. Caused "generator didn't stop after throw()" errors with retry logic.

### 2. Retry with Exponential Backoff (Failed)

Attempted retry logic inside the context manager:

```python
@contextmanager
def get_db_write(max_retries: int = 3):
    for attempt in range(max_retries + 1):
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries:
                time.sleep(0.1 * (2**attempt) + random.uniform(0, 0.1))
                continue
            raise
```

**Result**: Generators with retry loops don't work correctly with `@contextmanager`. Caused "generator didn't stop after throw()" errors.

### 3. Increased busy_timeout (Partial)

Changed from 5s to 30s:

```python
conn.execute("PRAGMA busy_timeout=30000")
```

**Result**: Should help, but errors still occur rapidly (sub-second), suggesting something prevents the timeout from working.

### 4. Worker Pause During Feed Operations (Implemented)

Pause transcript download workers during feed add/refresh:

```python
# worker/manager.py
def __init__(self):
    self._tdl_pause_event = threading.Event()
    self._tdl_pause_event.set()  # Not paused initially
    self._tdl_pause_count = 0
    self._tdl_pause_lock = threading.Lock()

def pause_transcript_downloads(self):
    with self._tdl_pause_lock:
        self._tdl_pause_count += 1
        self._tdl_pause_event.clear()

def resume_transcript_downloads(self):
    with self._tdl_pause_lock:
        self._tdl_pause_count = max(0, self._tdl_pause_count - 1)
        if self._tdl_pause_count == 0:
            self._tdl_pause_event.set()

# api/feeds.py
def create_feed(feed_data: FeedCreate):
    # ... validate feed ...

    manager = WorkerManager()
    manager.pause_transcript_downloads()
    try:
        result = discover_new_episodes(feed, auto_queue=True)
    finally:
        manager.resume_transcript_downloads()
```

**Result**: Helps during feed operations, but doesn't solve worker-vs-worker contention.

### 5. Reduced Worker Count (Current Workaround)

Reduced from 4-5 workers to 2:

```python
max_transcript_download_workers: int = 2  # SQLite limit
```

**Result**: Works reliably. No lock errors with 2 workers.

## Analysis

### Why busy_timeout Might Not Work

1. **Multiple connections from same process**: SQLite's busy handler may behave differently when connections are from the same process
2. **WAL mode specifics**: WAL allows concurrent readers, but writers still serialize. The busy_timeout should apply, but...
3. **Connection pooling absence**: Each `get_db()` creates a new connection. Rapid open/close might cause issues
4. **Python GIL**: Thread switching during lock acquisition might cause unexpected behavior

### Contention Points

1. **get_next_job()**: All workers call this frequently, doing SELECT + UPDATE
2. **mark_running()**: Called immediately after getting a job
3. **mark_completed()/mark_failed()**: Called after processing
4. **Feed discovery**: Creates many episodes and jobs in rapid succession

## Potential Solutions

### 1. Single Writer Thread

Route all database writes through a dedicated thread with a queue:

```python
class DatabaseWriter:
    def __init__(self):
        self._queue = queue.Queue()
        self._thread = threading.Thread(target=self._writer_loop, daemon=True)

    def _writer_loop(self):
        conn = get_connection()  # Single long-lived connection
        while True:
            operation, args, result_queue = self._queue.get()
            try:
                result = operation(conn, *args)
                conn.commit()
                result_queue.put((True, result))
            except Exception as e:
                conn.rollback()
                result_queue.put((False, e))

    def execute(self, operation, *args):
        result_queue = queue.Queue()
        self._queue.put((operation, args, result_queue))
        success, result = result_queue.get()
        if not success:
            raise result
        return result
```

### 2. PostgreSQL Migration

Switch to PostgreSQL for proper MVCC concurrency:
- Multiple concurrent writers supported
- Row-level locking instead of database-level
- Connection pooling with pgBouncer

### 3. Job Claim Pattern

Instead of SELECT-then-UPDATE, use atomic UPDATE with RETURNING:

```sql
UPDATE job_queue
SET status = 'running', started_at = ?
WHERE id = (
    SELECT id FROM job_queue
    WHERE status = 'queued' AND job_type = ?
    ORDER BY priority DESC, created_at ASC
    LIMIT 1
)
RETURNING *;
```

### 4. Batch Status Updates

Collect status updates and apply in batches:

```python
class StatusBatcher:
    def __init__(self, flush_interval=1.0):
        self._pending = []
        self._lock = threading.Lock()

    def mark_completed(self, job_id):
        with self._lock:
            self._pending.append(('completed', job_id))

    def flush(self):
        with self._lock:
            updates = self._pending
            self._pending = []

        if updates:
            with get_db() as conn:
                # Batch UPDATE
                conn.executemany(...)
```

### 5. Read Replica Pattern

Use separate connections for reads vs writes:

```python
def get_read_db():
    """Connection optimized for reads - can use shared cache."""
    conn = sqlite3.connect(db_path, uri=True,
                          check_same_thread=False)
    conn.execute("PRAGMA query_only=ON")
    return conn
```

## Current State

The application works reliably with 2 transcript download workers. This is a workaround, not a fix. For higher concurrency, a more fundamental change to the database access pattern or database engine is needed.

## Files Reference

- `src/cast2md/db/connection.py` - Connection management
- `src/cast2md/db/repository.py` - Database operations
- `src/cast2md/worker/manager.py` - Worker threads and job processing
- `src/cast2md/api/feeds.py` - Feed API with worker pausing
- `src/cast2md/config/settings.py` - Worker count configuration
