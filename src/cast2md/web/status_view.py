"""View model for the admin status page.

Split into two halves on purpose. `collect_status_data` performs the repository
reads and touches nothing else; `build_status_context` derives the template
context from that data plus the worker manager's status and touches no
database. The second half is therefore testable without one, which the route it
came from was not.
"""

from dataclasses import dataclass, field
from typing import Any

from cast2md.db.models import Episode, Job, JobType, TranscriberNode
from cast2md.db.repository import (
    EpisodeRepository,
    FeedRepository,
    JobRepository,
    TranscriberNodeRepository,
)
from cast2md.db.sql import Connection
from cast2md.search.repository import TranscriptSearchRepository

# How many jobs without a worker slot the page lists before it shows a count.
MAX_ORPHANED_DISPLAY = 3


@dataclass
class RunningJob:
    """A running job together with the episode it belongs to."""

    job: Job
    episode: Episode


@dataclass
class StatusData:
    """Everything the admin status page reads from the database."""

    status_counts: dict[str, int]
    search_stats: dict[str, int]
    feed_count: int
    performance_stats: dict[str, Any]
    running_downloads: list[RunningJob] = field(default_factory=list)
    running_transcriptions: list[RunningJob] = field(default_factory=list)
    running_transcript_downloads: list[RunningJob] = field(default_factory=list)
    nodes: list[TranscriberNode] = field(default_factory=list)


def _calc_throughput(audio_minutes: int, processing_seconds: int) -> float:
    """Audio minutes produced per wall-clock minute of processing."""
    if processing_seconds <= 0:
        return 0.0
    wall_clock_minutes = processing_seconds / 60
    return round(audio_minutes / wall_clock_minutes, 1) if wall_clock_minutes > 0 else 0.0


def _pair_with_episodes(jobs: list[Job], episodes: dict[int, Episode]) -> list[RunningJob]:
    """Pair each job with its episode, dropping jobs whose episode is gone."""
    return [
        RunningJob(job=job, episode=episodes[job.episode_id])
        for job in jobs
        if job.episode_id in episodes
    ]


def collect_status_data(conn: Connection) -> StatusData:
    """Read everything the admin status page needs from one connection.

    Args:
        conn: Open database connection; the caller owns the block.

    Returns:
        The raw figures, with no derivation applied.
    """
    episode_repo = EpisodeRepository(conn)
    feed_repo = FeedRepository(conn)
    job_repo = JobRepository(conn)
    node_repo = TranscriberNodeRepository(conn)
    search_repo = TranscriptSearchRepository(conn)

    hour_stats = job_repo.get_completed_jobs_stats(hours=1, job_type=JobType.TRANSCRIBE)
    hour_audio = job_repo.get_audio_minutes_processed(hours=1)
    day_stats = job_repo.get_completed_jobs_stats(hours=24, job_type=JobType.TRANSCRIBE)
    day_audio = job_repo.get_audio_minutes_processed(hours=24)

    running_downloads = job_repo.get_running_jobs(JobType.DOWNLOAD)
    running_transcriptions = job_repo.get_running_jobs(JobType.TRANSCRIBE)
    running_transcript_downloads = job_repo.get_running_jobs(JobType.TRANSCRIPT_DOWNLOAD)

    # One query for all three lists, rather than a get_by_id per job.
    all_jobs = running_downloads + running_transcriptions + running_transcript_downloads
    episodes = episode_repo.get_by_ids([job.episode_id for job in all_jobs])

    return StatusData(
        status_counts=episode_repo.count_by_status(),
        search_stats={
            "indexed_episodes": len(search_repo.get_indexed_episodes()),
            "embedded_episodes": len(search_repo.get_embedded_episodes()),
        },
        feed_count=len(feed_repo.get_all()),
        performance_stats={
            "hour_throughput": _calc_throughput(hour_audio, hour_stats["total_duration_seconds"]),
            "hour_episodes": hour_stats["count"],
            "day_throughput": _calc_throughput(day_audio, day_stats["total_duration_seconds"]),
            "day_episodes": day_stats["count"],
        },
        running_downloads=_pair_with_episodes(running_downloads, episodes),
        running_transcriptions=_pair_with_episodes(running_transcriptions, episodes),
        running_transcript_downloads=_pair_with_episodes(running_transcript_downloads, episodes),
        nodes=node_repo.get_all(),
    )


