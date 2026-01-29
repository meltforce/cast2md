"""Node API endpoints for distributed transcription."""

import logging
import secrets
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from cast2md.config.settings import get_settings
from cast2md.db.connection import get_db
from cast2md.db.models import JobStatus, JobType, NodeStatus
from cast2md.db.repository import (
    EpisodeRepository,
    JobRepository,
    TranscriberNodeRepository,
)
from cast2md.distributed import get_coordinator

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


def verify_node_api_key(x_transcriber_key: str = Header(None)) -> str:
    """Verify the node API key from header."""
    if not x_transcriber_key:
        raise HTTPException(status_code=401, detail="Missing X-Transcriber-Key header")
    return x_transcriber_key


def get_node_from_key(api_key: str):
    """Get the node associated with an API key."""
    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        node = repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return node


# === Request/Response Models ===


class RegisterNodeRequest(BaseModel):
    """Request to register a new node."""

    name: str
    url: str
    whisper_model: str | None = None
    whisper_backend: str | None = None


class RegisterNodeResponse(BaseModel):
    """Response with node registration details."""

    node_id: str
    api_key: str
    message: str


class NodeResponse(BaseModel):
    """Response with node details."""

    id: str
    name: str
    url: str
    whisper_model: str | None
    whisper_backend: str | None
    status: str
    last_heartbeat: str | None
    current_job_id: int | None
    priority: int


class NodesListResponse(BaseModel):
    """Response with list of nodes."""

    nodes: list[NodeResponse]
    total: int


class HeartbeatRequest(BaseModel):
    """Heartbeat request from node."""

    name: str | None = None
    whisper_model: str | None = None
    whisper_backend: str | None = None
    current_job_id: int | None = None  # Job currently being processed
    claimed_job_ids: list[int] | None = None  # All jobs node has claimed (current + prefetch)


class HeartbeatResponse(BaseModel):
    """Heartbeat response."""

    status: str
    message: str


class ClaimJobResponse(BaseModel):
    """Response when claiming a job."""

    job_id: int | None
    episode_id: int | None
    episode_title: str | None
    audio_url: str
    has_job: bool


class ClaimEmbedJobResponse(BaseModel):
    """Response when claiming an embed job."""

    job_id: int | None
    episode_id: int | None
    episode_title: str | None
    transcript_url: str
    has_job: bool


class JobCompleteRequest(BaseModel):
    """Request to mark a job as complete."""

    transcript_text: str
    whisper_model: str | None = None


class JobFailRequest(BaseModel):
    """Request to mark a job as failed."""

    error_message: str
    retry: bool = True  # Set to False for permanent failures (404/410)


class JobProgressRequest(BaseModel):
    """Request to update job progress."""

    progress_percent: int


class EmbedCompleteRequest(BaseModel):
    """Request to complete an embed job with embeddings."""

    embeddings: list[dict]  # List of {segment_index, text, embedding}


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


class AddNodeRequest(BaseModel):
    """Admin request to add a node."""

    name: str
    url: str
    whisper_model: str | None = None
    whisper_backend: str | None = None
    priority: int = 10


# === Node Registration Endpoints (called by nodes) ===


@router.post("/register", response_model=RegisterNodeResponse)
def register_node(request: RegisterNodeRequest):
    """Register a new transcriber node.

    This endpoint is called by nodes during initial setup to get credentials.
    If a node with the same name already exists and is offline, it will be
    reused with a new API key (prevents orphaned node entries from pod restarts).
    """
    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)

        # Check if node with same name exists
        existing = repo.get_by_name(request.name)

        if existing and existing.status == NodeStatus.OFFLINE:
            # Reuse existing offline node with new API key
            api_key = secrets.token_urlsafe(32)
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE transcriber_node
                SET url = %s, api_key = %s, whisper_model = %s, whisper_backend = %s,
                    status = %s, last_heartbeat = NULL, current_job_id = NULL,
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    request.url, api_key, request.whisper_model, request.whisper_backend,
                    NodeStatus.OFFLINE.value, datetime.now().isoformat(), existing.id
                ),
            )
            conn.commit()

            return RegisterNodeResponse(
                node_id=existing.id,
                api_key=api_key,
                message=f"Node '{request.name}' re-registered (reused existing entry)",
            )

        # Create new node
        node_id = str(uuid.uuid4())
        api_key = secrets.token_urlsafe(32)

        repo.create(
            node_id=node_id,
            name=request.name,
            url=request.url,
            api_key=api_key,
            whisper_model=request.whisper_model,
            whisper_backend=request.whisper_backend,
        )

    return RegisterNodeResponse(
        node_id=node_id,
        api_key=api_key,
        message=f"Node '{request.name}' registered successfully",
    )