def _build_download_card(data: StatusData, queue_status: dict) -> dict:
    """Fill the configured download worker slots, then list what did not fit."""
    assigned_ids = set()
    workers = []
    job_index = 0

    for _ in range(queue_status["download_workers"]):
        item = None
        if job_index < len(data.running_downloads):
            item = data.running_downloads[job_index]
            assigned_ids.add(item.job.id)
            job_index += 1

        workers.append(
            {
                "status": "busy" if item else "idle",
                "job": item.job if item else None,
                "episode": item.episode if item else None,
            }
        )

    orphaned = [
        {"status": "stuck", "job": item.job, "episode": item.episode}
        for item in data.running_downloads
        if item.job.id not in assigned_ids
    ]

    return {
        "title": "Audio Download",
        "workers": workers + orphaned[:MAX_ORPHANED_DISPLAY],
        "queued": queue_status["download_queue"]["queued"],
    }


def _build_transcript_fetch_card(data: StatusData, queue_status: dict) -> dict:
    """Transcript fetch has no per-slot display, only counts.

    The orphaned list is always empty and is kept so the template context keeps
    its shape. The route this came from marked every running job as assigned and
    then collected the ones that were not assigned, which selects nothing. Fixing
    that changes what the page shows, so it is a separate item rather than part
    of this move — see ROADMAP.md.
    """
    orphaned: list[dict] = []

    return {
        "title": "Transcript Fetch",
        "active_count": len(data.running_transcript_downloads),
        "total_count": queue_status["transcript_download_workers"],
        "queued": queue_status["transcript_download_queue"]["queued"],
        "orphaned": orphaned[:MAX_ORPHANED_DISPLAY],
        "orphaned_total": len(orphaned),
    }


def _build_transcription_card(data: StatusData, queue_status: dict) -> dict:
    """Split the running transcriptions between the server and the remote nodes."""
    assigned_ids: set[int] = set()

    # Server worker: the first job not claimed by a node.
    server_standby = queue_status.get("transcribe_workers_standby", False)
    server_worker = {
        "status": "standby" if server_standby else "idle",
        "job": None,
        "episode": None,
        "progress": None,
    }
    for item in data.running_transcriptions:
        node_id = item.job.assigned_node_id
        if not node_id or node_id == "local":
            server_worker = {
                "status": "busy",
                "job": item.job,
                "episode": item.episode,
                "progress": item.job.progress_percent,
            }
            assigned_ids.add(item.job.id)
            break

    remote_nodes = []
    if queue_status.get("distributed_enabled"):
        for node in data.nodes:
            node_item = next(
                (i for i in data.running_transcriptions if i.job.assigned_node_id == node.id),
                None,
            )
            if node_item:
                assigned_ids.add(node_item.job.id)

            # MLX reports no intermediate progress, so showing 0 would mislead.
            is_mlx = node.whisper_backend in ("mlx", "auto")
            remote_nodes.append(
                {
                    "name": node.name,
                    "status": node.status.value,
                    "job": node_item.job if node_item else None,
                    "episode": node_item.episode if node_item else None,
                    "progress": None
                    if is_mlx
                    else (node_item.job.progress_percent if node_item else None),
                }
            )

    # A job with an assigned_node_id is prefetched by that node, not orphaned.
    orphaned = [
        {
            "status": "stuck",
            "job": item.job,
            "episode": item.episode,
            "progress": item.job.progress_percent,
        }
        for item in data.running_transcriptions
        if item.job.id not in assigned_ids and not item.job.assigned_node_id
    ]

    return {
        "title": "Transcription",
        "server": server_worker,
        "nodes": remote_nodes,
        "orphaned": orphaned[:MAX_ORPHANED_DISPLAY],
        "orphaned_total": len(orphaned),
        "queued": queue_status["transcribe_queue"]["queued"],
        "distributed_enabled": queue_status.get("distributed_enabled", False),
    }


def build_status_context(data: StatusData, queue_status: dict) -> dict:
    """Derive the status.html template context. Touches no database.

    Args:
        data: Figures read by collect_status_data.
        queue_status: The worker manager's live status dict.

    Returns:
        Template context, without the request key the route adds.
    """
    return {
        "status_counts": data.status_counts,
        "feed_count": data.feed_count,
        "queue_status": queue_status,
        "worker_groups": {
            "download": _build_download_card(data, queue_status),
            "transcript_fetch": _build_transcript_fetch_card(data, queue_status),
            "transcription": _build_transcription_card(data, queue_status),
        },
        "search_stats": data.search_stats,
        "performance_stats": data.performance_stats,
    }