@router.post("/{node_id}/heartbeat", response_model=HeartbeatResponse)
def node_heartbeat(
    node_id: str,
    request: HeartbeatRequest,
    api_key: str = Depends(verify_node_api_key),
):
    """Receive heartbeat from a node.

    Nodes should call this every 30 seconds to indicate they're alive.
    If node reports a current_job_id that lost its assignment (e.g., after server
    restart), the job assignment is restored.
    """
    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        job_repo = JobRepository(conn)
        node = repo.get_by_id(node_id)

        if not node:
            raise HTTPException(status_code=404, detail="Node not found")

        if node.api_key != api_key:
            raise HTTPException(status_code=401, detail="Invalid API key for this node")

        # Update heartbeat via coordinator (in-memory) or direct DB if coordinator not running
        coordinator = get_coordinator()
        if coordinator.is_running:
            # In-memory heartbeat (no DB write)
            coordinator.record_heartbeat(node_id)
        else:
            # Fallback: direct DB write
            repo.update_heartbeat(node_id)

        if request.name or request.whisper_model or request.whisper_backend:
            repo.update_info(
                node_id,
                name=request.name or node.name,
                whisper_model=request.whisper_model or node.whisper_model,
                whisper_backend=request.whisper_backend or node.whisper_backend,
            )

        # Track effective status (may change from OFFLINE to ONLINE/BUSY)
        effective_status = node.status
        if node.status == NodeStatus.OFFLINE:
            # Node came back online - set to BUSY if working, else ONLINE
            effective_status = NodeStatus.BUSY if request.current_job_id else NodeStatus.ONLINE

        # Resync job assignment if node reports a job that lost its assignment
        # (can happen after server restart)
        if request.current_job_id:
            job = job_repo.get_by_id(request.current_job_id)
            if job and job.status == JobStatus.RUNNING and job.assigned_node_id is None:
                job_repo.resync_job(request.current_job_id, node_id)
                logger.info(f"Resynced job {request.current_job_id} to node {node_id}")

            # Update node's current_job_id and status
            repo.update_status(node_id, effective_status, current_job_id=request.current_job_id)
        elif effective_status != node.status:
            # No current job but status changed (offline -> online)
            repo.update_status(node_id, effective_status)

        # Release orphaned jobs: jobs assigned to this node but not in claimed_job_ids
        # This handles cases where node lost its prefetch queue (e.g., node restart)
        if request.claimed_job_ids is not None:
            assigned_jobs = job_repo.get_jobs_by_node(node_id)
            claimed_set = set(request.claimed_job_ids)
            for job in assigned_jobs:
                if job.id not in claimed_set and job.status == JobStatus.RUNNING:
                    job_repo.release_job(job.id)
                    logger.info(f"Released orphaned job {job.id} from node {node_id}")

    return HeartbeatResponse(status="ok", message="Heartbeat received")


class TerminationRequestResponse(BaseModel):
    """Response for termination request."""

    status: str
    message: str
    terminated: bool


@router.post("/{node_id}/request-termination", response_model=TerminationRequestResponse)
def request_termination(
    node_id: str,
    api_key: str = Depends(verify_node_api_key),
):
    """Request termination of a RunPod worker pod.

    Called by node workers before auto-terminating. This allows the server
    to terminate the pod and clean up state atomically, preventing orphaned
    setup states.

    Only works for RunPod Afterburner nodes (name matches "RunPod Afterburner *").
    """
    import re

    from cast2md.services.runpod_service import get_runpod_service

    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        node = repo.get_by_id(node_id)

        if not node:
            raise HTTPException(status_code=404, detail="Node not found")

        if node.api_key != api_key:
            raise HTTPException(status_code=401, detail="Invalid API key for this node")

        # Check if this is a RunPod Afterburner node
        match = re.match(r"RunPod Afterburner (\w+)", node.name)
        if not match:
            return TerminationRequestResponse(
                status="ignored",
                message="Not a RunPod Afterburner node, termination not applicable",
                terminated=False,
            )

        instance_id = match.group(1)

        # Get RunPod service and terminate
        service = get_runpod_service()
        if not service.is_available():
            return TerminationRequestResponse(
                status="unavailable",
                message="RunPod service not available",
                terminated=False,
            )

        success = service.terminate_by_instance_id(instance_id)
        if success:
            return TerminationRequestResponse(
                status="ok",
                message=f"Terminated pod for instance {instance_id}",
                terminated=True,
            )
        else:
            return TerminationRequestResponse(
                status="failed",
                message=f"Failed to terminate pod for instance {instance_id}",
                terminated=False,
            )


@router.post("/{node_id}/claim", response_model=ClaimJobResponse)
def claim_job(
    node_id: str,
    api_key: str = Depends(verify_node_api_key),
):
    """Claim the next available transcription job.

    The node will poll this endpoint to get work. If a job is available,
    it will be assigned to the node.
    """
    with get_db() as conn:
        node_repo = TranscriberNodeRepository(conn)
        job_repo = JobRepository(conn)
        episode_repo = EpisodeRepository(conn)

        node = node_repo.get_by_id(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")

        if node.api_key != api_key:
            raise HTTPException(status_code=401, detail="Invalid API key for this node")

        # Update heartbeat
        node_repo.update_heartbeat(node_id)

        # Get next unclaimed transcription job
        job = job_repo.get_next_unclaimed_job(JobType.TRANSCRIBE)

        if not job:
            return ClaimJobResponse(
                job_id=None,
                episode_id=None,
                episode_title=None,
                audio_url="",
                has_job=False,
            )

        # Claim the job
        job_repo.claim_job(job.id, node_id)

        # Update node status to busy
        node_repo.update_status(node_id, NodeStatus.BUSY, current_job_id=job.id)

        # Get episode details
        episode = episode_repo.get_by_id(job.episode_id)

        return ClaimJobResponse(
            job_id=job.id,
            episode_id=job.episode_id,
            episode_title=episode.title if episode else None,
            audio_url=f"/api/nodes/jobs/{job.id}/audio",
            has_job=True,
        )


@router.get("/jobs/{job_id}/audio")
def get_job_audio(
    job_id: int,
    api_key: str = Depends(verify_node_api_key),
):
    """Stream the audio file for a job to the node."""
    with get_db() as conn:
        job_repo = JobRepository(conn)
        node_repo = TranscriberNodeRepository(conn)
        episode_repo = EpisodeRepository(conn)

        # Verify API key belongs to a valid node
        node = node_repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")

        job = job_repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Verify this node owns this job
        if job.assigned_node_id != node.id:
            raise HTTPException(status_code=403, detail="Job not assigned to this node")

        episode = episode_repo.get_by_id(job.episode_id)
        if not episode or not episode.audio_path:
            raise HTTPException(status_code=404, detail="Audio file not found")

        audio_path = Path(episode.audio_path)
        if not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found on disk")

        return FileResponse(
            path=audio_path,
            media_type="audio/mpeg",
            filename=audio_path.name,
        )


@router.post("/jobs/{job_id}/complete", response_model=MessageResponse)
def complete_job(
    job_id: int,
    request: JobCompleteRequest,
    api_key: str = Depends(verify_node_api_key),
):
    """Mark a job as complete and submit the transcript.

    The node calls this after successfully transcribing the audio.
    """
    import logging

    from cast2md.db.models import EpisodeStatus
    from cast2md.db.repository import FeedRepository
    from cast2md.search.repository import TranscriptSearchRepository
    from cast2md.storage.filesystem import ensure_podcast_directories, get_transcript_path

    logger = logging.getLogger(__name__)

    with get_db() as conn:
        job_repo = JobRepository(conn)
        node_repo = TranscriberNodeRepository(conn)
        episode_repo = EpisodeRepository(conn)

        node = node_repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")

        job = job_repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.assigned_node_id != node.id:
            raise HTTPException(status_code=403, detail="Job not assigned to this node")

        episode = episode_repo.get_by_id(job.episode_id)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        # Get feed info for storage path
        feed_repo = FeedRepository(conn)
        feed = feed_repo.get_by_id(episode.feed_id)
        feed_title = feed.display_title if feed else "unknown"

        # Ensure directories exist
        ensure_podcast_directories(feed_title)

        # Get transcript path and save
        transcript_path = get_transcript_path(
            feed_title,
            episode.title,
            episode.published_at,
        )
        transcript_path.write_text(request.transcript_text, encoding="utf-8")

        # Update episode with transcript path and model info
        episode_repo.update_transcript_path_and_model(
            episode.id,
            str(transcript_path),
            request.whisper_model or "unknown"
        )
        episode_repo.update_status(episode.id, EpisodeStatus.COMPLETED)

        # Index transcript for search
        try:
            search_repo = TranscriptSearchRepository(conn)
            search_repo.index_episode(episode.id, str(transcript_path))
        except Exception as index_error:
            # Don't fail job completion if indexing fails
            logger.warning(f"Failed to index transcript for episode {episode.id}: {index_error}")

        # Mark job complete
        job_repo.mark_completed(job_id)

        # Update node status back to online
        node_repo.update_status(node.id, NodeStatus.ONLINE, current_job_id=None)

    return MessageResponse(message="Job completed successfully")


@router.post("/jobs/{job_id}/progress", response_model=MessageResponse)
def update_job_progress(
    job_id: int,
    request: JobProgressRequest,
    api_key: str = Depends(verify_node_api_key),
):
    """Update job progress.

    The node calls this periodically during transcription to report progress.
    """
    with get_db() as conn:
        job_repo = JobRepository(conn)
        node_repo = TranscriberNodeRepository(conn)

        node = node_repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")

        job = job_repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.assigned_node_id != node.id:
            raise HTTPException(status_code=403, detail="Job not assigned to this node")

        # Update progress
        job_repo.update_progress(job_id, request.progress_percent)

    return MessageResponse(message="Progress updated")


@router.post("/jobs/{job_id}/fail", response_model=MessageResponse)
def fail_job(
    job_id: int,
    request: JobFailRequest,
    api_key: str = Depends(verify_node_api_key),
):
    """Mark a job as failed.

    The node calls this if transcription fails.
    """
    import logging

    logger = logging.getLogger(__name__)

    with get_db() as conn:
        job_repo = JobRepository(conn)
        node_repo = TranscriberNodeRepository(conn)
        episode_repo = EpisodeRepository(conn)

        node = node_repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")

        job = job_repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.assigned_node_id != node.id:
            raise HTTPException(status_code=403, detail="Job not assigned to this node")

        # Log the failure with details
        episode = episode_repo.get_by_id(job.episode_id)
        episode_title = episode.title if episode else f"episode {job.episode_id}"
        logger.warning(
            f"Job {job_id} failed on node '{node.name}': {request.error_message} "
            f"(episode: {episode_title}, attempt {job.attempts + 1}/{job.max_attempts}, "
            f"retry={request.retry})"
        )

        # Update episode status
        from cast2md.db.models import EpisodeStatus

        episode_repo.update_status(job.episode_id, EpisodeStatus.FAILED, request.error_message)

        # Mark episode as permanently failed if retry=False (e.g., 404/410)
        if not request.retry:
            episode_repo.mark_permanent_failure(job.episode_id)

        # Mark job failed - this will handle retry logic
        job_repo.mark_failed(job_id, request.error_message, retry=request.retry)

        # Also unclaim the job so it can be picked up again
        job_repo.unclaim_job(job_id)

        # Update node status back to online
        node_repo.update_status(node.id, NodeStatus.ONLINE, current_job_id=None)

    return MessageResponse(message="Job marked as failed")


@router.post("/jobs/{job_id}/release", response_model=MessageResponse)
def release_job(
    job_id: int,
    api_key: str = Depends(verify_node_api_key),
):
    """Release a job back to queue.

    Called by a node on shutdown to release its current job so it can
    be picked up by another node (or the same node after restart).
    """
    with get_db() as conn:
        job_repo = JobRepository(conn)
        node_repo = TranscriberNodeRepository(conn)

        node = node_repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")

        job = job_repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.assigned_node_id != node.id:
            raise HTTPException(status_code=403, detail="Job not assigned to this node")

        # Reset job to queued
        job_repo.force_reset(job_id)

        # Clear node's current job
        node_repo.update_status(node.id, NodeStatus.ONLINE, current_job_id=None)

    return MessageResponse(message="Job released back to queue")


# === Embedding Job Endpoints (for distributed embedding) ===


@router.post("/{node_id}/claim-embed", response_model=ClaimEmbedJobResponse)
def claim_embed_job(
    node_id: str,
    api_key: str = Depends(verify_node_api_key),
):
    """Claim the next available embedding job.

    Nodes can call this when idle to help with embedding backfills.
    Returns transcript URL for download and embedding generation.
    """
    with get_db() as conn:
        node_repo = TranscriberNodeRepository(conn)
        job_repo = JobRepository(conn)
        episode_repo = EpisodeRepository(conn)

        node = node_repo.get_by_id(node_id)
        if not node:
            raise HTTPException(status_code=404, detail="Node not found")

        if node.api_key != api_key:
            raise HTTPException(status_code=401, detail="Invalid API key for this node")

        # Update heartbeat
        node_repo.update_heartbeat(node_id)

        # Get next unclaimed embed job
        job = job_repo.get_next_unclaimed_job(JobType.EMBED)

        if not job:
            return ClaimEmbedJobResponse(
                job_id=None,
                episode_id=None,
                episode_title=None,
                transcript_url="",
                has_job=False,
            )

        # Claim the job
        job_repo.claim_job(job.id, node_id)

        # Get episode details
        episode = episode_repo.get_by_id(job.episode_id)

        return ClaimEmbedJobResponse(
            job_id=job.id,
            episode_id=job.episode_id,
            episode_title=episode.title if episode else None,
            transcript_url=f"/api/nodes/jobs/{job.id}/transcript",
            has_job=True,
        )


@router.get("/jobs/{job_id}/transcript")
def get_job_transcript(
    job_id: int,
    api_key: str = Depends(verify_node_api_key),
):
    """Get the transcript content for an embed job."""
    with get_db() as conn:
        job_repo = JobRepository(conn)
        node_repo = TranscriberNodeRepository(conn)
        episode_repo = EpisodeRepository(conn)

        # Verify API key belongs to a valid node
        node = node_repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")

        job = job_repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Verify this node owns this job
        if job.assigned_node_id != node.id:
            raise HTTPException(status_code=403, detail="Job not assigned to this node")

        episode = episode_repo.get_by_id(job.episode_id)
        if not episode or not episode.transcript_path:
            raise HTTPException(status_code=404, detail="Transcript not found")

        transcript_path = Path(episode.transcript_path)
        if not transcript_path.exists():
            raise HTTPException(status_code=404, detail="Transcript file not found on disk")

        # Return transcript content as JSON
        content = transcript_path.read_text(encoding="utf-8")
        return {"transcript_content": content}


@router.post("/jobs/{job_id}/complete-embed", response_model=MessageResponse)
def complete_embed_job(
    job_id: int,
    request: EmbedCompleteRequest,
    api_key: str = Depends(verify_node_api_key),
):
    """Complete an embed job by uploading generated embeddings.

    The node calls this after generating embeddings for the transcript.
    """
    import logging

    from cast2md.search.repository import TranscriptSearchRepository

    logger = logging.getLogger(__name__)

    with get_db() as conn:
        job_repo = JobRepository(conn)
        node_repo = TranscriberNodeRepository(conn)
        episode_repo = EpisodeRepository(conn)

        node = node_repo.get_by_api_key(api_key)
        if not node:
            raise HTTPException(status_code=401, detail="Invalid API key")

        job = job_repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.assigned_node_id != node.id:
            raise HTTPException(status_code=403, detail="Job not assigned to this node")

        episode = episode_repo.get_by_id(job.episode_id)
        if not episode:
            raise HTTPException(status_code=404, detail="Episode not found")

        # Store embeddings in database
        try:
            search_repo = TranscriptSearchRepository(conn)
            count = search_repo.store_embeddings_from_node(
                episode_id=job.episode_id,
                embeddings=request.embeddings,
            )
            logger.info(f"Stored {count} embeddings for episode {job.episode_id} from node {node.name}")
        except Exception as e:
            logger.error(f"Failed to store embeddings: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to store embeddings: {e}")

        # Mark job complete
        job_repo.mark_completed(job_id)

        # Update node status back to online (don't track embed jobs as current_job)
        node_repo.update_status(node.id, NodeStatus.ONLINE, current_job_id=None)

    return MessageResponse(message=f"Embed job completed, stored {count} embeddings")


# === Admin Endpoints (for UI/management) ===


@router.get("", response_model=NodesListResponse)
def list_nodes():
    """List all registered nodes (admin endpoint)."""
    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        nodes = repo.get_all()

    return NodesListResponse(
        nodes=[
            NodeResponse(
                id=n.id,
                name=n.name,
                url=n.url,
                whisper_model=n.whisper_model,
                whisper_backend=n.whisper_backend,
                status=n.status.value,
                last_heartbeat=n.last_heartbeat.isoformat() if n.last_heartbeat else None,
                current_job_id=n.current_job_id,
                priority=n.priority,
            )
            for n in nodes
        ],
        total=len(nodes),
    )


@router.post("", response_model=RegisterNodeResponse)
def admin_add_node(request: AddNodeRequest):
    """Manually add a node (admin endpoint)."""
    node_id = str(uuid.uuid4())
    api_key = secrets.token_urlsafe(32)

    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        repo.create(
            node_id=node_id,
            name=request.name,
            url=request.url,
            api_key=api_key,
            whisper_model=request.whisper_model,
            whisper_backend=request.whisper_backend,
            priority=request.priority,
        )

    return RegisterNodeResponse(
        node_id=node_id,
        api_key=api_key,
        message=f"Node '{request.name}' added successfully",
    )


@router.delete("/{node_id}", response_model=MessageResponse)
def delete_node(node_id: str):
    """Delete a node (admin endpoint)."""
    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        if repo.delete(node_id):
            return MessageResponse(message="Node deleted")
        raise HTTPException(status_code=404, detail="Node not found")


@router.post("/{node_id}/test", response_model=MessageResponse)
def test_node(node_id: str):
    """Test connectivity to a node (admin endpoint)."""
    import httpx

    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        node = repo.get_by_id(node_id)

        if not node:
            raise HTTPException(status_code=404, detail="Node not found")

    try:
        # Try to reach the node's status endpoint
        response = httpx.get(f"{node.url}/status", timeout=5.0)
        if response.status_code == 200:
            return MessageResponse(message=f"Node '{node.name}' is reachable")
        else:
            return MessageResponse(message=f"Node returned status {response.status_code}")
    except httpx.RequestError as e:
        return MessageResponse(message=f"Failed to reach node: {e}")


# === Cleanup Endpoints ===


class StaleNodeInfo(BaseModel):
    """Info about a stale node."""

    id: str
    name: str
    last_heartbeat: str | None
    offline_hours: int


class StaleNodesResponse(BaseModel):
    """Response with stale nodes info."""

    stale_count: int
    threshold_hours: int
    nodes: list[StaleNodeInfo]


class CleanupResponse(BaseModel):
    """Response for cleanup operations."""

    deleted_count: int
    message: str


@router.get("/stale", response_model=StaleNodesResponse)
def get_stale_nodes(offline_hours: int = 24):
    """Get nodes that have been offline for longer than threshold.

    Args:
        offline_hours: Hours a node must be offline to be considered stale.
    """
    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        stale_nodes = repo.get_stale_offline_nodes(offline_hours)

    nodes = []
    for n in stale_nodes:
        hours_offline = 0
        if n.last_heartbeat:
            delta = datetime.now() - n.last_heartbeat
            hours_offline = int(delta.total_seconds() / 3600)
        nodes.append(StaleNodeInfo(
            id=n.id,
            name=n.name,
            last_heartbeat=n.last_heartbeat.isoformat() if n.last_heartbeat else None,
            offline_hours=hours_offline,
        ))

    return StaleNodesResponse(
        stale_count=len(nodes),
        threshold_hours=offline_hours,
        nodes=nodes,
    )


@router.delete("/stale", response_model=CleanupResponse)
def cleanup_stale_nodes(offline_hours: int = 24):
    """Delete nodes that have been offline for longer than threshold.

    Args:
        offline_hours: Hours a node must be offline before deletion.
    """
    with get_db() as conn:
        repo = TranscriberNodeRepository(conn)
        deleted = repo.cleanup_stale_nodes(offline_hours)

    return CleanupResponse(
        deleted_count=deleted,
        message=f"Deleted {deleted} stale nodes (offline > {offline_hours}h)",
    )
